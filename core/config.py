"""
Configuración persistente de JARVIS
====================================
Los ajustes viven en data/ajustes.json. Este módulo aporta los valores por
defecto, valida cada cambio (solo se aceptan claves conocidas, del tipo
correcto y dentro de sus opciones) y avisa a quien esté suscrito cuando
algo cambia.

Las claves se nombran con puntos: "voz.motor_habla", "ia.modelo"...
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def dir_datos() -> Path:
    """Carpeta donde JARVIS guarda todo lo que genera (BD, logs, audio…).

    Se puede redirigir con la variable de entorno JARVIS_DATA_DIR (lo usan
    las pruebas para no tocar los datos reales).
    """
    ruta = Path(os.environ.get("JARVIS_DATA_DIR") or (RAIZ / "data"))
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


VERSION_AJUSTES = 2                       # sube cuando cambia algún valor por defecto (ver _migrar)

AJUSTES_POR_DEFECTO = {
    "version": VERSION_AJUSTES,
    "usuario": {"nombre": "Ruth", "genero": "femenino"},   # cómo le habla la IA: "femenino" | "masculino" | "neutro"
    "idioma": "es",
    "modo": "texto",                      # "texto" | "voz"
    "ia": {
        "modelo": "granite4.1:8b",        # sabe usar herramientas: busca en vez de inventar
        "temperatura": 0.3,
        "mensajes_contexto": 8,           # intercambios recientes que ve el modelo
        "contexto": 8192,                 # tokens de contexto del modelo (Ollama usa 4096 y corta las instrucciones)
        "host": "http://127.0.0.1:11434",
        "arrancar_ollama": True,          # lanzar «ollama serve» si no está en marcha
    },
    "personalidad": {"sarcasmo": 2},      # 0 neutro · 1 suave · 2 sarcástico · 3 afilado
    "voz": {
        "palabra_activacion": "jarvis",
        "motor_escucha": "whisper",       # "whisper" (local) | "google"
        "motor_activacion": "whisper",    # cómo se oye «Jarvis»: "whisper" | "openwakeword" («Hey Jarvis»)
        "modelo_whisper": "base",         # tiny | base | small | medium
        "motor_habla": "edge",            # "edge" (voz neuronal de Microsoft) | "google" | "elevenlabs" | "windows"
        "genero": "femenino",             # "femenino" | "masculino": voz y forma de hablar de sí
        "voz_edge": "",                   # vacío = la voz por defecto del género elegido
        "voz_elevenlabs": "",             # vacío = la voz por defecto del género elegido
        "velocidad": 1.0,
        "leer_en_modo_texto": False,      # leer las respuestas en voz alta también en modo texto
        "ventana_fluidez": 8,             # segundos de escucha sin repetir «Jarvis»
        "silencio_fin": 0.9,              # segundos de silencio que cierran una frase
    },
    "memoria": {
        "aprender_automaticamente": True, # guardar sola lo que cuentas de ti (lo ves y lo borras en Memoria)
        "sugerir_recuerdos": True,        # si no aprende sola: preguntar antes de guardar un dato personal
        "guardar_historial": True,        # conservar las conversaciones
        "modelo_embeddings": "nomic-embed-text",
    },
    "internet": {"max_resultados": 5, "ciudad": ""},
    "musica": {"servicio": "youtube"},   # dónde pone las canciones que pidas: "youtube" | "spotify"
    # Agenda: un calendario iCal (.ics) opcional, de solo lectura (p. ej. la dirección secreta de Google Calendar)
    "agenda": {"calendario": "", "aviso_minutos": 15},
    # «Buenos días»: el tiempo, lo que tienes hoy y titulares. Automático: al abrir JARVIS por la mañana.
    "resumen": {"automatico": False, "noticias": True},
    # Usar JARVIS desde el navegador del móvil, en la misma Wi-Fi (con PIN). Se aplica al reiniciar.
    "movil": {"activo": False, "puerto": 8770},
    "herramientas": {
        "apps": True, "cerrar": True, "busqueda": True, "tiempo": True,
        "escritura": True, "teclas": True, "comandos": True, "memoria": True, "musica": True,
        "recordatorios": True, "equipo": True, "pantalla": True, "archivos": True, "mcp": True, "mensajes": True,
    },
    "interfaz": {
        "ancho": 1320,
        "alto": 820,
        "aceleracion_grafica": False,     # GPU en la ventana nativa: más fluida, pero inestable en algunos equipos
        "overlay": True,                  # el reactor flotante, siempre encima: se pulsa para hablar
        "posicion_overlay": "",           # «x,y» donde lo dejaste (vacío: abajo a la derecha)
    },
}

# Valores permitidos para los ajustes de tipo «lista cerrada».
OPCIONES = {
    "idioma": ["es"],
    "modo": ["texto", "voz"],
    "voz.motor_escucha": ["whisper", "google"],
    "voz.motor_activacion": ["whisper", "openwakeword"],
    "voz.modelo_whisper": ["tiny", "base", "small", "medium"],
    "voz.motor_habla": ["edge", "google", "elevenlabs", "windows"],
    "voz.genero": ["femenino", "masculino"],
    "usuario.genero": ["femenino", "masculino", "neutro"],
    "musica.servicio": ["youtube", "spotify"],
}

# Límites para los ajustes numéricos.
RANGOS = {
    "version": (1, 99),
    "ia.temperatura": (0.0, 1.5),
    "ia.contexto": (2048, 32768),
    "ia.mensajes_contexto": (1, 30),
    "personalidad.sarcasmo": (0, 3),
    "voz.velocidad": (0.6, 1.6),
    "voz.ventana_fluidez": (0, 30),
    "voz.silencio_fin": (0.4, 3.0),
    "internet.max_resultados": (1, 10),
    "agenda.aviso_minutos": (0, 1440),
    "movil.puerto": (1024, 65535),
    "interfaz.ancho": (900, 4000),
    "interfaz.alto": (600, 3000),
}

LONGITUD_MAXIMA_TEXTO = 200


# ----------------------------------------------------------------------
# SECRETOS (claves de servicios externos)
# ----------------------------------------------------------------------
# Se guardan aparte de los ajustes, en data/secretos.json, y nunca se envían a
# la interfaz ni se escriben en los logs. Una variable de entorno con el mismo
# nombre en mayúsculas (p. ej. ELEVENLABS_API_KEY) tiene prioridad.
SECRETOS_CONOCIDOS = {"elevenlabs_api_key", "movil_pin"}


def leer_secreto(nombre: str) -> str:
    valor = os.environ.get(nombre.upper(), "").strip()
    if valor:
        return valor
    try:
        datos = json.loads((dir_datos() / "secretos.json").read_text(encoding="utf-8"))
        return str(datos.get(nombre, "")).strip()
    except (OSError, ValueError):
        return ""


def guardar_secreto(nombre: str, valor: str) -> bool:
    if nombre not in SECRETOS_CONOCIDOS:
        return False
    ruta = dir_datos() / "secretos.json"
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    except (OSError, ValueError):
        datos = {}
    if valor and valor.strip():
        datos[nombre] = valor.strip()
    else:
        datos.pop(nombre, None)
    try:
        ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _migrar(guardado: dict) -> bool:
    """Actualiza un ajustes.json de una versión anterior. Devuelve True si ha cambiado algo.

    ajustes.json guarda todos los valores, también los que nadie ha tocado. Por eso, cuando
    cambia un valor por defecto, los ficheros antiguos se quedarían con el viejo. Aquí solo se
    cambian los valores que coinciden con el antiguo valor por defecto; lo que la usuaria
    haya elegido a mano se respeta.
    """
    version = guardado.get("version") if isinstance(guardado.get("version"), int) else 1
    if version >= VERSION_AJUSTES:
        return False
    if version < 2:
        voz = guardado.setdefault("voz", {}) if isinstance(guardado.get("voz", {}), dict) else {}
        ia = guardado.setdefault("ia", {}) if isinstance(guardado.get("ia", {}), dict) else {}
        # Google era la voz por defecto mientras la de Microsoft fallaba (error 403 de edge-tts antiguo).
        if voz.get("motor_habla") == "google":
            voz["motor_habla"] = "edge"
        if voz.get("voz_edge") in ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"):
            voz["voz_edge"] = ""                       # automática según el género de la voz
        # llama3 no sabe usar herramientas: se inventaba datos en vez de buscarlos.
        if ia.get("modelo") == "llama3":
            ia["modelo"] = "granite4.1:8b"
        # Con una temperatura más baja se inventa menos (evaluado con granite4.1:8b).
        if ia.get("temperatura") == 0.7:
            ia["temperatura"] = 0.3
    guardado["version"] = VERSION_AJUSTES
    return True


def _aplanar(datos: dict, prefijo: str = "") -> dict:
    plano = {}
    for clave, valor in datos.items():
        ruta = f"{prefijo}{clave}"
        if isinstance(valor, dict):
            plano.update(_aplanar(valor, ruta + "."))
        else:
            plano[ruta] = valor
    return plano


class Ajustes:
    def __init__(self, ruta: str | Path | None = None):
        self.ruta = Path(ruta) if ruta else dir_datos() / "ajustes.json"
        self._cerrojo = threading.RLock()
        self._datos = copy.deepcopy(AJUSTES_POR_DEFECTO)
        self._oyentes = []
        self._cargar()

    # ------------------------------------------------------------------
    # LECTURA
    # ------------------------------------------------------------------
    def _cargar(self) -> None:
        if not self.ruta.exists():
            return
        try:
            guardado = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(guardado, dict):
            return
        migrado = _migrar(guardado)
        # Cada valor guardado pasa por la misma validación que un cambio normal:
        # un ajustes.json manipulado a mano no puede colar valores inválidos.
        for clave, valor in _aplanar(guardado).items():
            self._asignar(clave, valor)
        if migrado:
            self.guardar()

    def obtener(self, clave: str, defecto=None):
        with self._cerrojo:
            nodo = self._datos
            for parte in clave.split("."):
                if not isinstance(nodo, dict) or parte not in nodo:
                    return defecto
                nodo = nodo[parte]
            return copy.deepcopy(nodo)

    def todo(self) -> dict:
        with self._cerrojo:
            return copy.deepcopy(self._datos)

    # ------------------------------------------------------------------
    # ESCRITURA (validada)
    # ------------------------------------------------------------------
    @staticmethod
    def _valor_valido(clave: str, valor):
        """Devuelve el valor normalizado, o lanza ValueError si no es válido."""
        defecto = AJUSTES_POR_DEFECTO
        for parte in clave.split("."):
            if not isinstance(defecto, dict) or parte not in defecto:
                raise ValueError(f"Ajuste desconocido: {clave}")
            defecto = defecto[parte]
        if isinstance(defecto, dict):
            raise ValueError(f"{clave} es un grupo de ajustes, no un valor")

        if isinstance(defecto, bool):
            if not isinstance(valor, bool):
                raise ValueError(f"{clave} debe ser verdadero o falso")
        elif isinstance(defecto, (int, float)):
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise ValueError(f"{clave} debe ser un número")
            if isinstance(defecto, int):
                valor = int(round(valor))
            minimo, maximo = RANGOS.get(clave, (None, None))
            if minimo is not None and not (minimo <= valor <= maximo):
                raise ValueError(f"{clave} debe estar entre {minimo} y {maximo}")
        elif isinstance(defecto, str):
            if not isinstance(valor, str):
                raise ValueError(f"{clave} debe ser un texto")
            valor = valor.strip()
            if len(valor) > LONGITUD_MAXIMA_TEXTO:
                raise ValueError(f"{clave} es demasiado largo")
            if clave in OPCIONES and valor not in OPCIONES[clave]:
                raise ValueError(f"{clave} debe ser uno de: {', '.join(OPCIONES[clave])}")
        return valor

    def _asignar(self, clave: str, valor) -> bool:
        try:
            valor = self._valor_valido(clave, valor)
        except ValueError:
            return False
        partes = clave.split(".")
        nodo = self._datos
        for parte in partes[:-1]:
            nodo = nodo[parte]
        nodo[partes[-1]] = valor
        return True

    def establecer(self, clave: str, valor, guardar: bool = True) -> None:
        """Cambia un ajuste. Lanza ValueError si la clave o el valor no son válidos."""
        with self._cerrojo:
            valor = self._valor_valido(clave, valor)
            anterior = self.obtener(clave)
            self._asignar(clave, valor)
            if guardar:
                self.guardar()
        if anterior != valor:
            self._avisar([clave])

    def actualizar(self, cambios: dict) -> tuple[list, dict]:
        """Aplica varios cambios a la vez (dict anidado o con claves con puntos).

        Devuelve (claves aplicadas, {clave: motivo del rechazo}).
        """
        aplicadas, rechazadas = [], {}
        with self._cerrojo:
            for clave, valor in _aplanar(cambios).items():
                try:
                    valor = self._valor_valido(clave, valor)
                except ValueError as error:
                    rechazadas[clave] = str(error)
                    continue
                if self.obtener(clave) != valor:
                    self._asignar(clave, valor)
                    aplicadas.append(clave)
            if aplicadas:
                self.guardar()
        if aplicadas:
            self._avisar(aplicadas)
        return aplicadas, rechazadas

    def guardar(self) -> None:
        with self._cerrojo:
            try:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                temporal = self.ruta.with_suffix(".tmp")
                temporal.write_text(json.dumps(self._datos, ensure_ascii=False, indent=2), encoding="utf-8")
                temporal.replace(self.ruta)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # AVISOS
    # ------------------------------------------------------------------
    def al_cambiar(self, funcion) -> None:
        """Registra una función que recibirá la lista de claves modificadas."""
        self._oyentes.append(funcion)

    def _avisar(self, claves: list) -> None:
        for funcion in list(self._oyentes):
            try:
                funcion(claves)
            except Exception:
                pass
