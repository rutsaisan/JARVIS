"""
Pruebas del motor de intenciones (Fase 8 del plan: "Motor de intenciones probado").
Ejecutar con:  pytest tests/test_intent_engine.py -v
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from tools.intent_engine import interpretar, UMBRAL_CONFIANZA_MINIMO


# ==========================================
# SISTEMA
# ==========================================
@pytest.mark.parametrize("frase", [
    "apagar sistema", "descansa", "apágate", "apagate", "duérmete jarvis",
])
def test_shutdown(frase):
    r = interpretar(frase)
    assert r["intent"] == "system"
    assert r["action"] == "shutdown_jarvis"
    assert r["confidence"] == 1.0


# ==========================================
# SEGURIDAD — CONFIGURACIÓN
# ==========================================
@pytest.mark.parametrize("frase,texto_esperado", [
    ("mi palabra de seguridad es fenix dorado", "fenix dorado"),
    ("establece la palabra de seguridad centinela", "centinela"),
    ("configura la palabra de seguridad azul marino", "azul marino"),
])
def test_security_setup_palabra(frase, texto_esperado):
    r = interpretar(frase)
    assert r["intent"] == "security_setup"
    assert r["action"] == "set_word"
    assert r["text"] == texto_esperado


@pytest.mark.parametrize("frase", [
    "usa modo confirmación", "solo pídeme confirmación",
    "no quiero palabra de seguridad", "modo confirmación",
])
def test_security_setup_modo_confirmacion(frase):
    r = interpretar(frase)
    assert r["intent"] == "security_setup"
    assert r["action"] == "set_confirm_mode"


# ==========================================
# MEMORIA — GUARDAR
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("recuerda que me gusta el rock", "me gusta el rock"),
    ("recuerda mi cumpleaños es en marzo", "mi cumpleaños es en marzo"),
    ("memoriza que trabajo en turno de tarde", "trabajo en turno de tarde"),
    ("guarda que prefiero el café solo", "prefiero el café solo"),
    ("apunta que la reunión es el jueves", "la reunión es el jueves"),
    ("no olvides que soy alérgica al polen", "soy alérgica al polen"),
])
def test_memory_save(frase, esperado):
    r = interpretar(frase)
    assert r["intent"] == "memory_save"
    assert r["target"] == esperado
    assert r["confidence"] >= UMBRAL_CONFIANZA_MINIMO


def test_memory_save_vacio_baja_confianza():
    r = interpretar("recuerda")
    assert r["intent"] == "memory_save"
    assert r["confidence"] < UMBRAL_CONFIANZA_MINIMO


# ==========================================
# MEMORIA — CONSULTAR
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("qué recuerdas de mi cumpleaños", "mi cumpleaños"),
    ("qué sabes sobre mis preferencias de música", "mis preferencias de música"),
    ("tienes algún recuerdo de mi trabajo", "mi trabajo"),
])
def test_memory_query(frase, esperado):
    r = interpretar(frase)
    assert r["intent"] == "memory_query"
    assert r["action"] == "query"
    assert r["query"] == esperado


# ==========================================
# MEMORIA — BORRAR
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("olvida que me gusta el rock", "me gusta el rock"),
    ("borra el recuerdo de mi cumpleaños", "mi cumpleaños"),
    ("elimina de tu memoria mi antiguo trabajo", "mi antiguo trabajo"),
])
def test_memory_delete(frase, esperado):
    r = interpretar(frase)
    assert r["intent"] == "memory_delete"
    assert r["target"] == esperado


# ==========================================
# MEMORIA — EDITAR
# ==========================================
@pytest.mark.parametrize("frase,target,nuevo", [
    ("cambia mi color favorito a verde", "mi color favorito", "verde"),
    ("modifica el recuerdo de mi cumpleaños por el 5 de mayo", "mi cumpleaños", "el 5 de mayo"),
    ("actualiza lo que recuerdas de mi trabajo a diseñadora", "mi trabajo", "diseñadora"),
])
def test_memory_edit(frase, target, nuevo):
    r = interpretar(frase)
    assert r["intent"] == "memory_edit"
    assert r["target"] == target
    assert r["text"] == nuevo
    assert r["confidence"] >= UMBRAL_CONFIANZA_MINIMO


# ==========================================
# MEMORIA — LISTAR TODO
# ==========================================
@pytest.mark.parametrize("frase", [
    "qué recuerdas de mí", "muéstrame mis recuerdos", "lista tus recuerdos", "enséñame tu memoria",
])
def test_memory_list(frase):
    r = interpretar(frase)
    assert r["intent"] == "memory_query"
    assert r["action"] == "list"


def test_memory_query_especifica_no_es_list():
    # Debe distinguir "qué recuerdas de mí" (listar) de "qué recuerdas de X" (consulta puntual)
    r = interpretar("qué recuerdas de mi cumpleaños")
    assert r["intent"] == "memory_query"
    assert r["action"] == "query"
    assert r["query"] == "mi cumpleaños"


# ==========================================
# BÚSQUEDA WEB
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("busca el tiempo en Sevilla", "el tiempo en Sevilla"),
    ("búscame restaurantes cerca de aquí", "restaurantes cerca de aquí"),
    ("busca información sobre la fórmula 1", "la fórmula 1"),
    ("investiga sobre inteligencia artificial", "inteligencia artificial"),
    ("busca en internet noticias de tecnología", "noticias de tecnología"),
])
def test_search_web(frase, esperado):
    r = interpretar(frase)
    assert r["intent"] == "search_web"
    assert r["query"] == esperado
    assert r["confidence"] >= 0.9


# ==========================================
# CONTROL PC — ABRIR APP
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("abre spotify", "spotify"),
    ("abrir visual studio code", "visual studio code"),
    ("por favor abre chrome", "chrome"),
    ("ejecuta el programa steam", "steam"),
    ("lanza la aplicación de discord", "discord"),
    ("avre spotify", "spotify"),  # error de transcripción / ASR
])
def test_open_app(frase, esperado):
    r = interpretar(frase)
    assert r["intent"] == "pc"
    assert r["action"] == "open_app"
    assert r["target"] == esperado
    assert r["confidence"] >= 0.9


def test_correccion_asr_no_afecta_palabras_normales():
    # "usa" no debe confundirse con "pulsa"/"pausa"/"busca" (regresión del
    # corrector de ASR: ver test_security_setup_modo_confirmacion).
    r = interpretar("usa modo confirmación")
    assert r["intent"] == "security_setup"


# ==========================================
# CONTROL PC — CERRAR APP
# ==========================================
def test_close_app_especifica():
    r = interpretar("cierra spotify")
    assert r["intent"] == "pc"
    assert r["action"] == "close_app"
    assert r["target"] == "spotify"


@pytest.mark.parametrize("frase", ["cierra", "cierra esta ventana", "cierra la ventana activa"])
def test_close_app_ventana_activa(frase):
    r = interpretar(frase)
    assert r["intent"] == "pc"
    assert r["action"] == "close_app"
    assert r["target"] == "active"


# ==========================================
# CONTROL PC — ESCRIBIR TEXTO
# ==========================================
def test_type_text():
    r = interpretar("escribe hola, ¿qué tal estás?")
    assert r["intent"] == "pc"
    assert r["action"] == "type_text"
    assert r["text"] == "hola, ¿qué tal estás?"


# ==========================================
# CONTROL PC — TECLAS
# ==========================================
@pytest.mark.parametrize("frase,tecla", [
    ("pulsa intro", "enter"),
    ("presiona espacio", "space"),
    ("pulsa escape", "esc"),
])
def test_press_key(frase, tecla):
    r = interpretar(frase)
    assert r["intent"] == "pc"
    assert r["action"] == "press_key"
    assert r["key"] == tecla


# ==========================================
# CONTROL PC — MULTIMEDIA
# ==========================================
@pytest.mark.parametrize("frase,target", [
    ("siguiente canción", "siguiente"),
    ("canción anterior", "anterior"),
    ("sube el volumen", "subir_volumen"),
    ("baja el volumen", "bajar_volumen"),
    ("silencia", "silenciar"),
])
def test_media(frase, target):
    r = interpretar(frase)
    assert r["intent"] == "pc"
    assert r["action"] == "media"
    assert r["target"] == target


def test_pausa():
    # Cambio respecto a la v4: «pausa» pausa de verdad (controles multimedia de Windows)
    # en vez de pulsar la tecla «play/pausa», que alterna y podía hacer lo contrario.
    r = interpretar("pausa")
    assert (r["intent"], r["action"]) == ("music", "pause")


# ==========================================
# SECUENCIAS (MULTI)
# ==========================================
def test_secuencia_abrir_y_buscar():
    r = interpretar("abre spotify y busca lo-fi beats")
    assert r["intent"] == "multi"
    assert len(r["steps"]) == 2
    assert r["steps"][0]["action"] == "open_app"
    assert r["steps"][1]["intent"] == "search_web"


def test_secuencia_no_se_activa_con_una_sola_accion():
    # "restaurantes en Madrid" no es una acción reconocida -> no debe ser "multi"
    r = interpretar("busca hoteles y restaurantes en Madrid")
    assert r["intent"] == "search_web"


# ==========================================
# CHAT / FALLBACK
# ==========================================
@pytest.mark.parametrize("frase", [
    "hola jarvis, ¿cómo estás?",
    "¿qué opinas de la vida?",
    "cuéntame un chiste",
    "vale, gracias",
    "claro que sí",
    "perfecto entonces",
])
def test_chat_fallback(frase):
    r = interpretar(frase)
    assert r["intent"] == "chat"
    assert r["confidence"] == 0.0


def test_comando_vacio():
    r = interpretar("")
    assert r["intent"] == "chat"
    assert r["confidence"] == 0.0
