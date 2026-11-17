"""
Configuración común de las pruebas.

Las pruebas nunca tocan los datos reales ni el ordenador:
  - JARVIS_DATA_DIR      -> carpeta temporal (ajustes, base de datos, logs)
  - JARVIS_SIN_EMBEDDINGS -> la memoria usa solo la comparación léxica (sin Ollama)
  - JARVIS_SIMULAR       -> las herramientas del PC no ejecutan nada de verdad
"""
import os
import shutil
import sys
import tempfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

_DIR_TEMPORAL = tempfile.mkdtemp(prefix="jarvis_pruebas_")
os.environ["JARVIS_DATA_DIR"] = _DIR_TEMPORAL
os.environ["JARVIS_SIN_EMBEDDINGS"] = "1"
os.environ["JARVIS_SIMULAR"] = "1"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DIR_TEMPORAL, ignore_errors=True)
