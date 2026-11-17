"""Registro (logs) de JARVIS: consola + fichero rotativo en data/logs/jarvis.log."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import dir_datos

FORMATO = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configurar_registro(nivel_consola: int = logging.INFO, consola: bool = True) -> logging.Logger:
    """Prepara el registro «jarvis». Se puede llamar varias veces sin duplicar salidas."""
    registro = logging.getLogger("jarvis")
    if registro.handlers:
        return registro
    registro.setLevel(logging.DEBUG)
    registro.propagate = False

    try:
        carpeta = dir_datos() / "logs"
        carpeta.mkdir(parents=True, exist_ok=True)
        fichero = RotatingFileHandler(carpeta / "jarvis.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fichero.setLevel(logging.DEBUG)
        fichero.setFormatter(logging.Formatter(FORMATO, "%Y-%m-%d %H:%M:%S"))
        registro.addHandler(fichero)
    except OSError:
        pass

    # Con pythonw (sin consola) sys.stderr es None: en ese caso solo se escribe al fichero.
    if consola and sys.stderr is not None:
        try:
            sys.stderr.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        salida = logging.StreamHandler(sys.stderr)
        salida.setLevel(nivel_consola)
        salida.setFormatter(logging.Formatter(FORMATO, "%H:%M:%S"))
        registro.addHandler(salida)
    return registro
