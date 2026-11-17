"""
Pruebas de buscar y abrir archivos (tools/archivos.py) en carpetas de prueba.
No se busca en las carpetas reales ni se abre nada (JARVIS_SIMULAR=1).

Ejecutar con:  pytest tests/test_archivos.py -v
"""
import os
import time

import pytest

from tools import archivos
from tools.intent_engine import interpretar


@pytest.fixture
def carpetas(tmp_path):
    documentos, descargas = tmp_path / "Documents", tmp_path / "Downloads"
    (documentos / "Universidad" / "TFG").mkdir(parents=True)
    descargas.mkdir()
    for ruta in ("Universidad/TFG/TFG final.pdf", "Universidad/TFG/TFG borrador.docx", "Apuntes de historia.docx",
                 "playa.txt"):
        (documentos / ruta).write_text("x", encoding="utf-8")
    (documentos / "instalar TFG.exe").write_text("x", encoding="utf-8")
    (documentos / ".oculta").mkdir()
    (documentos / ".oculta" / "TFG secreto.pdf").write_text("x", encoding="utf-8")
    viejo, nuevo = descargas / "antiguo.zip", descargas / "factura.pdf"
    viejo.write_text("x", encoding="utf-8")
    nuevo.write_text("x", encoding="utf-8")
    (descargas / "a medias.crdownload").write_text("x", encoding="utf-8")
    os.utime(viejo, (time.time() - 3600, time.time() - 3600))
    return documentos, descargas


def test_interpretar_lo_que_se_busca():
    assert archivos.interpretar_busqueda("el PDF del TFG") == (["tfg"], "pdf")
    assert archivos.interpretar_busqueda("la foto de la playa") == (["playa"], "foto")
    assert archivos.interpretar_busqueda("la hoja de cálculo de gastos") == (["gastos"], "excel")
    assert archivos.interpretar_busqueda("mis apuntes de historia") == (["apuntes", "historia"], "")


def test_buscar_en_carpetas(carpetas):
    documentos, _ = carpetas
    nombres = [d["nombre"] for d in archivos.buscar("el PDF del TFG", carpetas=[documentos])]
    assert nombres == ["TFG final.pdf"]                              # ni la carpeta oculta ni otros tipos
    todos = [d["nombre"] for d in archivos.buscar("el archivo del TFG", carpetas=[documentos])]
    assert sorted(todos) == ["TFG borrador.docx", "TFG final.pdf", "instalar TFG.exe"]
    assert archivos.buscar("apuntes de historia", carpetas=[documentos])[0]["carpeta"] == "Documents"
    assert archivos.buscar("el", carpetas=[documentos]) == []                            # nada que buscar


def test_ultimo_descargado(carpetas):
    _, descargas = carpetas
    assert [d["nombre"] for d in archivos.ultimos_descargados(2, descargas)] == ["factura.pdf", "antiguo.zip"]


def test_los_programas_no_se_abren(carpetas):
    documentos, _ = carpetas
    resultado = archivos.abrir(str(documentos / "instalar TFG.exe"))
    assert not resultado.ok and "programa o un script" in resultado.mensaje
    assert not archivos.se_puede_abrir("acceso.lnk") and archivos.se_puede_abrir("foto.JPG")
    assert archivos.abrir(str(documentos / "playa.txt")).ok                          # en simulación
    assert "ya no está" in archivos.abrir(str(documentos / "no existe.pdf")).mensaje


def test_ruta_real_del_indice_de_windows():
    assert archivos._ruta_de_url("file:C:/Users/Ruth/Documents/Mi%20TFG.pdf") == \
        os.path.normpath("C:/Users/Ruth/Documents/Mi TFG.pdf")
    assert archivos._ruta_de_url("file:///C:/a/b.txt") == os.path.normpath("C:/a/b.txt")
    assert archivos._ruta_de_url("https://ejemplo.es") == ""


@pytest.mark.parametrize("frase,accion,objetivo", [
    ("abre el PDF del TFG", "open", "PDF del TFG"), ("busca la foto de la playa", "search", "foto de la playa"),
    ("¿dónde está el documento de inglés?", "search", "documento de inglés"),
    ("abre lo último que he descargado", "open_recent", ""), ("abre el segundo", "open_index", "2"),
    ("abre el resultado 3", "open_index", "3"), ("abre mis apuntes de historia", "open", "apuntes de historia"),
])
def test_ordenes_de_archivos(frase, accion, objetivo):
    i = interpretar(frase)
    assert (i["intent"], i["action"], i["target"]) == ("files", accion, objetivo)


@pytest.mark.parametrize("frase,intencion", [("abre el Excel", "pc"), ("abre Word", "pc"),
                                             ("busca recetas de pasta", "search_web"), ("abre Spotify", "pc")])
def test_lo_que_no_son_archivos(frase, intencion):
    assert interpretar(frase)["intent"] == intencion
