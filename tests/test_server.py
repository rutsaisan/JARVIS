"""
Pruebas del servidor local de la interfaz: quién puede entrar, qué operaciones
exigen la palabra de seguridad y qué ajustes se aceptan.

Ejecutar con:  pytest tests/test_server.py -v
"""
import shutil
import tempfile
import time

import pytest

from core.events import BusEventos


class CerebroFalso:
    modelo = "falso"

    def pensar(self, texto, **_):
        return "Respuesta de prueba."

    def resumir(self, mensajes):
        return ""

    def modelos(self):
        return ["llama3:latest"]

    def cerrar(self):
        pass


@pytest.fixture
def entorno(monkeypatch):
    from core.assistant import Asistente
    from ui.server import Servidor
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_srv_")
    monkeypatch.setenv("JARVIS_DATA_DIR", carpeta)
    asistente = Asistente(bus=BusEventos(), con_voz=False)
    asistente.cerebro = asistente.orquestador.cerebro = CerebroFalso()
    asistente.ajustes.establecer("personalidad.sarcasmo", 0)
    servidor = Servidor(asistente, testigo="testigo-de-prueba")
    cliente = servidor.app.test_client()
    base = f"http://127.0.0.1:{servidor.puerto}"

    class Entorno:
        pass

    e = Entorno()
    e.asistente, e.servidor, e.cliente, e.base = asistente, servidor, cliente, base
    e.pedir = lambda metodo, ruta, **opciones: cliente.open(ruta, method=metodo, base_url=base, **opciones)
    yield e
    servidor._http.server_close()
    shutil.rmtree(carpeta, ignore_errors=True)


@pytest.fixture
def sesion(entorno):
    """Entorno con la sesión ya iniciada (como hace la ventana al abrirse)."""
    respuesta = entorno.pedir("GET", "/entrar?t=testigo-de-prueba")
    assert respuesta.status_code == 302
    return entorno


# ==========================================
# ACCESO
# ==========================================
def test_sin_sesion_no_hay_api(entorno):
    assert entorno.pedir("GET", "/api/estado").status_code == 401
    assert entorno.pedir("POST", "/api/mensaje", json={"texto": "abre spotify"}).status_code == 401
    assert entorno.pedir("GET", "/").status_code == 401


def test_testigo_incorrecto(entorno):
    assert entorno.pedir("GET", "/entrar?t=otro").status_code == 403
    assert entorno.pedir("GET", "/api/estado").status_code == 401


def test_con_sesion_se_puede_entrar(sesion):
    estado = sesion.pedir("GET", "/api/estado")
    assert estado.status_code == 200
    datos = estado.get_json()
    assert datos["primera_vez"] is True and datos["modo"] == "texto"
    assert sesion.pedir("GET", "/").status_code == 200


def test_otro_nombre_de_equipo_se_rechaza(sesion):
    # Protección frente a «DNS rebinding»: aunque lleve la cookie, el Host debe ser el local.
    respuesta = sesion.cliente.get("/api/estado", base_url=f"http://malicioso.example:{sesion.servidor.puerto}")
    assert respuesta.status_code == 403


def test_peticion_desde_otra_web_se_rechaza(sesion):
    respuesta = sesion.pedir("POST", "/api/mensaje", json={"texto": "apaga el ordenador"},
                             headers={"Origin": "https://malicioso.example"})
    assert respuesta.status_code == 403


def test_cabeceras_de_seguridad(sesion):
    cabeceras = sesion.pedir("GET", "/").headers
    assert "default-src 'self'" in cabeceras["Content-Security-Policy"]
    assert cabeceras["X-Frame-Options"] == "DENY"


# ==========================================
# CONVERSAR
# ==========================================
def test_enviar_un_mensaje(sesion):
    sesion.asistente.iniciar = lambda: None
    assert sesion.pedir("POST", "/api/mensaje", json={"texto": "¿qué hora es?"}).get_json()["ok"] is True
    texto, origen = sesion.asistente._trabajos.get_nowait()
    assert (texto, origen) == ("¿qué hora es?", "texto")


def test_mensaje_vacio(sesion):
    assert sesion.pedir("POST", "/api/mensaje", json={"texto": "   "}).get_json()["ok"] is False


# ==========================================
# AJUSTES
# ==========================================
def test_cambiar_ajustes_validos_e_invalidos(sesion):
    datos = sesion.pedir("POST", "/api/ajustes", json={
        "usuario.nombre": "Ruth", "voz": {"velocidad": 1.2}, "modo": "telepatía", "inventado": 1,
        "ia.temperatura": 99,
    }).get_json()
    assert set(datos["aplicadas"]) == {"voz.velocidad"}              # el nombre ya era ese: no cambia
    assert set(datos["rechazadas"]) == {"modo", "inventado", "ia.temperatura"}
    assert sesion.asistente.ajustes.obtener("modo") == "texto"


def test_los_ajustes_no_devuelven_secretos(sesion):
    sesion.pedir("POST", "/api/clave", json={"servicio": "elevenlabs", "clave": "sk-secreta-123"})
    texto = sesion.pedir("GET", "/api/ajustes").get_data(as_text=True)
    assert "sk-secreta-123" not in texto
    assert sesion.pedir("GET", "/api/ajustes").get_json()["clave_elevenlabs"] is True


# ==========================================
# MEMORIA
# ==========================================
def test_ciclo_de_un_recuerdo(sesion):
    assert sesion.pedir("POST", "/api/recuerdos", json={"texto": "me gusta el té"}).status_code == 200
    lista = sesion.pedir("GET", "/api/recuerdos").get_json()
    assert lista["total"] == 1 and lista["conteo"] == {"preferencias": 1}
    recuerdo = lista["recuerdos"][0]

    assert sesion.pedir("PUT", f"/api/recuerdos/{recuerdo['id']}", json={"texto": "prefiero el té al café"}).status_code == 200
    assert sesion.pedir("GET", "/api/recuerdos?q=café").get_json()["recuerdos"][0]["texto"] == "prefiero el té al café"

    assert sesion.pedir("DELETE", f"/api/recuerdos/{recuerdo['id']}", json={}).get_json()["ok"] is True
    assert sesion.pedir("GET", "/api/recuerdos").get_json()["total"] == 0


def test_no_se_guardan_contrasenas_desde_la_interfaz(sesion):
    respuesta = sesion.pedir("POST", "/api/recuerdos", json={"texto": "mi contraseña es gato123"})
    assert respuesta.status_code == 400 and "sensible" in respuesta.get_json()["mensaje"]


# ==========================================
# PALABRA DE SEGURIDAD EN LA INTERFAZ
# ==========================================
def test_borrar_exige_la_palabra_de_seguridad(sesion):
    sesion.pedir("POST", "/api/recuerdos", json={"texto": "me gusta el té"})
    recuerdo = sesion.pedir("GET", "/api/recuerdos").get_json()["recuerdos"][0]
    assert sesion.pedir("POST", "/api/seguridad", json={"modo": "palabra", "palabra": "fénix dorado"}).status_code == 200

    sin_palabra = sesion.pedir("DELETE", f"/api/recuerdos/{recuerdo['id']}", json={})
    assert sin_palabra.status_code == 403 and sin_palabra.get_json()["pide_palabra"] is True
    assert sesion.pedir("DELETE", f"/api/recuerdos/{recuerdo['id']}", json={"palabra": "otra"}).status_code == 403
    assert sesion.pedir("GET", "/api/recuerdos").get_json()["total"] == 1

    assert sesion.pedir("DELETE", f"/api/recuerdos/{recuerdo['id']}", json={"palabra": "Fénix Dorado"}).status_code == 200
    assert sesion.pedir("GET", "/api/recuerdos").get_json()["total"] == 0


def test_cambiar_la_seguridad_exige_la_palabra_actual(sesion):
    sesion.pedir("POST", "/api/seguridad", json={"modo": "palabra", "palabra": "fénix dorado"})
    assert sesion.pedir("POST", "/api/seguridad", json={"modo": "confirmar"}).status_code == 403
    assert sesion.asistente.seguridad.modo == "palabra"
    correcto = sesion.pedir("POST", "/api/seguridad", json={"modo": "confirmar", "palabra_actual": "fénix dorado"})
    assert correcto.status_code == 200 and sesion.asistente.seguridad.modo == "confirmar"


def test_bloqueo_tras_demasiados_intentos(sesion):
    sesion.pedir("POST", "/api/seguridad", json={"modo": "palabra", "palabra": "fénix dorado"})
    for _ in range(5):
        assert sesion.pedir("DELETE", "/api/recuerdos", json={"palabra": "mal"}).status_code == 403
    # Ni siquiera la palabra correcta vale durante el bloqueo.
    assert sesion.pedir("DELETE", "/api/recuerdos", json={"palabra": "fénix dorado"}).status_code == 429
    sesion.servidor._bloqueado_hasta = time.time() - 1
    assert sesion.pedir("DELETE", "/api/recuerdos", json={"palabra": "fénix dorado"}).status_code == 200


def test_palabra_demasiado_corta(sesion):
    respuesta = sesion.pedir("POST", "/api/seguridad", json={"modo": "palabra", "palabra": "ab"})
    assert respuesta.status_code == 400 and "al menos" in respuesta.get_json()["mensaje"]
    assert sesion.asistente.seguridad.modo_configurado is False


# ==========================================
# CONVERSACIONES
# ==========================================
def test_historial_por_la_api(sesion):
    sesion.asistente.orquestador.procesar("hola Jarvis")
    conversaciones = sesion.pedir("GET", "/api/conversaciones").get_json()
    assert len(conversaciones["conversaciones"]) == 1
    identificador = conversaciones["conversaciones"][0]["id"]

    sesion.pedir("POST", "/api/conversaciones")
    assert sesion.pedir("GET", "/api/estado").get_json()["mensajes"] == []
    abierta = sesion.pedir("POST", f"/api/conversaciones/{identificador}/abrir").get_json()
    assert abierta["mensajes"][0]["texto"] == "hola Jarvis"

    assert sesion.pedir("DELETE", f"/api/conversaciones/{identificador}", json={}).get_json()["ok"] is True
    assert sesion.pedir("GET", "/api/conversaciones").get_json()["conversaciones"] == []


def test_abrir_solo_admite_enlaces_web(sesion):
    assert sesion.pedir("POST", "/api/abrir", json={"url": "file:///C:/Windows/System32/calc.exe"}).get_json()["ok"] is False
    assert sesion.pedir("POST", "/api/abrir", json={"url": "https://ejemplo.es"}).get_json()["ok"] is True   # simulado
