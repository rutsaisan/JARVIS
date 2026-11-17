"""
Icono de JARVIS
===============
El icono de la aplicación es el reactor del centro de la interfaz (el que se pulsa para
hablar) sobre un disco oscuro. Se dibuja con el mismo código que el asistente flotante
(ui/reactor.py) y se guarda en ui/icono/:

    jarvis.ico   para el acceso directo y la barra de tareas (16, 24, 32, 48, 64, 128 y 256 px)
    jarvis.png   para la ventana (256 px)

Para volver a generarlo:  python scripts/crear_icono.py
"""
from __future__ import annotations

import struct
from pathlib import Path

CARPETA = Path(__file__).resolve().parent / "icono"
RUTA_ICO = CARPETA / "jarvis.ico"
RUTA_PNG = CARPETA / "jarvis.png"
TAMANOS = (16, 24, 32, 48, 64, 128, 256)
# Identificador de la aplicación para Windows: agrupa en la barra de tareas la ventana con el
# acceso directo anclado y hace que se vea el icono de JARVIS y no el de Python.
ID_APLICACION = "ProyectoJARVIS.Asistente"


def detalle_para(tamano: int) -> str:
    """Con pocos píxeles los anillos finos se emborronan: se dibuja menos."""
    return "completo" if tamano >= 64 else "medio" if tamano >= 32 else "minimo"


def imagen_icono(tamano: int):
    """QImage cuadrada con el icono. Necesita una QGuiApplication creada."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient

    from ui.reactor import dibujar_reactor

    imagen = QImage(tamano, tamano, QImage.Format.Format_ARGB32_Premultiplied)
    imagen.fill(Qt.GlobalColor.transparent)
    p = QPainter(imagen)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    centro = QPointF(tamano / 2, tamano / 2)
    borde = max(1.0, tamano / 64)
    radio = tamano / 2 - borde / 2

    fondo = QRadialGradient(centro, radio)
    fondo.setColorAt(0.0, QColor(34, 8, 66))
    fondo.setColorAt(0.65, QColor(12, 3, 26))
    fondo.setColorAt(1.0, QColor(5, 2, 11))
    p.setBrush(fondo)
    p.setPen(QPen(QColor(180, 75, 255, 200), borde))
    p.drawEllipse(centro, radio, radio)

    detalle = detalle_para(tamano)
    proporcion = {"completo": 0.84, "medio": 0.9, "minimo": 0.96}[detalle]
    # Giro elegido para que los tres arcos queden equilibrados en el icono.
    dibujar_reactor(p, centro, radio * proporcion, {"arcos": -51, "segmentos": 4, "nodos": 0},
                    detalle=detalle)
    p.end()
    return imagen


def _png(imagen) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    datos = QByteArray()
    buffer = QBuffer(datos)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    imagen.save(buffer, "PNG")
    buffer.close()
    return bytes(datos.data())


def guardar_ico(ruta: Path, imagenes: list) -> None:
    """Escribe un .ico con varias imágenes PNG dentro (el formato de Windows Vista y posteriores)."""
    pngs = [(imagen.width(), imagen.height(), _png(imagen)) for imagen in imagenes]
    cabecera = struct.pack("<HHH", 0, 1, len(pngs))
    desplazamiento = 6 + 16 * len(pngs)
    entradas, cuerpo = b"", b""
    for ancho, alto, datos in pngs:
        entradas += struct.pack("<BBBBHHII", ancho % 256, alto % 256, 0, 0, 1, 32, len(datos), desplazamiento)
        cuerpo += datos
        desplazamiento += len(datos)
    Path(ruta).write_bytes(cabecera + entradas + cuerpo)


def leer_ico(ruta: Path) -> list:
    """Tamaños que contiene un .ico, como [(16, 16), (24, 24)...] (para comprobarlo)."""
    datos = Path(ruta).read_bytes()
    _, tipo, cantidad = struct.unpack_from("<HHH", datos, 0)
    if tipo != 1:
        raise ValueError("no es un icono")
    tamanos = []
    for i in range(cantidad):
        ancho, alto = struct.unpack_from("<BB", datos, 6 + 16 * i)
        tamanos.append((ancho or 256, alto or 256))
    return tamanos


def generar(carpeta: Path = CARPETA) -> tuple:
    """Genera jarvis.ico y jarvis.png. Devuelve sus rutas."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _aplicacion = QGuiApplication.instance() or QGuiApplication([])     # noqa: F841 (la necesita QPainter)
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    imagenes = [imagen_icono(tamano) for tamano in TAMANOS]
    guardar_ico(carpeta / RUTA_ICO.name, imagenes)
    imagenes[-1].save(str(carpeta / RUTA_PNG.name), "PNG")
    return carpeta / RUTA_ICO.name, carpeta / RUTA_PNG.name
