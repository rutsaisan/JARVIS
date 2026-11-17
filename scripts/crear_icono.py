"""
Genera el icono de JARVIS (ui/icono/jarvis.ico y jarvis.png) a partir del reactor.

    python scripts/crear_icono.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.icono import generar  # noqa: E402

if __name__ == "__main__":
    for ruta in generar():
        print(f"Creado: {ruta}")
