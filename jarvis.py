"""
J.A.R.V.I.S. — asistente personal local
========================================
Punto de entrada.

    python jarvis.py                 abre la aplicación de escritorio
    python jarvis.py --consola       modo consola (texto), como la versión original
    python jarvis.py --consola --voz modo consola escuchando por el micrófono
    python jarvis.py --servidor      solo la interfaz web local (para desarrollo)

Opciones útiles:
    --simular    modo demostración: nada se ejecuta de verdad en el PC
    --datos DIR  usar otra carpeta de datos en lugar de ./data
    --depurar    más detalle en la consola
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time


_CERROJO_INSTANCIA = None        # se mantiene abierto mientras viva el proceso


def instancia_unica() -> bool:
    """Reserva el puesto de «JARVIS abierto» para esta carpeta de datos.

    Devuelve False si ya hay otro JARVIS en marcha con los mismos datos. Dos a la vez
    escucharían el mismo micrófono, responderían dos veces a cada orden y, al cerrarse
    uno, apagaría el Ollama que usa el otro (pasó: un doble clic en el acceso directo).
    Se usa un mutex con nombre de Windows, que el sistema libera solo al terminar el proceso.
    """
    global _CERROJO_INSTANCIA
    if os.name != "nt":
        return True
    import ctypes
    import hashlib
    from core.config import dir_datos

    carpeta = str(dir_datos().resolve()).lower()
    nombre = "Local\\JARVIS-" + hashlib.sha1(carpeta.encode("utf-8")).hexdigest()[:16]
    nucleo = ctypes.WinDLL("kernel32", use_last_error=True)
    nucleo.CreateMutexW.restype = ctypes.c_void_p
    nucleo.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    cerrojo = nucleo.CreateMutexW(None, False, nombre)
    ya_abierto = ctypes.get_last_error() == 183          # ERROR_ALREADY_EXISTS
    if cerrojo and not ya_abierto:
        _CERROJO_INSTANCIA = cerrojo
    return not ya_abierto


def mostrar_ventana_abierta() -> None:
    """Trae al frente la ventana del JARVIS que ya está abierto."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    usuario = ctypes.windll.user32
    usuario.FindWindowW.restype = wintypes.HWND
    usuario.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    usuario.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    usuario.SetForegroundWindow.argtypes = [wintypes.HWND]
    ventana = usuario.FindWindowW(None, "JARVIS")
    if ventana:
        usuario.ShowWindow(ventana, 9)                   # SW_RESTORE: por si estaba minimizada
        usuario.SetForegroundWindow(ventana)


def _consola(asistente, con_voz: bool) -> int:
    """Conversación por terminal. Con --voz también escucha el micrófono."""
    from core.events import bus

    def mostrar(evento: dict) -> None:
        datos = evento["datos"]
        if evento["tipo"] == "mensaje" and datos["rol"] == "asistente":
            print(f"\nJARVIS: {datos['texto']}")
        elif evento["tipo"] == "mensaje" and datos.get("origen") == "voz":
            print(f"\n(oído) {datos['texto']}")

    bus.escuchar(mostrar)
    asistente.ajustes.establecer("modo", "voz" if con_voz else "texto", guardar=False)
    asistente.iniciar()
    terminar = {"ya": False}
    asistente.al_cerrar = lambda: terminar.update(ya=True)

    print("J.A.R.V.I.S. — modo consola. Escribe «salir» para terminar.")
    if con_voz:
        print("Micrófono activo: di «Jarvis» seguido de tu orden. También puedes escribir.")
    try:
        while not terminar["ya"]:
            try:
                texto = input("\n> ").strip()
            except EOFError:
                break
            if texto.lower() in ("salir", "exit", "quit"):
                break
            if texto:
                asistente.orquestador.procesar(texto)
                if asistente.habla:
                    asistente.habla.esperar(60)
    except KeyboardInterrupt:
        print()
    finally:
        asistente.cerrar()
    return 0


def main(argumentos=None) -> int:
    analizador = argparse.ArgumentParser(description="J.A.R.V.I.S. — asistente personal local")
    analizador.add_argument("--consola", action="store_true", help="modo consola en lugar de la ventana")
    analizador.add_argument("--voz", action="store_true", help="en modo consola, escuchar por el micrófono")
    analizador.add_argument("--servidor", action="store_true", help="solo la interfaz web local, sin ventana")
    analizador.add_argument("--puerto", type=int, default=0, help="puerto local de la interfaz (0 = automático)")
    analizador.add_argument("--testigo", default="", help=argparse.SUPPRESS)
    analizador.add_argument("--simular", action="store_true",
                            help="modo demostración: las acciones sobre el PC se dan por hechas sin ejecutarse")
    analizador.add_argument("--datos", default="", help="carpeta de datos alternativa (por defecto, ./data)")
    analizador.add_argument("--depurar", action="store_true", help="mostrar más detalle en la consola")
    opciones = analizador.parse_args(argumentos)

    # Estas dos opciones se leen al importar los módulos, así que van antes que nada.
    if opciones.simular:
        os.environ["JARVIS_SIMULAR"] = "1"
    if opciones.datos:
        os.environ["JARVIS_DATA_DIR"] = os.path.abspath(opciones.datos)

    from core.log import configurar_registro
    configurar_registro(logging.DEBUG if opciones.depurar else logging.INFO if opciones.consola or opciones.servidor
                        else logging.WARNING, consola=not opciones.consola or opciones.depurar)

    if not instancia_unica():
        # Ya hay un JARVIS abierto con estos datos: dos a la vez oirían y ejecutarían cada orden dos veces.
        logging.getLogger("jarvis").info("JARVIS ya estaba abierto: se trae su ventana al frente.")
        print("JARVIS ya está abierto.")
        mostrar_ventana_abierta()
        return 0

    from core.assistant import Asistente
    asistente = Asistente()

    if opciones.consola:
        return _consola(asistente, opciones.voz)

    from ui.server import Servidor
    servidor = Servidor(asistente, puerto=opciones.puerto, testigo=opciones.testigo)
    servidor.iniciar()
    asistente.iniciar()

    if opciones.servidor:
        print(f"JARVIS escuchando en {servidor.url_de_entrada}")
        print("Pulsa Ctrl+C para terminar.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            asistente.cerrar()
        return 0

    from ui.window import abrir_ventana
    try:
        return abrir_ventana(asistente, servidor)
    finally:
        asistente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
