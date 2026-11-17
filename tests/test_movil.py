"""
Pruebas del acceso desde el móvil (ui/server.py): PIN, solo red de casa, sesiones y lo que
solo se puede hacer desde el ordenador. No se abre ningún puerto en la red.

Ejecutar con:  pytest tests/test_movil.py -v
"""
import shutil
import tempfile

import pytest

from core.events import BusEventos

PUERTO_MOVIL = 8770
CASA = {"REMOTE_ADDR": "192.168.1.60"}


class CerebroFalso:
    modelo = "falso"

    def pensar(self, texto, **_):
        return "Respuesta de prueba."

    def resumir(self, mensajes):
        return ""

    def modelos(self):
        return ["granite4.1:8b"]

    def cerrar(self):
        pass


def crear(monkeypatch, activo=True):
    from core.assistant import Asistente
    from ui.server import Servidor
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_movil_")
    monkeypatch.setenv("JARVIS_DATA_DIR", carpeta)
    asistente = Asistente(bus=BusEventos(), con_voz=False)
    asistente.cerebro = asistente.orquestador.cerebro = CerebroFalso()
    asistente.ajustes.establecer("movil.activo", activo)
    servidor = Servidor(asistente, testigo="testigo-de-prueba", puerto_movil=PUERTO_MOVIL, escuchar_movil=False)
    return servidor, carpeta


@pytest.fixture
def movil(monkeypatch):
    servidor, carpeta = crear(monkeypatch)
    cliente = servidor.app.test_client()

    def pedir(metodo, ruta, ip=CASA, **opciones):
        return cliente.open(ruta, method=metodo, base_url=f"http://192.168.1.50:{PUERTO_MOVIL}",
                            environ_overrides=ip, **opciones)
    servidor.pedir = pedir
    servidor.cliente = cliente
    yield servidor
    servidor._http.server_close()
    shutil.rmtree(carpeta, ignore_errors=True)


def pin():
    from core.config import leer_secreto
    return leer_secreto("movil_pin")


def test_desactivado_no_hay_entrada_para_el_movil(monkeypatch):
    servidor, carpeta = crear(monkeypatch, activo=False)
    respuesta = servidor.app.test_client().get("/movil", base_url=f"http://192.168.1.50:{PUERTO_MOVIL}",
                                              environ_overrides=CASA)
    assert respuesta.status_code == 403 and servidor.puerto_movil is None
    servidor._http.server_close()
    shutil.rmtree(carpeta, ignore_errors=True)


def test_sin_sesion_pide_el_pin(movil):
    assert len(pin()) == 6 and pin().isdigit()
    respuesta = movil.pedir("GET", "/")
    assert respuesta.status_code == 302 and respuesta.headers["Location"].endswith("/movil")
    assert movil.pedir("GET", "/api/estado").status_code == 401
    assert "Escribe el PIN" in movil.pedir("GET", "/movil").get_data(as_text=True)


def test_con_el_pin_correcto_entra(movil):
    respuesta = movil.pedir("POST", "/movil/entrar", data={"pin": pin()})
    assert respuesta.status_code == 302 and "jarvis_movil=" in respuesta.headers["Set-Cookie"]
    assert "HttpOnly" in respuesta.headers["Set-Cookie"]
    assert movil.pedir("GET", "/api/estado").status_code == 200
    assert movil.pedir("POST", "/api/mensaje", json={"texto": "hola"}).status_code == 200


def test_pin_incorrecto_y_bloqueo(movil):
    for _ in range(4):
        assert movil.pedir("POST", "/movil/entrar", data={"pin": "000000"}).status_code == 403
    assert movil.pedir("POST", "/movil/entrar", data={"pin": "000000"}).status_code == 403   # el quinto bloquea
    respuesta = movil.pedir("POST", "/movil/entrar", data={"pin": pin()})
    assert respuesta.status_code == 429                         # ni con el bueno, durante unos minutos


def test_solo_desde_la_red_de_casa(movil):
    assert movil.pedir("GET", "/movil", ip={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403
    assert movil.pedir("POST", "/movil/entrar", data={"pin": pin()}, ip={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403


def test_lo_que_solo_se_hace_desde_el_ordenador(movil):
    movil.pedir("POST", "/movil/entrar", data={"pin": pin()})
    for metodo, ruta in (("GET", "/api/movil"), ("POST", "/api/movil/pin"), ("POST", "/api/clave"),
                         ("POST", "/api/seguridad"), ("GET", "/entrar?t=testigo-de-prueba")):
        assert movil.pedir(metodo, ruta, json={}).status_code == 403, ruta


def test_origen_ajeno_rechazado(movil):
    movil.pedir("POST", "/movil/entrar", data={"pin": pin()})
    respuesta = movil.pedir("POST", "/api/mensaje", json={"texto": "hola"}, headers={"Origin": "http://malo.com"})
    assert respuesta.status_code == 403


def test_desde_el_ordenador_se_ve_el_pin_y_se_puede_cambiar(movil):
    base = f"http://127.0.0.1:{movil.puerto}"
    movil.cliente.get("/entrar?t=testigo-de-prueba", base_url=base)
    estado = movil.cliente.get("/api/movil", base_url=base).get_json()
    assert estado["activo"] and estado["pin"] == pin()
    anterior, sesion_anterior = pin(), movil.testigo_movil
    nuevo = movil.cliente.post("/api/movil/pin", base_url=base).get_json()
    assert nuevo["pin"] == pin() != anterior and movil.testigo_movil != sesion_anterior   # se cierran las sesiones


def test_la_cookie_del_ordenador_no_vale_en_el_movil(movil):
    movil.cliente.set_cookie("jarvis_sesion", "testigo-de-prueba", domain="192.168.1.50")
    assert movil.pedir("GET", "/api/estado").status_code == 401
