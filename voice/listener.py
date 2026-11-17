"""
Voz de JARVIS: escucha (micrófono, detección de voz y transcripción)
=====================================================================
Captura el micrófono de forma continua, detecta cuándo alguien habla,
transcribe cada frase y se la entrega al asistente.

  1. Captura: sounddevice, en bloques de 30 ms.
  2. Detección de voz (VAD) por energía, con suelo de ruido adaptativo: una
     frase empieza tras varios bloques seguidos con voz y termina tras un
     silencio (ajuste voz.silencio_fin). Mientras no habla nadie, no se
     transcribe nada y el consumo es prácticamente nulo.
  3. Transcripción: Whisper en local (ajuste voz.modelo_whisper) o, como
     alternativa, el reconocedor de Google.
  4. Palabra de activación: `separar_activacion()` busca «Jarvis» (y sus
     errores de transcripción habituales) en el texto.

Mientras JARVIS habla («modo eco») el micrófono oye su propia voz por los
altavoces, así que no se cortan frases: se revisa cada segundo el audio
reciente buscando solo la palabra de activación, para poder interrumpirle.
Al terminar de hablar se descarta lo capturado: JARVIS nunca se contesta a
sí mismo.

El audio nunca se guarda en disco: se transcribe en memoria y se descarta.
"""
from __future__ import annotations

import collections
import difflib
import logging
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

from core.events import bus as bus_global
from core.text import normalizar, plano

registro = logging.getLogger("jarvis.escucha")

FRECUENCIA = 16000               # la que espera Whisper
DURACION_BLOQUE = 0.03
UMBRAL_MINIMO = 0.008            # energía mínima (0..1) para considerar que hay voz
FACTOR_RUIDO = 3.5               # la voz debe superar este múltiplo del ruido de fondo
BLOQUES_PARA_EMPEZAR = 4         # ~120 ms seguidos de voz abren una frase
BLOQUES_PREVIOS = 12             # ~360 ms que se conservan de antes del inicio
DURACION_MAXIMA = 14.0           # una frase nunca dura más de esto
VOZ_MINIMA = 0.35                # frases más cortas se descartan (golpes, clics)
VOZ_TRAS_ACTIVACION = 0.25       # con el detector: menos voz tras «Hey Jarvis» = solo se ha dicho eso
VENTANA_ECO = 2.6                # segundos de audio reciente que se revisan mientras JARVIS habla
CADENCIA_ECO = 1.0               # cada cuánto se revisan
SORDERA_TRAS_HABLAR = 0.3        # se ignora el micrófono este tiempo al callar JARVIS (cola del altavoz)
REINTENTO_WHISPER = 300          # segundos antes de volver a intentar cargar Whisper si falló

# Pista que se le da a Whisper para que escriba bien los nombres propios. Tiene que ser un
# glosario y NO el principio de la frase: con la pista «Jarvis,» Whisper da esa palabra por
# ya dicha y la omite de la transcripción (comprobado: «Jarvis, abre…» salía como «abre…»),
# y sin ninguna pista la escribe «Y Arvis» o «Yarvis».
PISTA_WHISPER = "Glosario: {activacion}, Spotify, bloc de notas, YouTube."
# Frases que Whisper «oye» a veces en el silencio o en el ruido.
ALUCINACIONES = {
    "subtitulos realizados por la comunidad de amara org", "subtitulos por la comunidad de amara org",
    "subtitulado por la comunidad de amara org", "amara org", "gracias por ver el video", "gracias por ver",
    "suscribete", "suscribete al canal", "gracias", "muchas gracias", "hasta la proxima",
    "mas informacion www alimmenta com",
}

_VARIANTES_JARVIS = {
    "jarvis", "jarvi", "yarvis", "yarbis", "jarbis", "harvis", "charvis", "yervis", "jervis", "garvis",
    "llarvis", "dyarvis", "jarviz", "yarviz", "jarbiz", "jarves", "jarvys", "yarbiz", "jarvish", "djarvis",
}
# «hey» dicho con acento español sale a veces «ay», «hay» o «ei».
_SALUDOS_PREVIOS = {"oye", "hola", "ey", "hey", "eh", "ok", "okay", "vale", "venga", "escucha", "perdona",
                    "y", "ya", "e", "a", "ah", "oh", "pues", "bueno", "mira", "por favor",
                    "ay", "hay", "ei", "hei"}
_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)
# «Jarvis» partido en dos por la transcripción: "Y Arvis", "ya Arbis", "Ll Arvis"... y con «Hey»
# pegado delante: "Ella Arvis", "Eya Arvis".
_JARVIS_PARTIDO = re.compile(r"\b(?:e|he|ei|hei|ey|hey)?(?:y|ya|ll|lla|dy|d|ch)\s+ar[vb]i[sz]\b", re.IGNORECASE)


# ==========================================
# PALABRA DE ACTIVACIÓN
# ==========================================
def _es_activacion(palabra: str, activacion: str) -> bool:
    if activacion == "jarvis":
        if palabra in _VARIANTES_JARVIS:
            return True
        return (5 <= len(palabra) <= 8 and palabra[0] in "jyhgcld"
                and difflib.SequenceMatcher(None, palabra, "jarvis").ratio() >= 0.8)
    return palabra == activacion or (
        len(palabra) >= 4 and difflib.SequenceMatcher(None, palabra, activacion).ratio() >= 0.82)


def separar_activacion(texto: str, activacion: str = "jarvis") -> tuple:
    """Busca la palabra de activación en una frase transcrita.

    Devuelve (se ha dicho, resto de la frase sin ella):
        "Jarvis, abre Spotify."      -> (True, "abre Spotify.")
        "Oye Yarvis ¿qué hora es?"   -> (True, "¿qué hora es?")
        "abre Spotify, Jarvis"       -> (True, "abre Spotify")
        "qué buen día hace"          -> (False, "qué buen día hace")
    """
    texto = (texto or "").strip()
    activacion = normalizar(activacion or "jarvis") or "jarvis"
    if activacion == "jarvis":
        texto = _JARVIS_PARTIDO.sub("Jarvis", texto)
    for coincidencia in _PALABRA.finditer(texto):
        if not _es_activacion(plano(coincidencia.group(0)), activacion):
            continue
        antes = texto[:coincidencia.start()].strip(" ,.;:¡!¿?-")
        despues = texto[coincidencia.end():].lstrip(" ,.;:!?-")
        if normalizar(antes) in _SALUDOS_PREVIOS:
            antes = ""
        resto = f"{antes} {despues}".strip() if antes else despues
        return True, resto.strip()
    return False, texto


# ==========================================
# TRANSCRIPCIÓN
# ==========================================
class Transcriptor:
    """Convierte audio (int16, 16 kHz, mono) en texto con Whisper o con Google."""

    def __init__(self, ajustes, bus=None):
        self.ajustes = ajustes
        self.bus = bus or bus_global
        self._modelo = None
        self._nombre_modelo = None
        self._cerrojo = threading.Lock()
        self._sin_whisper_hasta = 0.0
        self._cargando = False
        self.motor_en_uso = ""

    def modelo_descargado(self, nombre: str | None = None) -> bool:
        nombre = nombre or self.ajustes.obtener("voz.modelo_whisper", "base")
        return (Path.home() / ".cache" / "whisper" / f"{nombre}.pt").exists()

    def preparar(self) -> bool:
        """Carga el modelo de Whisper (la primera vez lo descarga). True si queda listo."""
        if self.ajustes.obtener("voz.motor_escucha") != "whisper":
            self.motor_en_uso = "google"
            return True
        nombre = self.ajustes.obtener("voz.modelo_whisper", "base")
        with self._cerrojo:
            if self._modelo is not None and self._nombre_modelo == nombre:
                return True
            if time.time() < self._sin_whisper_hasta:
                return False
            self._cargando = True
            try:
                import whisper
                descargado = self.modelo_descargado(nombre)
                if not descargado:
                    registro.info("Descargando el modelo de voz «%s» (solo la primera vez)...", nombre)
                self.bus.emitir("sistema", componente="voz", estado="cargando" if descargado else "descargando")
                self._modelo = whisper.load_model(nombre)
                self._nombre_modelo = nombre
                self.motor_en_uso = "whisper"
                registro.info("Modelo de voz «%s» cargado.", nombre)
                return True
            except Exception as error:
                registro.warning("Whisper no está disponible (%s); se usará el reconocedor de Google.", error)
                self._modelo = None
                self._sin_whisper_hasta = time.time() + REINTENTO_WHISPER
                self.motor_en_uso = "google"
                return False
            finally:
                self._cargando = False

    def transcribir(self, audio: np.ndarray) -> str:
        if self.ajustes.obtener("voz.motor_escucha") == "whisper":
            if self._cargando:
                # El modelo aún se está descargando o cargando: la frase se descarta en vez de
                # quedarse en cola (no se ejecutan órdenes dichas hace un minuto).
                return ""
            if self.preparar() and self._modelo is not None:
                return self._con_whisper(audio)
        return self._con_google(audio)

    def _con_whisper(self, audio: np.ndarray) -> str:
        activacion = (self.ajustes.obtener("voz.palabra_activacion") or "jarvis").strip().capitalize()
        try:
            with self._cerrojo:
                resultado = self._modelo.transcribe(
                    audio.astype(np.float32) / 32768.0, language=self.ajustes.obtener("idioma", "es"),
                    fp16=False, temperature=0.0, condition_on_previous_text=False,
                    initial_prompt=PISTA_WHISPER.format(activacion=activacion),
                    no_speech_threshold=0.6, without_timestamps=True)
        except Exception as error:
            registro.error("Fallo al transcribir con Whisper: %s", error)
            return ""
        trozos = [segmento["text"] for segmento in resultado.get("segments", [])
                  if not (segmento.get("no_speech_prob", 0) > 0.6 and segmento.get("avg_logprob", 0) < -1.0)]
        return self._filtrar(" ".join(trozos))

    def _con_google(self, audio: np.ndarray) -> str:
        try:
            import speech_recognition as sr
            datos = sr.AudioData(audio.astype(np.int16).tobytes(), FRECUENCIA, 2)
            return self._filtrar(sr.Recognizer().recognize_google(datos, language="es-ES"))
        except Exception as error:
            # UnknownValueError = no se ha entendido nada; RequestError = sin conexión.
            registro.debug("El reconocedor de Google no ha devuelto texto (%s).", type(error).__name__)
            return ""

    @staticmethod
    def _filtrar(texto: str) -> str:
        texto = re.sub(r"\s+", " ", texto or "").strip()
        limpio = normalizar(texto)
        return "" if not limpio or limpio in ALUCINACIONES else texto


# ==========================================
# ESCUCHA CONTINUA
# ==========================================
class Escucha:
    """Micrófono + detección de voz.

    Entrega cada frase a `al_oir(texto, forzada, durante_habla)`:
      - `forzada`: responde a una pulsación del botón del micrófono (no necesita
        palabra de activación).
      - `durante_habla`: se ha oído mientras JARVIS hablaba (solo sirve para
        interrumpirle; el texto lleva mezclada su propia voz).
    `al_evento(nombre)` avisa de "inicio_voz", "fin_voz", "sin_texto" y "sin_voz".
    """

    def __init__(self, ajustes, al_oir, al_evento=None, bus=None, transcriptor: Transcriptor | None = None):
        self.ajustes = ajustes
        self.al_oir = al_oir
        self.al_evento = al_evento or (lambda nombre: None)
        self.bus = bus or bus_global
        self.transcriptor = transcriptor or Transcriptor(ajustes, self.bus)
        self.error = ""

        self._hilo = None
        self._parada = threading.Event()
        self._parada.set()
        self._frecuencia = FRECUENCIA
        self._forzada_hasta = 0.0
        self._temporal = False           # el micrófono se abrió solo para una pulsación
        self._ultimo_nivel = 0.0
        self._eco = False
        self._sordo_hasta = 0.0
        # Detector dedicado de «Hey Jarvis» (voice/activacion.py), opcional. Con él solo se transcribe
        # lo que va dirigido a JARVIS: tras la palabra, al pulsar el micrófono o mientras está atento.
        self.detector = None
        self.atento = lambda: False          # el asistente dice si está en su «ventana» de escucha
        self._reiniciar_vad()

    # ------------------------------------------------------------------
    # CONTROL
    # ------------------------------------------------------------------
    @property
    def activa(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive() and not self._parada.is_set()

    @property
    def esperando_pulsacion(self) -> bool:
        """True si se ha pulsado el micrófono y aún se espera la frase."""
        return bool(self._forzada_hasta)

    def iniciar(self) -> bool:
        if self.activa:
            self._temporal = False
            return True
        self.error = ""
        # Cada arranque tiene su propia señal de parada y su propia cola: un hilo
        # anterior que aún se esté cerrando no puede interferir con el nuevo.
        self._parada = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, args=(self._parada, queue.Queue()),
                                      name="jarvis-escucha", daemon=True)
        self._hilo.start()
        return True

    def detener(self) -> None:
        self._parada.set()
        self._forzada_hasta = 0.0
        self._temporal = False

    def escuchar_una_vez(self, espera: float = 8.0) -> None:
        """La próxima frase se entrega sin exigir la palabra de activación (botón del micrófono)."""
        self._forzada_hasta = time.time() + espera
        if not self.activa:
            self.iniciar()
            self._temporal = True

    def eco(self, activo: bool) -> None:
        """Avisa de que JARVIS empieza (True) o deja (False) de hablar por los altavoces."""
        if activo == self._eco:
            return
        self._eco = activo
        self._reiniciar_vad()
        if not activo:
            self._sordo_hasta = time.time() + SORDERA_TRAS_HABLAR

    # ------------------------------------------------------------------
    # CAPTURA
    # ------------------------------------------------------------------
    def _abrir_microfono(self, cola: queue.Queue):
        import sounddevice as sd

        def al_audio(entrada, cuadros, tiempos, estado):
            cola.put(entrada[:, 0].copy())

        ultimo_error = None
        for frecuencia in (FRECUENCIA, None):        # None = la frecuencia nativa del micrófono
            try:
                if frecuencia is None:
                    frecuencia = int(sd.query_devices(kind="input")["default_samplerate"])
                flujo = sd.InputStream(samplerate=frecuencia, channels=1, dtype="int16",
                                       blocksize=int(frecuencia * DURACION_BLOQUE), callback=al_audio)
                flujo.start()
                self._frecuencia = frecuencia
                return flujo
            except Exception as error:
                ultimo_error = error
        raise RuntimeError(f"no se pudo abrir el micrófono: {ultimo_error}")

    def _bucle(self, parada: threading.Event, cola: queue.Queue) -> None:
        flujo = None
        fallo = ""
        try:
            flujo = self._abrir_microfono(cola)
            self._reiniciar_vad()
            self.bus.emitir("sistema", componente="voz", estado="escuchando")
            registro.info("Micrófono abierto a %d Hz.", self._frecuencia)
            while not parada.is_set():
                try:
                    bloque = cola.get(timeout=0.3)
                except queue.Empty:
                    bloque = None
                if bloque is not None and time.time() >= self._sordo_hasta:
                    if self.detector is not None:
                        self._vigilar_activacion(bloque)
                    if self._eco:
                        self._procesar_eco(bloque)
                    else:
                        self._procesar_bloque(bloque)
                self._vigilar_pulsacion()
        except Exception as error:
            fallo = str(error)
            self.error = fallo
            registro.error("Escucha detenida: %s", error)
        finally:
            parada.set()
            if flujo is not None:
                try:
                    flujo.stop()
                    flujo.close()
                except Exception:
                    pass
            self.bus.emitir("nivel", valor=0)
            self.bus.emitir("sistema", componente="voz", estado="sin_microfono" if fallo else "apagada")

    def _vigilar_pulsacion(self) -> None:
        """Si se pulsó el micrófono y nadie ha hablado a tiempo, se abandona la espera."""
        if self._forzada_hasta and time.time() > self._forzada_hasta and not self._grabando:
            self._forzada_hasta = 0.0
            self.al_evento("sin_voz")
            if self._temporal:
                self.detener()

    # ------------------------------------------------------------------
    # DETECCIÓN DE VOZ
    # ------------------------------------------------------------------
    def _reiniciar_vad(self) -> None:
        self._ruido = getattr(self, "_ruido", 0.003)
        self._grabando = False
        self._seguidos = 0
        self._silencio = 0
        self._con_voz = 0
        self._bloques = []
        self._activacion_en = None       # en qué bloque de la frase en curso oyó el detector «Hey Jarvis»
        self._previos = collections.deque(maxlen=BLOQUES_PREVIOS)
        self._recientes = collections.deque(maxlen=int(VENTANA_ECO / DURACION_BLOQUE))
        self._desde_revision = 0

    @staticmethod
    def _rms(bloque: np.ndarray) -> float:
        return float(np.sqrt(np.mean(bloque.astype(np.float32) ** 2))) / 32768.0

    def _umbral_de_voz(self) -> float:
        return max(UMBRAL_MINIMO, self._ruido * FACTOR_RUIDO)

    def _energia(self, bloque: np.ndarray) -> float:
        energia = self._rms(bloque)
        ahora = time.time()
        if ahora - self._ultimo_nivel > 0.07:
            self._ultimo_nivel = ahora
            self.bus.emitir("nivel", valor=round(min(1.0, energia * 12), 3))
        return energia

    def _procesar_bloque(self, bloque: np.ndarray) -> None:
        energia = self._energia(bloque)
        hay_voz = energia > self._umbral_de_voz()
        if not self._grabando:
            self._previos.append(bloque)
            if hay_voz:
                self._seguidos += 1
            else:
                self._seguidos = 0
                self._ruido = 0.95 * self._ruido + 0.05 * energia      # el suelo de ruido se adapta
            if self._seguidos >= BLOQUES_PARA_EMPEZAR:
                self._grabando = True
                self._bloques = list(self._previos)
                self._silencio = 0
                self._con_voz = self._seguidos
                self.al_evento("inicio_voz")
            return

        self._bloques.append(bloque)
        self._silencio = 0 if hay_voz else self._silencio + 1
        self._con_voz += 1 if hay_voz else 0
        bloques_de_silencio = int(float(self.ajustes.obtener("voz.silencio_fin", 0.9)) / DURACION_BLOQUE)
        demasiado_larga = len(self._bloques) * DURACION_BLOQUE >= DURACION_MAXIMA
        if self._silencio < bloques_de_silencio and not demasiado_larga:
            return

        bloques, silencio = self._bloques, self._silencio
        activacion_en, self._activacion_en = self._activacion_en, None
        self._grabando, self._bloques, self._seguidos, self._silencio = False, [], 0, 0
        self._previos.clear()
        if demasiado_larga:
            # Un ruido constante se estaba tomando por voz: se sube el suelo de ruido.
            self._ruido = max(self._ruido, energia / FACTOR_RUIDO)
        self.al_evento("fin_voz")
        if self._con_voz * DURACION_BLOQUE < VOZ_MINIMA:      # solo cuenta el tiempo con voz de verdad
            self.al_evento("sin_texto")
            return
        if self.detector is not None and not self._forzada_hasta and not self.atento():
            self.al_evento("sin_texto")          # no iba dirigido a JARVIS: ni se transcribe
            return
        if activacion_en is not None and self._forzada_hasta:
            umbral = self._umbral_de_voz()
            tras = sum(DURACION_BLOQUE for b in bloques[activacion_en:] if self._rms(b) > umbral)
            if tras < VOZ_TRAS_ACTIVACION:
                # Solo ha dicho «Hey Jarvis»: no hace falta transcribirlo (con acento, Whisper lo
                # escribe «Ay Jarvis» o «Ella Arvis»). JARVIS contesta y queda a la escucha.
                self._entregar(None, texto=self.ajustes.obtener("voz.palabra_activacion") or "Jarvis")
                return
        self._entregar(np.concatenate(bloques))

    def _vigilar_activacion(self, bloque: np.ndarray) -> None:
        """Con el detector dedicado: al oír «Hey Jarvis» la frase en curso (y la siguiente) se atienden."""
        try:
            oido = self.detector.procesar(self._a_16k(bloque))
        except Exception:
            registro.exception("Fallo del detector de activación: se vuelve a Whisper")
            self.detector = None
            return
        if not oido:
            return
        registro.info("Palabra de activación detectada.")
        if self._eco:
            try:
                self.al_oir(self.ajustes.obtener("voz.palabra_activacion") or "Jarvis", False, True)
            except Exception:
                registro.exception("Error al atender una interrupción por voz")
            return
        self._activacion_en = len(self._bloques) if self._grabando else 0
        self.escuchar_una_vez(espera=6.0)
        self.al_evento("activacion")

    def _procesar_eco(self, bloque: np.ndarray) -> None:
        """Mientras JARVIS habla: se busca solo la palabra de activación en el audio reciente."""
        if self.detector is not None:
            self._energia(bloque)                # el detector ya vigila la palabra: no hace falta transcribir
            return
        self._energia(bloque)
        self._recientes.append(bloque)
        self._desde_revision += 1
        if (self._desde_revision * DURACION_BLOQUE < CADENCIA_ECO
                or len(self._recientes) * DURACION_BLOQUE < CADENCIA_ECO):
            return
        self._desde_revision = 0
        texto = self.transcriptor.transcribir(self._a_16k(np.concatenate(list(self._recientes))))
        if texto and self._eco:
            try:
                self.al_oir(texto, False, True)
            except Exception:
                registro.exception("Error al atender una interrupción por voz")

    def _a_16k(self, audio: np.ndarray) -> np.ndarray:
        if self._frecuencia == FRECUENCIA:
            return audio
        destino = int(len(audio) * FRECUENCIA / self._frecuencia)       # remuestreo sencillo a 16 kHz
        return np.interp(np.linspace(0, len(audio) - 1, destino), np.arange(len(audio)),
                         audio.astype(np.float32)).astype(np.int16)

    def _entregar(self, audio: np.ndarray | None, texto: str | None = None) -> None:
        if texto is None:
            texto = self.transcriptor.transcribir(self._a_16k(audio))
        forzada = bool(self._forzada_hasta)
        if not texto:
            self.al_evento("sin_texto")
            return
        self._forzada_hasta = 0.0
        try:
            self.al_oir(texto, forzada, False)
        except Exception:
            registro.exception("Error al atender una frase oída")
        if forzada and self._temporal:
            self.detener()
