"""
El reactor de JARVIS dibujado con QPainter
==========================================
Es el mismo reactor de anillos que hay en el centro de la interfaz (ui/web/index.html),
dibujado sin motor web: lo usan el asistente flotante (ui/overlay.py) y el icono de la
aplicación (ui/icono.py). Las medidas son las del SVG original, un lienzo de 400 × 400
con el centro en (200, 200); aquí el centro es (0, 0) y todo se escala al tamaño pedido.

Sin motor web es muy barato de animar: un fotograma de la ventana flotante (120 px) se
pinta en menos de un milisegundo, frente al coste de animar la interfaz web por software
(ver la nota de rendimiento en ui/window.py).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient

RADIO_EXTERIOR = 192              # el anillo más externo del SVG
NEON, CLARO = (180, 75, 255), (231, 204, 255)                 # --neon y --claro
ALERTA, ALERTA_CLARO = (255, 77, 141), (255, 196, 216)        # --alerta (estado de error)


def velocidades(estado: str) -> dict:
    """Grados por segundo de cada anillo, como en app.js (más deprisa al escuchar o pensar)."""
    return {
        "marcas": 2.6,
        "arcos": {"escuchando": 51, "procesando": 90, "hablando": 36}.get(estado, 18),
        "segmentos": -60 if estado == "procesando" else -9.5,
        "nodos": 12.9,
        "interior": -22.5,
        "barrido": 240 if estado == "procesando" else 0,
    }


class Angulos:
    """Ángulo actual de cada anillo."""

    def __init__(self, iniciales: dict | None = None):
        self.valores = {nombre: 0.0 for nombre in velocidades("reposo")}
        self.valores.update(iniciales or {})

    def avanzar(self, estado: str, segundos: float) -> None:
        for nombre, velocidad in velocidades(estado).items():
            self.valores[nombre] = (self.valores[nombre] + velocidad * segundos) % 360


def colores(estado: str) -> tuple:
    neon, claro = (ALERTA, ALERTA_CLARO) if estado == "error" else (NEON, CLARO)
    return neon, claro


def _color(rgb: tuple, opacidad: float = 1.0) -> QColor:
    color = QColor(*rgb)
    color.setAlphaF(max(0.0, min(1.0, opacidad)))
    return color


def _pluma(rgb: tuple, ancho: float, opacidad: float = 1.0, minimo: float = 0.0) -> QPen:
    pluma = QPen(_color(rgb, opacidad), max(ancho, minimo))
    pluma.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pluma


def _arcos(p: QPainter, radio: float, inicio: float, largo: float, paso: float, pluma: QPen) -> None:
    """Trazos de `largo` grados cada `paso` grados sobre una circunferencia (como stroke-dasharray).
    Los ángulos van en el sentido de las agujas del reloj desde las 3, igual que en el SVG."""
    p.setPen(pluma)
    p.setBrush(Qt.BrushStyle.NoBrush)
    caja = QRectF(-radio, -radio, 2 * radio, 2 * radio)
    angulo = inicio
    while angulo < inicio + 360 - 1e-6:
        p.drawArc(caja, int(round(-angulo * 16)), int(round(-largo * 16)))
        angulo += paso


def _circulo(p: QPainter, radio: float, pluma: QPen) -> None:
    p.setPen(pluma)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(0, 0), radio, radio)


def dibujar_reactor(p: QPainter, centro: QPointF, radio: float, angulos: dict | None = None,
                    estado: str = "reposo", pulso: float = 0.0, radio_halo: float = 0.0,
                    resaltado: bool = False, detalle: str = "completo", disco: bool = False) -> None:
    """Dibuja el reactor con el anillo exterior de `radio` píxeles alrededor de `centro`.

    estado     reposo / escuchando / procesando / hablando / error (cambia color y anillos)
    pulso      0..1: el núcleo y el halo laten con la voz
    radio_halo píxeles del resplandor de fondo (0 = sin halo)
    resaltado  el ratón está encima: los arcos se iluminan, como en la interfaz
    detalle    completo / medio / minimo: con pocos píxeles (iconos de 16-32 px) sobran los detalles
    disco      fondo oscuro detrás de los anillos, para que se vea también sobre fondos claros
    """
    angulos = angulos or {}
    escala = radio / RADIO_EXTERIOR
    minimo = 0.9 / escala                        # ningún trazo por debajo de ~1 píxel real
    neon, claro = colores(estado)
    blanco = (255, 255, 255)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(centro)

    if radio_halo > 0:
        r = radio_halo * (1 + pulso * 0.12)
        halo = QRadialGradient(QPointF(0, 0), r)
        halo.setColorAt(0.0, QColor(196, 110, 255, int(255 * (0.5 if estado != "error" else 0.4))))
        halo.setColorAt(0.5, QColor(138, 44, 255, 51))
        halo.setColorAt(1.0, QColor(138, 44, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(0, 0), r, r)

    p.scale(escala, escala)

    if disco:
        fondo = QRadialGradient(QPointF(0, 0), 206)
        fondo.setColorAt(0.0, QColor(34, 8, 66, 238))
        fondo.setColorAt(0.7, QColor(12, 3, 26, 238))
        fondo.setColorAt(1.0, QColor(5, 2, 11, 242))
        p.setBrush(QBrush(fondo))
        p.setPen(_pluma(neon, 2, 0.75, minimo))
        p.drawEllipse(QPointF(0, 0), 204, 204)

    def girar(nombre):
        p.save()
        p.rotate(angulos.get(nombre, 0.0))

    # Escuchando: un aro fino por fuera del reactor
    if estado == "escuchando":
        _circulo(p, 220, _pluma(claro, 1.2, 0.6, minimo))

    # Marcas exteriores
    if detalle == "completo":
        girar("marcas")
        _circulo(p, 192, _pluma(neon, 1, 0.55, minimo))
        _arcos(p, 183, 0, 0.7, 5, _pluma(neon, 7, 0.7))
        _arcos(p, 179, 0, 1.2, 30, _pluma(claro, 15, 0.95))
        p.restore()
    elif detalle == "medio":
        _circulo(p, 186, _pluma(neon, 2, 0.6, minimo))

    # Arcos principales (tres de 78°)
    girar("arcos")
    color_arco = blanco if (resaltado or estado == "escuchando") else claro
    _arcos(p, 160, 0, 78, 120, _pluma(neon, 17, 0.22))
    _arcos(p, 160, 0, 78, 120, _pluma(color_arco, 5 if detalle == "completo" else 9))
    p.restore()

    if detalle != "minimo":
        # Segmentos
        girar("segmentos")
        _arcos(p, 140, 0, 9, 12, _pluma(neon, 3, 0.85, minimo))
        p.restore()

    if detalle == "completo":
        # Nodos
        girar("nodos")
        _circulo(p, 120, _pluma(neon, 1, 0.55, minimo))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(claro))
        for x, y in ((0, -120), (103.9, 60), (-103.9, 60)):
            p.drawEllipse(QPointF(x, y), 4.5, 4.5)
        p.restore()

    # Barrido (pensando)
    if estado == "procesando":
        girar("barrido")
        cuna = QPainterPath(QPointF(0, 0))
        cuna.lineTo(0, -138)
        cuna.arcTo(QRectF(-138, -138, 276, 276), 90, -60)
        cuna.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(neon, 0.26))
        p.drawPath(cuna)
        p.restore()

    # Anillo fijo
    _circulo(p, 98, _pluma(neon, 13, 0.26))
    _circulo(p, 98, _pluma(claro, 2.4 if detalle == "completo" else 4, 1.0, minimo))

    if detalle == "completo":
        # Puntos interiores
        girar("interior")
        _arcos(p, 86, 0, 2, 8, _pluma(neon, 2, 0.75, minimo))
        p.restore()

    # Núcleo
    p.save()
    p.scale(1 + pulso * 0.16, 1 + pulso * 0.16)
    luz = QRadialGradient(QPointF(0, 0), 74)
    luz.setColorAt(0.0, QColor(255, 255, 255))
    luz.setColorAt(0.2, QColor(243, 224, 255))
    luz.setColorAt(0.48, _color(neon))
    luz.setColorAt(0.8, QColor(91, 22, 201, 191))
    luz.setColorAt(1.0, QColor(26, 3, 56, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(luz))
    p.drawEllipse(QPointF(0, 0), 74, 74)
    if detalle != "minimo":
        hexagono = QPolygonF([QPointF(x, y) for x, y in
                              ((0, -48), (41.6, -24), (41.6, 24), (0, 48), (-41.6, 24), (-41.6, -24))])
        p.setBrush(QColor(10, 2, 24, 77))
        p.setPen(_pluma(claro, 1.5, 0.9, minimo))
        p.drawPolygon(hexagono)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 242))
    p.drawEllipse(QPointF(0, 0), 20, 20)
    p.restore()

    p.restore()
