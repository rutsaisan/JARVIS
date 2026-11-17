"""
Pruebas de la ventana nativa: composición por software por defecto, aceleración gráfica opcional
y vuelta automática a software si, con la GPU, la ventana no se cerró bien la vez anterior.
No abren ninguna ventana.

Ejecutar con:  pytest tests/test_window.py -v
"""
import os
import shutil
import tempfile
from types import SimpleNamespace

import pytest

from core.config import Ajustes, dir_datos
from ui import window

SOFTWARE = "--disable-gpu-compositing"


@pytest.fixture
def asistente(monkeypatch):
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_ventana_")
    monkeypatch.setenv("JARVIS_DATA_DIR", carpeta)
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "")      # se restaura al terminar cada prueba
    yield SimpleNamespace(ajustes=Ajustes(os.path.join(carpeta, "ajustes.json")))
    shutil.rmtree(carpeta, ignore_errors=True)


def opciones():
    return os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")


def test_por_defecto_compone_por_software_y_deja_la_marca(asistente):
    window._preparar_aceleracion(asistente)
    assert SOFTWARE in opciones()
    assert (dir_datos() / "ventana_qt.abierta").exists()


def test_con_la_aceleracion_activada_usa_la_gpu(asistente):
    asistente.ajustes.establecer("interfaz.aceleracion_grafica", True)
    window._preparar_aceleracion(asistente)
    assert SOFTWARE not in opciones()


def test_tras_un_cierre_limpio_sigue_con_la_gpu(asistente):
    asistente.ajustes.establecer("interfaz.aceleracion_grafica", True)
    window._preparar_aceleracion(asistente)
    window._quitar_marca_de_ventana()                          # lo que hace el cierre normal
    window._preparar_aceleracion(asistente)
    assert asistente.ajustes.obtener("interfaz.aceleracion_grafica") is True
    assert SOFTWARE not in opciones()


def test_si_la_ventana_se_cayo_con_gpu_vuelve_a_software_y_lo_recuerda(asistente):
    asistente.ajustes.establecer("interfaz.aceleracion_grafica", True)
    window._preparar_aceleracion(asistente)                    # la marca se queda: simula una caída
    window._preparar_aceleracion(asistente)
    assert asistente.ajustes.obtener("interfaz.aceleracion_grafica") is False
    assert SOFTWARE in opciones()
    # Queda guardado en ajustes.json: el siguiente arranque también irá por software.
    assert Ajustes(asistente.ajustes.ruta).obtener("interfaz.aceleracion_grafica") is False


def test_una_opcion_de_gpu_puesta_a_mano_no_se_toca(asistente, monkeypatch):
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    window._preparar_aceleracion(asistente)
    assert opciones() == "--disable-gpu"
