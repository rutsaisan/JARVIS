"""
Pruebas del estado del equipo y el brillo (tools/sistema.py), con datos de ejemplo.

Ejecutar con:  pytest tests/test_sistema.py -v
"""
import pytest

from tools import sistema
from tools.intent_engine import interpretar

DATOS = {"cpu": 20, "ram": 91, "ram_total_gb": 15.3, "disco": "C:", "disco_libre_gb": 271, "disco_uso": 40,
         "encendido_horas": 5.0, "bateria": {"porcentaje": 100, "enchufado": True, "segundos_restantes": None},
         "gpu": {"nombre": "RTX 4060", "uso": 46, "memoria_usada_mb": 614, "memoria_total_mb": 8188, "temperatura": 60}}


def test_frase_del_estado_completo():
    assert sistema.frase_estado(DATOS) == (
        "El procesador va al 20 %; la memoria está al 91 % (de 15,3 GB); en el disco C: te quedan 271 GB libres; "
        "la gráfica va al 46 %, con 0,6 de 8 GB de memoria y 60 °C. Te queda un 100 % de batería, y está cargada.")


def test_frases_de_la_bateria():
    assert sistema.frase_bateria(dict(DATOS, bateria=None)).startswith("Este ordenador no tiene batería")
    cargando = dict(DATOS, bateria={"porcentaje": 40, "enchufado": True, "segundos_restantes": None})
    assert sistema.frase_bateria(cargando) == "Te queda un 40 % de batería, y se está cargando."
    sin_cable = dict(DATOS, bateria={"porcentaje": 40, "enchufado": False, "segundos_restantes": 5400})
    assert sistema.frase_bateria(sin_cable) == "Te queda un 40 % de batería: unas 1 h y 30 min de uso."


def test_sin_grafica_nvidia():
    assert sistema.frase_estado(dict(DATOS, gpu=None), "gpu").startswith("No encuentro ninguna gráfica")


def test_brillo_objetivo():
    assert sistema.objetivo_de_brillo("+", 95) == 100 and sistema.objetivo_de_brillo("-", 5) == 0
    assert sistema.objetivo_de_brillo("40%", 0) == 40 and sistema.objetivo_de_brillo("max", 0) == 100
    assert sistema.objetivo_de_brillo("+", None) == 60                     # sin poder leerlo: desde 50
    assert sistema.objetivo_de_brillo("mucho", 50) is None


def test_poner_brillo_en_simulacion():
    resultado = sistema.poner_brillo(140)
    assert resultado.ok and resultado.datos == {"brillo": 100}


@pytest.mark.parametrize("frase,accion,objetivo", [
    ("¿cuánta batería me queda?", "battery", ""), ("¿está cargando el portátil?", "battery", ""),
    ("¿cómo va el ordenador?", "status", ""), ("¿cuánta memoria estoy usando?", "status", "ram"),
    ("¿qué uso de CPU tengo?", "status", "cpu"), ("¿cuánto espacio libre me queda?", "status", "disco"),
    ("¿cómo va la gráfica?", "status", "gpu"), ("sube el brillo", "brightness", "+"),
    ("baja un poco el brillo", "brightness", "-"), ("pon el brillo al 40", "brightness", "40"),
    ("brillo al máximo", "brightness", "max"), ("¿qué brillo tengo?", "brightness", "?"),
])
def test_ordenes_del_equipo(frase, accion, objetivo):
    i = interpretar(frase)
    assert (i["intent"], i["action"], i["target"]) == ("system_info", accion, objetivo)


@pytest.mark.parametrize("frase", ["el brillo de sus ojos", "¿qué recuerdas de mí?", "sube el volumen"])
def test_lo_que_no_es_del_equipo(frase):
    assert interpretar(frase)["intent"] != "system_info"
