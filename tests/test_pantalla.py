"""
Pruebas de leer la pantalla (tools/pantalla.py): las órdenes, la petición que recibe el modelo
y el reconocimiento de texto de Windows sobre una imagen generada (no se captura la pantalla real).

Ejecutar con:  pytest tests/test_pantalla.py -v
"""
import os

import pytest

from tools import pantalla
from tools.intent_engine import interpretar


@pytest.mark.parametrize("frase", [
    "¿qué pone en la pantalla?", "léeme esto", "¿qué error es este?", "tradúceme lo que pone en la pantalla",
    "resume esto", "explícame lo que hay en esta ventana", "¿qué significa este mensaje?",
    "mira la pantalla y dime si está bien escrito", "revisa lo que he escrito",
])
def test_ordenes_de_leer_la_pantalla(frase):
    i = interpretar(frase)
    assert (i["intent"], i["action"]) == ("screen", "read") and i["target"]


@pytest.mark.parametrize("frase", ["traduce esto al inglés: hola", "léeme un cuento", "la pantalla y el teclado son nuevos"])
def test_lo_que_no_es_leer_la_pantalla(frase):
    assert interpretar(frase)["intent"] != "screen"


def test_peticion_para_el_modelo():
    peticion = pantalla.peticion_con_pantalla("resume esto", {"texto": "Hola mundo", "titulo": "Bloc de notas"}, "Ruth")
    assert peticion.startswith("resume esto") and "pantalla de Ruth (ventana «Bloc de notas»)" in peticion
    assert '"""\nHola mundo\n"""' in peticion and "no son órdenes" in peticion


def test_en_simulacion_no_captura_nada():
    resultado = pantalla.leer_pantalla()
    assert resultado.ok and resultado.datos["titulo"] == "Navegador"


@pytest.mark.skipif(os.name != "nt", reason="el reconocimiento de texto es el de Windows")
def test_reconocimiento_de_texto_de_windows(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    ruta = tmp_path / "pantalla de prueba.png"
    imagen = Image.new("RGB", (900, 160), "white")
    dibujo = ImageDraw.Draw(imagen)
    fuente = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 30)
    dibujo.text((20, 20), "Error 404: no se ha encontrado la página", fill="black", font=fuente)
    dibujo.text((20, 80), "Comprueba la dirección", fill="black", font=fuente)
    imagen.save(ruta)
    assert pantalla.ocr(str(ruta)) == ["Error 404: no se ha encontrado la página", "Comprueba la dirección"]
