"""
Crea el acceso directo de JARVIS en el escritorio y en el menú Inicio, con el icono del reactor.

    python scripts/crear_acceso_directo.py

El acceso directo abre JARVIS sin consola (pythonw) y lleva el mismo identificador de
aplicación que la ventana: así, si lo anclas a la barra de tareas, la ventana abierta y el
icono anclado son uno solo, con el icono de JARVIS y no el de Python.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from ui.icono import ID_APLICACION, RUTA_ICO  # noqa: E402

NOMBRE = "JARVIS"
DESCRIPCION = "J.A.R.V.I.S. — asistente personal"


def ejecutable_sin_consola() -> str:
    """pythonw.exe junto al Python que ejecuta esto (sin ventana de consola al abrir JARVIS)."""
    junto = Path(sys.executable).with_name("pythonw.exe")
    if junto.exists():
        return str(junto)
    return shutil.which("pythonw") or sys.executable


def carpetas_destino() -> list:
    """El escritorio y la carpeta de programas del menú Inicio de la usuaria."""
    from win32com.shell import shell, shellcon
    return [Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)),
            Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0))]


def datos_del_acceso(carpeta: Path) -> dict:
    return {
        "ruta": Path(carpeta) / f"{NOMBRE}.lnk",
        "objetivo": ejecutable_sin_consola(),
        "argumentos": f'"{RAIZ / "jarvis.py"}"',
        "carpeta_trabajo": str(RAIZ),
        "icono": str(RUTA_ICO),
        "descripcion": DESCRIPCION,
    }


def crear_acceso(datos: dict) -> Path:
    from win32com.client import Dispatch

    acceso = Dispatch("WScript.Shell").CreateShortcut(str(datos["ruta"]))
    acceso.TargetPath = datos["objetivo"]
    acceso.Arguments = datos["argumentos"]
    acceso.WorkingDirectory = datos["carpeta_trabajo"]
    acceso.IconLocation = f'{datos["icono"]},0'
    acceso.Description = datos["descripcion"]
    acceso.WindowStyle = 1
    acceso.Save()
    poner_id_de_aplicacion(datos["ruta"])
    return Path(datos["ruta"])


def poner_id_de_aplicacion(ruta: Path) -> None:
    """Guarda en el acceso directo el identificador de aplicación de JARVIS (ver ui/icono.py)."""
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shellcon

    almacen = propsys.SHGetPropertyStoreFromParsingName(str(ruta), None, shellcon.GPS_READWRITE,
                                                         propsys.IID_IPropertyStore)
    almacen.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(ID_APLICACION, pythoncom.VT_LPWSTR))
    almacen.Commit()


def leer_acceso(ruta: Path) -> dict:
    """Lo que tiene guardado un acceso directo (para comprobarlo)."""
    from win32com.client import Dispatch
    from win32com.propsys import propsys, pscon

    acceso = Dispatch("WScript.Shell").CreateShortcut(str(ruta))
    almacen = propsys.SHGetPropertyStoreFromParsingName(str(ruta))
    return {"objetivo": acceso.TargetPath, "argumentos": acceso.Arguments, "carpeta_trabajo": acceso.WorkingDirectory,
            "icono": acceso.IconLocation, "id_aplicacion": almacen.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()}


def main() -> int:
    if os.name != "nt":
        print("Los accesos directos de este script son de Windows.")
        return 1
    if not RUTA_ICO.exists():
        from ui.icono import generar
        generar()
    for carpeta in carpetas_destino():
        ruta = crear_acceso(datos_del_acceso(carpeta))
        print(f"Acceso directo creado: {ruta}")
    print("Para tenerlo en la barra de tareas: clic derecho sobre él → «Anclar a la barra de tareas».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
