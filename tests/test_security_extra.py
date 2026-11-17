"""
Pruebas añadidas en la versión final de la capa de seguridad.

Ejecutar con:  pytest tests/test_security_extra.py -v
"""
import json
import os
import shutil
import tempfile

import pytest

from tools.security import GestorSeguridad


@pytest.fixture
def seguridad():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_sec2_")
    yield GestorSeguridad(config_path=os.path.join(carpeta, "cfg.json"),
                          log_path=os.path.join(carpeta, "logs", "acciones.jsonl"))
    shutil.rmtree(carpeta, ignore_errors=True)


# ==========================================
# PALABRA DE SEGURIDAD
# ==========================================
def test_la_palabra_tolera_acentos_y_signos_de_la_transcripcion(seguridad):
    seguridad.configurar_palabra("fénix dorado")
    assert seguridad.verificar_respuesta("Fénix dorado.") is True
    assert seguridad.verificar_respuesta("fenix dorado") is True
    assert seguridad.verificar_respuesta("la palabra es fénix dorado") is True


def test_la_palabra_se_guarda_con_sal(seguridad):
    seguridad.configurar_palabra("fenix dorado")
    primera = json.load(open(seguridad.config_path, encoding="utf-8"))
    seguridad.configurar_palabra("fenix dorado")
    segunda = json.load(open(seguridad.config_path, encoding="utf-8"))
    assert primera["sal"] != segunda["sal"]
    assert primera["palabra_hash"] != segunda["palabra_hash"]      # misma palabra, hash distinto


def test_no_acepta_una_orden_habitual_como_palabra(seguridad):
    respuesta = seguridad.configurar_palabra("confirmo")
    assert "otra" in respuesta.lower()
    assert not seguridad.modo_configurado


def test_lee_configuraciones_de_la_v4(seguridad):
    import hashlib
    antiguo = {"modo": "palabra", "configurada": True,
               "palabra_hash": hashlib.sha256("centinela".encode("utf-8")).hexdigest()}
    with open(seguridad.config_path, "w", encoding="utf-8") as fichero:
        json.dump(antiguo, fichero)
    otra = GestorSeguridad(config_path=seguridad.config_path, log_path=seguridad.log_path)
    assert otra.verificar_respuesta("centinela") is True
    assert otra.verificar_respuesta("otra cosa") is False


def test_cambiar_la_seguridad_es_delicado_si_hay_palabra(seguridad):
    assert seguridad.es_sensible("security_setup", "set_word") is False      # primera configuración
    seguridad.configurar_palabra("fenix dorado")
    assert seguridad.es_sensible("security_setup", "set_word") is True
    assert seguridad.es_sensible("security_setup", "set_confirm_mode") is True


# ==========================================
# CONFIRMACIÓN SIMPLE
# ==========================================
@pytest.mark.parametrize("texto", ["sí, hazlo", "vale, adelante", "Confirmo.", "de acuerdo"])
def test_confirmaciones_naturales(seguridad, texto):
    assert seguridad.verificar_respuesta(texto) is True


@pytest.mark.parametrize("texto", [
    "si quieres luego lo miramos con calma y ya está",      # empieza por "si" pero no confirma nada
    "no sé qué decirte la verdad sobre este tema",
])
def test_frases_largas_no_confirman_ni_cancelan(seguridad, texto):
    assert seguridad.verificar_respuesta(texto) is False
    assert seguridad.es_cancelacion(texto) is False


# ==========================================
# CATÁLOGO DE ACCIONES DELICADAS
# ==========================================
@pytest.mark.parametrize("intent,action,target,esperado", [
    ("pc", "system_command", "apagar", True),
    ("pc", "system_command", "reiniciar", True),
    ("pc", "system_command", "vaciar_papelera", True),
    ("pc", "system_command", "cerrar_sesion", True),
    ("pc", "system_command", "bloquear", False),
    ("pc", "system_command", "captura", False),
    ("pc", "system_command", "modo_oscuro", False),
    ("pc", "press_key", "alt+f4", True),
    ("pc", "press_key", "enter", False),
    ("pc", "open_app", "Símbolo del sistema", True),
    ("pc", "open_app", "Windows PowerShell", True),
    ("pc", "open_app", "terminator salvation", False),      # "terminal" no es subcadena válida
    ("memory_delete", "delete_all", "", True),
    ("history", "delete_all", "", True),
    ("weather", "today", "", False),
    ("info", "time", "", False),
])
def test_es_sensible_ampliado(seguridad, intent, action, target, esperado):
    assert seguridad.es_sensible(intent, action, target) == esperado


def test_descripciones_de_confirmacion(seguridad):
    assert "apagar el ordenador" in seguridad.generar_prompt_confirmacion("pc", "system_command", "apagar")
    assert "ventana activa" in seguridad.generar_prompt_confirmacion("pc", "close_app", "active")
    assert "TODOS" in seguridad.generar_prompt_confirmacion("memory_delete", "delete_all")


# ==========================================
# AUDITORÍA
# ==========================================
def test_ultimas_acciones(seguridad):
    seguridad.registrar_accion("pc", "open_app", "spotify", confirmada=False, ejecutada=True, descripcion="Abriste: Spotify")
    seguridad.registrar_accion("pc", "close_app", "spotify", confirmada=False, ejecutada=False)   # pendiente
    seguridad.registrar_accion("search_web", "search", "clima", confirmada=False, ejecutada=True)
    acciones = seguridad.ultimas_acciones(5)
    assert [a["action"] for a in acciones] == ["search", "open_app"]       # la más reciente primero
    assert acciones[1]["descripcion"] == "Abriste: Spotify"
