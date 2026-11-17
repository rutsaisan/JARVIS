"""
Pruebas de las órdenes de música y del voseo en el motor de intenciones.
Incluyen las frases reales (transcritas por Whisper) con las que JARVIS falló:
acababan en la IA, que fingía poner música y se inventaba qué sonaba.

Ejecutar con:  pytest tests/test_musica_intenciones.py -v
"""
import pytest

from tools.intent_engine import interpretar


def accion(frase):
    r = interpretar(frase)
    return r["intent"], r["action"]


# ==========================================
# FRASES REALES QUE FALLARON
# ==========================================
def test_podes_reproducir_la_musica():
    assert accion("podés reproducir la música.") == ("music", "resume")


def test_abris_spotify_y_reproduce_la_ultima_cancion():
    r = interpretar("Abris, Spotify y Reproduce la última canción que estaba sonando.")
    assert r["intent"] == "multi"
    assert [(p["intent"], p["action"], p.get("target")) for p in r["steps"]] == [
        ("pc", "open_app", "Spotify"), ("music", "resume", "")]


# ==========================================
# QUÉ SUENA (se lee de verdad, nunca se supone)
# ==========================================
@pytest.mark.parametrize("frase", [
    "¿Qué canción está sonando?", "Jarvis, ¿qué canción estaba sonando?", "¿qué suena?",
    "¿cómo se llama esta canción?", "¿quién canta esta canción?", "dime qué canción suena",
    "¿qué estoy escuchando?", "¿qué está sonando en Spotify?",
])
def test_que_suena(frase):
    assert accion(frase) == ("music", "now_playing")


def test_que_suena_en_una_aplicacion_concreta():
    assert interpretar("¿qué está sonando en Spotify?")["target"] == "spotify"


# ==========================================
# PAUSAR Y REANUDAR (explícito, no la tecla que alterna)
# ==========================================
@pytest.mark.parametrize("frase", ["pausa", "pausa la música", "para la música", "quita la música",
                                   "pon pausa", "detén la canción", "pausa Spotify"])
def test_pausar(frase):
    assert accion(frase) == ("music", "pause")


@pytest.mark.parametrize("frase", [
    "reproduce", "reanuda", "continúa", "dale al play", "pon música", "poné música", "pon la música",
    "reproduce la música", "reproduce la última canción", "vuelve a poner la canción que estaba sonando",
    "pon la música que estaba sonando", "reanuda Spotify", "reproduce Spotify", "quita la pausa",
])
def test_reanudar(frase):
    assert accion(frase) == ("music", "resume")


def test_siguiente_y_anterior_siguen_siendo_teclas():
    assert interpretar("siguiente canción")["target"] == "siguiente"
    assert interpretar("canción anterior")["target"] == "anterior"


# ==========================================
# PONER UNA CANCIÓN
# ==========================================
@pytest.mark.parametrize("frase,consulta,servicio", [
    ("pon algo de Queen", "Queen", ""),
    ("ponme algo de Queen", "Queen", ""),
    ("reproduce Bohemian Rhapsody", "Bohemian Rhapsody", ""),
    ("reproduce Bohemian Rhapsody en Spotify", "Bohemian Rhapsody", "spotify"),
    ("reproducí la canción de Nicki Nicole", "Nicki Nicole", ""),
    ("pon la canción Despacito", "Despacito", ""),
    ("quiero escuchar música lo-fi", "música lo-fi", ""),
    ("pon canciones de Bad Bunny en YouTube", "Bad Bunny", "youtube"),
])
def test_poner_cancion(frase, consulta, servicio):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("music", "play_song")
    assert r["query"] == consulta
    assert r["target"] == servicio


@pytest.mark.parametrize("frase,objetivo", [("pon Netflix", "Netflix"), ("ponme Despacito", "Despacito"),
                                            ("pon Spotify", "Spotify")])
def test_pon_a_secas_abre_la_app_o_pone_la_cancion(frase, objetivo):
    # Si no existe una aplicación con ese nombre, el orquestador lo pone como canción.
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("pc", "open_app", objetivo)
    assert r["alternativa"] == "musica"


@pytest.mark.parametrize("frase", [
    "pon el volumen al máximo", "pon que mañana tengo examen", "pon en pantalla completa",
    "mi hermana pone la mesa", "¿qué tal la playa?", "el tiempo es oro",
])
def test_no_son_canciones(frase):
    assert interpretar(frase)["action"] not in ("play_song", "open_app")


# ==========================================
# VOSEO Y COMAS DE WHISPER
# ==========================================
@pytest.mark.parametrize("frase,esperado", [
    ("abrí Spotify", ("pc", "open_app")), ("¿me abrís el bloc de notas?", ("pc", "open_app")),
    ("cerrá Spotify", ("pc", "close_app")), ("decime qué hora es", ("info", "time")),
    ("mostrame mis recuerdos", ("memory_query", "list")), ("subí el volumen", ("pc", "media")),
    ("¿podés buscar recetas de pasta?", ("search_web", "search")), ("acordate que me gusta el té", ("memory_save", "save")),
    ("¿me abres Spotify?", ("pc", "open_app")), ("¿me pones algo de Queen?", ("music", "play_song")),
    ("¿me dices la hora?", ("info", "time")), ("¿me buscas el tiempo en Madrid?", ("search_web", "search")),
])
def test_voseo(frase, esperado):
    assert accion(frase) == esperado


@pytest.mark.parametrize("frase", ["Abre, Spotify", "abre, Spotify.", "Abrís, Spotify"])
def test_coma_tras_el_verbo(frase):
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("pc", "open_app", "Spotify")
