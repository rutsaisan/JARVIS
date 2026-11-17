"""
Voz de JARVIS: habla (síntesis y reproducción)
===============================================
Convierte en audio lo que JARVIS dice y lo reproduce, con posibilidad de
cortarlo en cualquier momento.

Motores disponibles (se elige en Ajustes; si uno falla se prueba el siguiente):
    edge        voces neuronales de Microsoft, muy naturales (necesita Internet, sin clave) — por defecto
    elevenlabs  ElevenLabs, la más natural (necesita Internet y una clave propia)
    google      Google Translate TTS, vía gTTS (necesita Internet, sin clave; suena más robótica)
    windows     voz instalada en Windows, SAPI (funciona sin conexión)

Si edge falla con un error 403, el paquete edge-tts está desfasado: pip install -U edge-tts.

Cómo funciona por dentro: dos hilos en cadena. Uno sintetiza cada frase en un
fichero y el otro las va reproduciendo, de modo que la frase siguiente ya se
está preparando mientras suena la actual. `detener()` invalida todo lo que hay
en cola y corta el audio que esté sonando.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from core.config import dir_datos, leer_secreto
from core.events import bus as bus_global

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

registro = logging.getLogger("jarvis.habla")

ORDEN_DE_RESPALDO = ["edge", "google", "windows"]   # si el motor elegido falla, se prueban estos
PAUSA_TRAS_FALLO = 60            # segundos sin reintentar un motor que acaba de fallar
# Voces por defecto según el género elegido en Ajustes (la voz de Google y la de
# Windows en español son siempre femeninas).
VOZ_EDGE_POR_GENERO = {"femenino": "es-ES-ElviraNeural", "masculino": "es-ES-AlvaroNeural"}
VOZ_ELEVENLABS_POR_GENERO = {"femenino": "21m00Tcm4TlvDq8ikWAM", "masculino": "JBFqnCBsd6RMkjVDRZzb"}
VOCES_EDGE = [
    {"id": "", "nombre": "Automática (según el género)"},
    {"id": "es-ES-ElviraNeural", "nombre": "Elvira · España"},
    {"id": "es-ES-XimenaNeural", "nombre": "Ximena · España"},
    {"id": "es-MX-DaliaNeural", "nombre": "Dalia · México"},
    {"id": "es-AR-ElenaNeural", "nombre": "Elena · Argentina"},
    {"id": "es-US-PalomaNeural", "nombre": "Paloma · Estados Unidos"},
    {"id": "es-ES-AlvaroNeural", "nombre": "Álvaro · España"},
    {"id": "es-MX-JorgeNeural", "nombre": "Jorge · México"},
    {"id": "es-AR-TomasNeural", "nombre": "Tomás · Argentina"},
    {"id": "es-US-AlonsoNeural", "nombre": "Alonso · Estados Unidos"},
]

_EMOJIS = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]")
_ENLACE = re.compile(r"https?://\S+|www\.\S+")


def limpiar_para_voz(texto: str) -> str:
    """Deja el texto listo para leerse en voz alta: sin formato, enlaces ni símbolos."""
    texto = _ENLACE.sub("un enlace", texto or "")
    texto = _EMOJIS.sub("", texto)
    texto = re.sub(r"```.*?```", " ", texto, flags=re.DOTALL)          # bloques de código
    texto = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", texto, flags=re.MULTILINE)   # viñetas y numeración
    texto = re.sub(r"[*_`#>|~«»\[\]{}•]", "", texto)
    texto = re.sub(r"\s*\n+\s*", ". ", texto.strip())                # cada línea, una frase
    texto = re.sub(r"([.!?:;,])\s*\.(?=\s|$)", r"\1", texto)         # sin puntos duplicados
    texto = re.sub(r"\s+", " ", texto).strip()
    return re.sub(r"^[.,;:\s]+", "", texto)


def _genero(ajustes) -> str:
    genero = ajustes.obtener("voz.genero", "femenino")
    return genero if genero in VOZ_EDGE_POR_GENERO else "femenino"


# ==========================================
# MOTORES DE SÍNTESIS: cada uno crea un fichero de audio y devuelve su ruta
# ==========================================
def _sintetizar_edge(texto: str, destino: Path, ajustes) -> Path:
    import edge_tts
    ritmo = f"{round((float(ajustes.obtener('voz.velocidad', 1.0)) - 1) * 100):+d}%"
    ruta = destino.with_suffix(".mp3")
    voz = ajustes.obtener("voz.voz_edge") or VOZ_EDGE_POR_GENERO[_genero(ajustes)]
    asyncio.run(edge_tts.Communicate(texto, voz, rate=ritmo).save(str(ruta)))
    return ruta


def _sintetizar_google(texto: str, destino: Path, ajustes) -> Path:
    from gtts import gTTS
    ruta = destino.with_suffix(".mp3")
    gTTS(text=texto, lang=ajustes.obtener("idioma", "es"), tld="es", slow=False).save(str(ruta))
    return ruta


def _sintetizar_elevenlabs(texto: str, destino: Path, ajustes) -> Path:
    clave = leer_secreto("elevenlabs_api_key")
    if not clave:
        raise RuntimeError("falta la clave de ElevenLabs")
    from elevenlabs.client import ElevenLabs
    audio = ElevenLabs(api_key=clave).text_to_speech.convert(
        voice_id=ajustes.obtener("voz.voz_elevenlabs") or VOZ_ELEVENLABS_POR_GENERO[_genero(ajustes)],
        model_id="eleven_multilingual_v2", text=texto, output_format="mp3_44100_128")
    ruta = destino.with_suffix(".mp3")
    with open(ruta, "wb") as fichero:
        for trozo in audio:
            if trozo:
                fichero.write(trozo)
    return ruta


def _sintetizar_windows(texto: str, destino: Path, ajustes) -> Path:
    import pythoncom
    import win32com.client
    ruta = destino.with_suffix(".wav")
    pythoncom.CoInitialize()
    try:
        voz = win32com.client.Dispatch("SAPI.SpVoice")
        for candidata in voz.GetVoices():
            if "spanish" in candidata.GetDescription().lower():
                voz.Voice = candidata
                break
        flujo = win32com.client.Dispatch("SAPI.SpFileStream")
        flujo.Open(str(ruta), 3)                     # 3 = crear para escritura
        voz.AudioOutputStream = flujo
        voz.Rate = max(-5, min(5, round((float(ajustes.obtener("voz.velocidad", 1.0)) - 1) * 10)))
        voz.Speak(texto)
        flujo.Close()
    finally:
        pythoncom.CoUninitialize()
    return ruta


MOTORES = {
    "edge": _sintetizar_edge,
    "google": _sintetizar_google,
    "elevenlabs": _sintetizar_elevenlabs,
    "windows": _sintetizar_windows,
}


def motores_disponibles() -> dict:
    """Qué motores se pueden usar en este equipo (para el panel de Ajustes)."""
    def instalado(modulo: str) -> bool:
        import importlib.util
        return importlib.util.find_spec(modulo) is not None

    return {
        "edge": instalado("edge_tts"),
        "google": instalado("gtts"),
        "elevenlabs": instalado("elevenlabs") and bool(leer_secreto("elevenlabs_api_key")),
        "windows": os.name == "nt" and instalado("win32com"),
    }


# ==========================================
# HABLA
# ==========================================
class Habla:
    def __init__(self, ajustes, bus=None, volumen: float = 1.0):
        self.ajustes = ajustes
        self.bus = bus or bus_global
        self.volumen = volumen
        self.al_empezar = None           # se llama cuando empieza a sonar la voz
        self.al_terminar = None          # se llama cuando JARVIS acaba de hablar del todo
        self.hablando = False            # hay audio sonando ahora mismo
        self.ultimo_motor = ""

        self._textos = queue.Queue()
        self._audios = queue.Queue()
        self._generacion = 0             # cambia con cada detener(): lo anterior queda invalidado
        self._pendientes = 0
        self._cerrojo = threading.Lock()
        self._hilos = []
        self._fallos = {}
        self._mezclador_listo = False

        self.carpeta = dir_datos() / "audio"
        self.carpeta.mkdir(parents=True, exist_ok=True)
        for resto in self.carpeta.glob("voz_*"):     # audios huérfanos de una ejecución anterior
            try:
                resto.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------------
    @property
    def ocupada(self) -> bool:
        """True mientras quede algo por sintetizar o por sonar."""
        return self._pendientes > 0

    def decir(self, texto: str) -> None:
        texto = limpiar_para_voz(texto)
        if not texto:
            return
        self._arrancar_hilos()
        with self._cerrojo:
            self._pendientes += 1
            generacion = self._generacion
        self._textos.put((generacion, texto))

    def detener(self) -> None:
        """Corta la voz inmediatamente y descarta lo que quedaba por decir."""
        with self._cerrojo:
            self._generacion += 1

    def esperar(self, maximo: float = 30.0) -> bool:
        """Bloquea hasta que termina de hablar (útil en consola y pruebas)."""
        limite = time.time() + maximo
        while self.ocupada and time.time() < limite:
            time.sleep(0.05)
        return not self.ocupada

    # ------------------------------------------------------------------
    # HILO 1: SÍNTESIS
    # ------------------------------------------------------------------
    def _arrancar_hilos(self) -> None:
        if self._hilos:
            return
        for nombre, funcion in (("jarvis-sintesis", self._bucle_sintesis), ("jarvis-audio", self._bucle_audio)):
            hilo = threading.Thread(target=funcion, name=nombre, daemon=True)
            hilo.start()
            self._hilos.append(hilo)

    def _orden_de_motores(self) -> list:
        elegido = self.ajustes.obtener("voz.motor_habla", "edge")
        orden = [elegido] + [motor for motor in ORDEN_DE_RESPALDO if motor != elegido]
        return [motor for motor in orden if time.time() >= self._fallos.get(motor, 0)]

    def sintetizar(self, texto: str) -> Path | None:
        """Crea el audio de `texto` con el primer motor que funcione. None si no hay ninguno."""
        for motor in self._orden_de_motores() or ORDEN_DE_RESPALDO[-1:]:
            destino = self.carpeta / f"voz_{uuid.uuid4().hex[:12]}"
            try:
                ruta = MOTORES[motor](texto, destino, self.ajustes)
                if ruta.exists() and ruta.stat().st_size > 200:
                    self.ultimo_motor = motor
                    return ruta
                raise RuntimeError("audio vacío")
            except Exception as error:
                self._fallos[motor] = time.time() + PAUSA_TRAS_FALLO
                registro.warning("El motor de voz «%s» ha fallado (%s: %s); se prueba el siguiente.",
                                 motor, type(error).__name__, str(error)[:90])
                self._borrar(destino.with_suffix(".mp3"))
                self._borrar(destino.with_suffix(".wav"))
        return None

    def _bucle_sintesis(self) -> None:
        while True:
            generacion, texto = self._textos.get()
            ruta = None
            if generacion == self._generacion:
                ruta = self.sintetizar(texto)
            if ruta is None:
                self._terminado()
            else:
                self._audios.put((generacion, ruta))

    # ------------------------------------------------------------------
    # HILO 2: REPRODUCCIÓN (interrumpible)
    # ------------------------------------------------------------------
    def _mezclador(self):
        import pygame
        if not self._mezclador_listo:
            pygame.mixer.init()
            self._mezclador_listo = True
        return pygame

    def _bucle_audio(self) -> None:
        while True:
            generacion, ruta = self._audios.get()
            try:
                if generacion == self._generacion:
                    self._reproducir(generacion, ruta)
            except Exception as error:
                registro.error("No se pudo reproducir el audio: %s", error)
                self._mezclador_listo = False
            finally:
                self._borrar(ruta)
                self._terminado()

    def _reproducir(self, generacion: int, ruta: Path) -> None:
        pygame = self._mezclador()
        musica = pygame.mixer.music
        musica.load(str(ruta))
        musica.set_volume(self.volumen)
        musica.play()
        if not self.hablando:
            self.hablando = True
            self.bus.emitir("estado", valor="hablando")
            if self.al_empezar:
                try:
                    self.al_empezar()
                except Exception:
                    registro.exception("Error en el aviso de inicio de voz")
        try:
            while musica.get_busy():
                if generacion != self._generacion:
                    musica.stop()
                    registro.info("Voz interrumpida.")
                    break
                time.sleep(0.03)
        finally:
            musica.unload()

    def _terminado(self) -> None:
        with self._cerrojo:
            self._pendientes = max(0, self._pendientes - 1)
            libre = self._pendientes == 0
        if libre:
            estaba_hablando, self.hablando = self.hablando, False
            self.bus.emitir("estado", valor="reposo")
            if self.al_terminar:
                try:
                    self.al_terminar(estaba_hablando)
                except Exception:
                    registro.exception("Error en el aviso de fin de voz")

    @staticmethod
    def _borrar(ruta: Path) -> None:
        try:
            ruta.unlink()
        except OSError:
            pass
