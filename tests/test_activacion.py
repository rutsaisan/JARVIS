"""
Pruebas del detector dedicado de la palabra de activación (voice/activacion.py) y de cómo cambia
la escucha con él: sin «Hey Jarvis» no se transcribe nada. Sin micrófono ni openWakeWord: el
modelo y el audio son de mentira.

Ejecutar con:  pytest tests/test_activacion.py -v
"""
import os
import tempfile

import numpy as np
import pytest

from core.config import Ajustes
from core.events import BusEventos
from voice import activacion
from voice.listener import BLOQUES_PARA_EMPEZAR, DURACION_BLOQUE, FRECUENCIA, Escucha

MUESTRAS = int(FRECUENCIA * DURACION_BLOQUE)


class ModeloFalso:
    """Da puntuación alta en el trozo número `en`."""

    def __init__(self, en=None):
        self.en, self.trozos = en, 0

    def predict(self, trozo):
        self.trozos += 1
        return {"hey_jarvis": 0.9 if self.trozos == self.en else 0.01}


class TranscriptorFalso:
    def __init__(self):
        self.llamadas = 0

    def transcribir(self, audio):
        self.llamadas += 1
        return "abre Spotify"


@pytest.fixture
def escucha():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_activacion_")
    ajustes = Ajustes(os.path.join(carpeta, "ajustes.json"))
    oido, eventos = [], []
    e = Escucha(ajustes, lambda texto, forzada, durante: oido.append((texto, forzada, durante)), eventos.append,
                BusEventos(), transcriptor=TranscriptorFalso())
    e.oido, e.eventos = oido, eventos
    return e


def frase(escucha, bloques_voz=20, bloques_silencio=40):
    """Una frase: voz fuerte y luego silencio hasta que se da por terminada."""
    voz = (np.sin(np.linspace(0, 400, MUESTRAS)) * 12000).astype(np.int16)
    silencio = np.zeros(MUESTRAS, dtype=np.int16)
    for bloque in [voz] * bloques_voz + [silencio] * bloques_silencio:
        escucha._procesar_bloque(bloque)


def test_detector_cuenta_trozos_de_80_ms():
    detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=3))
    assert not detector.procesar(np.zeros(2000, dtype=np.int16))        # 1 trozo (y sobran 720 muestras)
    assert detector.procesar(np.zeros(2000, dtype=np.int16))            # trozos 2 y 3: oído en el 3
    assert not detector.procesar(np.zeros(1280 * 5, dtype=np.int16))    # una sola vez


def test_sin_detector_se_transcribe_todo(escucha):
    frase(escucha)
    assert escucha.transcriptor.llamadas == 1 and escucha.oido == [("abre Spotify", False, False)]


def test_con_detector_lo_que_no_va_dirigido_a_jarvis_ni_se_transcribe(escucha):
    escucha.detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=None))
    frase(escucha)
    assert escucha.transcriptor.llamadas == 0 and escucha.oido == []
    assert "sin_texto" in escucha.eventos


def test_con_detector_tras_hey_jarvis_si(escucha):
    escucha.detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=1))
    escucha._vigilar_activacion(np.zeros(1280, dtype=np.int16))
    assert "activacion" in escucha.eventos and escucha.esperando_pulsacion
    frase(escucha)
    assert escucha.oido == [("abre Spotify", True, False)]                 # se atiende sin decir «Jarvis»


def hey_jarvis_y(escucha, bloques_despues):
    """«Hey Jarvis» con el detector disparando al final de la palabra, más voz detrás (o no)."""
    escucha.detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=1))
    voz = (np.sin(np.linspace(0, 400, MUESTRAS)) * 12000).astype(np.int16)
    silencio = np.zeros(MUESTRAS, dtype=np.int16)
    for _ in range(18):                                                  # «Hey Jarvis»: ~0,5 s
        escucha._procesar_bloque(voz)
    escucha._vigilar_activacion(np.zeros(1280, dtype=np.int16))         # el detector lo oye aquí
    for bloque in [voz] * bloques_despues + [silencio] * 40:
        escucha._procesar_bloque(bloque)


def test_solo_hey_jarvis_no_se_transcribe(escucha):
    hey_jarvis_y(escucha, bloques_despues=3)                             # solo la cola de «Jarvis»
    assert escucha.transcriptor.llamadas == 0
    assert [(t.lower(), f, d) for t, f, d in escucha.oido] == [("jarvis", True, False)]   # contesta «¿Sí?»


def test_hey_jarvis_y_la_orden_de_corrido(escucha):
    hey_jarvis_y(escucha, bloques_despues=25)                            # «..., abre Spotify»
    assert escucha.transcriptor.llamadas == 1
    assert escucha.oido == [("abre Spotify", True, False)]


def test_con_detector_mientras_esta_atento_tambien(escucha):
    escucha.detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=None))
    escucha.atento = lambda: True
    frase(escucha)
    assert escucha.oido == [("abre Spotify", False, False)]


def test_interrumpir_mientras_habla_sin_transcribir(escucha):
    escucha.detector = activacion.DetectorActivacion(modelo=ModeloFalso(en=1))
    escucha.eco(True)
    escucha._vigilar_activacion(np.zeros(1280, dtype=np.int16))
    for _ in range(80):
        escucha._procesar_eco(np.zeros(MUESTRAS, dtype=np.int16))
    assert [(t.lower(), f, d) for t, f, d in escucha.oido] == [("jarvis", False, True)]
    assert escucha.transcriptor.llamadas == 0


def test_sin_openwakeword_se_sigue_con_whisper(escucha, monkeypatch):
    monkeypatch.setattr(activacion, "disponible", lambda: False)
    escucha.ajustes.establecer("voz.motor_activacion", "openwakeword")
    assert activacion.crear(escucha.ajustes) is None
    escucha.ajustes.establecer("voz.motor_activacion", "whisper")
    assert activacion.crear(escucha.ajustes) is None


def test_un_detector_que_falla_vuelve_a_whisper(escucha):
    class Roto:
        def procesar(self, audio):
            raise RuntimeError("modelo roto")
    escucha.detector = Roto()
    escucha._vigilar_activacion(np.zeros(MUESTRAS, dtype=np.int16))
    assert escucha.detector is None


def test_cuantos_bloques_hacen_falta():
    assert BLOQUES_PARA_EMPEZAR * DURACION_BLOQUE < 0.2                  # la frase empieza enseguida
