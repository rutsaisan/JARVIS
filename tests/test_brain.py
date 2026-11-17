"""
Pruebas de la personalidad y del prompt del núcleo conversacional (sin llamar a Ollama).

Ejecutar con:  pytest tests/test_brain.py -v
"""
import os
import shutil
import tempfile

import pytest

from core.brain import Cerebro
from core.config import AJUSTES_POR_DEFECTO, Ajustes
from core.personality import FRASES, frase
from voice import speaker


@pytest.fixture
def ajustes():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_cerebro_")
    yield Ajustes(os.path.join(carpeta, "ajustes.json"))
    shutil.rmtree(carpeta, ignore_errors=True)


# ==========================================
# PROMPT DEL SISTEMA
# ==========================================
def test_prompt_incluye_nombre_idioma_y_separacion_de_acciones(ajustes):
    ajustes.establecer("usuario.nombre", "Ruth")
    prompt = Cerebro(ajustes).prompt_sistema()
    assert "Ruth" in prompt
    assert "español de España" in prompt
    assert "Tú solo conversas" in prompt            # el modelo no ejecuta acciones
    assert "Fecha y hora actuales" in prompt


def test_el_sarcasmo_es_ajustable(ajustes):
    cerebro = Cerebro(ajustes)
    assert "SARCÁSTICA" in cerebro.prompt_sistema()                 # por defecto
    ajustes.establecer("personalidad.sarcasmo", 0)
    assert "SARCÁSTICA" not in cerebro.prompt_sistema()
    assert "profesional" in cerebro.prompt_sistema()


def test_en_modo_voz_se_pide_texto_sin_formato(ajustes):
    cerebro = Cerebro(ajustes)
    assert "en voz alta" in cerebro.prompt_sistema(modo="voz")
    assert "en voz alta" not in cerebro.prompt_sistema(modo="texto")


def test_los_recuerdos_solo_aparecen_si_los_hay(ajustes):
    cerebro = Cerebro(ajustes)
    assert "Datos que recuerdas" not in cerebro.prompt_sistema()
    assert "le gusta el té" in cerebro.prompt_sistema(contexto_memoria="le gusta el té")


# ==========================================
# VOZ FEMENINA POR DEFECTO
# ==========================================
def test_la_voz_es_femenina_por_defecto(ajustes):
    assert AJUSTES_POR_DEFECTO["voz"]["genero"] == "femenino"
    assert speaker.VOZ_EDGE_POR_GENERO[speaker._genero(ajustes)] == "es-ES-ElviraNeural"
    assert "voz femenina" in Cerebro(ajustes).prompt_sistema()


def test_se_puede_cambiar_a_voz_masculina(ajustes):
    ajustes.establecer("voz.genero", "masculino")
    assert speaker.VOZ_EDGE_POR_GENERO[speaker._genero(ajustes)] == "es-ES-AlvaroNeural"
    assert "voz masculina" in Cerebro(ajustes).prompt_sistema()
    with pytest.raises(ValueError):
        ajustes.establecer("voz.genero", "robot")


# ==========================================
# FRASES HECHAS
# ==========================================
def test_las_frases_neutras_son_estables():
    assert frase("abierto", 0, nombre="Ruth", objeto="Spotify") == "He abierto Spotify."
    assert frase("activacion", 0, nombre="Ruth") == "Sí, Ruth."


@pytest.mark.parametrize("clave", sorted(FRASES))
def test_todas_las_frases_se_pueden_formar(clave):
    datos = dict(nombre="Ruth", objeto="Spotify", consulta="lo-fi", sitio="YouTube", categoria="preferencias",
                 hora="21:34", fecha="sábado, 24 de mayo de 2025", contacto="elPixel12", app="Discord")
    for sarcasmo in (0, 1, 2, 3):
        texto = frase(clave, sarcasmo, **datos)
        assert texto and "{" not in texto                # ninguna plantilla queda sin rellenar


def test_las_frases_no_tratan_de_usted():
    # La interfaz y el modelo tutean; las frases hechas también.
    for neutra, sarcasticas in FRASES.values():
        for texto in [neutra, *sarcasticas]:
            assert " usted" not in texto.lower() and "dígame" not in texto.lower()


# ==========================================
# HONESTIDAD Y FUNCIONES DEL MODELO (con un Ollama simulado)
# ==========================================
class OllamaFalso:
    """Responde por rondas: cada ronda es la lista de trozos que «envía» el modelo."""

    def __init__(self, rondas, capacidades=("completion", "tools")):
        self.rondas = list(rondas)
        self.capacidades = list(capacidades)
        self.peticiones = []

    def show(self, modelo):
        return {"capabilities": self.capacidades}

    def chat(self, **peticion):
        # Copia de los mensajes: JARVIS sigue añadiendo a la misma lista en las rondas siguientes.
        self.peticiones.append(dict(peticion, messages=list(peticion["messages"])))
        return iter(self.rondas.pop(0))


class FuncionesFalsas:
    def __init__(self):
        self.ejecutadas = []

    def definiciones(self):
        return [{"type": "function", "function": {"name": "que_esta_sonando", "description": "Qué suena.",
                                                  "parameters": {"type": "object", "properties": {}}}}]

    def ejecutar(self, nombre, argumentos):
        self.ejecutadas.append((nombre, argumentos))
        return "Ahora suena «MONEY MACHINE» de Nicki Nicole en Spotify."


def trozo(texto="", llamadas=None):
    mensaje = {"content": texto}
    if llamadas:
        mensaje["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in llamadas]
    return {"message": mensaje}


def con_ollama(ajustes, cliente):
    cerebro = Cerebro(ajustes)
    cerebro._cliente, cerebro._host_cliente = cliente, ajustes.obtener("ia.host")
    return cerebro


def test_la_honestidad_es_una_regla_del_prompt(ajustes):
    cerebro = Cerebro(ajustes)
    for con_funciones in (False, True):
        prompt = cerebro.prompt_sistema(con_funciones=con_funciones)
        assert "nunca inventes datos" in prompt
        assert "Nunca digas que has hecho algo" in prompt
    con = cerebro.prompt_sistema(con_funciones=True)
    assert "llama SIEMPRE a buscar_en_internet" in con and "Tú solo conversas" not in con
    assert "No pongas en la búsqueda un año" in con               # buscaba «mundial 2022» por su cuenta
    assert "no des datos ni cifras de memoria" in con and "tal como aparecen" in con
    assert "te cuenta algo de su vida" in con and "«estudio DAM»" in con   # no es una pregunta


def test_el_modelo_usa_una_funcion_y_responde_con_lo_que_devuelve(ajustes):
    cliente = OllamaFalso([[trozo(llamadas=[("que_esta_sonando", {})])],
                           [trozo("Suena MONEY "), trozo("MACHINE.")]])
    cerebro, funciones, fragmentos = con_ollama(ajustes, cliente), FuncionesFalsas(), []
    respuesta = cerebro.pensar("¿qué suena?", funciones=funciones, al_fragmento=fragmentos.append)
    assert respuesta == "Suena MONEY MACHINE." and "".join(fragmentos) == respuesta
    assert funciones.ejecutadas == [("que_esta_sonando", {})]
    primera, segunda = cliente.peticiones
    assert primera["tools"][0]["function"]["name"] == "que_esta_sonando"
    assert "buscar_en_internet" in primera["messages"][0]["content"]
    assert segunda["messages"][-2]["tool_calls"][0]["function"]["name"] == "que_esta_sonando"
    assert segunda["messages"][-1] == {"role": "tool", "tool_name": "que_esta_sonando",
                                       "content": "Ahora suena «MONEY MACHINE» de Nicki Nicole en Spotify."}


def test_las_rondas_de_funciones_tienen_limite(ajustes):
    from core.brain import AVISO_ULTIMA_RONDA, MAXIMO_RONDAS
    pide = [trozo(llamadas=[("que_esta_sonando", {})])]
    cliente = OllamaFalso([pide] * MAXIMO_RONDAS + [[trozo("Vale.")]])
    funciones = FuncionesFalsas()
    assert con_ollama(ajustes, cliente).pensar("hola", funciones=funciones) == "Vale."
    assert len(funciones.ejecutadas) == MAXIMO_RONDAS
    assert "tools" not in cliente.peticiones[-1]          # en la última ronda ya tiene que responder
    assert cliente.peticiones[-1]["messages"][-1] == {"role": "system", "content": AVISO_ULTIMA_RONDA}
    assert all(m.get("content") != AVISO_ULTIMA_RONDA for m in cliente.peticiones[0]["messages"])


def test_un_modelo_sin_herramientas_solo_conversa(ajustes):
    cliente = OllamaFalso([[trozo("Hola.")]], capacidades=("completion",))
    assert con_ollama(ajustes, cliente).pensar("hola", funciones=FuncionesFalsas()) == "Hola."
    assert "tools" not in cliente.peticiones[0]
    assert "Tú solo conversas" in cliente.peticiones[0]["messages"][0]["content"]


def test_los_modelos_que_razonan_no_hacen_esperar(ajustes):
    cliente = OllamaFalso([[trozo("Hola.")]], capacidades=("completion", "tools", "thinking"))
    con_ollama(ajustes, cliente).pensar("hola", funciones=FuncionesFalsas())
    assert cliente.peticiones[0]["think"] is False


def test_extraer_datos_para_aprender(ajustes):
    ajustes.establecer("usuario.nombre", "Ruth")
    cliente = OllamaFalso([[trozo('```json\n["me gusta el rock", "tengo un gato"]\n```')]])
    datos = con_ollama(ajustes, cliente).extraer_datos("me encanta el rock y tengo un gato", "Ruth")
    assert datos == ["me gusta el rock", "tengo un gato"]
    peticion = cliente.peticiones[0]
    assert peticion["options"]["temperature"] == 0.0 and "Ruth" in peticion["messages"][0]["content"]
    assert "dónde vive, qué estudia" in peticion["messages"][0]["content"]
    assert "tools" not in peticion


def test_interpretar_la_lista_de_datos():
    from core.brain import lista_de_textos
    assert lista_de_textos('Aquí tienes: ["me gusta el rock", "Me gusta el rock.", "vivo en  Valencia"]') == \
        ["me gusta el rock", "vivo en Valencia"]
    assert lista_de_textos("[]") == [] and lista_de_textos("No hay datos.") == []
    assert lista_de_textos("[roto") == [] and lista_de_textos('{"a": 1}') == []
    assert lista_de_textos('["ok", "' + "x" * 200 + '"]') == []           # ni demasiado corto ni largo
    assert lista_de_textos("[“me gusta el té”]") == ["me gusta el té"]     # comillas tipográficas


class FuncionesConPrevia(FuncionesFalsas):
    previas = [("buscar_en_internet", {"consulta": "Nicki Nicole"},
                "- Nicki Nicole (es.wikipedia.org): cantante argentina nacida en Rosario.")]


def test_lo_buscado_antes_llega_como_si_lo_hubiera_pedido_el_modelo(ajustes):
    cliente = OllamaFalso([[trozo("Es argentina, de Rosario.")]])
    respuesta = con_ollama(ajustes, cliente).pensar("¿la conoces?", funciones=FuncionesConPrevia())
    assert respuesta == "Es argentina, de Rosario."
    mensajes = cliente.peticiones[0]["messages"]
    assert mensajes[-3] == {"role": "user", "content": "¿la conoces?"}
    assert mensajes[-2] == {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "buscar_en_internet", "arguments": {"consulta": "Nicki Nicole"}}}]}
    assert mensajes[-1] == {"role": "tool", "tool_name": "buscar_en_internet",
                            "content": "- Nicki Nicole (es.wikipedia.org): cantante argentina nacida en Rosario."}


def test_un_modelo_sin_herramientas_recibe_lo_buscado_como_texto(ajustes):
    cliente = OllamaFalso([[trozo("Es argentina.")]], capacidades=("completion",))
    con_ollama(ajustes, cliente).pensar("¿la conoces?", funciones=FuncionesConPrevia())
    ultimo = cliente.peticiones[0]["messages"][-1]
    assert ultimo["role"] == "system" and "«Nicki Nicole»" in ultimo["content"]
    assert "cantante argentina" in ultimo["content"] and "tools" not in cliente.peticiones[0]


def test_un_modelo_que_razona_no_razona_al_extraer_datos(ajustes):
    # Sin haber conversado antes: también hay que desactivar el razonamiento (qwen3 lo hace por defecto).
    cliente = OllamaFalso([[trozo('["me gusta el rock"]')]], capacidades=("completion", "tools", "thinking"))
    assert con_ollama(ajustes, cliente).extraer_datos("me encanta el rock", "Ruth") == ["me gusta el rock"]
    assert cliente.peticiones[0]["think"] is False
