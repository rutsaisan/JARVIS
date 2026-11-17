"""
Ventana de escritorio de JARVIS
================================
Muestra la interfaz (ui/web) como una aplicación de escritorio.

  1. Con PySide6 instalado: ventana nativa Qt con la interfaz dentro
     (QWebEngineView), barra de título oscura y sin navegación exterior.
  2. Sin PySide6: Microsoft Edge (o Chrome) en «modo aplicación», una ventana
     propia sin barra de direcciones ni pestañas. No hay que instalar nada.
  3. Si no hay ninguno: se abre en el navegador por defecto.

En los tres casos la interfaz es la misma y habla con el mismo núcleo.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time
import webbrowser

from core.config import dir_datos

registro = logging.getLogger("jarvis.ventana")

COLOR_FONDO = "#05030f"
TAMANO_MINIMO = (980, 640)


def abrir_ventana(asistente, servidor) -> int:
    """Abre la ventana y no vuelve hasta que se cierra. Devuelve el código de salida."""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401  (solo se comprueba que está disponible)
    except ImportError:
        registro.info("PySide6 no está instalado: se usa el navegador en modo aplicación.")
        return _ventana_navegador(asistente, servidor)
    return _ventana_qt(asistente, servidor)


# ==========================================
# 1) VENTANA NATIVA (PySide6)
# ==========================================
def _preparar_aceleracion(asistente) -> None:
    """Decide si el motor web usa la tarjeta gráfica (GPU) o compone por software.

    Por defecto compone por software. Con GPU las animaciones son más baratas, pero el motor
    web de Qt 6.12 se cae con ella en el portátil de desarrollo (infracción de acceso dentro de
    Qt6WebEngineCore.dll entre los 20 y los 60 s, también con el diseño actual), mientras que
    por software no se ha caído nunca. La GPU se puede activar en Ajustes («Aceleración
    gráfica»), con una red de seguridad: mientras la ventana está abierta existe la marca
    data/ventana_qt.abierta; si al arrancar sigue ahí, la vez anterior la ventana no se cerró
    bien y se vuelve sola a la composición por software.
    """
    marca = dir_datos() / "ventana_qt.abierta"
    if marca.exists() and asistente.ajustes.obtener("interfaz.aceleracion_grafica", False):
        registro.warning("La ventana no se cerró bien la última vez: se desactiva la aceleración gráfica.")
        asistente.ajustes.establecer("interfaz.aceleracion_grafica", False)
    opciones = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if not asistente.ajustes.obtener("interfaz.aceleracion_grafica", False) and "gpu" not in opciones:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{opciones} --disable-gpu-compositing".strip()
    try:
        marca.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _quitar_marca_de_ventana() -> None:
    try:
        (dir_datos() / "ventana_qt.abierta").unlink()
    except OSError:
        pass


def _identificar_aplicacion() -> None:
    """Con este identificador Windows agrupa la ventana con el acceso directo de JARVIS y le pone
    su icono en la barra de tareas, en vez del de Python (ver scripts/crear_acceso_directo.py)."""
    if os.name != "nt":
        return
    try:
        from ui.icono import ID_APLICACION
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(ID_APLICACION))
    except Exception:
        pass


def _ventana_qt(asistente, servidor) -> int:
    _preparar_aceleracion(asistente)
    _identificar_aplicacion()

    from PySide6.QtCore import QMetaObject, Qt, QUrl
    from PySide6.QtGui import QColor, QIcon
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    def apagar_y_salir(codigo: int = 0) -> None:
        """Apaga lo de JARVIS y termina el proceso en el acto.

        Con PySide6 6.12 en Windows, ocultar o destruir la vista de QtWebEngine
        (Chromium) provoca a veces, unos instantes después, una infracción de
        acceso dentro de Qt6WebEngineCore.dll (comprobado en el registro de errores
        de Windows), que además dejaba sin apagar el Ollama arrancado por JARVIS.

        Por eso la ventana no se oculta ni se destruye: con ella aún en pantalla se
        hace un apagado rápido de lo propio (voz y Ollama) y el proceso se termina
        de inmediato, sin pasar por la finalización de Qt. Es lo mismo que hace el
        propio Chromium al cerrarse. El servidor local y las conexiones mueren con
        el proceso; los datos ya están guardados en disco en cada operación.
        """
        try:
            registro.info("Cerrando JARVIS...")
            asistente.cerrar(rapido=True)
            registro.info("JARVIS cerrado.")
        except Exception:
            registro.exception("Error al cerrar JARVIS")
        finally:
            _quitar_marca_de_ventana()       # cierre limpio: la próxima vez puede seguir usando la GPU
            try:
                # Sin consola (pythonw) este vaciado puede fallar; nada debe impedir el paso siguiente.
                logging.shutdown()
            except BaseException:
                pass
            _terminar_proceso(codigo)

    class Pagina(QWebEnginePage):
        """La ventana solo muestra la interfaz local; los enlaces externos se abren en el navegador."""

        def acceptNavigationRequest(self, url, tipo, es_marco_principal):
            if url.toString().startswith(servidor.url):
                return True
            if url.scheme() in ("http", "https"):
                webbrowser.open(url.toString())
            return False

        def createWindow(self, tipo):
            return None

    class Ventana(QMainWindow):
        def closeEvent(self, evento):
            if not self.isMaximized():
                asistente.ajustes.actualizar({"interfaz": {"ancho": self.width(), "alto": self.height()}})
            apagar_y_salir(0)                # no vuelve: la ventana desaparece al terminar el proceso

    aplicacion = QApplication.instance() or QApplication(sys.argv[:1])
    aplicacion.setApplicationName("JARVIS")
    from ui.icono import RUTA_ICO
    if RUTA_ICO.exists():
        aplicacion.setWindowIcon(QIcon(str(RUTA_ICO)))          # el reactor, también en la barra de tareas

    ventana = Ventana()
    ventana.setWindowTitle("JARVIS")
    ventana.setMinimumSize(*TAMANO_MINIMO)
    # El tamaño guardado nunca supera la pantalla disponible, y la ventana nace centrada.
    pantalla = aplicacion.primaryScreen().availableGeometry()
    ancho = max(TAMANO_MINIMO[0], min(int(asistente.ajustes.obtener("interfaz.ancho", 1320)), pantalla.width() - 40))
    alto = max(TAMANO_MINIMO[1], min(int(asistente.ajustes.obtener("interfaz.alto", 820)), pantalla.height() - 70))
    ventana.resize(ancho, alto)
    ventana.move(pantalla.x() + max(0, (pantalla.width() - ancho) // 2),
                 pantalla.y() + max(0, (pantalla.height() - alto - 30) // 2))
    ventana.setStyleSheet(f"background: {COLOR_FONDO};")

    vista = QWebEngineView(ventana)
    pagina = Pagina(vista)
    pagina.setBackgroundColor(QColor(COLOR_FONDO))
    vista.setPage(pagina)
    vista.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    ventana.setCentralWidget(vista)
    vista.load(QUrl(servidor.url_de_entrada))

    # «Jarvis, apágate» llega desde otro hilo: se pide cerrar la ventana de forma segura para Qt.
    asistente.al_cerrar = lambda: QMetaObject.invokeMethod(ventana, "close", Qt.ConnectionType.QueuedConnection)

    ventana.show()
    _barra_de_titulo_oscura(int(ventana.winId()))

    # El reactor flotante, siempre encima (se guarda en una variable para que no se destruya).
    from ui.overlay import crear_asistente_flotante
    flotante = crear_asistente_flotante(asistente, ventana)  # noqa: F841
    codigo = aplicacion.exec()
    apagar_y_salir(codigo)          # por si la aplicación Qt termina sin pasar por closeEvent
    return codigo


def _terminar_proceso(codigo: int = 0) -> None:
    """Termina este proceso en el acto, sin ejecutar las rutinas de salida de las bibliotecas."""
    if os.name == "nt":
        try:
            from ctypes import wintypes
            nucleo = ctypes.windll.kernel32
            nucleo.GetCurrentProcess.restype = wintypes.HANDLE
            nucleo.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            nucleo.TerminateProcess(nucleo.GetCurrentProcess(), codigo)
        except Exception:
            pass
    os._exit(codigo)


def _barra_de_titulo_oscura(hwnd: int) -> None:
    """Pinta la barra de título de Windows a juego con la interfaz."""
    if os.name != "nt":
        return
    try:
        dwm = ctypes.windll.dwmapi
        activado = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(activado), ctypes.sizeof(activado))      # modo oscuro
        color = ctypes.c_int(0x00200608)                                                         # #080620 (BGR)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))           # color de la barra
    except Exception:
        pass


# ==========================================
# 2) NAVEGADOR EN MODO APLICACIÓN (sin instalar nada)
# ==========================================
def _buscar_navegador() -> str | None:
    candidatos = []
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidatos.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
            candidatos.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for candidato in candidatos:
        if os.path.exists(candidato):
            return candidato
    return shutil.which("msedge") or shutil.which("chrome")


def _ventana_navegador(asistente, servidor) -> int:
    navegador = _buscar_navegador()
    if not navegador:
        return _pestana_del_navegador(asistente, servidor)

    # Perfil propio: la ventana es un proceso independiente (se puede esperar a que
    # se cierre) y no se mezcla con el historial ni las extensiones del navegador.
    perfil = dir_datos() / "ventana"
    ancho = int(asistente.ajustes.obtener("interfaz.ancho", 1320))
    alto = int(asistente.ajustes.obtener("interfaz.alto", 820))
    proceso = subprocess.Popen([
        navegador, f"--app={servidor.url_de_entrada}", f"--user-data-dir={perfil}",
        f"--window-size={ancho},{alto}", "--no-first-run", "--no-default-browser-check",
    ])
    try:
        from tools.computer import registrar_pid_propio
        registrar_pid_propio(proceso.pid)        # para no cerrar ni escribir en la propia ventana de JARVIS
    except Exception:
        pass

    def cerrar():
        try:
            proceso.terminate()
        except OSError:
            pass

    asistente.al_cerrar = cerrar
    try:
        proceso.wait()
    except KeyboardInterrupt:
        cerrar()
    servidor.detener()
    return 0


# ==========================================
# 3) ÚLTIMO RECURSO: pestaña del navegador por defecto
# ==========================================
def _pestana_del_navegador(asistente, servidor) -> int:
    terminar = {"ya": False}
    asistente.al_cerrar = lambda: terminar.update(ya=True)
    webbrowser.open(servidor.url_de_entrada)
    print(f"JARVIS está abierto en el navegador: {servidor.url_de_entrada}")
    print("Pulsa Ctrl+C aquí para cerrarlo.")
    try:
        while not terminar["ya"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    servidor.detener()
    return 0
