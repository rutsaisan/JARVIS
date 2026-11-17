"""
Pruebas de la instancia única: no puede haber dos JARVIS abiertos con la misma carpeta de
datos (oirían el mismo micrófono y ejecutarían cada orden dos veces).

Ejecutar con:  pytest tests/test_instancia.py -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

import pytest

import jarvis

pytestmark = pytest.mark.skipif(os.name != "nt", reason="el cerrojo de instancia es de Windows")

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def carpeta(monkeypatch):
    ruta = tempfile.mkdtemp(prefix="jarvis_test_instancia_")
    monkeypatch.setenv("JARVIS_DATA_DIR", ruta)
    monkeypatch.setattr(jarvis, "_CERROJO_INSTANCIA", None)
    yield ruta
    shutil.rmtree(ruta, ignore_errors=True)


def otro_proceso_puede_abrir(carpeta_datos: str) -> bool:
    """Pregunta a un proceso aparte si podría abrir JARVIS con esa carpeta de datos."""
    codigo = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {RAIZ!r})
        os.environ["JARVIS_DATA_DIR"] = {carpeta_datos!r}
        import jarvis
        print(jarvis.instancia_unica())
    """)
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
    return salida.stdout.strip() == "True"


def test_el_primero_abre_y_un_segundo_proceso_no(carpeta):
    assert jarvis.instancia_unica() is True
    assert otro_proceso_puede_abrir(carpeta) is False


def test_otra_carpeta_de_datos_no_choca(carpeta):
    assert jarvis.instancia_unica() is True
    otra = tempfile.mkdtemp(prefix="jarvis_test_instancia_otra_")
    try:
        assert otro_proceso_puede_abrir(otra) is True       # p. ej. una demostración con --datos
    finally:
        shutil.rmtree(otra, ignore_errors=True)


def test_al_cerrarse_el_primero_se_libera(carpeta):
    # Un proceso aparte reserva el puesto y termina: después se puede abrir otro.
    assert otro_proceso_puede_abrir(carpeta) is True
    assert otro_proceso_puede_abrir(carpeta) is True
