"""
Núcleo conversacional de JARVIS («el cerebro»)
===============================================
Integración local con Ollama: personalidad, contexto de la conversación y
generación de respuestas. Aquí no se ejecuta ninguna acción: el modelo solo
conversa (ver tools/intent_engine.py para la parte de las órdenes).

  - Arranca «ollama serve» si no está en marcha (ajuste ia.arrancar_ollama).
  - Genera en streaming: la interfaz ve la respuesta mientras se escribe y la
    voz puede empezar a hablar al acabar la primera frase.
  - Se puede cancelar a mitad (parada de emergencia).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import subprocess
import threading
import time

from core.personality import ESTILOS
from core.text import fecha_larga, hora_corta

registro = logging.getLogger("jarvis.cerebro")

MANTENER_MODELO = "30m"          # tiempo que Ollama conserva el modelo en memoria
MAXIMO_TOKENS = 600
MAXIMO_RONDAS = 4                # veces seguidas que el modelo puede pedir funciones
# Tamaño del contexto (tokens). Ollama usa 4096 por defecto y, si lo que se le manda no cabe, CORTA
# POR EL PRINCIPIO: se pierden las instrucciones (la personalidad, las reglas de honestidad). Medido:
# con 4096, el texto de la pantalla o de un archivo y el historial ya no cabían. Con 8192, atención
# «flash» y caché de 8 bits, granite4.1:8b sigue entero en 8 GB de gráfica y a la misma velocidad.
CONTEXTO = 8192
MARGEN_CONTEXTO = 300
CARACTERES_POR_TOKEN = 3.0       # estimación prudente para español (sale a unos 3,3)
ENTORNO_OLLAMA = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}
# Tope cuando la respuesta usa datos o acciones (la charla usa el ajuste). Medido: a 0,5 se inventó
# nombres de archivo («backend/auth.php») teniendo delante la lista buena.
TEMPERATURA_CON_FUNCIONES = 0.3
# Sin este aviso, en la última ronda (ya sin funciones) el modelo decía «vuelvo a buscarlo» y no lo hacía.
AVISO_ULTIMA_RONDA = ("Ya no puedes usar más herramientas. Responde ahora con lo que has averiguado; si no lo "
                      "has encontrado, dilo en una frase. No digas que vas a buscar ni a comprobar nada más.")
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

IDIOMAS = {"es": "español de España"}

# Se repite el tono al final del todo: con muchas reglas delante, el modelo lo olvidaba. Sin frases
# de ejemplo a propósito: granite4.1:8b las copiaba palabra por palabra en casi cualquier respuesta
# (medido: 10 de 24 respuestas empezaban con la misma frase de ejemplo, viniera o no a cuento).
_NO_ANUNCIES = "No anuncies tu estilo ni digas que eres irónica o sarcástica: sé así sin más."
RECORDATORIO_TONO = {
    1: "TONO: cercano y elegante, con alguna ironía ligera de vez en cuando. " + _NO_ANUNCIES,
    2: ("TONO: en cada respuesta, también en las útiles, pon un comentario ingenioso tuyo, breve y con cariño, y "
        "luego ayuda de verdad. Usa recursos como la ironía sobre lo evidente, la falsa modestia, la exageración "
        "dramática o un comentario seco, como un mayordomo británico con mucha paciencia; cada vez uno distinto "
        "y que encaje con lo que te dicen. " + _NO_ANUNCIES + " Si te piden un chiste, invéntalo tú: corto, "
        "ingenioso y para adultos, nada de juegos de palabras infantiles."),
    3: ("TONO: en casi cada respuesta, un comentario mordaz y teatral, sin crueldad, y luego ayuda de verdad: "
        "exageración dramática, falsa resignación, humor negro ligero; cada vez distinto. " + _NO_ANUNCIES +
        " Si te piden un chiste, invéntalo tú, con humor negro ligero."),
}


class ErrorCerebro(Exception):
    """El modelo no ha podido responder. `str(error)` es un mensaje apto para la usuaria."""

    def __init__(self, mensaje: str, motivo: str = "error"):
        super().__init__(mensaje)
        self.motivo = motivo


class Cerebro:
    def __init__(self, ajustes):
        self.ajustes = ajustes
        self._cliente = None
        self._host_cliente = None
        self._proceso_ollama = None
        self._cerrojo_arranque = threading.Lock()
        self._capacidades = {}

    # ------------------------------------------------------------------
    # CONEXIÓN CON OLLAMA
    # ------------------------------------------------------------------
    def _cli(self):
        host = self.ajustes.obtener("ia.host")
        if self._cliente is None or host != self._host_cliente:
            import ollama
            self._cliente = ollama.Client(host=host, timeout=120)
            self._host_cliente = host
        return self._cliente

    @property
    def modelo(self) -> str:
        return self.ajustes.obtener("ia.modelo")

    @property
    def contexto(self) -> int:
        try:
            return max(2048, int(self.ajustes.obtener("ia.contexto", CONTEXTO)))
        except (TypeError, ValueError):
            return CONTEXTO

    def disponible(self) -> bool:
        try:
            self._cli().list()
            return True
        except Exception:
            return False

    def asegurar_servidor(self, espera: float = 15.0) -> bool:
        """Comprueba que Ollama responde; si no, intenta arrancarlo en segundo plano."""
        if self.disponible():
            return True
        if not self.ajustes.obtener("ia.arrancar_ollama", True):
            return False
        with self._cerrojo_arranque:
            if self.disponible():
                return True
            ejecutable = shutil.which("ollama") or os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
            if not ejecutable or not os.path.exists(ejecutable):
                registro.warning("Ollama no está instalado o no se encuentra.")
                return False
            try:
                registro.info("Arrancando Ollama...")
                # Lo que ya hayas configurado tú en el sistema se respeta; si no, contexto más ligero.
                entorno = dict(os.environ)
                for clave, valor in ENTORNO_OLLAMA.items():
                    entorno.setdefault(clave, valor)
                self._proceso_ollama = subprocess.Popen(
                    [ejecutable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=SIN_VENTANA, env=entorno)
            except OSError as error:
                registro.warning("No se pudo arrancar Ollama: %s", error)
                return False
            limite = time.time() + espera
            while time.time() < limite:
                if self.disponible():
                    return True
                time.sleep(0.5)
            return False

    def cerrar(self, cerrar_conexiones: bool = True) -> None:
        """Detiene Ollama solo si lo arrancó JARVIS (si ya estaba abierto, no se toca)."""
        proceso, self._proceso_ollama = self._proceso_ollama, None
        if proceso and proceso.poll() is None:
            try:
                # También los procesos hijos, que son los que tienen el modelo cargado en memoria.
                import psutil
                for hijo in psutil.Process(proceso.pid).children(recursive=True):
                    hijo.terminate()
            except Exception:
                pass
            try:
                proceso.terminate()
                registro.info("Ollama detenido (lo había arrancado JARVIS).")
            except OSError:
                pass
        if cerrar_conexiones:
            try:
                self._cliente._client.close()    # cierra las conexiones abiertas con Ollama
            except Exception:
                pass
            self._cliente = None

    def modelos(self) -> list:
        """Modelos de lenguaje instalados en Ollama (sin los de embeddings)."""
        try:
            respuesta = self._cli().list()
            nombres = [m.get("model") or m.get("name") for m in respuesta.get("models", [])]
        except Exception:
            return []
        return sorted(n for n in nombres if n and "embed" not in n)

    def precargar(self) -> bool:
        """Carga el modelo en memoria para que la primera respuesta no tarde.

        Se le pide una respuesta de una sola palabra: así queda cargado e
        inicializado del todo, no solo leído del disco.
        """
        try:
            # Con el mismo contexto que las respuestas: si no, Ollama tendría que volver a cargarlo.
            self._cli().chat(model=self.modelo, messages=[{"role": "user", "content": "hola"}],
                             options={"num_predict": 1, "num_ctx": self.contexto}, keep_alive=MANTENER_MODELO)
            return True
        except Exception as error:
            registro.warning("No se pudo precargar el modelo %s: %s", self.modelo, error)
            return False

    # ------------------------------------------------------------------
    # PERSONALIDAD
    # ------------------------------------------------------------------
    def prompt_sistema(self, modo: str = "texto", contexto_memoria: str = "", resumen_previo: str = "",
                       con_funciones: bool = False) -> str:
        nombre = self.ajustes.obtener("usuario.nombre") or "la persona que te habla"
        sarcasmo = int(self.ajustes.obtener("personalidad.sarcasmo", 2))
        # Cómo dirigirse a la usuaria (el modelo dudaba: «¿Listo para…?» a Ruth).
        trato = {"femenino": " Al hablarle a ella, usa el femenino («¿estás lista?», «cansada»).",
                 "masculino": " Al hablarle a él, usa el masculino («¿estás listo?», «cansado»)."}.get(
            self.ajustes.obtener("usuario.genero", "femenino"), "")
        idioma = IDIOMAS.get(self.ajustes.obtener("idioma", "es"), IDIOMAS["es"])
        ahora = datetime.datetime.now()
        formato = ("Tu respuesta se va a leer en voz alta: escribe solo texto corrido, sin listas, símbolos, "
                   "asteriscos ni emojis." if modo == "voz" else
                   "No uses emojis ni negritas. Puedes usar una lista corta solo si de verdad ayuda.")
        # La forma de hablar de sí acompaña a la voz elegida (femenina por defecto).
        if self.ajustes.obtener("voz.genero", "femenino") == "masculino":
            genero = "Tienes voz masculina: cuando hables de ti, usa el masculino («estoy listo», «encantado»)."
        else:
            genero = "Tienes voz femenina: cuando hables de ti, usa siempre el femenino («estoy lista», «encantada»)."
        if con_funciones:
            # Evaluado con granite4.1:8b (varias respuestas por pregunta): sin estas precisiones daba de
            # memoria fechas y nacionalidades equivocadas y buscaba «mundial 2022» por su cuenta.
            no_sabes = ("Si te preguntan por una persona, un grupo, una canción, un lugar, una empresa, un "
                        "partido, una noticia o un precio concretos, llama SIEMPRE a buscar_en_internet antes de "
                        "responder, aunque creas saberlo, y di solo lo que digan los resultados, copiando nombres, "
                        "fechas y cifras tal como aparecen; si no lo dicen, reconoce que no lo has encontrado y no "
                        "des datos ni cifras de memoria. No pongas en la búsqueda un año que nadie ha dicho. Si los "
                        "resultados cuentan algo como ya ocurrido, es que ha ocurrido: nunca digas que «aún no ha "
                        "pasado» o que «es futuro» algo que los resultados cuentan. Lo mismo con lo que devuelvan "
                        "las demás herramientas (archivos, carpetas): copia los nombres tal cual, sin inventar otros.")
            sentidos = ("salvo lo que te digan tus herramientas (leer_pantalla, que_esta_sonando, leer_archivo, "
                        f"buscar_en_memoria...). Puedes buscar, mirar y leer los archivos y carpetas de {nombre} con "
                        "tus herramientas sin pedir permiso: es su ordenador y te lo pide ella.")
            acciones = ("Para hacer algo (poner música, abrir un programa, guardar un dato en tu memoria, preparar un "
                        "mensaje de Discord, leer un archivo...) llama a la herramienta que lo hace y cuenta solo lo "
                        "que haya devuelto. Si no tienes herramienta para algo (escribir en otra aplicación, cerrar "
                        "programas, apagar el ordenador...), dilo claramente y explica cómo pedírmelo con una orden "
                        "directa, por ejemplo «cierra Spotify».")
        else:
            no_sabes = "Si no lo sabes con seguridad, dilo claramente y sugiere buscarlo en Internet."
            sentidos = "en ningún caso."
            acciones = (f"Tú solo conversas: si te piden una acción, explica a {nombre} cómo pedírtela "
                        "(por ejemplo «abre Spotify» o «pon algo de Queen»).")
        partes = [
            f"Eres J.A.R.V.I.S., la inteligencia artificial personal de {nombre}, inspirada en la de Tony Stark. "
            "Funcionas en local, en su ordenador.",
            ESTILOS.get(sarcasmo, ESTILOS[2]),
            genero,
            "REGLAS:",
            f"1. Responde SIEMPRE en {idioma}, te hablen en el idioma que te hablen.",
            "2. Sé breve y directo: de una a tres frases, salvo que te pidan más detalle.",
            f"3. Hablas con {nombre}. Tutea siempre (usa «tú», nunca «usted») y usa su nombre solo de vez "
            f"en cuando, como en una conversación normal.{trato}",
            f"4. HONESTIDAD: nunca inventes datos, nombres, fechas, cifras ni canciones. {no_sabes}",
            # Con ejemplos concretos el modelo deja de «explicar» lo que se le cuenta (DAM = «Diseño y Arte
            # Multimedia», inventado) y de confundir a la usuaria con las personas de las que habla. Los
            # ejemplos llevan el tono elegido: con ejemplos neutros, el modelo copiaba su tono y dejaba
            # de ser sarcástico aunque el sarcasmo estuviera activado.
            f"5. Si {nombre} te cuenta algo de su vida, no busques nada ni le expliques lo que te cuenta: "
            "coméntalo con naturalidad o pregúntale algo. Nunca expliques qué significan unas siglas (DAM, TFG, "
            "ASIR...) si no te lo pregunta: no lo adivines. Por ejemplo, si te dice «estudio DAM», pregúntale en "
            "qué curso está o qué tal le va, sin decir qué es DAM. Si te habla de otra "
            "persona («mi profesora se llama Marta»), habla de esa persona en tercera persona. Para saludar, "
            "charlar, opinar o contar un chiste no uses ninguna herramienta.",
            "6. No ves la pantalla ni sabes qué está abierto o qué música suena, " + sentidos,
            "7. Nunca digas que has hecho algo (abrir, poner música, buscar, guardar, mandar un mensaje...) que no "
            f"haya hecho una herramienta en esta conversación. {acciones}",
            "8. La palabra de seguridad la pide y la comprueba JARVIS por su cuenta cuando una orden delicada la "
            "necesita: tú nunca la pidas, ni digas si es correcta, ni la repitas.",
            f"9. {formato}",
            f"Fecha y hora actuales: {fecha_larga(ahora)}, {hora_corta(ahora)}. Tu entrenamiento es anterior: "
            "para hechos recientes (deportes, noticias, precios, versiones) no des por hecho el año ni el resultado.",
        ]
        if contexto_memoria:
            # Están escritos como los diría ella («mi TFG se llama…»): sin avisarlo, el modelo contestaba
            # «Mi TFG, ClubApp_Connect, es…» como si fuera suyo.
            partes.append(f"Datos que recuerdas de {nombre}, escritos como los diría ella (el «mi» es de {nombre}, "
                          f"no tuyo; al hablarle, di «tu»). Úsalos solo si vienen al caso:\n{contexto_memoria}")
        if resumen_previo:
            partes.append(f"De una conversación anterior con {nombre}: {resumen_previo}")
        if RECORDATORIO_TONO.get(sarcasmo):
            partes.append(RECORDATORIO_TONO[sarcasmo])         # al final: es lo que más pesa en la respuesta
        return "\n".join(partes)

    # ------------------------------------------------------------------
    # CAPACIDADES DEL MODELO
    # ------------------------------------------------------------------
    def capacidades(self) -> list:
        """Lo que sabe hacer el modelo elegido según Ollama («tools», «thinking»...)."""
        modelo = self.modelo
        if modelo not in self._capacidades:
            try:
                informacion = self._cli().show(modelo)
                lista = getattr(informacion, "capabilities", None)
                if lista is None and hasattr(informacion, "get"):
                    lista = informacion.get("capabilities")
            except Exception:
                return []                        # no se guarda: puede que Ollama aún no esté listo
            self._capacidades[modelo] = [str(c) for c in (lista or [])]
        return self._capacidades[modelo]

    def admite_herramientas(self) -> bool:
        return "tools" in self.capacidades()

    # ------------------------------------------------------------------
    # GENERACIÓN
    # ------------------------------------------------------------------
    def _llamar(self, mensajes: list, definiciones: list | None = None, al_fragmento=None,
                cancelar: threading.Event | None = None, temperatura: float | None = None,
                maximo: int = MAXIMO_TOKENS) -> tuple:
        """Una petición en streaming al modelo. Devuelve (texto, funciones que pide)."""
        opciones = {
            "temperature": self.ajustes.obtener("ia.temperatura", 0.3) if temperatura is None else temperatura,
            "num_predict": maximo,
            "num_ctx": self.contexto,
        }
        mensajes = self._que_quepa(mensajes, definiciones, maximo)
        extra = {}
        if definiciones:
            extra["tools"] = definiciones
        if "thinking" in self.capacidades():
            # Razonar en voz alta haría esperar mucho más y, al extraer datos o resumir, gastaría
            # los tokens antes de dar la respuesta. Se consulta siempre (queda en caché).
            extra["think"] = False
        for intento in (1, 2):
            try:
                flujo = self._cli().chat(model=self.modelo, messages=mensajes, stream=True,
                                         options=opciones, keep_alive=MANTENER_MODELO, **extra)
                trozos, llamadas = [], []
                for parte in flujo:
                    if cancelar is not None and cancelar.is_set():
                        break
                    mensaje = parte.get("message") or {}
                    trozo = mensaje.get("content") or ""
                    if trozo:
                        trozos.append(trozo)
                        if al_fragmento:
                            al_fragmento(trozo)
                    for llamada in (mensaje.get("tool_calls") or []):
                        funcion = llamada.get("function") or {}
                        argumentos = funcion.get("arguments") or {}
                        llamadas.append({"function": {"name": funcion.get("name") or "",
                                                      "arguments": dict(argumentos) if hasattr(argumentos, "items")
                                                      else argumentos}})
                return "".join(trozos).strip(), llamadas
            except Exception as error:
                texto_error = str(error).lower()
                if "not found" in texto_error or "404" in texto_error:
                    raise ErrorCerebro(
                        f"El modelo «{self.modelo}» no está descargado en Ollama. "
                        "Elige otro en Ajustes o descárgalo con «ollama pull».", "sin_modelo") from error
                if intento == 1 and self.asegurar_servidor():
                    continue                     # Ollama estaba parado: ya arrancado, se reintenta
                registro.error("Fallo de Ollama: %s", error)
                raise ErrorCerebro("No puedo acceder ahora mismo a mi núcleo local. "
                                   "Comprueba que Ollama esté ejecutándose.", "sin_conexion") from error
        return "", []

    def _que_quepa(self, mensajes: list, definiciones: list | None, maximo: int) -> list:
        """Recorta lo que se manda para que quepa en el contexto sin perder las instrucciones.

        Primero se quitan los turnos más antiguos del historial (nunca el mensaje de sistema ni la
        pregunta actual con lo que hayan devuelto sus funciones); si aún no cabe, se acorta lo más
        largo que hayan devuelto las funciones (una página, un archivo, la pantalla).
        """
        limite = (self.contexto - maximo - MARGEN_CONTEXTO) * CARACTERES_POR_TOKEN
        fijo = len(json.dumps(definiciones, ensure_ascii=False)) if definiciones else 0

        def largo(lista):
            return fijo + sum(len(m.get("content") or "") + 30 for m in lista)

        if largo(mensajes) <= limite:
            return mensajes
        mensajes = list(mensajes)
        # El turno actual empieza en el último mensaje de la usuaria.
        actual = max((i for i, m in enumerate(mensajes) if m.get("role") == "user"), default=len(mensajes) - 1)
        quitados = 0
        while largo(mensajes) > limite and actual > 1:
            del mensajes[1]                                  # el turno más antiguo, tras el de sistema
            actual -= 1
            quitados += 1
        if quitados:
            registro.info("Contexto lleno: se dejan fuera %d mensajes antiguos.", quitados)
        while largo(mensajes) > limite:
            largos = [i for i in range(actual, len(mensajes))
                      if mensajes[i].get("role") in ("tool", "system") and len(mensajes[i].get("content") or "") > 800]
            if not largos:
                break
            i = max(largos, key=lambda j: len(mensajes[j]["content"]))
            sobra = int(largo(mensajes) - limite) + 200
            contenido = mensajes[i]["content"]
            mensajes[i] = dict(mensajes[i], content=contenido[:max(600, len(contenido) - sobra)] + "\n(…recortado)")
        return mensajes

    def _generar(self, mensajes: list, al_fragmento=None, cancelar: threading.Event | None = None,
                 temperatura: float | None = None, maximo: int = MAXIMO_TOKENS) -> str:
        return self._llamar(mensajes, None, al_fragmento, cancelar, temperatura, maximo)[0]

    def pensar(self, texto: str, historial: list | None = None, modo: str = "texto",
               contexto_memoria: str = "", resumen_previo: str = "", al_fragmento=None,
               cancelar: threading.Event | None = None, funciones=None) -> str:
        """Respuesta conversacional a `texto`, con la personalidad y el contexto de JARVIS.

        Si se le pasan `funciones` y el modelo sabe usarlas, el modelo puede pedirlas (buscar en
        Internet, mirar qué suena...) antes de responder: se ejecutan, se le devuelve el resultado
        y vuelve a intentarlo, hasta MAXIMO_RONDAS veces.
        """
        con_funciones = funciones is not None and self.admite_herramientas()
        mensajes = [{"role": "system",
                     "content": self.prompt_sistema(modo, contexto_memoria, resumen_previo, con_funciones)}]
        mensajes.extend(historial or [])
        mensajes.append({"role": "user", "content": texto})
        previas = list(getattr(funciones, "previas", None) or [])
        if not con_funciones:
            for _, argumentos, resultado in previas:      # un modelo sin herramientas lo recibe como texto
                mensajes.append({"role": "system", "content": (
                    f"Has buscado «{argumentos.get('consulta', '')}» en Internet y esto es lo que has encontrado. "
                    f"Responde con lo que diga; si no lo dice, reconoce que no lo sabes:\n{resultado}")})
            return self._generar(mensajes, al_fragmento, cancelar)
        for nombre, argumentos, resultado in previas:      # como si el modelo lo hubiera pedido
            mensajes.append({"role": "assistant", "content": "",
                             "tool_calls": [{"function": {"name": nombre, "arguments": argumentos}}]})
            mensajes.append({"role": "tool", "content": resultado, "tool_name": nombre})

        definiciones = funciones.definiciones()
        # Para datos y acciones, poca «creatividad» aunque el ajuste esté alto: medido con granite4.1:8b,
        # a 1,1 inventaba («DAM es Diseño Asistido por Computadora») y confundía a quién hablaba.
        temperatura = min(float(self.ajustes.obtener("ia.temperatura", 0.3)), TEMPERATURA_CON_FUNCIONES)
        partes = []
        for ronda in range(MAXIMO_RONDAS + 1):
            ultima = ronda == MAXIMO_RONDAS          # en la última ya no se ofrecen funciones: toca responder
            if ultima:
                mensajes.append({"role": "system", "content": AVISO_ULTIMA_RONDA})
            contenido, llamadas = self._llamar(mensajes, None if ultima else definiciones, al_fragmento, cancelar,
                                               temperatura=temperatura)
            if contenido:
                partes.append(contenido)
            if not llamadas or (cancelar is not None and cancelar.is_set()):
                break
            mensajes.append({"role": "assistant", "content": contenido, "tool_calls": llamadas})
            for llamada in llamadas:
                nombre = llamada["function"]["name"]
                resultado = funciones.ejecutar(nombre, llamada["function"]["arguments"])
                mensajes.append({"role": "tool", "content": resultado, "tool_name": nombre})
        return " ".join(partes).strip()

    def resumir(self, mensajes: list) -> str:
        """Resumen breve de una conversación terminada (para recordarla más adelante)."""
        dialogo = "\n".join(f"{'Usuario' if m['role'] == 'user' else 'Jarvis'}: {m['content']}"
                            for m in mensajes)[-6000:]
        nombre = self.ajustes.obtener("usuario.nombre") or "la usuaria"
        instrucciones = (
            "Resume en dos frases, en español y en tercera persona, lo más importante de esta conversación "
            f"entre {nombre} y su asistente: temas tratados, datos personales mencionados y decisiones. "
            "Sin introducción, sin opiniones y sin sarcasmo; solo el resumen.")
        try:
            return self._generar([{"role": "system", "content": instrucciones},
                                  {"role": "user", "content": dialogo}], temperatura=0.2, maximo=160)
        except ErrorCerebro:
            return ""

    def extraer_datos(self, texto: str, nombre: str = "la usuaria") -> list:
        """Datos personales duraderos que contiene un mensaje (para aprender de lo que se habla)."""
        # Con ejemplos acierta mucho más (probado con granite4.1:8b: de 9 a 13 de 13 frases de prueba);
        # sin ellos no guardaba ni dónde vive ni qué estudia.
        # Pasó: «quiero que lo añadas a tu memoria: que mi TFG se llama ClubApp_Connect y que busques…»
        # no dejó nada, porque era una orden. Los datos que van dentro de una petición también cuentan.
        instrucciones = (
            f"Extrae del mensaje de {nombre} los datos DURADEROS sobre su vida que convenga recordar: gustos y lo "
            "que no le gusta, aficiones, dónde vive, qué estudia o en qué trabaja, sus proyectos (su TFG, sus "
            "trabajos), costumbres, y personas o mascotas importantes para ella. Aunque el mensaje sea una "
            "pregunta o una orden al asistente, extrae los datos sobre ella que vayan dentro. NO extraigas: "
            "planes o cosas de un día concreto, cómo se siente ahora, las órdenes en sí ni datos sensibles "
            "(contraseñas, salud, dinero). Escribe cada dato como una frase corta en primera persona, como la "
            "diría ella, que se entienda sin el resto del mensaje. Ejemplos:\n"
            "«me encanta el rock y tengo un gato que se llama Mishi» -> "
            "[\"me encanta el rock\", \"tengo un gato que se llama Mishi\"]\n"
            "«vivo en Valencia y a mi novio Dani le encanta el fútbol» -> "
            "[\"vivo en Valencia\", \"a mi novio Dani le encanta el fútbol\"]\n"
            "«guarda en tu memoria que mi TFG se llama ClubApp y busca info sobre él» -> "
            "[\"mi TFG se llama ClubApp\"]\n"
            "«mañana tengo médico» -> []\n«abre Spotify» -> []\n"
            "Responde SOLO con la lista JSON.")
        try:
            salida = self._generar([{"role": "system", "content": instrucciones},
                                    {"role": "user", "content": texto}], temperatura=0.0, maximo=200)
        except ErrorCerebro:
            return []
        return lista_de_textos(salida)


def lista_de_textos(salida: str) -> list:
    """Interpreta la lista JSON que devuelve el modelo (tolera ```json y texto alrededor)."""
    inicio, fin = salida.find("["), salida.rfind("]")
    if inicio < 0 or fin <= inicio:
        return []
    try:
        datos = json.loads(salida[inicio:fin + 1].replace("“", '"').replace("”", '"'))
    except ValueError:
        return []
    textos = []
    for dato in datos if isinstance(datos, list) else []:
        texto = " ".join(str(dato).split()).strip(" .")
        if 3 <= len(texto) <= 160 and texto.lower() not in (t.lower() for t in textos):
            textos.append(texto)
    return textos
