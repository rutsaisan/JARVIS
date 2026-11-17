"""
Pruebas de la capa de seguridad (tools/security.py).
Cubre la sección 9 del plan técnico: lista blanca, confirmación,
palabra de seguridad y registro de acciones para auditoría.

Ejecutar con:  pytest tests/test_security.py -v
"""
import sys
import os
import json
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from tools.security import GestorSeguridad


@pytest.fixture
def seguridad():
    tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_sec_")
    g = GestorSeguridad(
        config_path=os.path.join(tmp_dir, "cfg.json"),
        log_path=os.path.join(tmp_dir, "logs", "acciones.jsonl"),
    )
    yield g
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ==========================================
# ESTADO INICIAL
# ==========================================
def test_sin_configurar_al_inicio(seguridad):
    assert not seguridad.modo_configurado


# ==========================================
# LISTA BLANCA / ACCIONES SENSIBLES
# ==========================================
@pytest.mark.parametrize("intent,action,target,esperado", [
    ("pc", "close_app", "spotify", True),
    ("pc", "type_text", "", True),
    ("memory_delete", "delete", "x", True),
    ("pc", "open_app", "spotify", False),
    ("pc", "open_app", "chrome", False),
    ("pc", "open_app", "cmd", True),
    ("pc", "open_app", "powershell", True),
    ("pc", "open_app", "regedit", True),
    ("pc", "press_key", "", False),
    ("pc", "media", "siguiente", False),
    ("search_web", "search", "", False),
    ("memory_save", "save", "", False),
])
def test_es_sensible(seguridad, intent, action, target, esperado):
    assert seguridad.es_sensible(intent, action, target) == esperado


# ==========================================
# MODO POR DEFECTO (sin configurar) — confirmación simple
# ==========================================
@pytest.mark.parametrize("texto", ["sí", "si", "confirmo", "adelante", "afirmativo"])
def test_verificar_respuesta_confirma_sin_configurar(seguridad, texto):
    assert seguridad.verificar_respuesta(texto) is True


@pytest.mark.parametrize("texto", ["no", "cancela", "qué tiempo hace", ""])
def test_verificar_respuesta_no_confirma_sin_configurar(seguridad, texto):
    assert seguridad.verificar_respuesta(texto) is False


@pytest.mark.parametrize("texto", ["cancela", "cancelar", "no", "negativo", "para"])
def test_es_cancelacion(seguridad, texto):
    assert seguridad.es_cancelacion(texto) is True


# ==========================================
# CONFIGURAR MODO CONFIRMACIÓN
# ==========================================
def test_configurar_modo_confirmacion(seguridad):
    seguridad.configurar_modo_confirmacion()
    assert seguridad.modo_configurado
    assert seguridad.verificar_respuesta("confirmo") is True


def test_configuracion_persiste_entre_instancias(seguridad):
    seguridad.configurar_modo_confirmacion()
    otra_instancia = GestorSeguridad(config_path=seguridad.config_path, log_path=seguridad.log_path)
    assert otra_instancia.modo_configurado


# ==========================================
# CONFIGURAR PALABRA DE SEGURIDAD
# ==========================================
def test_configurar_palabra_valida(seguridad):
    respuesta = seguridad.configurar_palabra("fenix dorado")
    assert "configurada" in respuesta.lower()
    assert seguridad.modo_configurado


def test_palabra_correcta_confirma(seguridad):
    seguridad.configurar_palabra("fenix dorado")
    assert seguridad.verificar_respuesta("fenix dorado") is True
    assert seguridad.verificar_respuesta("FENIX DORADO") is True  # no distingue mayúsculas


def test_palabra_incorrecta_no_confirma(seguridad):
    seguridad.configurar_palabra("fenix dorado")
    assert seguridad.verificar_respuesta("palabra incorrecta") is False


def test_decir_confirmo_no_basta_con_palabra_configurada(seguridad):
    """Caso crítico: si hay palabra de seguridad, un simple 'confirmo' NO debe bastar."""
    seguridad.configurar_palabra("fenix dorado")
    assert seguridad.verificar_respuesta("confirmo") is False
    assert seguridad.verificar_respuesta("sí") is False


def test_palabra_demasiado_corta_rechazada(seguridad):
    respuesta = seguridad.configurar_palabra("ab")
    assert "al menos" in respuesta.lower()
    assert not seguridad.modo_configurado


def test_palabra_no_se_guarda_en_texto_plano(seguridad):
    seguridad.configurar_palabra("fenix dorado")
    with open(seguridad.config_path, "r", encoding="utf-8") as f:
        contenido = f.read()
    assert "fenix dorado" not in contenido.lower()


# ==========================================
# PROMPTS DE CONFIRMACIÓN
# ==========================================
def test_prompt_modo_palabra_pide_la_palabra(seguridad):
    seguridad.configurar_palabra("fenix dorado")
    prompt = seguridad.generar_prompt_confirmacion("pc", "close_app", "spotify")
    assert "palabra de seguridad" in prompt.lower()


def test_prompt_modo_confirmar_pide_confirmo(seguridad):
    seguridad.configurar_modo_confirmacion()
    prompt = seguridad.generar_prompt_confirmacion("memory_delete", "delete", "mi cumpleaños")
    assert "confirmo" in prompt.lower()


def test_prompt_describe_la_accion(seguridad):
    prompt = seguridad.generar_prompt_confirmacion("pc", "type_text", "", "hola mundo")
    assert "hola mundo" in prompt


# ==========================================
# AUDITORÍA (registro de acciones)
# ==========================================
def test_registrar_accion_crea_log(seguridad):
    seguridad.registrar_accion("pc", "close_app", "spotify", confirmada=True, ejecutada=True)
    assert os.path.exists(seguridad.log_path)
    with open(seguridad.log_path, "r", encoding="utf-8") as f:
        lineas = f.readlines()
    assert len(lineas) == 1
    entrada = json.loads(lineas[0])
    assert entrada["action"] == "close_app"
    assert entrada["confirmada"] is True
    assert entrada["ejecutada"] is True


def test_registrar_varias_acciones_acumula(seguridad):
    seguridad.registrar_accion("pc", "open_app", "spotify", confirmada=False, ejecutada=True)
    seguridad.registrar_accion("pc", "close_app", "spotify", confirmada=True, ejecutada=True)
    with open(seguridad.log_path, "r", encoding="utf-8") as f:
        lineas = f.readlines()
    assert len(lineas) == 2
