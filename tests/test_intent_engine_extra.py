"""
Pruebas añadidas en la versión final del motor de intenciones (v3):
peticiones naturales, intenciones nuevas y regresiones de falsos positivos
detectados al auditar la v2.

Ejecutar con:  pytest tests/test_intent_engine_extra.py -v
"""
import pytest
from tools.intent_engine import interpretar


# ==========================================
# PARÁMETROS: conservan mayúsculas y acentos
# ==========================================
def test_parametros_conservan_mayusculas():
    assert interpretar("Busca el tiempo en Sevilla")["query"] == "el tiempo en Sevilla"
    assert interpretar("Escribe Hola Mundo")["text"] == "Hola Mundo"
    assert interpretar("Recuerda que mi perro se llama Toby")["target"] == "mi perro se llama Toby"


# ==========================================
# PALABRA DE ACTIVACIÓN Y SIGNOS (salida típica de Whisper)
# ==========================================
@pytest.mark.parametrize("frase", [
    "Jarvis, abre Spotify.",
    "jarvis abre spotify",
    "Oye Jarvis, abre Spotify",
    "¡Jarvis! Abre Spotify.",
    "Abre Spotify, Jarvis.",
    "Abre Spotify, por favor.",
])
def test_activacion_y_signos_no_estorban(frase):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("pc", "open_app")
    assert r["target"].lower() == "spotify"


def test_una_y_inicial_no_estorba():
    # Whisper antepone a veces una «Y» a la frase.
    r = interpretar("Y recuerda que me gusta el té.")
    assert (r["intent"], r["target"]) == ("memory_save", "me gusta el té")


def test_la_activacion_final_no_se_come_el_texto_literal():
    assert interpretar("escribe hola, Jarvis")["text"] == "hola, Jarvis"
    assert interpretar("escribe muchas gracias")["text"] == "muchas gracias"


# ==========================================
# PETICIONES NATURALES
# ==========================================
@pytest.mark.parametrize("frase,accion,objetivo", [
    ("¿Puedes abrir Spotify?", "open_app", "Spotify"),
    ("¿me puedes abrir el bloc de notas?", "open_app", "bloc de notas"),
    ("quiero que abras Discord", "open_app", "Discord"),
    ("necesito que me abras chrome", "open_app", "chrome"),
    ("ábreme la calculadora", "open_app", "calculadora"),
    ("podrías cerrar spotify", "close_app", "spotify"),
    ("quiero abrir steam", "open_app", "steam"),
])
def test_peticiones_corteses(frase, accion, objetivo):
    r = interpretar(frase)
    assert r["intent"] == "pc"
    assert r["action"] == accion
    assert r["target"] == objetivo


def test_peticion_de_busqueda():
    r = interpretar("¿podrías buscar recetas de pasta?")
    assert r["intent"] == "search_web"
    assert r["query"] == "recetas de pasta"


@pytest.mark.parametrize("frase", [
    "¿puedes explicarme la relatividad?",
    "quiero un café",
    "no abras spotify",
    "necesito ayuda con mi TFG",
])
def test_peticion_sin_verbo_de_orden_es_chat(frase):
    assert interpretar(frase)["intent"] == "chat"


# ==========================================
# REGRESIONES: falsos positivos de la v2
# ==========================================
@pytest.mark.parametrize("frase", [
    "¿qué tal la playa?",                       # "play" dentro de "playa"
    "saludos desde Valencia",                   # "sal" dentro de "saludos"
    "hoy necesito descansar un rato",           # "descansa" dentro de "descansar"
    "mi hermano busca trabajo",                 # "busca" en mitad de la frase
    "recuerdo que de pequeña iba al pueblo",    # "recuerdo" corregido a "recuerda"
    "abril es mi mes favorito",                 # "abril" corregido a "abrir"
    "cerca de mi casa hay un parque",           # "cerca" corregido a "cerrar"
    "un regalo para mi madre",                  # "para" como orden de parada
    "olvídalo todo, era broma",
    "el tiempo es oro",
    "el silenciador del coche hace ruido",
])
def test_frases_normales_no_disparan_acciones(frase):
    r = interpretar(frase)
    assert r["intent"] == "chat", f"{frase!r} se interpretó como {r['intent']}/{r['action']}"


def test_recuerdas_no_se_convierte_en_guardar():
    r = interpretar("¿recuerdas algo de mi trabajo?")
    assert (r["intent"], r["action"]) == ("memory_query", "query")
    assert r["query"] == "mi trabajo"


def test_secuencia_conserva_el_nombre_completo():
    # La v2 partía por la letra "y": "spotify" quedaba como "spotif".
    r = interpretar("abre spotify y busca lo-fi beats")
    assert r["steps"][0]["target"] == "spotify"
    assert r["steps"][1]["query"] == "lo-fi beats"


def test_secuencia_de_tres_pasos():
    r = interpretar("abre el bloc de notas, luego escribe hola y después pulsa intro")
    assert r["intent"] == "multi"
    assert [p["action"] for p in r["steps"]] == ["open_app", "type_text", "press_key"]


def test_texto_con_y_no_se_parte():
    r = interpretar("escribe hola y adiós")
    assert r["action"] == "type_text"
    assert r["text"] == "hola y adiós"


# ==========================================
# SISTEMA: parada y apagado de JARVIS
# ==========================================
@pytest.mark.parametrize("frase", ["para", "cállate", "silencio", "basta ya", "Jarvis, para", "stop"])
def test_parada(frase):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("system", "stop")


@pytest.mark.parametrize("frase", ["hasta luego Jarvis", "adiós jarvis", "cierra jarvis", "apágate ya"])
def test_apagar_jarvis(frase):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("system", "shutdown_jarvis")


# ==========================================
# AJUSTES
# ==========================================
@pytest.mark.parametrize("frase,modo", [
    ("modo voz", "voz"), ("activa el modo texto", "texto"), ("cambia a modo voz", "voz"),
    ("pasa al modo escrito", "texto"),
])
def test_modo_voz_texto(frase, modo):
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("settings", "set_mode", modo)


def test_cambio_de_idioma_no_es_editar_memoria():
    r = interpretar("cambia el idioma a inglés")
    assert (r["intent"], r["action"], r["target"]) == ("settings", "set_language", "ingles")


def test_nueva_conversacion():
    assert interpretar("nueva conversación")["intent"] == "conversation"


def test_cambiar_palabra_de_seguridad():
    r = interpretar("cambia la palabra de seguridad a tormenta")
    assert (r["intent"], r["action"], r["text"]) == ("security_setup", "set_word", "tormenta")


def test_quitar_palabra_de_seguridad():
    r = interpretar("quita la palabra de seguridad")
    assert (r["intent"], r["action"]) == ("security_setup", "set_confirm_mode")


# ==========================================
# MEMORIA E HISTORIAL
# ==========================================
def test_borrar_toda_la_memoria():
    r = interpretar("borra todos tus recuerdos")
    assert (r["intent"], r["action"]) == ("memory_delete", "delete_all")


def test_borrar_historial():
    r = interpretar("borra el historial")
    assert (r["intent"], r["action"]) == ("history", "delete_all")


# ==========================================
# HORA, FECHA Y TIEMPO
# ==========================================
@pytest.mark.parametrize("frase,accion", [
    ("¿qué hora es?", "time"), ("dime qué hora es", "time"), ("Jarvis, ¿me dices la hora?", "time"),
    ("¿qué día es hoy?", "date"), ("¿a qué estamos hoy?", "date"), ("¿puedes decirme qué día es?", "date"),
])
def test_hora_y_fecha(frase, accion):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("info", accion)


@pytest.mark.parametrize("frase,accion,ciudad", [
    ("Jarvis, ¿qué tiempo hace hoy en Sevilla?", "today", "Sevilla"),
    ("¿qué tiempo hace?", "today", ""),
    ("qué tiempo hará mañana en Valencia", "tomorrow", "Valencia"),
    ("el tiempo en Madrid", "today", "Madrid"),
    ("¿va a llover mañana?", "tomorrow", ""),
    ("dime qué temperatura hace en Sevilla", "today", "Sevilla"),
    ("¿cómo está el tiempo en San Sebastián?", "today", "San Sebastián"),
])
def test_tiempo(frase, accion, ciudad):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("weather", accion)
    assert r["target"] == ciudad


# ==========================================
# INTERNET
# ==========================================
def test_busqueda_en_un_sitio():
    r = interpretar("busca gatos graciosos en YouTube")
    assert (r["intent"], r["action"], r["target"]) == ("search_web", "site", "youtube")
    assert r["query"] == "gatos graciosos"


@pytest.mark.parametrize("frase,consulta", [
    ("¿qué noticias hay de tecnología?", "tecnología"),
    ("últimas noticias sobre inteligencia artificial", "inteligencia artificial"),
    ("dime las noticias", "noticias de hoy"),
])
def test_noticias(frase, consulta):
    r = interpretar(frase)
    assert (r["intent"], r["action"]) == ("search_web", "news")
    assert r["query"] == consulta


def test_reproducir_algo_concreto():
    # «reproduce X» pone X de verdad (antes solo abría la búsqueda de YouTube).
    r = interpretar("reproduce lo-fi beats")
    assert (r["intent"], r["action"]) == ("music", "play_song")
    assert r["query"] == "lo-fi beats"


# ==========================================
# CONTROL DEL PC
# ==========================================
@pytest.mark.parametrize("frase,comando", [
    ("apaga el ordenador", "apagar"), ("reinicia el pc", "reiniciar"), ("bloquea la pantalla", "bloquear"),
    ("cierra sesión", "cerrar_sesion"), ("activa el modo oscuro", "modo_oscuro"), ("modo claro", "modo_claro"),
    ("haz una captura de pantalla", "captura"), ("hazme una captura", "captura"),
    ("vacía la papelera", "vaciar_papelera"), ("cancela el apagado", "cancelar_apagado"),
    ("suspende el equipo", "suspender"), ("muestra el escritorio", "mostrar_escritorio"),
])
def test_comandos_de_sistema(frase, comando):
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("pc", "system_command", comando)


def test_apagar_el_sistema_sigue_siendo_apagar_a_jarvis():
    assert interpretar("apaga el sistema")["action"] == "shutdown_jarvis"


def test_abrir_quita_el_articulo():
    assert interpretar("abre la carpeta de descargas")["target"] == "carpeta de descargas"
    assert interpretar("abre el navegador")["target"] == "navegador"


def test_escribir_en_una_aplicacion():
    r = interpretar("escribe Lista de la compra en el bloc de notas")
    assert (r["action"], r["text"], r["target"]) == ("type_text", "Lista de la compra", "bloc de notas")


def test_escribir_con_en_que_no_es_aplicacion():
    r = interpretar("escribe estoy en casa")
    assert (r["text"], r["target"]) == ("estoy en casa", "")


@pytest.mark.parametrize("frase,tecla", [
    ("pulsa control c", "ctrl+c"), ("pulsa alt tab", "alt+tab"), ("presiona la tecla suprimir", "delete"),
    ("pulsa control + mayúsculas + escape", "ctrl+shift+esc"),
])
def test_combinaciones_de_teclas(frase, tecla):
    assert interpretar(frase)["key"] == tecla


@pytest.mark.parametrize("frase,objetivo", [
    ("pon la siguiente canción", "siguiente"), ("pasa de canción", "siguiente"),
    ("sube un poco el volumen", "subir_volumen"), ("quita el sonido", "silenciar"),
])
def test_multimedia_natural(frase, objetivo):
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("pc", "media", objetivo)


def test_pon_una_aplicacion():
    r = interpretar("pon Netflix")
    assert (r["intent"], r["action"], r["target"]) == ("pc", "open_app", "Netflix")
