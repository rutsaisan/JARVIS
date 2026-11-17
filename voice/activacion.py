"""
Palabra de activación dedicada («Hey Jarvis»), opcional
========================================================
Por defecto JARVIS transcribe con Whisper cada frase que oye y busca «Jarvis» en el texto.
Funciona, pero gasta procesador con todo lo que suena cerca del micrófono (la tele, otra
conversación...).

Con openWakeWord (un modelo pequeño y local que ya trae «hey jarvis» entrenado) la escucha
cambia: el modelo escucha continuamente y solo cuando oye «Hey Jarvis» se transcribe la
frase. También sirve para interrumpirle mientras habla sin transcribir nada.

Se activa en Ajustes → Escucha → «Detector de la palabra de activación». Necesita el paquete
openwakeword (pip install openwakeword) y sus modelos, que se descargan la primera vez.
Si no está instalado, JARVIS sigue como siempre.
"""
from __future__ import annotations

import logging
import time

import numpy as np

registro = logging.getLogger("jarvis.activacion")

MUESTRAS_POR_TROZO = 1280            # 80 ms a 16 kHz: lo que espera openWakeWord
# El modelo está entrenado con voces en inglés: con acento español «Hey Jarvis» puntúa más bajo
# (medido con las voces de Windows: con 0,5 se perdía 1 de cada 3; con 0,4, 1 de cada 5), y las
# frases normales en español no pasaron de 0,3. Por eso el umbral es algo menor que el habitual.
UMBRAL = 0.4
SEPARACION = 2.0                     # segundos mínimos entre dos detecciones


def disponible() -> bool:
    try:
        import openwakeword  # noqa: F401
        return True
    except ImportError:
        return False


class DetectorActivacion:
    """Recibe audio a 16 kHz (int16) por trozos y dice si acaba de oír la palabra de activación."""

    def __init__(self, modelo=None, umbral: float = UMBRAL):
        if modelo is None:
            from openwakeword.model import Model
            try:
                modelo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            except Exception:
                # Primera vez: faltan los modelos. Se descargan (unos pocos MB) y se vuelve a intentar.
                from openwakeword.utils import download_models
                download_models(model_names=["hey_jarvis"])
                modelo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.modelo = modelo
        self.umbral = umbral
        self._resto = np.zeros(0, dtype=np.int16)
        self._ultima = 0.0

    def procesar(self, audio: np.ndarray) -> bool:
        self._resto = np.concatenate([self._resto, np.asarray(audio, dtype=np.int16)])
        oido = False
        while len(self._resto) >= MUESTRAS_POR_TROZO:
            trozo, self._resto = self._resto[:MUESTRAS_POR_TROZO], self._resto[MUESTRAS_POR_TROZO:]
            puntuaciones = self.modelo.predict(trozo)
            if puntuaciones and max(puntuaciones.values()) >= self.umbral and time.time() - self._ultima > SEPARACION:
                self._ultima = time.time()
                oido = True
        return oido


def crear(ajustes) -> DetectorActivacion | None:
    """El detector si está elegido en Ajustes y se puede usar; si no, None (JARVIS usa Whisper)."""
    if ajustes.obtener("voz.motor_activacion", "whisper") != "openwakeword":
        return None
    if not disponible():
        registro.warning("openWakeWord no está instalado: se sigue usando Whisper para oír «Jarvis».")
        return None
    try:
        return DetectorActivacion()
    except Exception as error:
        registro.warning("No se pudo preparar openWakeWord (%s): se sigue usando Whisper.", error)
        return None
