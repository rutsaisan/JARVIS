"""
Asistente flotante de JARVIS
============================
El reactor del centro de la interfaz en pequeño, siempre por encima de las demás
ventanas, para hablar con JARVIS sin tener que buscar su ventana:

    clic          hablar: escucha una frase sin tener que decir «Jarvis». Si está
                  hablando o pensando, la detiene.
    Ctrl+Alt+J    lo mismo desde cualquier programa
    arrastrar     moverlo (recuerda dónde lo dejas)
    clic derecho  menú: hablar, escribir, detener, abrir la ventana, ocultarlo, salir

Cuando la ventana principal no está delante, lo que dice JARVIS y lo que ha oído aparecen
en un bocadillo junto al reactor. Se oculta o se vuelve a mostrar en Ajustes («Asistente
flotante»).

Va dibujado con QPainter (ui/reactor.py) y no con el motor web: está siempre a la vista, y
animar la interfaz web por software cuesta mucho procesador (ver ui/window.py).
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import threading
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget

from ui.reactor import Angulos, dibujar_reactor

registro = logging.getLogger("jarvis.flotante")

LADO = 124                      # lado de la ventana flotante, en píxeles
RADIO_REACTOR = 44              # radio del anillo exterior del reactor
MARGEN_PANTALLA = 24
UMBRAL_ARRASTRE = 6             # píxeles que hay que mover el ratón para que sea arrastrar y no un clic
FPS_REPOSO, FPS_ACTIVO = 6, 30          # medido: ~3 % de un núcleo girando, 6-9 % escuchando o hablando
QUIETO_TRAS = 20                         # segundos sin actividad tras los que deja de girar (y de gastar)
ESTADOS_ACTIVOS = ("escuchando", "procesando", "hablando")
ESTADOS_OCUPADO = ("procesando", "hablando")
ATAJO = "Ctrl+Alt+J"
MAXIMO_BOCADILLO = 240          # caracteres que se enseñan de una respuesta larga
ANCHO_BOCADILLO = 300
TE_ESCUCHO = "Te escucho…"

ESTILO_MENU = """
QMenu { background: #0b0418; color: #f1e6ff; border: 1px solid rgba(208, 136, 255, .78);
        padding: 6px 0; font: 10pt "Segoe UI"; }
QMenu::item { padding: 6px 22px 6px 16px; }
QMenu::item:selected { background: rgba(180, 75, 255, .28); }
QMenu::item:disabled { color: #7a639d; }
QMenu::separator { height: 1px; background: rgba(180, 75, 255, .26); margin: 5px 10px; }
"""
ESTILO_ENTRADA = """
QLineEdit { background: rgba(3, 1, 8, .85); color: #f1e6ff; border: 1px solid rgba(190, 100, 255, .45);
            padding: 6px 8px; font: 10pt "Segoe UI"; selection-background-color: #8a2cff; }
QLineEdit:focus { border-color: #b44bff; }
"""


# ==========================================
# CÁLCULOS (sin pantalla: se prueban solos)
# ==========================================
def posicion_por_defecto(zona: QRect, lado: int = LADO) -> QPoint:
    """Abajo a la derecha de la pantalla, por encima de la barra de tareas."""
    return QPoint(zona.right() + 1 - lado - MARGEN_PANTALLA, zona.bottom() + 1 - lado - MARGEN_PANTALLA)


def leer_posicion(texto: str, zonas: list, lado: int = LADO) -> QPoint | None:
    """La posición guardada («x,y»), solo si sigue cayendo dentro de alguna pantalla
    (si se desconecta un monitor, el reactor no se queda fuera de la vista)."""
    try:
        x, y = (int(float(parte)) for parte in str(texto).split(","))
    except ValueError:
        return None
    centro = QPoint(x + lado // 2, y + lado // 2)
    return QPoint(x, y) if any(zona.contains(centro) for zona in zonas) else None


def resumir(texto: str, maximo: int = MAXIMO_BOCADILLO) -> str:
    """Una sola línea de texto, cortada por una palabra si es muy larga."""
    texto = " ".join(str(texto or "").replace("**", "").split())
    if len(texto) <= maximo:
        return texto
    corte = texto.rfind(" ", 0, maximo - 1)
    return texto[:corte if corte > maximo // 2 else maximo - 1].rstrip(" ,.;:") + "…"


def segundos_visible(texto: str) -> float:
    """Lo justo para leerlo: unos 4 segundos y un poco más por palabra, como mucho 14."""
    return min(14.0, 4.0 + 0.32 * len(str(texto).split()))


def accion_al_pulsar(estado: str) -> str:
    """Pulsar el reactor habla con JARVIS; si está hablando o pensando, la para."""
    return "detener" if estado in ESTADOS_OCUPADO else "escuchar"


def pulso_objetivo(estado: str, nivel: float, segundos: float) -> float:
    """Cuánto late el núcleo (como en la interfaz): con tu voz al escuchar, con la suya al hablar."""
    if estado == "escuchando":
        return max(0.4, min(1.0, nivel * 1.4))
    if estado == "hablando":
        return 0.6 + 0.36 * abs(math.sin(segundos * 6.3) * math.sin(segundos * 2.1))
    return 0.0


# ==========================================
# ATAJO DE TECLADO GLOBAL
# ==========================================
class AtajoGlobal(threading.Thread):
    """Ctrl+Alt+J desde cualquier programa (RegisterHotKey de Windows, en su propio hilo)."""

    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, al_pulsar, tecla: str = "J"):
        super().__init__(name="jarvis-atajo", daemon=True)
        self.al_pulsar = al_pulsar
        self.tecla = ord(tecla.upper())
        self.listo = threading.Event()
        self.registrado = False
        self._hilo = 0

    def run(self) -> None:
        from ctypes import wintypes
        usuario = ctypes.windll.user32
        self._hilo = ctypes.windll.kernel32.GetCurrentThreadId()
        self.registrado = bool(usuario.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                                                       self.tecla))
        self.listo.set()
        if not self.registrado:
            registro.warning("El atajo %s ya lo usa otro programa; el reactor flotante funciona igual con el clic.",
                             ATAJO)
            return
        mensaje = wintypes.MSG()
        try:
            while usuario.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
                if mensaje.message == self.WM_HOTKEY:
                    try:
                        self.al_pulsar()
                    except Exception:
                        registro.exception("Fallo al atender el atajo de teclado")
        finally:
            usuario.UnregisterHotKey(None, 1)

    def parar(self) -> None:
        if self._hilo:
            ctypes.windll.user32.PostThreadMessageW(self._hilo, self.WM_QUIT, 0, 0)


class _Puente(QObject):
    """Lleva al hilo de la interfaz lo que llega de otros hilos (eventos, ajustes, atajo)."""
    evento = Signal(dict)
    ajustes = Signal(list)
    atajo = Signal()


# ==========================================
# BOCADILLO
# ==========================================
class Bocadillo(QWidget):
    """Junto al reactor: lo que dice JARVIS, lo que ha oído, o un campo para escribirle."""

    def __init__(self, flotante: "AsistenteFlotante"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("JARVIS · bocadillo")
        self.flotante = flotante
        self.escribiendo = False

        diseno = QVBoxLayout(self)
        diseno.setContentsMargins(18, 12, 14, 12)
        diseno.setSpacing(8)
        self.texto = QLabel()
        self.texto.setTextFormat(Qt.TextFormat.PlainText)
        self.texto.setWordWrap(True)
        self.texto.setMaximumWidth(ANCHO_BOCADILLO)
        self.texto.setStyleSheet("color: #f1e6ff; font: 10pt 'Segoe UI'; background: transparent;")
        self.entrada = QLineEdit()
        self.entrada.setPlaceholderText("Escribe y pulsa Intro…")
        self.entrada.setMaxLength(2000)
        self.entrada.setMinimumWidth(240)
        self.entrada.setStyleSheet(ESTILO_ENTRADA)
        self.entrada.returnPressed.connect(self._enviar)
        self.entrada.hide()
        diseno.addWidget(self.texto)
        diseno.addWidget(self.entrada)

        self.reloj = QTimer(self)
        self.reloj.setSingleShot(True)
        self.reloj.timeout.connect(self.cerrar)

    def mostrar(self, texto: str, segundos: float | None = None) -> None:
        if self.escribiendo:
            return                                   # no se le quita el campo mientras escribe
        self.texto.setText(texto)
        self.entrada.hide()
        self._ensenar()
        self.reloj.start(int(1000 * (segundos or segundos_visible(texto))))

    def pedir_texto(self, aviso: str = "¿Qué quieres decirme?") -> None:
        self.escribiendo = True
        self.texto.setText(aviso)
        self.entrada.clear()
        self.entrada.show()
        self._ensenar()
        self.activateWindow()
        self.entrada.setFocus()
        self.reloj.start(60_000)

    def cerrar(self) -> None:
        self.escribiendo = False
        self.reloj.stop()
        self.entrada.hide()
        self.hide()

    def _enviar(self) -> None:
        texto = self.entrada.text().strip()
        self.escribiendo = False
        self.entrada.clear()
        if not texto:
            self.cerrar()
            return
        self.flotante.asistente.enviar(texto)
        self.mostrar(f"«{resumir(texto, 120)}»", 3)

    def _ensenar(self) -> None:
        self.adjustSize()
        self.colocar()
        self.show()
        self.raise_()

    def colocar(self) -> None:
        """A la izquierda del reactor si está en la mitad derecha de la pantalla; si no, a la derecha."""
        reactor = self.flotante.geometry()
        pantalla = QGuiApplication.screenAt(reactor.center()) or QGuiApplication.primaryScreen()
        zona = pantalla.availableGeometry()
        if reactor.center().x() > zona.center().x():
            x = reactor.left() + 8 - self.width()
        else:
            x = reactor.right() - 8
        y = reactor.center().y() - self.height() // 2
        x = max(zona.left() + 4, min(x, zona.right() - self.width() - 4))
        y = max(zona.top() + 4, min(y, zona.bottom() - self.height() - 4))
        self.move(x, y)

    def keyPressEvent(self, evento) -> None:
        if evento.key() == Qt.Key.Key_Escape:
            self.cerrar()
        else:
            super().keyPressEvent(evento)

    def paintEvent(self, evento) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        caja = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        forma = QPainterPath()
        forma.addRoundedRect(caja, 10, 10)
        p.fillPath(forma, QColor(11, 4, 24, 242))
        p.setPen(QPen(QColor(208, 136, 255, 190), 1))
        p.drawPath(forma)
        p.fillRect(QRectF(caja.left() + 1, caja.top() + 10, 3, caja.height() - 20), QColor(180, 75, 255))
        p.end()


# ==========================================
# EL REACTOR FLOTANTE
# ==========================================
class AsistenteFlotante(QWidget):
    def __init__(self, asistente, ventana_principal=None, con_atajo: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Distinto de «JARVIS»: es el título que busca un segundo arranque para traer la ventana principal.
        self.setWindowTitle("JARVIS · asistente flotante")
        self.setFixedSize(LADO, LADO)
        # Solo el círculo recibe el ratón: alrededor se puede seguir haciendo clic en lo de debajo.
        self.setMask(QRegion(0, 0, LADO, LADO, QRegion.RegionType.Ellipse))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Pulsa para hablar ({ATAJO})\nArrástrame para moverme · clic derecho: menú")

        self.asistente = asistente
        self.ventana = ventana_principal
        self.estado = getattr(asistente, "estado_actual", "reposo") or "reposo"
        self.nivel = 0.0
        self.pulso = 0.0
        self.encima = False
        self.angulos = Angulos()
        self._arrastre = None                 # (posición del ratón, posición de la ventana) al pulsar
        self._arrastrando = False
        self._ultimo = time.monotonic()
        self._actividad = time.monotonic()   # última vez que pasó algo (para quedarse quieto en reposo)
        self.bocadillo = Bocadillo(self)

        self.puente = _Puente()
        self.puente.evento.connect(self._al_evento)
        self.puente.ajustes.connect(self._al_cambiar_ajustes)
        self.puente.atajo.connect(self.pulsar)
        asistente.bus.escuchar(self._desde_otro_hilo)
        asistente.ajustes.al_cambiar(self._ajustes_desde_otro_hilo)

        self.reloj = QTimer(self)
        self.reloj.timeout.connect(self._fotograma)
        self._ajustar_reloj()

        self.atajo = None
        if con_atajo and os.name == "nt":
            self.atajo = AtajoGlobal(self.puente.atajo.emit)
            self.atajo.start()
        self.move(self._posicion_inicial())

    # ---------- lo que llega del núcleo (en otros hilos) ----------
    def _desde_otro_hilo(self, evento: dict) -> None:
        if evento.get("tipo") in ("estado", "mensaje", "nivel", "aprendido"):
            self.puente.evento.emit(evento)

    def _ajustes_desde_otro_hilo(self, claves) -> None:
        try:
            self.puente.ajustes.emit(list(claves))
        except RuntimeError:
            pass                                  # el reactor flotante ya se ha cerrado

    def _al_evento(self, evento: dict) -> None:
        tipo, datos = evento.get("tipo"), evento.get("datos") or {}
        if tipo != "nivel":
            self.despertar()
        if tipo == "nivel":
            self.nivel = float(datos.get("valor") or 0)
        elif tipo == "estado":
            anterior, self.estado = self.estado, datos.get("valor") or "reposo"
            self._ajustar_reloj()
            if self.estado == "escuchando" and anterior != "escuchando" and self.ventana_detras():
                self.bocadillo.mostrar(TE_ESCUCHO, 10)
            elif (anterior == "escuchando" and self.estado == "reposo" and not self.bocadillo.escribiendo
                  and self.bocadillo.texto.text() == TE_ESCUCHO):
                self.bocadillo.cerrar()
            self.update()
        elif tipo == "mensaje" and self.ventana_detras():
            texto = resumir(datos.get("texto", ""))
            if texto and datos.get("rol") == "asistente":
                self.bocadillo.mostrar(texto)
            elif texto and datos.get("origen") == "voz":
                self.bocadillo.mostrar(f"«{texto}»", 4)
        elif tipo == "aprendido" and self.ventana_detras() and not self.bocadillo.isVisible():
            textos = datos.get("textos") or []
            if textos:
                self.bocadillo.mostrar(f"He aprendido algo de ti: «{resumir(textos[0], 120)}»", 6)

    def _al_cambiar_ajustes(self, claves: list) -> None:
        if "interfaz.overlay" in claves:
            self.aplicar_ajuste()

    def ventana_detras(self) -> bool:
        """¿La ventana principal no está delante? Entonces el bocadillo cuenta lo que pasa."""
        ventana = self.ventana
        return ventana is None or ventana.isMinimized() or not ventana.isVisible() or not ventana.isActiveWindow()

    # ---------- acciones ----------
    def pulsar(self) -> None:
        if accion_al_pulsar(self.estado) == "detener":
            self.asistente.detener()
            return
        if self.estado == "escuchando":
            return
        if not self.asistente.pulsar_microfono():
            self.bocadillo.pedir_texto("No tengo micrófono disponible. Escríbeme:")

    def escribir(self) -> None:
        self.bocadillo.pedir_texto()

    def abrir_ventana(self) -> None:
        ventana = self.ventana
        if ventana is None:
            return
        if ventana.isMinimized():
            ventana.showNormal()
        ventana.raise_()
        ventana.activateWindow()

    def ocultar_por_ajuste(self) -> None:
        self.asistente.ajustes.establecer("interfaz.overlay", False)
        self.aplicar_ajuste()

    def aplicar_ajuste(self) -> None:
        self.setVisible(bool(self.asistente.ajustes.obtener("interfaz.overlay", True)))

    def menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(ESTILO_MENU)

        def opcion(texto, funcion, activa=True):
            accion = menu.addAction(texto)
            accion.triggered.connect(funcion)
            accion.setEnabled(activa)
            return accion

        opcion(f"Hablar\t{ATAJO}", self.pulsar)
        opcion("Escribir…", self.escribir)
        opcion("Detener", self.asistente.detener, self.estado in ESTADOS_OCUPADO)
        menu.addSeparator()
        if self.ventana is not None:
            opcion("Abrir JARVIS", self.abrir_ventana)
        opcion("Ocultar el asistente flotante", self.ocultar_por_ajuste)
        if self.ventana is not None:
            menu.addSeparator()
            opcion("Salir de JARVIS", self.ventana.close)
        return menu

    def cerrar(self) -> None:
        if self.atajo:
            self.atajo.parar()
        try:
            self.asistente.bus.dejar_de_escuchar(self._desde_otro_hilo)
        except Exception:
            pass
        self.bocadillo.cerrar()
        self.reloj.stop()
        self.hide()

    # ---------- posición ----------
    @staticmethod
    def _zonas() -> list:
        return [pantalla.availableGeometry() for pantalla in QGuiApplication.screens()]

    def _posicion_inicial(self) -> QPoint:
        guardada = leer_posicion(self.asistente.ajustes.obtener("interfaz.posicion_overlay", ""), self._zonas())
        return guardada or posicion_por_defecto(QGuiApplication.primaryScreen().availableGeometry())

    def _guardar_posicion(self) -> None:
        try:
            self.asistente.ajustes.establecer("interfaz.posicion_overlay", f"{self.x()},{self.y()}")
        except ValueError:
            pass

    # ---------- dibujo y animación ----------
    def despertar(self) -> None:
        """Ha pasado algo: vuelve a girar (en reposo se queda quieto al rato para no gastar)."""
        self._actividad = time.monotonic()
        self._ajustar_reloj()

    def _ajustar_reloj(self) -> None:
        self.reloj.setInterval(int(1000 / (FPS_ACTIVO if self.estado in ESTADOS_ACTIVOS else FPS_REPOSO)))
        if self.isVisible() and not self.reloj.isActive():
            self._ultimo = time.monotonic()
            self.reloj.start()

    def quieto(self) -> bool:
        return (self.estado not in ESTADOS_ACTIVOS and not self.encima and self.pulso < 0.01
                and time.monotonic() - self._actividad > QUIETO_TRAS)

    def _fotograma(self) -> None:
        if self.quieto():
            self.reloj.stop()                     # se queda como está hasta que pase algo
            return
        ahora = time.monotonic()
        transcurrido, self._ultimo = min(0.25, ahora - self._ultimo), ahora
        self.angulos.avanzar(self.estado, transcurrido)
        self.pulso += (pulso_objetivo(self.estado, self.nivel, ahora) - self.pulso) * 0.6
        self.update()

    def paintEvent(self, evento) -> None:
        p = QPainter(self)
        dibujar_reactor(p, QPointF(LADO / 2, LADO / 2), RADIO_REACTOR, self.angulos.valores, self.estado,
                        self.pulso, radio_halo=LADO / 2, resaltado=self.encima, disco=True)
        p.end()

    def showEvent(self, evento) -> None:
        super().showEvent(evento)
        self.despertar()

    def hideEvent(self, evento) -> None:
        self.reloj.stop()                       # oculto no gasta nada
        self.bocadillo.cerrar()
        super().hideEvent(evento)

    # ---------- ratón ----------
    def mousePressEvent(self, evento) -> None:
        self.despertar()
        if evento.button() == Qt.MouseButton.LeftButton:
            self._arrastre = (evento.globalPosition().toPoint(), self.pos())
            self._arrastrando = False
        evento.accept()

    def mouseMoveEvent(self, evento) -> None:
        if not self._arrastre or not (evento.buttons() & Qt.MouseButton.LeftButton):
            return
        raton, origen = self._arrastre
        desplazamiento = evento.globalPosition().toPoint() - raton
        if self._arrastrando or desplazamiento.manhattanLength() > UMBRAL_ARRASTRE:
            self._arrastrando = True
            self.move(origen + desplazamiento)
            if self.bocadillo.isVisible():
                self.bocadillo.colocar()

    def mouseReleaseEvent(self, evento) -> None:
        if evento.button() != Qt.MouseButton.LeftButton:
            return
        if self._arrastrando:
            self._guardar_posicion()
        elif self._arrastre:
            self.pulsar()
        self._arrastre = None
        self._arrastrando = False

    def enterEvent(self, evento) -> None:
        self.encima = True
        self.despertar()
        self.update()

    def leaveEvent(self, evento) -> None:
        self.encima = False
        self.update()

    def contextMenuEvent(self, evento) -> None:
        self.menu().exec(evento.globalPos())


def crear_asistente_flotante(asistente, ventana_principal=None):
    """Crea el reactor flotante y lo muestra si está activado en Ajustes. Si algo falla,
    JARVIS se abre igual: el reactor flotante es un extra, nunca un requisito."""
    try:
        flotante = AsistenteFlotante(asistente, ventana_principal)
        flotante.aplicar_ajuste()
        return flotante
    except Exception:
        registro.exception("No se pudo crear el asistente flotante")
        return None
