"""
Orquestador de JARVIS
======================
Coordina todo el recorrido de una frase, venga del teclado o del micrófono:

    entrada -> intención -> ¿herramienta activa? -> ¿necesita permiso?
            -> herramienta o núcleo conversacional -> respuesta (texto y voz)

Reglas que garantiza este módulo:
  - Conversación y acciones van separadas: el modelo de lenguaje nunca lanza
    herramientas; solo las órdenes de la usuaria, pasadas por el motor de
    intenciones y por la capa de seguridad, llegan a ejecutarse.
  - Una acción delicada queda en espera hasta que se confirma (o se dice la
    palabra de seguridad). Caduca sola y admite un máximo de intentos.
  - En una secuencia ("abre X y luego escribe Y"), si un paso necesita permiso
    la secuencia se detiene ahí y continúa solo cuando se concede.
  - Todo lo que hacen las herramientas queda en el registro de auditoría.
  - La palabra de seguridad nunca se guarda ni se muestra en la conversación.

No conoce la interfaz: publica lo que ocurre en el bus de eventos.
"""
from __future__ import annotations

import datetime
import itertools
import logging
import re
import threading
import time
import urllib.parse

from core.brain import ErrorCerebro
from core.events import bus as bus_global
from core.funciones import (CORRECCIONES, Funciones, accion_inventada, es_charla, nombre_por_el_que_pregunta,
                            pide_datos_actuales)
from core.personality import frase
from core.text import fecha_larga, hora_corta, normalizar, plano
from tools import registry
from tools.intent_engine import UMBRAL_CONFIANZA_MINIMO, interpretar
from tools.memory import UMBRAL_OBJETIVO, es_dato_sensible
from tools.recordatorios import (Recordatorios, describir_duracion, describir_momento, frase_aviso, frase_creado,
                                  frase_lista, interpretar_momento, publico, rango_de, segundos_de, separar)
from tools.security import DESCRIPCION_COMANDOS

registro = logging.getLogger("jarvis.orquestador")

CADUCIDAD_CONFIRMACION = 45      # segundos para confirmar una acción delicada
CADUCIDAD_SUGERENCIA = 120       # segundos para aceptar «¿lo guardo en mi memoria?»
MAXIMO_INTENTOS = 3
MASCARA = "••••••"
MAXIMO_CONTEXTO = 80

ETIQUETAS_MULTIMEDIA = {
    "siguiente": "siguiente pista", "anterior": "pista anterior", "subir_volumen": "subir volumen",
    "bajar_volumen": "bajar volumen", "silenciar": "silenciar", "play_pausa": "reproducir / pausar",
}

_EMOJIS = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
# El modelo anuncia su estilo («…siempre con mi toque de sarcasmo») en vez de usarlo: esa frase sobra.
_AUTOBOMBO = re.compile(r"\b(?:sarcasmo|sarcastic[oa]|ironia|ironic[oa])\b")


def _sin_autobombo(texto: str) -> str:
    """Quita las frases en las que la IA habla de su propio sarcasmo (si queda algo)."""
    partes = re.split(r"((?<=[.!?…])\s+)", (texto or "").strip())   # [frase, separador, frase, …]
    frases = partes[0::2]
    quitar = {i for i, f in enumerate(frases) if _AUTOBOMBO.search(plano(f))}
    if not quitar or len(quitar) == len(frases):
        return texto
    salida = []
    for i, frase in enumerate(frases):
        if i not in quitar:
            salida.append(frase)
            if i * 2 + 1 < len(partes):
                salida.append(partes[i * 2 + 1])
    return "".join(salida).strip()

_INTERROGATIVO = re.compile(r"^(?:que|quien|quienes|cual|cuales|como|donde|cuando|cuanto|cuanta|cuantos|"
                            r"cuantas|por que|para que|a que)\b")

# Frases en las que la usuaria cuenta algo de sí misma que podría merecer un recuerdo.
# Marcas de que la usuaria habla de sí misma: solo entonces se intenta aprender algo del mensaje.
_PRIMERA_PERSONA = re.compile(
    r"\b(?:me|mi|mis|yo|soy|estoy|tengo|vivo|trabajo|estudio|prefiero|odio|detesto|gusta|gustan|encanta|"
    r"encantan|llamo|naci|suelo|mio|mia)\b")
MAXIMO_APRENDIDOS = 3            # datos nuevos como mucho por mensaje
UMBRAL_REPETIDO = 0.75           # un dato tan parecido a uno guardado no se vuelve a guardar

_DATO_PERSONAL = re.compile(
    r"^(?:(?:pues|bueno|oye|mira|por cierto|sabes que|que sepas que)[,\s]+)*"
    r"(?P<dato>(?:me gustan?|me encantan?|me flipan?|prefiero|odio|detesto|no me gustan?|no soporto|"
    r"me llamo|vivo en|trabajo (?:en|de|como)|estudio|soy alergic[oa] a|naci (?:en|el)|"
    r"mi cumpleanos es|tengo (?:un|una|dos|tres) (?:perr|gat|herman|hij)\w*|"
    r"mi (?:perro|perra|gato|gata|hermano|hermana|madre|padre|pareja|novio|novia|hijo|hija|mascota) se llama|"
    r"mi \w+(?: \w+)? favorit[oa]s? (?:es|son))\b.+)$"
)


class Herramientas:
    """Puerta a las herramientas reales. Las pruebas la sustituyen por una de mentira."""

    def ejecutar_pc(self, intencion: dict):
        from tools.computer import ejecutar_intencion
        return ejecutar_intencion(intencion)

    def buscar(self, consulta: str, maximo: int = 5, tipo: str = "web") -> list:
        from tools.browser import buscar_resultados
        return buscar_resultados(consulta, maximo, tipo)

    def abrir_busqueda(self, sitio: str, consulta: str) -> tuple:
        from tools.browser import abrir_busqueda
        return abrir_busqueda(sitio, consulta)

    def leer_pagina(self, url: str) -> str:
        from tools.browser import leer_pagina
        return leer_pagina(url)

    def tiempo(self, ciudad: str, dia: str = "hoy") -> dict:
        from tools.weather import obtener_tiempo
        return obtener_tiempo(ciudad, dia)

    def buscar_archivos(self, texto: str) -> list:
        from tools.archivos import buscar
        return buscar(texto)

    def abrir_archivo(self, ruta: str):
        from tools.archivos import abrir
        return abrir(ruta)

    def ultimos_descargados(self, cantidad: int = 1) -> list:
        from tools.archivos import ultimos_descargados
        return ultimos_descargados(cantidad)

    def leer_pantalla(self):
        from tools.pantalla import leer_pantalla
        return leer_pantalla()

    def estado_equipo(self) -> dict:
        from tools.sistema import estado
        return estado()

    def brillo(self):
        from tools.sistema import brillo
        return brillo()

    def poner_brillo(self, valor: int):
        from tools.sistema import poner_brillo
        return poner_brillo(valor)

    def musica_sonando(self, preferida: str = ""):
        from tools import media
        return media.sonando(preferida)

    def musica_controlar(self, accion: str, preferida: str = ""):
        from tools import media
        return media.controlar(accion, preferida)

    def musica_reproducir(self, consulta: str, servicio: str = "youtube"):
        from tools import media
        return media.reproducir(consulta, servicio)

    def enviar_mensaje(self, app: str, contacto: str, texto: str):
        from tools.mensajes import enviar
        return enviar(app, contacto, texto)

    def leer_mensajes(self, app: str, contacto: str):
        from tools.mensajes import leer
        return leer(app, contacto)

    def app_de_mensajes_activa(self) -> str:
        """La aplicación de chat en la que estaba la usuaria («escríbele hola a Pepe» sin decir dónde)."""
        from tools.computer import ventana_externa_activa
        from tools.intent_engine import APPS_DE_MENSAJES
        proceso = plano(ventana_externa_activa().get("proceso", ""))
        return next((app for app in APPS_DE_MENSAJES if proceso.startswith(app)), "")

    def buscar_carpetas(self, texto: str) -> list:
        from tools.archivos import buscar_carpetas
        return buscar_carpetas(texto)

    def leer_archivo(self, ruta: str) -> dict:
        from tools.archivos import leer
        return leer(ruta)

    def explorar_carpeta(self, ruta: str) -> dict:
        from tools.archivos import explorar
        return explorar(ruta)

    def buscar_en_archivos(self, texto: str, carpeta: str = "") -> list:
        from tools.archivos import buscar_contenido
        return buscar_contenido(texto, carpeta)


class _LocutorPorFrases:
    """Va pasando a la voz las frases completas mientras el modelo sigue escribiendo."""
    _FIN_DE_FRASE = re.compile(r"(?<=[.!?…:])\s+|\n+")
    MINIMO = 40

    def __init__(self, habla):
        self.habla = habla
        self.pendiente = ""

    def recibir(self, trozo: str) -> None:
        self.pendiente += trozo
        cortes = [c for c in self._FIN_DE_FRASE.finditer(self.pendiente) if c.start() >= self.MINIMO]
        if cortes:
            corte = cortes[-1]
            self.habla.decir(self.pendiente[:corte.start()])
            self.pendiente = self.pendiente[corte.end():]

    def terminar(self) -> None:
        if self.pendiente.strip():
            self.habla.decir(self.pendiente)
        self.pendiente = ""


class Orquestador:
    def __init__(self, ajustes, memoria, historial, seguridad, cerebro, habla=None, herramientas=None,
                 bus=None, recordatorios=None):
        self.ajustes = ajustes
        self.memoria = memoria
        self.recordatorios = recordatorios or Recordatorios(memoria.bd)
        self.ultimo_aviso = None          # (recordatorio, cuándo sonó): para «pospón la alarma»
        self.ultimos_archivos = []        # resultados de la última búsqueda: para «abre el segundo»
        self.resumen_dado = None          # día en que ya se dio el resumen de «buenos días»
        self.mcp = None                   # gestor de herramientas MCP (core/mcp.py), si hay servidores
        self.historial = historial
        self.seguridad = seguridad
        self.cerebro = cerebro
        self.habla = habla
        self.herramientas = herramientas or Herramientas()
        self.bus = bus or bus_global

        self.conversacion_id = None
        self.sesion = []             # mensajes de la conversación activa, tal como los ve la interfaz
        self._contexto = []          # los mismos mensajes en el formato que entiende el modelo
        self.pendiente = None        # acción delicada o sugerencia de recuerdo a la espera de respuesta
        self.al_apagar = None        # función a la que llamar cuando se pide cerrar JARVIS

        self._cerrojo = threading.RLock()
        self._cancelar = threading.Event()
        self._respuestas = []
        self._autorizado = False
        self._contador = itertools.count(1)
        self.aprender_en_segundo_plano = True     # las pruebas lo ponen a False para que sea inmediato

    # ------------------------------------------------------------------
    # ATAJOS
    # ------------------------------------------------------------------
    @property
    def nombre(self) -> str:
        return self.ajustes.obtener("usuario.nombre") or ""

    @property
    def modo(self) -> str:
        return self.ajustes.obtener("modo", "texto")

    def _frase(self, clave: str, **datos) -> str:
        return frase(clave, int(self.ajustes.obtener("personalidad.sarcasmo", 2)), nombre=self.nombre, **datos)

    def _debe_hablar(self) -> bool:
        return self.habla is not None and (self.modo == "voz" or self.ajustes.obtener("voz.leer_en_modo_texto"))

    def _nuevo_id(self) -> str:
        return f"m{next(self._contador)}-{int(time.time() * 1000)}"

    def _guardar_historial(self) -> bool:
        return bool(self.ajustes.obtener("memoria.guardar_historial", True))

    # ------------------------------------------------------------------
    # ENTRADA PRINCIPAL
    # ------------------------------------------------------------------
    def procesar(self, texto: str, origen: str = "texto") -> list:
        """Atiende una frase de la usuaria. Devuelve los mensajes con los que JARVIS ha respondido."""
        texto = (texto or "").strip()
        if not texto:
            return []
        with self._cerrojo:
            self._respuestas = []
            self._cancelar.clear()
            self.bus.emitir("estado", valor="procesando")
            try:
                self._atender(texto, origen)
            except Exception:
                registro.exception("Error inesperado al procesar una orden")
                self._decir(self._frase("error"), tipo="error")
            finally:
                if not (self.habla and self.habla.ocupada):
                    self.bus.emitir("estado", valor="reposo")
            return list(self._respuestas)

    def _atender(self, texto: str, origen: str) -> None:
        # 1) ¿Hay algo esperando respuesta?
        if self.pendiente and time.time() > self.pendiente["expira"]:
            registro.info("La petición pendiente ha caducado.")
            self._limpiar_pendiente()
        if self.pendiente:
            if self.pendiente["tipo"] == "accion":
                self._responder_confirmacion(texto, origen)
                return
            if self._responder_sugerencia(texto, origen):
                return

        # 2) Interpretar y ejecutar
        intencion = interpretar(texto)
        if intencion["intent"] != "security_setup" and self.seguridad.contiene_palabra(
                texto, comprobar_corto=intencion["intent"] == "chat"):
            # La palabra de seguridad escrita sin que nadie la pida: ni se guarda ni le llega a la IA.
            self._registrar_usuario(MASCARA, origen)
            self._decir("Eso es tu palabra de seguridad. Dímela solo cuando yo te la pida para confirmar una "
                        "acción delicada; así no se queda en la conversación ni la ve la IA. No la he guardado.",
                        tipo="aviso")
            return
        self._registrar_usuario(self._enmascarar(texto, intencion), origen)
        if intencion["intent"] == "multi":
            self._ejecutar_pasos(intencion["steps"], texto)
        else:
            self._despachar(texto, intencion)

    def _ejecutar_pasos(self, pasos: list, comando: str) -> bool:
        registro.info("Secuencia de %d pasos.", len(pasos))
        for indice, paso in enumerate(pasos):
            if self._cancelar.is_set():
                return False
            if not self._despachar(paso.get("raw") or comando, paso, restantes=pasos[indice + 1:]):
                return False                     # espera permiso, se canceló o JARVIS se apaga
        return True

    def _despachar(self, comando: str, intencion: dict, restantes=(), autorizado: bool = False) -> bool:
        """Ejecuta una intención. Devuelve False si la secuencia no debe continuar."""
        intent = intencion.get("intent", "chat")
        if intent not in ("chat", "system", "multi") and intencion.get("confidence", 0) < UMBRAL_CONFIANZA_MINIMO:
            registro.info("Confianza baja (%.2f) para %s: se trata como conversación.",
                          intencion.get("confidence", 0), intent)
            intencion = dict(intencion, intent="chat", action="unknown")
            intent = "chat"
        if intent == "multi":
            return self._ejecutar_pasos(intencion.get("steps", []), comando)

        herramienta = registry.herramienta_de(intencion)
        if herramienta and not self.ajustes.obtener(f"herramientas.{herramienta}", True):
            self._decir(f"La herramienta «{registry.nombre_de(herramienta)}» está desactivada. "
                        "Puedes activarla en el panel de Herramientas.", tipo="aviso")
            return True

        if not autorizado:
            necesita_permiso, intencion, respuesta_directa = self._revisar_permiso(intencion)
            if respuesta_directa:
                self._decir(respuesta_directa, tipo="aviso")
                return True
            if necesita_permiso:
                self._pedir_confirmacion(comando, intencion, restantes)
                return False

        self._autorizado = autorizado
        manejador = getattr(self, f"_h_{intent}", None) or self._h_chat
        return manejador(comando, intencion) is not False

    # ------------------------------------------------------------------
    # SEGURIDAD: permisos y confirmaciones
    # ------------------------------------------------------------------
    def _revisar_permiso(self, intencion: dict) -> tuple:
        """Devuelve (necesita permiso, intención ajustada, respuesta directa si no hay nada que hacer)."""
        intent, action = intencion.get("intent"), intencion.get("action")
        objetivo = intencion.get("key") if action == "press_key" else intencion.get("target", "")
        if intent == "memory_delete" and action == "delete":
            # Se localiza antes el recuerdo: la confirmación muestra QUÉ se va a borrar exactamente.
            candidato = self.memoria.mejor_coincidencia(intencion.get("target") or "")
            if not candidato:
                return False, intencion, f"No encuentro ningún recuerdo parecido a «{intencion.get('target', '')}»."
            intencion = dict(intencion, _id=candidato["id"], target=candidato["texto"])
            return True, intencion, None
        if intent == "memory_delete" and action == "delete_all" and self.memoria.contar() == 0:
            return False, intencion, self._frase("sin_recuerdos")
        if intent == "message":
            # Antes de pedir permiso: ¿por dónde, y sabe hacerlo por ahí?
            from tools.mensajes import nombre_de, soportada
            app = intencion.get("app") or self.herramientas.app_de_mensajes_activa()
            quien = intencion.get("target") or "Pepe"
            if not app and action == "read":
                return False, intencion, ("¿Dónde lo miro? De momento sé leer los mensajes de Discord: por ejemplo, "
                                          f"«¿qué me ha dicho {quien} por Discord?».")
            if not app:
                return False, intencion, ("¿Por dónde se lo mando? De momento sé hacerlo por Discord: por ejemplo, "
                                          f"«escríbele hola a {quien} por Discord».")
            if not soportada(app):
                if action == "read":
                    return False, intencion, (f"Todavía no sé leer los mensajes de {nombre_de(app)}; de momento, solo "
                                              "los de Discord.")
                return False, intencion, (f"Todavía no sé mandar mensajes por {nombre_de(app)}; de momento, solo "
                                          "por Discord. No he escrito nada.")
            intencion = dict(intencion, app=app)
        return self.seguridad.es_sensible(intent, action, objetivo or ""), intencion, None

    def _pedir_confirmacion(self, comando: str, intencion: dict, restantes=()) -> None:
        descripcion = self.seguridad.describir(intencion)
        modo = self.seguridad.modo
        expira = time.time() + CADUCIDAD_CONFIRMACION
        self.pendiente = {"tipo": "accion", "comando": comando, "intencion": intencion,
                          "restantes": list(restantes), "expira": expira, "intentos": 0,
                          "descripcion": descripcion}
        self.seguridad.registrar_accion(intencion["intent"], intencion["action"], "",
                                        confirmada=False, ejecutada=False)
        if modo == "palabra":
            texto = (f"Eso es una acción delicada: {descripcion}. "
                     "Dime la palabra de seguridad para continuar, o di «cancela».")
        else:
            texto = f"Eso es una acción delicada: {descripcion}. ¿Confirmas? Di «confirmo» o «cancela»."
        datos = {"descripcion": descripcion, "modo": modo, "expira": expira}
        self.bus.emitir("confirmacion", activa=True, **datos)
        self._decir(texto, tipo="confirmacion", datos=datos)

    def _limpiar_pendiente(self) -> None:
        if self.pendiente and self.pendiente["tipo"] == "accion":
            self.bus.emitir("confirmacion", activa=False)
        elif self.pendiente:
            self.bus.emitir("sugerencia", activa=False)
        self.pendiente = None

    def _responder_confirmacion(self, texto: str, origen: str) -> None:
        """La usuaria ha contestado (por voz o por texto) a una petición de permiso."""
        con_palabra = self.seguridad.modo == "palabra"
        if self.seguridad.es_cancelacion(texto):
            self._registrar_usuario(texto, origen)
            if self.habla:
                self.habla.detener()             # corta la petición de permiso si aún se está leyendo
            self._cancelar_pendiente()
        elif self.seguridad.verificar_respuesta(texto):
            self._registrar_usuario(MASCARA if con_palabra else texto, origen)
            self._ejecutar_pendiente()
        else:
            self._registrar_usuario(MASCARA if con_palabra else texto, origen)
            self._intento_fallido()

    def _intento_fallido(self) -> None:
        self.pendiente["intentos"] += 1
        if self.pendiente["intentos"] >= MAXIMO_INTENTOS:
            self._cancelar_pendiente("Demasiados intentos fallidos. Acción cancelada.")
            return
        datos = {"descripcion": self.pendiente["descripcion"], "modo": self.seguridad.modo,
                 "expira": self.pendiente["expira"]}
        if self.seguridad.modo == "palabra":
            aviso = "Esa no es la palabra de seguridad. Inténtalo de nuevo o di «cancela»."
        else:
            aviso = "No lo he entendido. Di «confirmo» para continuar o «cancela» para anularlo."
        self._decir(aviso, tipo="confirmacion", datos=datos)

    def _cancelar_pendiente(self, mensaje: str | None = None) -> None:
        habia_mas_pasos = bool(self.pendiente and self.pendiente.get("restantes"))
        self._limpiar_pendiente()
        texto = mensaje or self._frase("cancelado")
        if habia_mas_pasos:
            texto += " El resto de la secuencia tampoco se ejecutará."
        self._decir(texto, tipo="aviso")

    def _ejecutar_pendiente(self) -> None:
        pendiente = self.pendiente
        self._limpiar_pendiente()
        continuar = self._despachar(pendiente["comando"], pendiente["intencion"], autorizado=True)
        if continuar and pendiente["restantes"]:
            self._ejecutar_pasos(pendiente["restantes"], pendiente["comando"])

    def resolver_confirmacion(self, aceptar: bool, palabra: str = "") -> list:
        """Respuesta a la tarjeta de confirmación de la interfaz (botones)."""
        with self._cerrojo:
            self._respuestas = []
            if not self.pendiente or self.pendiente["tipo"] != "accion":
                return []
            if time.time() > self.pendiente["expira"]:
                self._cancelar_pendiente("La confirmación ha caducado. Acción cancelada.")
            elif not aceptar:
                self._cancelar_pendiente()
            elif self.seguridad.modo == "palabra" and not self.seguridad.verificar_palabra(palabra):
                self._intento_fallido()
            else:
                self.bus.emitir("estado", valor="procesando")
                try:
                    self._ejecutar_pendiente()
                except Exception:
                    registro.exception("Error al ejecutar una acción confirmada")
                    self._decir(self._frase("error"), tipo="error")
            if not (self.habla and self.habla.ocupada):
                self.bus.emitir("estado", valor="reposo")
            return list(self._respuestas)

    # ------------------------------------------------------------------
    # MEMORIA AUTORIZADA: «¿lo guardo?»
    # ------------------------------------------------------------------
    def _detectar_recuerdo(self, texto: str) -> str:
        """Si la frase cuenta algo personal que aún no está guardado, devuelve ese dato."""
        if not self.ajustes.obtener("memoria.sugerir_recuerdos", True):
            return ""
        if not self.ajustes.obtener("herramientas.memoria", True) or "?" in texto or "¿" in texto:
            return ""
        limpio = re.sub(r"\s+", " ", texto).strip().rstrip(".!")
        coincidencia = _DATO_PERSONAL.match(plano(limpio))
        if not coincidencia or not (3 <= len(limpio.split()) <= 25):
            return ""
        dato = limpio[coincidencia.start("dato"):]
        if es_dato_sensible(dato):
            return ""
        clave = normalizar(dato)
        if any(normalizar(recuerdo["texto"]) == clave for recuerdo in self.memoria.listar_recuerdos()):
            return ""
        return dato

    def _responder_sugerencia(self, texto: str, origen: str) -> bool:
        """Devuelve True si la frase era la respuesta al «¿lo guardo en mi memoria?»."""
        if self.seguridad.es_afirmacion(texto):
            self._registrar_usuario(texto, origen)
            self._guardar_sugerencia()
            return True
        if self.seguridad.es_cancelacion(texto) or normalizar(texto) in ("no gracias", "no hace falta"):
            self._registrar_usuario(texto, origen)
            self._limpiar_pendiente()
            self._decir(self._frase("recuerdo_descartado"), tipo="aviso")
            return True
        self._limpiar_pendiente()                # habla de otra cosa: no se guarda nada
        return False

    def _guardar_sugerencia(self) -> None:
        dato = self.pendiente["texto"]
        self._limpiar_pendiente()
        resultado = self.memoria.guardar(dato, origen="sugerido")
        if resultado["ok"]:
            self._registrar({"intent": "memory_save", "action": "save"}, True, "Guardaste un recuerdo")
            self.bus.emitir("recuerdos")
            self._decir(self._frase("recuerdo_guardado", categoria=resultado["categoria"]), tipo="memoria")
        else:
            self._decir(resultado["mensaje"], tipo="aviso")

    def resolver_sugerencia(self, aceptar: bool) -> list:
        """Respuesta a los botones «Guardar / No» de una sugerencia de recuerdo."""
        with self._cerrojo:
            self._respuestas = []
            if not self.pendiente or self.pendiente["tipo"] != "recuerdo":
                return []
            if aceptar:
                self._guardar_sugerencia()
            else:
                self._limpiar_pendiente()
                self._decir(self._frase("recuerdo_descartado"), tipo="aviso", hablar=False)
            return list(self._respuestas)

    # ------------------------------------------------------------------
    # MANEJADORES DE INTENCIONES (uno por tipo)
    # ------------------------------------------------------------------
    def _h_system(self, comando: str, intencion: dict):
        if intencion["action"] == "stop":
            self.detener()
            self._decir(self._frase("parada"), tipo="aviso", hablar=False)
            return False
        self._decir(self._frase("despedida"), tipo="aviso")
        threading.Thread(target=self._apagar_tras_hablar, name="jarvis-apagado", daemon=True).start()
        return False

    def _apagar_tras_hablar(self) -> None:
        limite = time.time() + 10
        time.sleep(0.4)
        while self.habla and self.habla.ocupada and time.time() < limite:
            time.sleep(0.2)
        self.bus.emitir("apagar")
        if self.al_apagar:
            self.al_apagar()

    def _h_security_setup(self, comando: str, intencion: dict):
        if intencion["action"] == "set_word":
            respuesta = self.seguridad.configurar_palabra(intencion.get("text", ""))
        else:
            respuesta = self.seguridad.configurar_modo_confirmacion()
        self._registrar(intencion, True, "Cambiaste la configuración de seguridad")
        self.bus.emitir("ajustes", seguridad=self.seguridad.estado())
        self._decir(respuesta, tipo="aviso")

    def _h_settings(self, comando: str, intencion: dict):
        if intencion["action"] == "set_mode":
            modo = intencion["target"]
            self.ajustes.establecer("modo", modo)
            self._decir(self._frase("modo_voz" if modo == "voz" else "modo_texto"), tipo="aviso")
        elif normalizar(intencion.get("target", "")) in ("espanol", "castellano"):
            self._decir("Ya hablo español. De momento, de hecho, es lo único que hablo.", tipo="aviso")
        else:
            self._decir("De momento solo hablo español. El cambio de idioma llegará en una próxima versión.",
                        tipo="aviso")

    def _h_conversation(self, comando: str, intencion: dict):
        self.nueva_conversacion()
        self._decir(self._frase("conversacion_nueva"), tipo="aviso")

    def _h_history(self, comando: str, intencion: dict):
        if intencion["action"] == "delete_current":
            if self.conversacion_id:
                self.historial.borrar(self.conversacion_id)
            mensaje = "He borrado esta conversación."
        else:
            cantidad = self.historial.borrar_todo()
            mensaje = f"He borrado todo el historial: {cantidad} conversaciones."
        self._registrar(intencion, True, "Borraste el historial")
        self.nueva_conversacion(resumir=False)
        self.bus.emitir("historial")
        # Este aviso no se guarda: si no, el historial recién borrado nacería con un mensaje.
        self._decir(mensaje, tipo="aviso", guardar=False)

    # --- Memoria ---
    def _h_memory_save(self, comando: str, intencion: dict):
        resultado = self.memoria.guardar(intencion.get("target") or comando)
        if resultado["ok"]:
            self._registrar(intencion, True, "Guardaste un recuerdo")
            self.bus.emitir("recuerdos")
            self._decir(self._frase("recuerdo_guardado", categoria=resultado["categoria"]), tipo="memoria")
        else:
            self._decir(resultado["mensaje"], tipo="aviso")

    def _h_memory_query(self, comando: str, intencion: dict):
        if intencion.get("action") == "list":
            recuerdos = self.memoria.listar_recuerdos()
            if not recuerdos:
                self._decir(self._frase("sin_recuerdos"), tipo="memoria")
                return True
            resumen = "; ".join(recuerdo["texto"] for recuerdo in recuerdos[:6])
            extra = "" if len(recuerdos) <= 6 else f"; y {len(recuerdos) - 6} más"
            plural = "recuerdo guardado" if len(recuerdos) == 1 else "recuerdos guardados"
            self._decir(f"Tengo {len(recuerdos)} {plural}. Los más recientes: {resumen}{extra}.", tipo="memoria",
                        datos={"recuerdos": [self._recuerdo_publico(r) for r in recuerdos[:8]]})
            return True
        consulta = intencion.get("query") or comando
        encontrados = self.memoria.buscar(consulta, 3, UMBRAL_OBJETIVO)
        if encontrados:
            self._decir("Esto es lo que recuerdo: " + "; ".join(r["texto"] for r in encontrados) + ".",
                        tipo="memoria", datos={"recuerdos": [self._recuerdo_publico(r) for r in encontrados]})
            return True
        # No hay ningún recuerdo sobre eso: quizá era una pregunta general ("¿qué sabes sobre Marte?").
        return self._h_chat(comando, intencion)

    def _h_memory_edit(self, comando: str, intencion: dict):
        respuesta = self.memoria.editar_recuerdo(intencion.get("target", ""), intencion.get("text", ""))
        if respuesta.startswith("Actualizado"):
            self._registrar(intencion, True, "Modificaste un recuerdo")
            self.bus.emitir("recuerdos")
        self._decir(respuesta, tipo="memoria")

    def _h_memory_delete(self, comando: str, intencion: dict):
        if intencion.get("action") == "delete_all":
            cantidad = self.memoria.borrar_todo()
            self._registrar(intencion, True, "Borraste todos los recuerdos")
            self.bus.emitir("recuerdos")
            self._decir(f"He borrado todos mis recuerdos: {cantidad} en total.", tipo="memoria")
            return True
        if intencion.get("_id") is None:
            candidato = self.memoria.mejor_coincidencia(intencion.get("target") or comando)
            if not candidato:
                self._decir(f"No encuentro ningún recuerdo parecido a «{intencion.get('target', '')}».",
                            tipo="aviso")
                return True
            intencion = dict(intencion, _id=candidato["id"], target=candidato["texto"])
        borrado = self.memoria.borrar_por_id(intencion["_id"])
        self._registrar(intencion, borrado, "Borraste un recuerdo")
        self.bus.emitir("recuerdos")
        self._decir(f"He olvidado esto: «{intencion['target']}»." if borrado
                    else "No he podido borrar ese recuerdo.", tipo="memoria")

    @staticmethod
    def _recuerdo_publico(recuerdo: dict) -> dict:
        return {"id": recuerdo["id"], "texto": recuerdo["texto"], "categoria": recuerdo["categoria"],
                "fecha": recuerdo["fecha_creacion"]}

    # --- Información local ---
    def _h_info(self, comando: str, intencion: dict):
        ahora = datetime.datetime.now()
        if intencion["action"] == "time":
            self._decir(self._frase("hora", hora=hora_corta(ahora)), tipo="info")
        else:
            self._decir(self._frase("fecha", fecha=fecha_larga(ahora)), tipo="info")

    # --- Tiempo ---
    def _h_weather(self, comando: str, intencion: dict):
        ciudad = (intencion.get("target") or self.ajustes.obtener("internet.ciudad") or "").strip()
        if not ciudad:
            self._decir("¿De qué ciudad? Pregúntame, por ejemplo, «¿qué tiempo hace en Valencia?», "
                        "o guarda tu ciudad en Ajustes para no tener que repetirla.", tipo="aviso")
            return True
        dia = "manana" if intencion.get("action") == "tomorrow" else "hoy"
        try:
            datos = self.herramientas.tiempo(ciudad, dia)
        except Exception as error:
            self._registrar(dict(intencion, target=ciudad), False, f"Tiempo en {ciudad}")
            if str(error) == "ciudad_desconocida":
                self._decir(f"No encuentro ninguna ciudad llamada «{ciudad}».", tipo="aviso")
            else:
                self._decir("No he podido consultar el tiempo. Comprueba la conexión a Internet.", tipo="error")
            return True
        from tools.weather import frase_tiempo
        self._registrar(dict(intencion, target=datos["ciudad"]), True, f"Consultaste el tiempo: {datos['ciudad']}")
        self._decir(frase_tiempo(datos), tipo="tiempo", datos=datos)

    # --- Internet ---
    def _h_search_web(self, comando: str, intencion: dict):
        accion = intencion.get("action")
        consulta = (intencion.get("query") or "").strip()

        if accion == "site":
            abierto, sitio, url = self.herramientas.abrir_busqueda(intencion.get("target", ""), consulta)
            self._registrar(dict(intencion, target=consulta), abierto, f"Buscaste en {sitio}: {consulta}")
            if abierto:
                self._decir(self._frase("buscado_en_sitio", consulta=consulta, sitio=sitio), tipo="accion",
                            datos={"herramienta": "busqueda", "url": url})
            else:
                self._decir(f"No he podido abrir la búsqueda en {sitio}.", tipo="aviso")
            return True

        if accion == "read":
            url = intencion.get("target", "")
            texto_pagina = self.herramientas.leer_pagina(url)
            dominio = urllib.parse.urlparse(url if "//" in url else f"//{url}").netloc or url
            self._registrar(dict(intencion, target=dominio), bool(texto_pagina), f"Leíste: {dominio}")
            if not texto_pagina:
                self._decir("No he podido leer esa página.", tipo="aviso")
                return True
            peticion = (f"Este es el contenido de la página {url}:\n\n{texto_pagina}\n\n"
                        "Resume en tres o cuatro frases lo más importante.")
            self._responder_con_modelo(peticion, con_historial=False, tipo="busqueda",
                                       datos={"consulta": url, "resultados": [
                                           {"titulo": dominio, "url": url if "//" in url else f"https://{url}",
                                            "fuente": dominio, "resumen": ""}]})
            return True

        if not consulta:
            self._decir("¿Qué quieres que busque?", tipo="aviso")
            return True
        tipo = "noticias" if accion == "news" else "web"
        maximo = int(self.ajustes.obtener("internet.max_resultados", 5))
        try:
            resultados = self.herramientas.buscar(consulta, maximo, tipo)
            if not resultados and tipo == "noticias":
                resultados = self.herramientas.buscar(consulta, maximo, "web")
        except Exception:
            self._registrar(dict(intencion, target=consulta), False, f"Buscaste: {consulta}")
            self._decir("No he podido conectar con el buscador. Comprueba la conexión a Internet.", tipo="error")
            return True
        self._registrar(dict(intencion, target=consulta), True, f"Buscaste: {consulta}")
        if not resultados:
            self._decir(f"No he encontrado nada sobre «{consulta}».", tipo="aviso")
            return True

        contexto = "\n".join(f"- {r['titulo']} ({r['fuente']}): {r['resumen']}" for r in resultados)
        peticion = (f"PETICIÓN: {comando}\n\n"
                    f"Esto es lo que se ha encontrado en Internet:\n{contexto}\n\n"
                    "Contesta directamente a la petición en dos o tres frases, basándote solo en esa "
                    "información. No digas que has buscado ni comentes los resultados como tales. "
                    "Si la información no basta para contestar, dilo claramente.")
        respaldo = "Esto es lo que he encontrado: " + "; ".join(r["titulo"] for r in resultados[:3]) + "."
        self._responder_con_modelo(peticion, con_historial=False, tipo="busqueda", respaldo=respaldo,
                                   datos={"consulta": consulta, "resultados": resultados,
                                          "noticias": tipo == "noticias"})

    # --- Música ---
    def _h_music(self, comando: str, intencion: dict):
        accion = intencion.get("action")
        objetivo = intencion.get("target", "")
        if accion == "now_playing":
            from tools.media import describir
            sesion = self.herramientas.musica_sonando(objetivo)
            self._decir(describir(sesion), tipo="musica" if sesion else "aviso",
                        datos={"sesion": sesion} if sesion else {})
            return True
        if accion == "play_song":
            return self._poner_cancion(intencion.get("query") or comando, objetivo)
        orden, descripcion = {"resume": ("reproducir", "Reanudaste la música"),
                              "pause": ("pausar", "Pausaste la música")}.get(accion, (None, ""))
        if not orden:
            self._decir("No conozco esa orden de música.", tipo="aviso")
            return True
        resultado = self.herramientas.musica_controlar(orden, objetivo)
        self._registrar(intencion, resultado.ok, descripcion)
        self._decir(resultado.mensaje, tipo="musica" if resultado.ok else "aviso", datos=resultado.datos)
        return True

    def _poner_cancion(self, consulta: str, servicio: str = "") -> bool:
        servicio = servicio or self.ajustes.obtener("musica.servicio", "youtube")
        resultado = self.herramientas.musica_reproducir(consulta, servicio)
        self._registrar({"intent": "music", "action": "play_song", "target": consulta}, resultado.ok,
                        f"Pusiste: {consulta}")
        self._decir(resultado.mensaje, tipo="musica" if resultado.ok else "aviso", datos=resultado.datos)
        return True

    # --- Recordatorios, temporizadores, alarmas y agenda ---
    def _h_reminder(self, comando: str, intencion: dict):
        accion, tipo = intencion.get("action"), intencion.get("tipo") or ""
        objetivo = intencion.get("target", "")
        ahora = datetime.datetime.now().replace(microsecond=0)
        if accion == "create":
            return self._crear_recordatorio(tipo or "recordatorio", objetivo, ahora)
        if accion == "list":
            desde, hasta, nombre = rango_de(objetivo, ahora)
            lista = [r for r in self.recordatorios.entre(desde, hasta) if not tipo or r["tipo"] == tipo]
            self._decir(frase_lista(lista, nombre, ahora), tipo="recordatorio",
                        datos={"recordatorios": [publico(r, ahora) for r in lista[:12]]})
            return True
        if accion == "cancel":
            nombre = tipo or "recordatorio"
            ninguno = "ninguna" if nombre in ("alarma", "cita") else "ningún"
            cancelados = self.recordatorios.cancelar(tipo or None, objetivo, todos=bool(intencion.get("todos")))
            if not cancelados:
                self._decir(f"No tienes {ninguno} {nombre}" + (f" de «{objetivo}»" if objetivo else "") + " pendiente.",
                            tipo="aviso")
                return True
            self._registrar(intencion, True, f"Cancelaste {len(cancelados)}: {nombre}")
            if len(cancelados) > 1:
                texto = f"He cancelado {len(cancelados)} avisos."
            else:
                r = cancelados[0]
                texto = (f"Cancelado: «{r['texto']}» ({describir_momento(r['cuando'], ahora)})." if r["texto"]
                         else f"Cancelad{'a' if r['tipo'] in ('alarma', 'cita') else 'o'} {describir_momento(r['cuando'], ahora)}.")
                texto = texto if r["tipo"] != "temporizador" else "Temporizador cancelado."
            self._decir(texto, tipo="recordatorio")
            return True
        if accion == "status":
            temporizadores = self.recordatorios.pendientes("temporizador")
            if not temporizadores:
                self._decir("No hay ningún temporizador en marcha.", tipo="aviso")
            else:
                quedan = describir_duracion((temporizadores[0]["cuando"] - ahora).total_seconds())
                self._decir(f"Quedan {quedan}." + (f" Y hay {len(temporizadores) - 1} temporizador(es) más."
                                                    if len(temporizadores) > 1 else ""), tipo="recordatorio")
            return True
        if accion == "snooze":
            if not self.ultimo_aviso or time.time() - self.ultimo_aviso[1] > 3600:
                self._decir("No hay ninguna alarma que posponer.", tipo="aviso")
                return True
            anterior = self.ultimo_aviso[0]
            segundos = segundos_de(objetivo) or 600
            nuevo = self.recordatorios.crear("alarma" if anterior["tipo"] == "alarma" else "recordatorio",
                                             anterior["texto"], ahora + datetime.timedelta(seconds=segundos))
            self.ultimo_aviso = None
            self._decir(f"Vale, te aviso otra vez en {describir_duracion(segundos)}.", tipo="recordatorio",
                        datos={"recordatorios": [publico(nuevo, ahora)]})
            return True
        self._decir("No conozco esa orden de recordatorios.", tipo="aviso")
        return True

    def _crear_recordatorio(self, tipo: str, objetivo: str, ahora: datetime.datetime) -> bool:
        if tipo == "temporizador":
            segundos = segundos_de(objetivo)
            if segundos <= 0:
                self._decir("¿De cuánto tiempo? Por ejemplo: «pon un temporizador de 10 minutos».", tipo="aviso")
                return True
            cuando, texto = ahora + datetime.timedelta(seconds=segundos), describir_duracion(segundos)
        else:
            momento = interpretar_momento(objetivo, ahora)
            if not momento:
                self._decir("¿A qué hora pongo la alarma? Por ejemplo: «pon una alarma a las 7»." if tipo == "alarma"
                            else "¿Para cuándo? Por ejemplo: «recuérdame mañana a las 9 llamar al médico».",
                            tipo="aviso")
                return True
            cuando, texto = momento.cuando, separar(objetivo, momento)
            if not texto and tipo in ("recordatorio", "cita"):
                if not momento.relativo:
                    self._decir("¿Qué quieres que te recuerde? Dímelo todo junto, por ejemplo: «recuérdame mañana "
                                "a las 9 llamar al médico».", tipo="aviso")
                    return True
                tipo, texto = "temporizador", describir_duracion((cuando - ahora).total_seconds())
        recordatorio = self.recordatorios.crear(tipo, texto, cuando)
        self._registrar({"intent": "reminder", "action": "create", "target": texto}, True,
                        f"{tipo.capitalize()}: {texto or describir_momento(cuando, ahora)}")
        self._decir(frase_creado(recordatorio, ahora), tipo="recordatorio",
                    datos={"recordatorios": [publico(recordatorio, ahora)]})
        return True

    def avisar(self, recordatorios: list, perdidos: bool = False) -> None:
        """Lo llama el avisador (desde su hilo) cuando vence algo. Suena aunque estés en modo texto."""
        ahora = datetime.datetime.now()
        if perdidos:
            detalle = "; ".join(f"{r['texto'] or r['tipo']} ({describir_momento(r['cuando'], ahora)})"
                                for r in recordatorios[:6])
            texto = (f"Mientras no estabas se pasó un aviso: {detalle}." if len(recordatorios) == 1
                     else f"Mientras no estabas se pasaron {len(recordatorios)} avisos: {detalle}.")
        else:
            texto = frase_aviso(recordatorios[0], self.nombre or "")
        with self._cerrojo:
            self._respuestas = []
            self._decir(texto, tipo="recordatorio", hablar=False,
                        datos={"recordatorios": [publico(r, ahora) for r in recordatorios[:12]], "aviso": True})
        self.bus.emitir("aviso", texto=texto)
        if self.habla:
            self.habla.decir(texto)
        if not perdidos:
            self.ultimo_aviso = (recordatorios[0], time.time())

    # --- Resumen del día ---
    def _h_briefing(self, comando: str, intencion: dict):
        if intencion.get("action") == "greeting" and self.resumen_dado == datetime.date.today():
            return self._h_chat(comando, intencion)      # ya se dio hoy: es un saludo sin más
        self._dar_resumen(saludo="Buenos días" if intencion.get("action") == "greeting" else "")
        return True

    def resumen_automatico(self) -> None:
        """Lo llama el asistente al arrancar por la mañana, si está activado en Ajustes."""
        if self.resumen_dado == datetime.date.today():
            return
        with self._cerrojo:
            self._respuestas = []
            texto = self._dar_resumen(hablar=False)
        if self.habla and texto:
            self.habla.decir(texto)

    def _dar_resumen(self, saludo: str = "", hablar: bool = True) -> str:
        from tools.weather import frase_tiempo
        ahora = datetime.datetime.now()
        self.resumen_dado = ahora.date()
        saludo = saludo or ("Buenos días" if ahora.hour < 14 else "Buenas tardes" if ahora.hour < 21 else "Buenas noches")
        partes = [f"{saludo}{', ' + self.nombre if self.nombre else ''}. Hoy es {fecha_larga(ahora).rsplit(' de ', 1)[0]}."]
        datos = {"resumen": True}
        ciudad = (self.ajustes.obtener("internet.ciudad") or "").strip()
        if ciudad and self.ajustes.obtener("herramientas.tiempo", True):
            try:
                partes.append(frase_tiempo(self.herramientas.tiempo(ciudad, "hoy")))
            except Exception:
                partes.append("No he podido consultar el tiempo.")
        if self.ajustes.obtener("herramientas.recordatorios", True):
            fin_del_dia = datetime.datetime.combine(ahora.date() + datetime.timedelta(days=1), datetime.time())
            lista = self.recordatorios.entre(ahora, fin_del_dia)
            partes.append(frase_lista(lista, "para hoy", ahora) if lista else "No tienes nada apuntado para hoy.")
            if lista:
                datos["recordatorios"] = [publico(r, ahora) for r in lista[:12]]
        if self.ajustes.obtener("resumen.noticias", True) and self.ajustes.obtener("herramientas.busqueda", True):
            try:
                noticias = self.herramientas.buscar("noticias de hoy en España", 5, "noticias")[:3]
            except Exception:
                noticias = []
            titulares = [re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", n["titulo"]).strip().rstrip(".") for n in noticias]
            if titulares:
                partes.append("Titulares: " + " ".join(f"{t}." for t in titulares))
                datos.setdefault("resultados", noticias)
                datos["noticias"] = True
        texto = " ".join(partes)
        self._decir(texto, tipo="resumen", datos=datos, hablar=hablar)
        return texto

    # --- Archivos ---
    def _h_files(self, comando: str, intencion: dict):
        from tools.archivos import describir
        accion, consulta = intencion.get("action"), intencion.get("target", "")
        if accion in ("open_recent", "recent"):
            ultimos = self.herramientas.ultimos_descargados(1)
            if not ultimos:
                self._decir("No encuentro nada en tu carpeta de Descargas.", tipo="aviso")
                return True
            if accion == "recent":
                self.ultimos_archivos = ultimos
                self._decir(f"Lo último que descargaste es {describir(ultimos[0])}.", tipo="archivos",
                            datos={"archivos": ultimos})
                return True
            return self._abrir_archivo(ultimos[0])
        if accion == "open_index":
            numero = int(consulta or 1)
            if not self.ultimos_archivos:
                self._decir("Antes dime qué archivo busco, por ejemplo: «busca el PDF del TFG».", tipo="aviso")
                return True
            indice = len(self.ultimos_archivos) - 1 if numero == -1 else numero - 1
            if not 0 <= indice < len(self.ultimos_archivos):
                self._decir(f"Solo hay {len(self.ultimos_archivos)} resultados.", tipo="aviso")
                return True
            return self._abrir_archivo(self.ultimos_archivos[indice])

        resultados = self.herramientas.buscar_archivos(consulta)
        self.ultimos_archivos = resultados
        self._registrar(intencion, True, f"Buscaste el archivo: {consulta}")
        if not resultados:
            self._decir(f"No encuentro ningún archivo que encaje con «{consulta}» en tus carpetas.", tipo="aviso")
            return True
        if accion == "open":
            abierto = self._abrir_archivo(resultados[0], callar=len(resultados) > 1)
            if len(resultados) > 1:
                otros = len(resultados) - 1
                self._decir(f"He abierto {describir(resultados[0])}. Hay {otros} más parecido{'s' if otros > 1 else ''}: "
                            "si no era ese, dime «abre el segundo» o el que sea.", tipo="archivos",
                            datos={"archivos": resultados})
            return abierto
        lista = "; ".join(f"{i}) {describir(dato)}" for i, dato in enumerate(resultados[:5], start=1))
        self._decir(f"He encontrado {len(resultados)}: {lista}. Para abrir uno, dime «abre el primero», «abre el "
                    "segundo»...", tipo="archivos", datos={"archivos": resultados})
        return True

    def _abrir_archivo(self, dato: dict, callar: bool = False) -> bool:
        resultado = self.herramientas.abrir_archivo(dato["ruta"])
        self._registrar({"intent": "files", "action": "open", "target": dato["nombre"]}, resultado.ok,
                        f"Abriste el archivo: {dato['nombre']}")
        if not resultado.ok or not callar:
            self._decir(resultado.mensaje, tipo="archivos" if resultado.ok else "aviso",
                        datos={"archivos": [dato]} if resultado.ok else {})
        return True

    # --- Leer la pantalla ---
    def _h_screen(self, comando: str, intencion: dict):
        from tools.pantalla import peticion_con_pantalla
        resultado = self.herramientas.leer_pantalla()
        titulo = resultado.datos.get("titulo", "")
        self._registrar(intencion, resultado.ok, "Leíste la pantalla" + (f": {titulo}" if titulo else ""))
        if not resultado.ok:
            self._decir(resultado.mensaje, tipo="aviso")
            return True
        peticion = peticion_con_pantalla(intencion.get("target") or comando, resultado.datos,
                                         self.nombre or "la usuaria")
        return self._responder_con_modelo(
            peticion, con_historial=False, tipo="pantalla",
            datos={"pantalla": {"titulo": titulo, "lineas": resultado.datos.get("lineas", 0)}},
            respaldo="En la pantalla pone: " + resultado.datos["texto"][:500])

    # --- Estado del equipo y brillo ---
    def _h_system_info(self, comando: str, intencion: dict):
        from tools.sistema import frase_estado, objetivo_de_brillo
        accion, objetivo = intencion.get("action"), intencion.get("target", "")
        if accion == "brightness":
            actual = self.herramientas.brillo()
            if objetivo == "?":
                self._decir(f"El brillo está al {actual} %." if actual is not None
                            else "No puedo leer el brillo de esta pantalla.", tipo="equipo" if actual is not None else "aviso")
                return True
            valor = objetivo_de_brillo(objetivo, actual)
            if valor is None:
                self._decir("¿A cuánto pongo el brillo? Por ejemplo: «pon el brillo al 60».", tipo="aviso")
                return True
            resultado = self.herramientas.poner_brillo(valor)
            self._registrar(intencion, resultado.ok, f"Brillo al {valor} %")
            self._decir(resultado.mensaje, tipo="equipo" if resultado.ok else "aviso", datos=resultado.datos)
            return True
        datos = self.herramientas.estado_equipo()
        self._decir(frase_estado(datos, "bateria" if accion == "battery" else objetivo), tipo="equipo",
                    datos={"equipo": datos})
        return True

    # --- Ordenador ---
    def _h_pc(self, comando: str, intencion: dict):
        resultado = self.herramientas.ejecutar_pc(intencion)
        accion = intencion.get("action")
        objetivo = intencion.get("target", "")
        if (accion == "open_app" and not resultado.ok and intencion.get("alternativa") == "musica"
                and self.ajustes.obtener("herramientas.musica", True)):
            # «pon Despacito»: no hay ninguna aplicación con ese nombre, así que era una canción.
            return self._poner_cancion(objetivo)
        if accion == "open_app":
            nombre = resultado.datos.get("nombre") or objetivo
            descripcion, texto = f"Abriste: {nombre}", self._frase("abierto", objeto=nombre)
        elif accion == "close_app":
            activa = objetivo in ("active", "activa", "")
            descripcion = "Cerraste: la ventana activa" if activa else f"Cerraste: {objetivo}"
            texto = resultado.mensaje if activa else self._frase("cerrado", objeto=objetivo)
        elif accion == "type_text":
            descripcion = "Escribiste un texto" + (f" en {objetivo}" if objetivo else "")
            texto = self._frase("escrito")
        elif accion == "press_key":
            descripcion, texto = f"Pulsaste: {intencion.get('key', '')}", resultado.mensaje
        elif accion == "media":
            descripcion, texto = f"Multimedia: {ETIQUETAS_MULTIMEDIA.get(objetivo, objetivo)}", resultado.mensaje
        else:
            descripcion, texto = f"Comando: {DESCRIPCION_COMANDOS.get(objetivo, objetivo)}", resultado.mensaje
        # El texto escrito nunca se copia al registro: solo dónde se escribió.
        self._registrar(intencion, resultado.ok, descripcion)
        datos = dict(resultado.datos, herramienta=registry.herramienta_de(intencion), ok=resultado.ok)
        self._decir(texto if resultado.ok else resultado.mensaje, tipo="accion" if resultado.ok else "aviso",
                    datos=datos)

    # --- Mensajes (Discord...) ---
    def _h_message(self, comando: str, intencion: dict):
        from tools.mensajes import nombre_de, resumen_de_conversacion
        app, contacto = intencion.get("app", ""), intencion.get("target", "")
        if intencion.get("action") == "read":
            resultado = self.herramientas.leer_mensajes(app, contacto)
            # Al registro va con quién y dónde, nunca lo que dicen los mensajes.
            self._registrar(intencion, resultado.ok, f"Leíste la conversación con {contacto} en {nombre_de(app)}")
            if not resultado.ok:
                self._decir(resultado.mensaje, tipo="aviso")
                return False
            visto_como = resultado.datos.get("contacto") or contacto
            self._decir(resumen_de_conversacion(resultado.datos["mensajes"], visto_como,
                                                int(intencion.get("cantidad") or 1), bool(intencion.get("mios"))),
                        tipo="chat")
            return True
        resultado = self.herramientas.enviar_mensaje(app, contacto, intencion.get("text", ""))
        # Al registro va a quién y por dónde, nunca lo que dice el mensaje.
        self._registrar(intencion, resultado.ok, f"Mensaje a {contacto} por {nombre_de(app)}")
        if not resultado.ok:
            self._decir(resultado.mensaje, tipo="aviso")
            return False
        self._decir(self._frase("mensaje_enviado", contacto=contacto, app=nombre_de(app)), tipo="accion",
                    datos={"herramienta": "mensajes", "ok": True, "nombre": contacto})
        return True

    # --- Conversación ---
    def _guardar_lo_anterior(self) -> bool:
        """«Guárdalo en tu memoria», «recuerda eso»: lo que acaba de contar (su mensaje anterior).

        Sin la IA: pasó que el modelo decía «¡guardado!» sin guardar nada (o lo negaba cuando el
        aprendizaje automático ya lo había guardado). Si el mensaje anterior no trae ningún dato
        suyo (p. ej. era «resúmeme el informe»), devuelve False y lo resuelve la IA.
        """
        anteriores = [m["content"] for m in self._contexto[:-1] if m.get("role") == "user"]
        if not anteriores or not self.ajustes.obtener("herramientas.memoria", True):
            return False
        try:
            datos = self.cerebro.extraer_datos(anteriores[-1], self.nombre or "la usuaria")
        except Exception:
            registro.exception("No se pudo extraer lo que había que guardar")
            return False
        nuevos, ya_estaban = [], []
        for dato in datos[:MAXIMO_APRENDIDOS]:
            if es_dato_sensible(dato):
                continue
            if self.memoria.buscar(dato, 1, UMBRAL_REPETIDO):
                ya_estaban.append(dato)
                continue
            resultado = self.memoria.guardar(dato, origen="usuario")
            if resultado["ok"]:
                nuevos.append(resultado["texto"])
        if not nuevos and not ya_estaban:
            return False
        if nuevos:
            self._registrar({"intent": "memory_save", "action": "save"}, True, "Guardaste un recuerdo")
            self.bus.emitir("recuerdos")
            texto = "Guardado en mi memoria: " + "; ".join(f"«{t}»" for t in nuevos) + "."
            if ya_estaban:
                texto += " Lo demás ya lo tenía."
        else:
            texto = "Eso ya lo tenía guardado: " + "; ".join(f"«{t}»" for t in ya_estaban) + "."
        self._decir(texto, tipo="memoria")
        return True

    def _h_chat(self, comando: str, intencion: dict):
        if (intencion.get("action") == "memory_save_llm" and len(plano(comando).split()) <= 7
                and self._guardar_lo_anterior()):
            return True
        contexto_memoria, resumen_previo = "", ""
        try:
            contexto_memoria = self.memoria.buscar_contexto(comando, 3)
            if not contexto_memoria and self._guardar_historial():
                resumen_previo = self.historial.buscar_resumen(comando, self.conversacion_id)
        except Exception:
            registro.exception("No se pudo consultar la memoria")

        sugerencias = []
        # Para la búsqueda sugerida se usa la frase ya limpia (sin «Jarvis,» ni fórmulas de cortesía).
        consulta = (intencion.get("raw") or comando).strip(" ¿?¡!.")
        if (("?" in comando or _INTERROGATIVO.match(plano(consulta)))
                and self.ajustes.obtener("herramientas.busqueda", True)):
            sugerencias.append({"etiqueta": "Buscar en Internet", "comando": f"busca en internet {consulta}"})

        datos = {}
        aprende_sola = self._aprende_sola()
        # Si aprende sola no pregunta «¿lo guardo?»: lo extrae la IA en segundo plano tras responder.
        dato_personal = "" if aprende_sola else self._detectar_recuerdo(comando)
        if dato_personal:
            datos["sugerir_recuerdo"] = dato_personal

        funciones = Funciones(self)
        nombre = nombre_por_el_que_pregunta(comando, excluir=(self.nombre,))
        if pide_datos_actuales(comando) and self.ajustes.obtener("herramientas.busqueda", True):
            # Actualidad: se busca la pregunta entera (con el nombre solo salía el artículo general).
            funciones.prebuscar((intencion.get("raw") or comando).strip(" ¿?¡!."))
        elif (nombre and self.ajustes.obtener("herramientas.busqueda", True)
                and not self.memoria.listar_recuerdos(buscar=nombre)):
            # Pregunta por alguien o algo con nombre propio que no es de su vida (no sale en sus
            # recuerdos): se busca antes, porque si el modelo cree saberlo responde de memoria.
            funciones.prebuscar(nombre)
        elif es_charla(comando) and intencion.get("action") != "memory_save_llm":
            funciones = None                     # charla: sin funciones, el modelo no las usa a destiempo
        respondido = self._responder_con_modelo(comando, con_historial=True, tipo="chat", datos=datos,
                                                sugerencias=sugerencias, contexto_memoria=contexto_memoria,
                                                resumen_previo=resumen_previo, funciones=funciones)
        if respondido and aprende_sola:
            self._aprender_de(comando)
        if respondido and dato_personal:
            self.pendiente = {"tipo": "recuerdo", "texto": dato_personal,
                              "expira": time.time() + CADUCIDAD_SUGERENCIA}
            self.bus.emitir("sugerencia", activa=True, texto=dato_personal)
            if self._debe_hablar():
                self.habla.decir("¿Quieres que lo guarde en mi memoria?")

    def _responder_con_modelo(self, peticion: str, con_historial: bool, tipo: str, datos: dict | None = None,
                              sugerencias: list | None = None, contexto_memoria: str = "",
                              resumen_previo: str = "", respaldo: str = "", funciones: Funciones | None = None) -> bool:
        """Genera la respuesta con el modelo, emitiéndola a trozos. Devuelve False si falló."""
        id_mensaje = self._nuevo_id()
        locutor = _LocutorPorFrases(self.habla) if self._debe_hablar() else None

        def al_fragmento(trozo: str) -> None:
            self.bus.emitir("fragmento", id=id_mensaje, texto=trozo)
            if locutor:
                locutor.recibir(trozo)

        historial = []
        if con_historial:
            turnos = int(self.ajustes.obtener("ia.mensajes_contexto", 8)) * 2
            historial = self._contexto[-(turnos + 1):-1]     # sin la frase actual, que va aparte
        try:
            respuesta = self.cerebro.pensar(peticion, historial=historial, modo=self.modo,
                                            contexto_memoria=contexto_memoria, resumen_previo=resumen_previo,
                                            al_fragmento=al_fragmento, cancelar=self._cancelar, funciones=funciones)
        except ErrorCerebro as error:
            self.bus.emitir("sistema", componente="ia", estado="error", detalle=error.motivo)
            if respaldo:
                self._decir(respaldo, tipo=tipo, datos=datos, id_mensaje=id_mensaje)
                return True
            self._decir(str(error), tipo="error", id_mensaje=id_mensaje)
            return False

        if self._cancelar.is_set():
            self._decir(respuesta or "(Respuesta cancelada.)", tipo=tipo, datos=datos, hablar=False,
                        id_mensaje=id_mensaje)
            return False
        inventada = accion_inventada(respuesta, funciones.usadas if funciones is not None else ())
        if inventada:
            # Dice haber hecho algo que no ha hecho ninguna función: no se le da por bueno.
            registro.info("La IA decía haber hecho algo sin hacerlo (%s): se corrige.", inventada)
            if locutor and self.habla:
                self.habla.detener()
            locutor = None
            respuesta = self._respuesta_sin_inventar(peticion, historial, contexto_memoria, resumen_previo,
                                                     funciones, inventada)
        if locutor:
            locutor.terminar()
        # Las reglas piden no usar emojis, pero el modelo a veces los pone (y la voz los leería).
        respuesta = re.sub(r"[ \t]+([.,;:!?])", r"\1",
                           re.sub(r"[ \t]{2,}", " ", _EMOJIS.sub("", respuesta or ""))).strip()
        if not re.search(r"sarcas|ironi", plano(peticion)):
            respuesta = _sin_autobombo(respuesta)
        if funciones is not None:
            tipo, datos = self._tarjeta_de(funciones, tipo, datos)
            if funciones.resultados_web:             # ya ha buscado: el botón «Buscar en Internet» sobra
                sugerencias = [s for s in sugerencias or [] if not s["comando"].startswith("busca en internet")]
        # Si el modelo ha respondido, el locutor ya lo ha ido leyendo frase a frase.
        ya_leido = bool(locutor and respuesta)
        self._decir(respuesta or respaldo or "No he sabido qué responder a eso.", tipo=tipo, datos=datos,
                    sugerencias=sugerencias, hablar=not ya_leido, id_mensaje=id_mensaje)
        if funciones is not None and funciones.guardados:
            self.bus.emitir("recuerdos")
            self.bus.emitir("aprendido", textos=list(funciones.guardados))
        if funciones is not None and funciones.propuesta:
            # La IA ha preparado un mensaje: se pide permiso como si lo hubiera pedido ella con una orden.
            self._despachar(peticion, funciones.propuesta)
        return True

    def _respuesta_sin_inventar(self, peticion: str, historial: list, contexto_memoria: str, resumen_previo: str,
                                funciones: Funciones | None, tipo: str) -> str:
        """Se le pide al modelo que vuelva a responder sin fingir; si insiste, una corrección honesta."""
        if tipo == "palabra":
            aviso = ("\n\n(Aviso de JARVIS: no pidas la palabra de seguridad ni hables de ella: la pide JARVIS. Si hay "
                     "que hacer algo delicado, usa la herramienta que lo hace (por ejemplo enviar_mensaje) y JARVIS "
                     "pedirá la confirmación; si no hay herramienta, dilo.)")
        else:
            aviso = ("\n\n(Aviso de JARVIS: en tu respuesta anterior decías haber hecho algo sin haber usado ninguna "
                     "herramienta, y eso es falso. Responde otra vez: si tienes una herramienta que lo hace, úsala; "
                     "si no, di claramente que no puedes hacerlo y cómo pedírmelo con una orden.)")
        try:
            otra = self.cerebro.pensar(peticion + aviso, historial=historial, modo=self.modo,
                                       contexto_memoria=contexto_memoria, resumen_previo=resumen_previo,
                                       cancelar=self._cancelar, funciones=funciones)
        except ErrorCerebro:
            otra = ""
        if otra and not accion_inventada(otra, funciones.usadas if funciones is not None else ()):
            return otra
        if tipo == "mensaje" and funciones is not None and funciones.propuesta:
            return "Te lo dejo preparado, pero todavía no lo he mandado: antes necesito que lo confirmes."
        return CORRECCIONES.get(tipo, CORRECCIONES["accion"])

    @staticmethod
    def _tarjeta_de(funciones: Funciones, tipo: str, datos: dict | None) -> tuple:
        """Lo que hayan devuelto las funciones (tiempo, música, resultados) se enseña junto a la respuesta."""
        datos = dict(datos or {})
        if funciones.recordatorios:
            return "recordatorio", dict(datos, recordatorios=funciones.recordatorios)
        if funciones.tiempo:
            return "tiempo", dict(datos, **funciones.tiempo)
        if funciones.musica:
            return "musica", dict(datos, **funciones.musica)
        if funciones.archivos and not funciones.resultados_web:
            return "archivos", dict(datos, archivos=funciones.archivos[:8])
        if funciones.resultados_web:
            return "busqueda", dict(datos, resultados=funciones.resultados_web[:5])
        return tipo, datos

    # ------------------------------------------------------------------
    # APRENDER DE LO QUE SE HABLA
    # ------------------------------------------------------------------
    def _aprende_sola(self) -> bool:
        return bool(self.ajustes.obtener("memoria.aprender_automaticamente", True)
                    and self.ajustes.obtener("herramientas.memoria", True))

    def _aprender_de(self, texto: str) -> None:
        """Si la usuaria ha contado algo de sí misma, la IA extrae el dato y se guarda (sin preguntar).

        Lo ha pedido ella («que aprenda de mí según hablamos»). Para que no sea opaco, cada dato
        aprendido se anuncia en la interfaz y se puede revisar o borrar en Memoria. Nunca se
        guardan datos sensibles (contraseñas, claves, datos bancarios).
        """
        limpio = plano(texto)
        if len(limpio.split()) < 3 or not _PRIMERA_PERSONA.search(limpio) or es_dato_sensible(texto):
            return
        if self.aprender_en_segundo_plano:
            threading.Thread(target=self._extraer_y_guardar, args=(texto,), name="jarvis-aprender",
                             daemon=True).start()
        else:
            self._extraer_y_guardar(texto)

    def _extraer_y_guardar(self, texto: str) -> list:
        try:
            datos = self.cerebro.extraer_datos(texto, self.nombre or "la usuaria")
        except Exception:
            registro.exception("No se pudo extraer lo aprendido")
            return []
        aprendidos = []
        for dato in datos[:MAXIMO_APRENDIDOS]:
            if es_dato_sensible(dato) or self.memoria.buscar(dato, 1, UMBRAL_REPETIDO):
                continue                             # sensible, o ya lo sabía dicho de otra forma
            resultado = self.memoria.guardar(dato, origen="aprendido")
            if resultado["ok"]:
                aprendidos.append(resultado["texto"])
        if aprendidos:
            registro.info("Aprendidos %d datos nuevos.", len(aprendidos))
            self._registrar({"intent": "memory_save", "action": "learn"}, True, "Aprendí algo sobre ti")
            self.bus.emitir("recuerdos")
            self.bus.emitir("aprendido", textos=aprendidos)
        return aprendidos

    # ------------------------------------------------------------------
    # MENSAJES, HISTORIAL Y AUDITORÍA
    # ------------------------------------------------------------------
    @staticmethod
    def _enmascarar(texto: str, intencion: dict) -> str:
        """Oculta la palabra de seguridad si aparece en la frase que se va a mostrar y guardar."""
        pasos = intencion.get("steps") or [intencion]
        for paso in pasos:
            if paso.get("intent") == "security_setup" and paso.get("text"):
                texto = re.sub(re.escape(paso["text"]), MASCARA, texto, flags=re.IGNORECASE)
        return texto

    def _persistir(self, rol: str, texto: str, extra: dict) -> None:
        if not self._guardar_historial():
            return
        try:
            if self.conversacion_id is None:
                self.conversacion_id = self.historial.crear()
                self.bus.emitir("conversacion", id=self.conversacion_id)
            self.historial.anadir(self.conversacion_id, rol, texto, extra)
        except Exception:
            registro.exception("No se pudo guardar el mensaje en el historial")

    def _anotar(self, mensaje: dict) -> None:
        self.sesion.append(mensaje)
        self._contexto.append({"role": "user" if mensaje["rol"] == "usuario" else "assistant",
                               "content": mensaje["texto"]})
        del self._contexto[:-MAXIMO_CONTEXTO]

    def _registrar_usuario(self, texto: str, origen: str) -> None:
        mensaje = {"id": self._nuevo_id(), "rol": "usuario", "texto": texto, "tipo": "chat", "datos": {},
                   "sugerencias": [], "fecha": time.time(), "origen": origen}
        self._persistir("usuario", texto, {"origen": origen})
        self._anotar(mensaje)
        self.bus.emitir("mensaje", **mensaje)

    def _decir(self, texto: str, tipo: str = "chat", datos: dict | None = None, sugerencias: list | None = None,
               hablar: bool = True, id_mensaje: str | None = None, guardar: bool = True) -> dict:
        mensaje = {"id": id_mensaje or self._nuevo_id(), "rol": "asistente", "texto": texto, "tipo": tipo,
                   "datos": datos or {}, "sugerencias": sugerencias or [], "fecha": time.time(),
                   "origen": "jarvis"}
        if guardar:
            self._persistir("asistente", texto, {"tipo": tipo, "datos": mensaje["datos"]})
        self._anotar(mensaje)
        self.bus.emitir("mensaje", **mensaje)
        self._respuestas.append(mensaje)
        if hablar and self._debe_hablar():
            self.habla.decir(texto)
        return mensaje

    def _registrar(self, intencion: dict, ejecutada: bool, descripcion: str) -> None:
        """Anota una acción de herramienta en el registro de auditoría y avisa a la interfaz."""
        intent, action = intencion.get("intent", ""), intencion.get("action", "")
        # De la memoria y de la escritura no se copia el contenido: es información personal.
        objetivo = "" if intent.startswith("memory") or action == "type_text" else (
            intencion.get("key") if action == "press_key" else intencion.get("target", ""))
        entrada = self.seguridad.registrar_accion(
            intent, action, objetivo or "", confirmada=self._autorizado, ejecutada=ejecutada,
            descripcion=descripcion, herramienta=registry.herramienta_de(intencion))
        if ejecutada:
            self.bus.emitir("accion", **entrada)

    # ------------------------------------------------------------------
    # CONTROL
    # ------------------------------------------------------------------
    def detener(self) -> None:
        """Parada de emergencia: corta la voz, la generación y cualquier acción pendiente.

        No espera al cerrojo: tiene que funcionar mientras JARVIS está ocupado.
        """
        self._cancelar.set()
        if self.habla:
            self.habla.detener()
        if self.pendiente:
            self._limpiar_pendiente()
        self.bus.emitir("estado", valor="reposo")

    def estado_pendiente(self) -> dict | None:
        """Lo que hay a la espera de respuesta, para que la interfaz lo pinte al conectarse."""
        pendiente = self.pendiente
        if not pendiente or time.time() > pendiente["expira"]:
            return None
        if pendiente["tipo"] == "accion":
            return {"tipo": "accion", "descripcion": pendiente["descripcion"], "modo": self.seguridad.modo,
                    "expira": pendiente["expira"]}
        return {"tipo": "recuerdo", "texto": pendiente["texto"], "expira": pendiente["expira"]}

    # ------------------------------------------------------------------
    # CONVERSACIONES
    # ------------------------------------------------------------------
    def nueva_conversacion(self, resumir: bool = True) -> None:
        anterior = self.conversacion_id
        with self._cerrojo:
            self._limpiar_pendiente()
            self.conversacion_id = None
            self.sesion = []
            self._contexto = []
        self.bus.emitir("conversacion", id=None)
        if resumir and anterior is not None and self._guardar_historial():
            threading.Thread(target=self.resumir_conversacion, args=(anterior,), name="jarvis-resumen",
                             daemon=True).start()

    def abrir_conversacion(self, conversacion_id: int) -> bool:
        """Retoma una conversación guardada: se muestra y el modelo recupera su contexto."""
        if not self.historial.obtener(conversacion_id):
            return False
        mensajes = self.historial.mensajes(conversacion_id)
        with self._cerrojo:
            self._limpiar_pendiente()
            self.conversacion_id = conversacion_id
            self.sesion = [dict(m, id=f"h{m['id']}", sugerencias=[]) for m in mensajes]
            self._contexto = [{"role": "user" if m["rol"] == "usuario" else "assistant", "content": m["texto"]}
                              for m in mensajes][-MAXIMO_CONTEXTO:]
        self.bus.emitir("conversacion", id=conversacion_id)
        return True

    def resumir_conversacion(self, conversacion_id: int) -> str:
        """Genera y guarda el resumen de una conversación (para recordarla en el futuro)."""
        try:
            conversacion = self.historial.obtener(conversacion_id)
            if not conversacion or conversacion["resumen"] or conversacion["n_mensajes"] < 4:
                return ""
            dialogo = [{"role": "user" if m["rol"] == "usuario" else "assistant", "content": m["texto"]}
                       for m in self.historial.mensajes(conversacion_id) if m["tipo"] != "confirmacion"]
            resumen = self.cerebro.resumir(dialogo)
            if resumen:
                self.historial.guardar_resumen(conversacion_id, resumen)
                self.bus.emitir("historial")
            return resumen
        except Exception:
            registro.exception("No se pudo resumir la conversación %s", conversacion_id)
            return ""
