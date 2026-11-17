"""
Pruebas del asistente flotante (el reactor siempre encima), del icono y del acceso directo.
Qt funciona en modo «offscreen»: no se abre nada en pantalla.

Ejecutar con:  pytest tests/test_overlay.py -v
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from core.config import Ajustes  # noqa: E402
from core.events import BusEventos  # noqa: E402
from ui import icono  # noqa: E402
from ui.overlay import (ATAJO, LADO, TE_ESCUCHO, AsistenteFlotante, accion_al_pulsar, leer_posicion,  # noqa: E402
                        posicion_por_defecto, pulso_objetivo, resumir, segundos_visible)

RAIZ = Path(__file__).resolve().parents[1]
CENTRO = QPoint(LADO // 2, LADO // 2)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class AsistenteFalso:
    def __init__(self, carpeta):
        self.bus = BusEventos()
        self.ajustes = Ajustes(os.path.join(carpeta, "ajustes.json"))
        self.estado_actual = "reposo"
        self.llamadas = []
        self.con_microfono = True

    def pulsar_microfono(self):
        self.llamadas.append("microfono")
        return self.con_microfono

    def detener(self):
        self.llamadas.append("detener")

    def enviar(self, texto, origen="texto"):
        self.llamadas.append(("enviar", texto))
        return True


@pytest.fixture
def flotante(app, tmp_path):
    asistente = AsistenteFalso(str(tmp_path))
    reactor = AsistenteFlotante(asistente, con_atajo=False)
    reactor.aplicar_ajuste()
    yield reactor
    reactor.cerrar()
    reactor.deleteLater()


def procesar(app):
    for _ in range(5):
        app.processEvents()


def desde_otro_hilo(funcion):
    hilo = threading.Thread(target=funcion)
    hilo.start()
    hilo.join()


def raton(widget, tipo, punto, boton=Qt.MouseButton.LeftButton, botones=Qt.MouseButton.LeftButton):
    global_ = widget.mapToGlobal(punto)
    QApplication.sendEvent(widget, QMouseEvent(tipo, QPointF(punto), QPointF(global_), boton, botones,
                                               Qt.KeyboardModifier.NoModifier))


# ==========================================
# CÁLCULOS
# ==========================================
def test_posicion_por_defecto_abajo_a_la_derecha():
    assert posicion_por_defecto(QRect(0, 0, 1920, 1040)) == QPoint(1920 - LADO - 24, 1040 - LADO - 24)


def test_posicion_guardada_solo_si_cae_en_una_pantalla():
    zonas = [QRect(0, 0, 1920, 1040)]
    assert leer_posicion("100,200", zonas) == QPoint(100, 200)
    assert leer_posicion("-300,10", [QRect(-1920, 0, 1920, 1080)]) == QPoint(-300, 10)   # monitor a la izquierda
    for texto in ("", "abc", "1,2,3", "5000,5000"):
        assert leer_posicion(texto, zonas) is None


def test_resumir_y_tiempo_de_lectura():
    largo = "palabra " * 80
    corto = resumir(largo)
    assert corto.endswith("…") and len(corto) <= 240
    assert resumir("Es **argentina**.") == "Es argentina."
    assert segundos_visible("hola") == pytest.approx(4.32)
    assert segundos_visible(largo) == 14.0


def test_que_hace_pulsar_segun_el_estado():
    assert accion_al_pulsar("hablando") == accion_al_pulsar("procesando") == "detener"
    assert accion_al_pulsar("reposo") == accion_al_pulsar("escuchando") == "escuchar"


def test_el_nucleo_late_con_la_voz():
    assert pulso_objetivo("escuchando", 0.0, 0) == 0.4 and pulso_objetivo("escuchando", 1.0, 0) == 1.0
    assert 0.6 <= pulso_objetivo("hablando", 0.0, 1.3) <= 0.96
    assert pulso_objetivo("reposo", 1.0, 0) == 0.0


# ==========================================
# EL REACTOR FLOTANTE
# ==========================================
def test_es_una_ventana_flotante_siempre_encima(flotante):
    banderas = flotante.windowFlags()
    assert banderas & Qt.WindowType.WindowStaysOnTopHint and banderas & Qt.WindowType.FramelessWindowHint
    assert banderas & Qt.WindowType.Tool                      # sin botón propio en la barra de tareas
    assert flotante.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert flotante.windowTitle() != "JARVIS"                 # el título que busca un segundo arranque
    assert flotante.isVisible()


def test_dibuja_el_reactor(flotante):
    imagen = flotante.grab().toImage()
    assert imagen.pixelColor(CENTRO).lightness() > 200       # el núcleo blanco
    assert imagen.pixelColor(0, 0).alpha() == 0               # las esquinas, transparentes


def test_pulsar_escucha_y_si_habla_la_detiene(flotante, app):
    flotante.pulsar()
    assert flotante.asistente.llamadas == ["microfono"]
    desde_otro_hilo(lambda: flotante.asistente.bus.emitir("estado", valor="hablando"))
    procesar(app)
    flotante.pulsar()
    assert flotante.asistente.llamadas == ["microfono", "detener"]


def test_los_eventos_de_otros_hilos_llegan(flotante, app):
    desde_otro_hilo(lambda: flotante.asistente.bus.emitir("estado", valor="escuchando"))
    procesar(app)
    assert flotante.estado == "escuchando" and flotante.reloj.interval() == 33      # 30 fotogramas al escuchar
    assert flotante.bocadillo.isVisible() and flotante.bocadillo.texto.text() == TE_ESCUCHO
    desde_otro_hilo(lambda: flotante.asistente.bus.emitir("estado", valor="reposo"))
    procesar(app)
    assert flotante.reloj.interval() == 166 and not flotante.bocadillo.isVisible()          # 6 en reposo


def test_la_respuesta_y_lo_oido_salen_en_el_bocadillo(flotante, app):
    bus = flotante.asistente.bus
    desde_otro_hilo(lambda: bus.emitir("mensaje", rol="usuario", origen="voz", texto="¿quién es Nicki Nicole?"))
    procesar(app)
    assert flotante.bocadillo.texto.text() == "«¿quién es Nicki Nicole?»"
    desde_otro_hilo(lambda: bus.emitir("mensaje", rol="asistente", texto="Es una cantante **argentina**."))
    procesar(app)
    assert flotante.bocadillo.isVisible() and flotante.bocadillo.texto.text() == "Es una cantante argentina."


def test_sin_microfono_se_le_puede_escribir(flotante):
    flotante.asistente.con_microfono = False
    flotante.pulsar()
    assert flotante.bocadillo.escribiendo and flotante.bocadillo.entrada.isVisible()
    flotante.bocadillo.entrada.setText("abre Spotify")
    flotante.bocadillo.entrada.returnPressed.emit()
    assert ("enviar", "abre Spotify") in flotante.asistente.llamadas
    assert not flotante.bocadillo.escribiendo


def test_mientras_escribe_no_se_le_quita_el_campo(flotante, app):
    flotante.escribir()
    desde_otro_hilo(lambda: flotante.asistente.bus.emitir("mensaje", rol="asistente", texto="Otra cosa"))
    procesar(app)
    assert flotante.bocadillo.entrada.isVisible() and flotante.bocadillo.texto.text() == "¿Qué quieres decirme?"


def test_clic_sin_moverse_es_hablar(flotante):
    raton(flotante, QEvent.Type.MouseButtonPress, CENTRO)
    raton(flotante, QEvent.Type.MouseButtonRelease, CENTRO, botones=Qt.MouseButton.NoButton)
    assert flotante.asistente.llamadas == ["microfono"]


def test_arrastrar_lo_mueve_y_recuerda_donde(flotante):
    antes = flotante.pos()
    raton(flotante, QEvent.Type.MouseButtonPress, CENTRO)
    raton(flotante, QEvent.Type.MouseMove, CENTRO + QPoint(-40, -30), boton=Qt.MouseButton.NoButton)
    raton(flotante, QEvent.Type.MouseButtonRelease, CENTRO + QPoint(-40, -30), botones=Qt.MouseButton.NoButton)
    assert flotante.pos() == antes + QPoint(-40, -30)
    assert flotante.asistente.llamadas == []                  # arrastrar no es hablar
    assert flotante.asistente.ajustes.obtener("interfaz.posicion_overlay") == f"{flotante.x()},{flotante.y()}"


def test_aparece_donde_se_dejo(app, tmp_path):
    asistente = AsistenteFalso(str(tmp_path))
    asistente.ajustes.establecer("interfaz.posicion_overlay", "100,120")
    reactor = AsistenteFlotante(asistente, con_atajo=False)
    assert reactor.pos() == QPoint(100, 120)
    reactor.cerrar()


def test_menu(flotante):
    acciones = [a for a in flotante.menu().actions() if not a.isSeparator()]
    assert [a.text() for a in acciones] == [f"Hablar\t{ATAJO}", "Escribir…", "Detener", "Ocultar el asistente flotante"]
    assert not acciones[2].isEnabled()                          # nada que detener en reposo


def test_menu_con_la_ventana_principal(app, tmp_path):
    ventana = QWidget()
    reactor = AsistenteFlotante(AsistenteFalso(str(tmp_path)), ventana, con_atajo=False)
    textos = [a.text() for a in reactor.menu().actions() if not a.isSeparator()]
    assert "Abrir JARVIS" in textos and textos[-1] == "Salir de JARVIS"
    reactor.cerrar()


def test_ocultarlo_y_volver_a_mostrarlo_desde_ajustes(flotante, app):
    flotante.ocultar_por_ajuste()
    assert not flotante.isVisible() and not flotante.reloj.isActive()     # oculto no gasta nada
    assert flotante.asistente.ajustes.obtener("interfaz.overlay") is False
    desde_otro_hilo(lambda: flotante.asistente.ajustes.establecer("interfaz.overlay", True))   # desde Ajustes
    procesar(app)
    assert flotante.isVisible() and flotante.reloj.isActive()


# ==========================================
# ICONO
# ==========================================
def test_el_icono_tiene_todos_los_tamanos():
    assert icono.leer_ico(icono.RUTA_ICO) == [(t, t) for t in icono.TAMANOS]
    assert icono.RUTA_PNG.exists()


def test_guardar_un_icono(app, tmp_path):
    ruta = tmp_path / "prueba.ico"
    icono.guardar_ico(ruta, [icono.imagen_icono(16), icono.imagen_icono(256)])
    assert icono.leer_ico(ruta) == [(16, 16), (256, 256)]
    imagen = icono.imagen_icono(64)
    assert imagen.pixelColor(32, 32).lightness() > 200 and imagen.pixelColor(0, 0).alpha() == 0


# ==========================================
# ACCESO DIRECTO
# ==========================================
@pytest.fixture
def acceso_directo():
    sys.path.insert(0, str(RAIZ / "scripts"))
    import crear_acceso_directo
    return crear_acceso_directo


def test_datos_del_acceso_directo(acceso_directo, tmp_path):
    datos = acceso_directo.datos_del_acceso(tmp_path)
    assert datos["ruta"] == tmp_path / "JARVIS.lnk"
    assert datos["objetivo"].lower().endswith("pythonw.exe")             # sin ventana de consola
    assert datos["argumentos"] == f'"{RAIZ / "jarvis.py"}"' and datos["carpeta_trabajo"] == str(RAIZ)
    assert datos["icono"] == str(icono.RUTA_ICO)


@pytest.mark.skipif(os.name != "nt", reason="los accesos directos .lnk son de Windows")
def test_crear_un_acceso_directo(acceso_directo, tmp_path):
    ruta = acceso_directo.crear_acceso(acceso_directo.datos_del_acceso(tmp_path))
    guardado = acceso_directo.leer_acceso(ruta)
    assert guardado["id_aplicacion"] == icono.ID_APLICACION              # se agrupa con la ventana de JARVIS
    assert guardado["icono"] == f"{icono.RUTA_ICO},0" and guardado["objetivo"].lower().endswith("pythonw.exe")


def test_en_reposo_se_queda_quieto_al_rato_y_despierta_con_cualquier_cosa(flotante, app):
    import time
    from ui.overlay import QUIETO_TRAS
    flotante._actividad = time.monotonic() - QUIETO_TRAS - 1        # hace rato que no pasa nada
    flotante._fotograma()
    assert not flotante.reloj.isActive()                              # quieto: no gasta procesador
    desde_otro_hilo(lambda: flotante.asistente.bus.emitir("estado", valor="escuchando"))
    procesar(app)
    assert flotante.reloj.isActive() and flotante.reloj.interval() == 33
