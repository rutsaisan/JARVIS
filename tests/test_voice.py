"""
Pruebas de la voz que no necesitan micrófono ni altavoces: palabra de
activación, detección de voz (con audio sintético), limpieza del texto para
leer y política de escucha del asistente.

Ejecutar con:  pytest tests/test_voice.py -v
"""
import os
import shutil
import tempfile
import time

import numpy as np
import pytest

from core.config import Ajustes
from core.events import BusEventos
from voice.listener import DURACION_BLOQUE, FRECUENCIA, Escucha, Transcriptor, separar_activacion
from voice.speaker import limpiar_para_voz


# ==========================================
# PALABRA DE ACTIVACIÓN
# ==========================================
@pytest.mark.parametrize("texto,resto", [
    ("Jarvis, abre Spotify.", "abre Spotify."),
    ("jarvis abre spotify", "abre spotify"),
    ("Oye Jarvis, ¿qué hora es?", "¿qué hora es?"),
    ("¡Jarvis! Para.", "Para."),
    ("Abre Spotify, Jarvis.", "Abre Spotify"),
    ("Yarvis, pon música", "pon música"),            # errores típicos de transcripción
    ("Jarbis sube el volumen", "sube el volumen"),
    ("Y Arvis, abre el blog de notas.", "abre el blog de notas."),      # así lo escribe Whisper sin pista
    ("Oye ya Arbis, busca noticias de tecnología.", "busca noticias de tecnología."),
    ("Y Jarvis, recuerda que me gusta el té.", "recuerda que me gusta el té."),
    ("Jarvis", ""),
    ("Jarvis.", ""),
    # «Hey Jarvis» con acento español, tal como lo escribió Whisper en las pruebas
    ("Ay Jarvis.", ""),
    ("Ay Jarvis, a Spotify.", "a Spotify."),
    ("¡Ella Arvis! ¡Qué hora es!", "¡Qué hora es!"),
    ("Hay Jarvis, pon música", "pon música"),
    ("Hey Jarvis, abre Spotify.", "abre Spotify."),
])
def test_detecta_la_activacion(texto, resto):
    assert separar_activacion(texto) == (True, resto)


@pytest.mark.parametrize("texto", [
    "qué buen día hace hoy", "voy a ver una película de Marvel", "mi amigo Javi viene luego",
    "pásame el jarabe", "ya es tarde y hace frío", "", "ella es mi hermana", "hay que comprar pan",
])
def test_no_confunde_otras_palabras(texto):
    assert separar_activacion(texto) == (False, texto)


def test_palabra_de_activacion_personalizada():
    assert separar_activacion("Viernes, abre Spotify", "viernes") == (True, "abre Spotify")
    assert separar_activacion("Jarvis, abre Spotify", "viernes") == (False, "Jarvis, abre Spotify")


# ==========================================
# TEXTO PARA LEER EN VOZ ALTA
# ==========================================
@pytest.mark.parametrize("entrada,salida", [
    ("**Hola**, _Ruth_.", "Hola, Ruth."),
    ("Mira https://ejemplo.es/pagina?x=1 cuando puedas", "Mira un enlace cuando puedas"),
    ("1. Ruta del Turia\n2. La Albufera", "Ruta del Turia. La Albufera"),
    ("- uno\n- dos", "uno. dos"),
    ("Todo listo 😀👍", "Todo listo"),
    ("••••••", ""),
    ("   ", ""),
    ("Primera frase.\nSegunda frase.", "Primera frase. Segunda frase."),
])
def test_limpiar_para_voz(entrada, salida):
    assert limpiar_para_voz(entrada) == salida


# ==========================================
# DETECCIÓN DE VOZ CON AUDIO SINTÉTICO
# ==========================================
class TranscriptorFalso:
    def __init__(self, texto="Jarvis, abre Spotify"):
        self.texto = texto
        self.audios = []

    def transcribir(self, audio):
        self.audios.append(audio)
        return self.texto

    def preparar(self):
        return True


def _bloques(segundos, amplitud):
    """Bloques de 30 ms de un tono (amplitud > 0) o de ruido de fondo muy bajo."""
    n = int(FRECUENCIA * DURACION_BLOQUE)
    t = np.arange(n) / FRECUENCIA
    onda = (np.sin(2 * np.pi * 220 * t) * amplitud * 32767).astype(np.int16)
    return [onda.copy() for _ in range(int(segundos / DURACION_BLOQUE))]


@pytest.fixture
def escucha():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_voz_")
    oido, eventos = [], []
    instancia = Escucha(Ajustes(os.path.join(carpeta, "ajustes.json")),
                        al_oir=lambda texto, forzada, durante: oido.append((texto, forzada, durante)),
                        al_evento=eventos.append, bus=BusEventos(), transcriptor=TranscriptorFalso())
    instancia.oido, instancia.eventos = oido, eventos
    yield instancia
    shutil.rmtree(carpeta, ignore_errors=True)


def _alimentar(escucha, bloques):
    for bloque in bloques:
        escucha._procesar_bloque(bloque)


def test_una_frase_se_entrega_al_terminar_el_silencio(escucha):
    _alimentar(escucha, _bloques(0.5, 0.0005))           # ruido de fondo
    _alimentar(escucha, _bloques(1.2, 0.2))              # alguien habla
    assert escucha.oido == []                            # todavía no ha terminado la frase
    _alimentar(escucha, _bloques(1.0, 0.0005))           # silencio: fin de frase
    assert escucha.oido == [("Jarvis, abre Spotify", False, False)]
    assert escucha.eventos[:2] == ["inicio_voz", "fin_voz"]
    duracion = len(escucha.transcriptor.audios[0]) / FRECUENCIA
    assert 1.5 < duracion < 3.0                          # la frase, con su margen previo y final


def test_el_silencio_no_dispara_nada(escucha):
    _alimentar(escucha, _bloques(3.0, 0.0005))
    assert escucha.oido == [] and escucha.transcriptor.audios == []


def test_un_golpe_corto_se_descarta(escucha):
    _alimentar(escucha, _bloques(0.5, 0.0005))
    _alimentar(escucha, _bloques(0.15, 0.3))             # un clic o un golpe en la mesa
    _alimentar(escucha, _bloques(1.2, 0.0005))
    assert escucha.transcriptor.audios == []             # ni siquiera se transcribe
    assert "sin_texto" in escucha.eventos


def test_una_frase_demasiado_larga_se_corta(escucha):
    _alimentar(escucha, _bloques(16, 0.2))               # ruido continuo tomado por voz
    assert len(escucha.transcriptor.audios) == 1
    assert len(escucha.transcriptor.audios[0]) / FRECUENCIA <= 14.5


def test_pulsar_el_microfono_marca_la_frase_como_forzada(escucha):
    escucha._forzada_hasta = time.time() + 5             # equivale a escuchar_una_vez() sin abrir el micrófono
    _alimentar(escucha, _bloques(0.3, 0.0005) + _bloques(1.0, 0.2) + _bloques(1.0, 0.0005))
    assert escucha.oido == [("Jarvis, abre Spotify", True, False)]
    assert escucha._forzada_hasta == 0.0


def test_mientras_jarvis_habla_solo_se_revisa_el_audio_reciente(escucha):
    escucha.eco(True)
    for bloque in _bloques(2.2, 0.2):
        escucha._procesar_eco(bloque)
    assert escucha.oido and all(durante for _, _, durante in escucha.oido)      # marcado como «durante_habla»
    escucha.eco(False)
    assert escucha._grabando is False and len(escucha._recientes) == 0          # nada se arrastra al terminar


def test_transcripcion_vacia_no_se_entrega(escucha):
    escucha.transcriptor.texto = ""
    _alimentar(escucha, _bloques(0.3, 0.0005) + _bloques(1.0, 0.2) + _bloques(1.0, 0.0005))
    assert escucha.oido == [] and escucha.eventos[-1] == "sin_texto"


# ==========================================
# FILTRO DE ALUCINACIONES DE WHISPER
# ==========================================
@pytest.mark.parametrize("texto,esperado", [
    ("Subtítulos realizados por la comunidad de Amara.org", ""),
    ("¡Gracias por ver el vídeo!", ""),
    ("  Jarvis,   abre  Spotify. ", "Jarvis, abre Spotify."),
    ("Jarvis.", "Jarvis."),
    ("...", ""),
])
def test_filtro_de_alucinaciones(texto, esperado):
    assert Transcriptor._filtrar(texto) == esperado


# ==========================================
# POLÍTICA DE ESCUCHA DEL ASISTENTE
# ==========================================
@pytest.fixture
def asistente(monkeypatch):
    from core.assistant import Asistente
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_asis_")
    monkeypatch.setenv("JARVIS_DATA_DIR", carpeta)
    instancia = Asistente(bus=BusEventos(), con_voz=False)
    instancia.enviadas = []
    instancia.enviar = lambda texto, origen="texto": instancia.enviadas.append(texto) or True
    yield instancia
    shutil.rmtree(carpeta, ignore_errors=True)


def test_en_reposo_solo_atiende_a_su_nombre(asistente):
    asistente._al_oir("abre Spotify")
    assert asistente.enviadas == []
    asistente._al_oir("Jarvis, abre Spotify")
    assert asistente.enviadas == ["abre Spotify"]


def test_solo_el_nombre_abre_la_ventana_de_escucha(asistente):
    asistente._al_oir("Jarvis")
    assert asistente.enviadas == []
    asistente._al_oir("abre Spotify")                    # ya no hace falta repetir «Jarvis»
    assert asistente.enviadas == ["abre Spotify"]


def test_la_ventana_de_fluidez_caduca(asistente):
    asistente._al_oir("Jarvis")
    asistente._ventana_hasta = time.time() - 1
    asistente._al_oir("abre Spotify")
    assert asistente.enviadas == []


def test_con_confirmacion_pendiente_cualquier_frase_es_respuesta(asistente):
    asistente.orquestador.pendiente = {"tipo": "accion", "expira": time.time() + 30}
    asistente._al_oir("confirmo")
    assert asistente.enviadas == ["confirmo"]


def test_pulsacion_del_microfono_no_necesita_nombre(asistente):
    asistente._al_oir("qué hora es", forzada=True)
    assert asistente.enviadas == ["qué hora es"]


def test_hey_jarvis_y_una_pausa_no_se_manda_al_modelo(asistente):
    asistente._al_oir("Hey Jarvis.", forzada=True)       # el detector lo oyó y la frase quedó en eso
    assert asistente.enviadas == [] and asistente._ventana_hasta > time.time()
    asistente._al_oir("abre Spotify")                    # la orden, en la frase siguiente
    assert asistente.enviadas == ["abre Spotify"]
    asistente._al_oir("Hey Jarvis, ¿qué hora es?", forzada=True)
    assert asistente.enviadas[-1] == "¿qué hora es?"


def test_mientras_habla_ignora_todo_salvo_su_nombre(asistente):
    asistente._al_oir("y entonces le dije que abre Spotify", durante_habla=True)
    assert asistente.enviadas == [] and asistente._ventana_hasta == 0.0
    asistente._al_oir("Jarvis espera", durante_habla=True)
    assert asistente.enviadas == []                      # lo oído no se ejecuta: lleva su propia voz mezclada
    assert asistente._ventana_hasta > time.time()        # pero se calla y queda a la escucha
