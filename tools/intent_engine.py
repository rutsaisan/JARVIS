"""
Motor de intenciones de JARVIS (v3)
====================================
Analizador de lenguaje natural basado en patrones (RegEx), de latencia cero.
No usa el modelo de lenguaje: es la capa rápida que decide SI una frase es una
acción real (abrir, buscar, memoria, sistema...) o si debe pasar al núcleo
conversacional. Así la conversación y las acciones quedan separadas: el modelo
nunca ejecuta nada; solo este motor, sobre lo que dice la usuaria, decide qué
herramienta se lanza.

Novedades respecto a la v2:
  - Los parámetros conservan las mayúsculas y acentos originales
    ("busca el tiempo en Sevilla" -> query "el tiempo en Sevilla").
  - Entiende peticiones naturales ("¿puedes abrir Spotify?", "quiero que
    busques...", "ábreme el bloc de notas, por favor").
  - Intenciones nuevas: tiempo, hora/fecha, comandos de sistema, noticias,
    búsqueda en un sitio, modo voz/texto, historial y parada de emergencia.
  - Corregidos falsos positivos por subcadenas ("playa" ya no pulsa play,
    "saludos" ya no cierra ventanas, "descansar" ya no apaga a JARVIS...).
  - Las secuencias se parten por el conector completo (" y "), no por la letra.

Esquema de intención devuelto por `interpretar()`:
{
    "intent":     str,   # system | security_setup | settings | conversation | history
                          # | memory_save | memory_query | memory_edit | memory_delete
                          # | info | weather | search_web | pc | multi | chat
    "action":     str,   # acción concreta dentro de la categoría
    "target":     str,   # aplicación / ciudad / objetivo de la acción
    "query":      str,   # consulta de búsqueda o de memoria
    "text":       str,   # texto literal a escribir / nuevo valor a guardar
    "key":        str,   # tecla (o combinación) a pulsar
    "steps":      list,  # sub-intenciones si intent == "multi"
    "confidence": float, # 0.0 (sin coincidencia) a 1.0 (coincidencia exacta)
    "raw":        str,   # frase ya preparada que generó esta intención
}
"""
from __future__ import annotations

import difflib
import logging
import re

from core.text import plano

registro = logging.getLogger("jarvis.intenciones")

UMBRAL_CONFIANZA_MINIMO = 0.45


# ==========================================
# FRASE: texto original + versión «plana» con los mismos índices
# ==========================================

def _limpiar(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()
    return texto.strip(" .,;:").strip()


def _objetivo(texto: str) -> str:
    """Limpia un parámetro (app, ciudad, consulta): sin signos sueltos en los extremos."""
    return _limpiar(texto.strip(" ¿?¡!\"'«»"))


class _Frase:
    """Los patrones se buscan en `.plano` (minúsculas, sin acentos) y los
    parámetros se recortan de `.texto`, que conserva la escritura original."""
    __slots__ = ("texto", "plano")

    def __init__(self, texto: str):
        self.texto = _limpiar(texto)
        self.plano = plano(self.texto)

    def __bool__(self) -> bool:
        return bool(self.texto)

    def empieza(self, prefijo: str) -> bool:
        return self.plano == prefijo or self.plano.startswith(prefijo + " ")

    def desde(self, indice: int) -> "_Frase":
        return _Frase(self.texto[indice:])

    def grupo(self, coincidencia, numero: int = 1) -> str:
        inicio, fin = coincidencia.span(numero)
        return _limpiar(self.texto[inicio:fin]) if inicio >= 0 else ""


def _intencion(**campos) -> dict:
    base = {
        "intent": "chat", "action": "unknown", "target": "", "query": "",
        "text": "", "key": "", "steps": [], "confidence": 0.0, "raw": "",
    }
    base.update(campos)
    return base


# ==========================================
# PREPARACIÓN: de la frase natural a la orden canónica
# ==========================================

_ACTIVACION_INICIO = re.compile(
    r"^(?:(?:hola|oye|ey|hey|eh|ok|okay|vale|venga|buenas|buenos dias|buenas tardes|buenas noches)[,\s]+)?"
    r"jarvis?\b[\s,.:;!]*"
)
_ACTIVACION_FINAL = re.compile(r"\s*,\s*jarvis?[\s.!?]*$")

_RELLENO_INICIO = re.compile(
    r"^(?:(?:por favor|porfavor|porfa|porfi|oye|venga|anda|a ver|mira|bueno|pues|vale|ok|okay|eh|ey|hey|"
    r"entonces|ahora|y|e)[,\s]+)+"
)
_RELLENO_FINAL = re.compile(
    r"(?:[,\s]+(?:por favor|porfavor|porfa|porfi|gracias|si puedes|cuando puedas|si no te importa))+[\s.!?]*$"
)

# Verbos cuyo complemento es texto literal: ahí no se recorta nada del final.
_VERBOS_LITERALES = re.compile(
    r"^(?:(?:en|por|desde)\s+(?:discord|whatsapp|telegram|slack|teams)[\s,]+)?"
    r"(?:escribe|escribir|teclea|teclear|dicta|recuerda|memoriza|guarda|apunta|anota|no olvides|ten en cuenta|"
    r"busca|buscar|investiga|olvida|escribele|escribeles|mandale|mandales|enviale|enviales|dile|diles|manda|envia)\b"
)

_PETICION = re.compile(
    r"^(?:(?:me|nos)\s+)?"
    r"(?:puedes|podes|podrias|puede|podria|podeis|quieres|queres|necesito que|quiero que|querria que|me gustaria que|"
    r"te importa|te importaria|seria posible|serias tan amable de|haz el favor de|hazme el favor de|"
    r"te pido que|tienes que|deberias|a ver si puedes|quiero|necesito|me gustaria|quisiera|querria)"
    r"\s+(?:(?:por favor|porfa)\s+)?(.+)$"
)

# imperativo: (infinitivo, subjuntivo de «tú»)
_VERBOS = {
    "abre": ("abrir", "abras"), "cierra": ("cerrar", "cierres"), "busca": ("buscar", "busques"),
    "investiga": ("investigar", "investigues"), "escribe": ("escribir", "escribas"),
    "teclea": ("teclear", "teclees"), "recuerda": ("recordar", "recuerdes"),
    "memoriza": ("memorizar", "memorices"), "guarda": ("guardar", "guardes"),
    "apunta": ("apuntar", "apuntes"), "anota": ("anotar", "anotes"), "olvida": ("olvidar", "olvides"),
    "borra": ("borrar", "borres"), "elimina": ("eliminar", "elimines"), "cambia": ("cambiar", "cambies"),
    "modifica": ("modificar", "modifiques"), "actualiza": ("actualizar", "actualices"),
    "corrige": ("corregir", "corrijas"), "pulsa": ("pulsar", "pulses"), "presiona": ("presionar", "presiones"),
    "pon": ("poner", "pongas"), "sube": ("subir", "subas"), "baja": ("bajar", "bajes"),
    "silencia": ("silenciar", "silencies"), "pausa": ("pausar", "pauses"),
    "reproduce": ("reproducir", "reproduzcas"), "apaga": ("apagar", "apagues"),
    "reinicia": ("reiniciar", "reinicies"), "suspende": ("suspender", "suspendas"),
    "bloquea": ("bloquear", "bloquees"), "ejecuta": ("ejecutar", "ejecutes"), "inicia": ("iniciar", "inicies"),
    "lanza": ("lanzar", "lances"), "arranca": ("arrancar", "arranques"), "di": ("decir", "digas"),
    "muestra": ("mostrar", "muestres"), "ensena": ("ensenar", "ensenes"), "lista": ("listar", "listes"),
    "haz": ("hacer", "hagas"), "activa": ("activar", "actives"), "desactiva": ("desactivar", "desactives"),
    "quita": ("quitar", "quites"), "vacia": ("vaciar", "vacies"), "toma": ("tomar", "tomes"),
    "saca": ("sacar", "saques"), "consulta": ("consultar", "consultes"), "cancela": ("cancelar", "canceles"),
    "sal": ("salir", "salgas"),
}

_DESDE_INFINITIVO, _DESDE_SUBJUNTIVO, _DESDE_CLITICO = {}, {}, {}
for _imperativo, (_infinitivo, _subjuntivo) in _VERBOS.items():
    _DESDE_INFINITIVO[_infinitivo] = _imperativo
    _DESDE_INFINITIVO[_infinitivo + "me"] = _imperativo      # abrirme, decirme
    _DESDE_INFINITIVO[_infinitivo + "nos"] = _imperativo
    _DESDE_SUBJUNTIVO[_subjuntivo] = _imperativo
    _DESDE_CLITICO[_imperativo + "me"] = _imperativo         # ábreme, búscame, dime
_DESDE_INFINITIVO.update({"saber": "di", "escuchar": "reproduce", "oir": "reproduce"})

# Infinitivos que valen como orden aunque abran la frase ("abrir Spotify").
# Los de memoria quedan fuera a propósito: "recordar es vivir" no es una orden.
_INFINITIVOS_INICIO = {
    "abrir", "cerrar", "ejecutar", "iniciar", "lanzar", "arrancar", "escribir", "teclear", "buscar",
    "investigar", "reiniciar", "apagar", "bloquear", "suspender", "silenciar", "pausar", "reproducir",
}

# Voseo («abrí», «poné», «reproducí», «¿podés…?») y formas que Whisper transcribe así:
# pasó con «podés reproducir la música» y «Abrís, Spotify», que acababan en la IA.
_VOSEO = {
    "abri": "abre", "abris": "abre", "abrime": "abre", "cerra": "cierra", "cerras": "cierra", "cerrame": "cierra",
    "buscas": "busca", "pone": "pon", "pones": "pon", "poneme": "pon", "reproduci": "reproduce",
    "reproducis": "reproduce", "reproducime": "reproduce", "escribi": "escribe", "escribis": "escribe",
    "escribime": "escribe", "decime": "di", "deci": "di", "mostrame": "muestra", "mostra": "muestra",
    "subi": "sube", "subis": "sube", "bajas": "baja", "apagas": "apaga", "apagame": "apaga", "pausas": "pausa",
    "acordate": "recuerda", "anotame": "anota", "guardame": "guarda",
    "escribile": "escribele", "decile": "dile", "mandale": "mandale",
}

# «Quiero que le escribas a Pepe…», «¿puedes decirle a Pepe…?»: a la forma de mensaje («escríbele…»).
_A_FORMA_DE_MENSAJE = {
    "le escribas": "escribele", "les escribas": "escribeles", "le mandes": "mandale", "les mandes": "mandales",
    "le envies": "enviale", "les envies": "enviales", "le digas": "dile", "les digas": "diles",
    "escribirle": "escribele", "escribirles": "escribeles", "mandarle": "mandale", "mandarles": "mandales",
    "enviarle": "enviale", "enviarles": "enviales", "decirle": "dile", "decirles": "diles",
    "avisar": "avisa", "avisarle": "avisale", "le avises": "avisale", "avises": "avisa",
}

# «¿Me abres Spotify?», «¿me pones música?», «¿me abrís…?»: presente usado como petición.
_PRESENTE_TU = {imperativo + "s": imperativo for imperativo in _VERBOS if imperativo.endswith(("a", "e"))}
_PRESENTE_TU.update({"pones": "pon", "dices": "di", "haces": "haz"})
_PETICION_PRESENTE = re.compile(r"^(?:me|nos)\s+([a-z]+)\b")

_PREGUNTA_INDIRECTA = re.compile(
    r"^(?:di|dime|decirme|sabes|me dices|quiero saber|necesito saber)\s+"
    r"(?=(?:que|cual|cuales|cuanto|cuanta|cuantos|cuantas|como|donde|quien|quienes|cuando|si|a que|a cuantos|"
    r"la hora|el tiempo|las noticias|las ultimas noticias)\b)"
)

# --- Corrección de errores leves de transcripción en el verbo inicial ---
VERBOS_CONOCIDOS = [
    "abre", "abrir", "ejecuta", "ejecutar", "inicia", "iniciar", "lanza", "lanzar", "arranca", "arrancar",
    "cierra", "cerrar", "escribe", "escribir", "teclea", "busca", "buscar", "investiga", "investigar",
    "recuerda", "recordar", "memoriza", "memorizar", "guarda", "guardar", "apunta", "anota",
    "olvida", "olvidar", "borra", "borrar", "elimina", "eliminar",
    "cambia", "modifica", "actualiza", "corrige", "establece", "configura", "fija",
    "pulsa", "presiona", "reproduce", "reproducir", "pausa", "pausar", "sube", "baja", "silencia",
    "reanuda", "continua",               # sin ellas, «continúa» se «corregía» a «configura»
    "apaga", "reinicia", "bloquea", "suspende",
    "juega", "jugar", "jugamos",
]

# Palabras corrientes que se parecen a un verbo de orden y NO deben corregirse
# ("abril" no es "abrir", "recuerdo que..." no es "recuerda que...").
_NO_CORREGIR = frozenset("""
abril abro abres abria abrio abierto cierro cierras cierto cierta cerca cerro busco buscas buscan buscaba
buscando busque recuerdo recuerdas recuerdan recuerdos recorde olvido olvide olvidas borro borre borde guardo
guapa guapo guardia apunto apunte anoto anote anoche cambio cambie cama modifico actualizo corrijo corre corri
configuro fijo fijate establezco pulso presion presiono reproduzco pauso pausado paso pasa pasar pase subo
sabe sabes sobre bajo bala silencio escribo escribes escrito escribia teclado tecla teclas ejecuto inicio
inicie lanzo arranco elimino investigo memorizo memoria apago apagado apagon reinicio bloqueo suspendo
suspenso basta bastante bueno buena buenas buenos para pero pongo pone ponen listo claro vale hola
""".split())

_VERBO_CON_PRONOMBRE = re.compile(
    r"(?:" + "|".join(sorted(VERBOS_CONOCIDOS, key=len, reverse=True)) + r")"
    r"(?:me|te|se|nos|le|les|lo|la|los|las|melo|mela|selo|sela)"
)

# Primeras palabras que ya forman parte de algún patrón (tampoco se corrigen).
_PRIMERAS_CONOCIDAS = frozenset("""
que cual cuales como donde cuando quien mi mis la el las los un una usa solo prefiero sin modo palabra
establece configura fija tienes consulta muestra ensena lista di dime no ten nueva empieza pon haz toma saca
vacia quita activa desactiva sal descansa apagate duermete cierrate calla callate detente stop cancela
siguiente anterior proxima mas menos va llueve llovera hace noticias ultimas tiempo clima temperatura
""".split())


_PALABRAS_DE_ORDEN = (set(VERBOS_CONOCIDOS) | set(_VERBOS) | set(_DESDE_INFINITIVO)
                      | set(_DESDE_CLITICO) | set(_VOSEO))


def _quitar_coma_tras_verbo(frase: _Frase) -> _Frase:
    """«Abre, Spotify» -> «Abre Spotify». Whisper escribe como coma la pausa tras el verbo."""
    coincidencia = re.match(r"^([a-z]+)\s*,\s+", frase.plano)
    if coincidencia and coincidencia.group(1) in _PALABRAS_DE_ORDEN:
        return _Frase(frase.texto[:coincidencia.end(1)] + " " + frase.texto[coincidencia.end():])
    return frase


def _quitar_signos(frase: _Frase) -> _Frase:
    """«¿Qué hora es?» -> «Qué hora es». Solo si la frase entera va entre signos."""
    if frase.texto[:1] in ("¿", "¡"):
        return _Frase(frase.texto[1:].rstrip(" ?!"))
    return frase


def _quitar_activacion(frase: _Frase) -> _Frase:
    coincidencia = _ACTIVACION_INICIO.match(frase.plano)
    if coincidencia:
        frase = frase.desde(coincidencia.end())
    if not _VERBOS_LITERALES.match(frase.plano):
        coincidencia = _ACTIVACION_FINAL.search(frase.plano)
        if coincidencia and coincidencia.start() > 0:
            frase = _Frase(frase.texto[:coincidencia.start()])
    return frase


def _quitar_relleno(frase: _Frase) -> _Frase:
    coincidencia = _RELLENO_INICIO.match(frase.plano)
    if coincidencia and coincidencia.end() < len(frase.plano):
        frase = frase.desde(coincidencia.end())
    if not _VERBOS_LITERALES.match(frase.plano):
        coincidencia = _RELLENO_FINAL.search(frase.plano)
        if coincidencia and coincidencia.start() > 0:
            frase = _Frase(frase.texto[:coincidencia.start()])
    return frase


def _primera_palabra(frase: _Frase) -> tuple:
    coincidencia = re.match(r"^[^\s,;:!?]+", frase.plano)
    return (coincidencia.group(0), coincidencia.end()) if coincidencia else ("", 0)


def _sustituir_primera(frase: _Frase, nueva: str) -> _Frase:
    _, fin = _primera_palabra(frase)
    return _Frase(nueva + frase.texto[fin:])


def _a_imperativo(frase: _Frase) -> _Frase:
    """Convierte peticiones corteses en la orden directa equivalente."""
    pide = _PIDE_RECORDATORIO.match(frase.plano)
    if pide:
        # «¿Puedes recordarme…?» es un recordatorio, no «recuerda» (guardar en la memoria).
        inicio_verbo = pide.start("verbo")
        return _sustituir_primera(frase.desde(inicio_verbo), "avisame" if "avis" in pide.group("verbo") else "recuerdame")
    primera, _ = _primera_palabra(frase)
    coincidencia = _PETICION.match(frase.plano)
    if coincidencia:
        resto = frase.desde(coincidencia.start(1))
        mensaje = re.match(r"^(?:(?:le|les)\s+(?:escribas|mandes|envies|digas|avises)|(?:escribir|mandar|enviar|decir|"
                           r"avisar)les?|avisar(?=\s+a\s)|avises(?=\s+a\s))\b", resto.plano)
        if mensaje:                                                    # "quiero que le escribas a Pepe…"
            return _Frase(_A_FORMA_DE_MENSAJE[mensaje.group(0)] + resto.texto[mensaje.end():])
        pronombre = re.match(r"^(?:me|nos|te)\s+", resto.plano)        # "quiero que me abras…"
        if pronombre:
            resto = resto.desde(pronombre.end())
        verbo, _ = _primera_palabra(resto)
        imperativo = (_DESDE_INFINITIVO.get(verbo) or _DESDE_SUBJUNTIVO.get(verbo)
                      or _DESDE_CLITICO.get(verbo) or _VOSEO.get(verbo) or (verbo if verbo in _VERBOS else None))
        if imperativo:
            frase = _sustituir_primera(resto, imperativo)
    elif primera in _DESDE_CLITICO:
        frase = _sustituir_primera(frase, _DESDE_CLITICO[primera])
    elif primera in _VOSEO:
        frase = _sustituir_primera(frase, _VOSEO[primera])
    elif (presente := _PETICION_PRESENTE.match(frase.plano)) and (
            presente.group(1) in _PRESENTE_TU or presente.group(1) in _VOSEO):
        verbo = presente.group(1)
        resto = frase.desde(presente.start(1))
        frase = _sustituir_primera(resto, _PRESENTE_TU.get(verbo) or _VOSEO[verbo])
    elif primera in _INFINITIVOS_INICIO:
        frase = _sustituir_primera(frase, _DESDE_INFINITIVO[primera])
    elif primera in _A_FORMA_DE_MENSAJE:                               # "escribirle a Pepe…"
        frase = _sustituir_primera(frase, _A_FORMA_DE_MENSAJE[primera])

    coincidencia = _PREGUNTA_INDIRECTA.match(frase.plano)              # "dime qué hora es" -> "qué hora es"
    if coincidencia:
        frase = frase.desde(coincidencia.end())
    return frase


# «¿Puedes recordarme…?», «quiero que me recuerdes…» son recordatorios; «quiero que recuerdes que…»
# (sin «me») es guardar algo en la memoria. Pasó: «quiero que recuerdes que se llama así mi TFG»
# acabó en «¿Para cuándo?».
_PIDE_RECORDATORIO = re.compile(
    r"^(?:(?:puedes|podrias|podes|quiero\s+que|me\s+puedes|necesito\s+que|por\s+favor)\s+)*(?:me\s+)?"
    r"(?P<verbo>recuerdame|recordarme|avisame|avisarme|(?<=me\s)(?:recuerdes|recordaras|avises|avisas))\b")


def _corregir_primera_palabra(frase: _Frase) -> _Frase:
    """Corrige errores leves de transcripción de voz en el verbo inicial ("avre" -> "abre").

    Para no confundir conversación con órdenes, solo se corrige si la frase es
    corta, la palabra tiene al menos 4 letras, comparte inicial con el verbo y
    no es una palabra corriente conocida.
    """
    primera, _ = _primera_palabra(frase)
    if (len(primera) < 4 or primera in VERBOS_CONOCIDOS or primera in _VERBOS
            or primera in _NO_CORREGIR or primera in _PRIMERAS_CONOCIDAS
            or _VERBO_CON_PRONOMBRE.fullmatch(primera)          # "olvídalo", "ciérrala": no son erratas
            or len(frase.plano.split()) > 8):
        return frase
    candidatos = difflib.get_close_matches(primera, VERBOS_CONOCIDOS, n=3, cutoff=0.7)
    candidatos = [c for c in candidatos if c[0] == primera[0]]
    if candidatos:
        return _sustituir_primera(frase, candidatos[0])
    return frase


def _preparar(frase: _Frase) -> _Frase:
    for _ in range(3):
        antes = frase.texto
        frase = _quitar_signos(frase)
        frase = _quitar_activacion(frase)
        if frase.texto == antes:
            break
    frase = _quitar_relleno(frase)
    frase = _quitar_signos(frase)                     # «oye, ¿puedes…?»: el «¿» va detrás de la muletilla
    frase = _quitar_coma_tras_verbo(frase)
    frase = _a_imperativo(frase)
    frase = _quitar_signos(frase)
    return _corregir_primera_palabra(frase)


# ==========================================
# PATRONES (siempre sobre texto plano: minúsculas, sin acentos, ñ -> n)
# ==========================================

_FIN = r"[\s?.!]*$"

_DESPEDIDA = re.compile(
    r"^(?:apagar sistema|apaga el sistema|apagar el sistema|descansa|apagate|duermete|hasta luego jarvis|"
    r"adios jarvis|cierra el sistema|cerrar el sistema|modo reposo total|cierra jarvis|cierrate|apaga jarvis|"
    r"desconectate)(?:\s+(?:ya|ahora|por hoy))?(?:[,\s]+jarvis?)?" + _FIN
)

_PARADA = re.compile(
    r"^(?:para|para ya|para de hablar|deja de hablar|callate|calla|calla ya|silencio|basta|basta ya|detente|"
    r"stop|cancela|cancelar|cancelalo|ya vale|suficiente|olvidalo|dejalo)" + _FIN
)

FRASES_SEGURIDAD_MODO_CONFIRMACION = [plano(f) for f in (
    "usa modo confirmación", "modo confirmación", "solo pídeme confirmación", "pídeme solo confirmación",
    "no quiero palabra de seguridad", "prefiero que me pidas confirmación", "sin palabra de seguridad",
    "quita la palabra de seguridad", "elimina la palabra de seguridad", "desactiva la palabra de seguridad",
    "borra la palabra de seguridad", "usa solo confirmación", "pide solo confirmación",
)]

_SEGURIDAD_PALABRA = re.compile(
    r"^(?:(?:mi|la|una|nueva|pon|ponme|establece|configura|fija|cambia|usa|elige|quiero|quiero que)\s+)?"
    r"(?:(?:la|mi|una|como|de)\s+)?(?:nueva\s+)?palabra de seguridad"
    r"(?:\s+(?:es|sea|sera|va a ser|a|por)(?=\s|:|$))?\s*:?\s*(.*)$"
)

_MODO = re.compile(
    r"^(?:(?:activa|pon|usa|cambia a|cambia al|pasa a|pasa al|entra en|vuelve a|vuelve al)\s+)?(?:el\s+)?"
    r"modo\s+(?:de\s+)?(voz|hablado|texto|escrito|escritura|silencioso|chat)" + _FIN
)
_IDIOMA = re.compile(
    r"^(?:(?:habla|hablame|responde|respondeme|contesta|contestame)\s+en|"
    r"(?:cambia|pon)\s+(?:el\s+|de\s+)?idioma\s+(?:a|al|en))\s+([a-z]+)" + _FIN
)
_CONVERSACION_NUEVA = re.compile(
    r"^(?:nueva conversacion|(?:empieza|inicia|abre|crea) una (?:nueva conversacion|conversacion nueva)|"
    r"empecemos de cero|borra el contexto|reinicia la conversacion|cambiemos de tema)" + _FIN
)
_HISTORIAL_BORRAR = re.compile(
    r"^(?:borra|elimina|limpia|vacia)\s+(?:todo\s+)?(?:(?:el|mi|tu)\s+)?historial(?:\s+de\s+conversaciones)?" + _FIN
    + r"|^(?:borra|elimina)\s+(?:todas\s+)?(?:las|mis|nuestras)\s+conversaciones" + _FIN
)
_CONVERSACION_BORRAR = re.compile(r"^(?:borra|elimina)\s+esta\s+conversacion" + _FIN)

_MEMORIA_EDITAR = re.compile(
    r"^(?:cambia|modifica|actualiza|corrige)\s+"
    r"(?:el recuerdo de\s+|el recuerdo sobre\s+|lo que recuerdas de\s+|lo que sabes de\s+|mi recuerdo de\s+)?"
    r"(.+?)\s+(?:a|por)\s+(.+)$"
)
_MEMORIA_LISTAR = re.compile(
    r"^(?:que (?:recuerdas|sabes) de mi|que recuerdas|"
    r"(?:muestra|muestrame|ensena|ensename|di|dime|lista|listame) (?:todos )?(?:(?:mis|tus|los) )?recuerdos|"
    r"(?:muestra|muestrame|ensena|ensename) (?:tu|la) memoria|"
    r"(?:cuales son|que hay en) (?:mis|tus) recuerdos|"
    r"que tienes (?:apuntado|guardado|memorizado)(?: (?:de|sobre) mi)?)" + _FIN
)
_MEMORIA_BORRAR_TODO = re.compile(
    r"^(?:borra|elimina|olvida)\s+(?:todos\s+)?(?:(?:tus|mis|los)\s+recuerdos|toda\s+(?:tu|la)\s+memoria|"
    r"(?:tu|la)\s+memoria|todo lo que (?:sabes|recuerdas)(?: de mi)?)" + _FIN
)
PREFIJOS_MEMORIA_BORRAR = sorted((plano(p) for p in (
    "olvida que", "olvida lo de", "olvida", "borra el recuerdo de", "borra el recuerdo sobre",
    "borra tus recuerdos sobre", "borra tus recuerdos de", "elimina de tu memoria", "borra de tu memoria",
    "quita de tu memoria", "elimina el recuerdo de", "elimina el recuerdo sobre",
    "deja de recordar que", "deja de recordar",
)), key=len, reverse=True)
PREFIJOS_MEMORIA_CONSULTAR = sorted((plano(p) for p in (
    "qué recuerdas de", "recuerdas algo de", "recuerdas algo sobre", "recuerdas que", "qué sabes sobre",
    "qué sabes de", "tienes algún recuerdo de", "tienes algún recuerdo sobre", "consulta tu memoria sobre",
    "consulta la memoria sobre", "qué recuerdas sobre",
)), key=len, reverse=True)
PREFIJOS_MEMORIA_GUARDAR = sorted((plano(p) for p in (
    "recuerda que", "recuerda", "memoriza que", "memoriza", "guarda que", "guarda", "apunta que", "apunta",
    "anota que", "anota", "no olvides que", "ten en cuenta que", "acuérdate de que",
    "guarda en tu memoria que", "apunta en tu memoria que", "guarda en tu memoria", "apunta en tu memoria",
    "añade a tu memoria que", "añade a tu memoria", "añade en tu memoria que", "agrega a tu memoria que",
    "agrega a tu memoria", "mete en tu memoria que", "guarda en la memoria que", "añade a la memoria que",
)), key=len, reverse=True)
# Lo que hay que guardar no se entiende sin la conversación («guárdalo», «recuerda eso», «que se llama
# así mi TFG») o la frase pide más cosas («…y que busques información sobre él»): lo resuelve la IA,
# que tiene la función guardar_en_memoria.
_CONTENIDO_DIFUSO = re.compile(
    r"^(?:lo|eso|esto|esto ultimo|lo anterior|lo que (?:te |me )?(?:he|has|acabo|acabas|dije|dijiste)|todo eso|"
    r"todo esto)\b|\basi\b|"
    r"\by\s+que\s+(?:busques|leas|mires|abras|me|lo|la|averigues|investigues|guardes)\b|"
    r"\b(?:busca|buscar|lee|leer|mira|mirar)\s+(?:en\s+)?(?:mi\s+ordenador|mis\s+archivos|el\s+ordenador)\b")
# «Recuérdame cómo se llama mi TFG», «recuérdame qué te dije»: es una pregunta a la memoria, no un
# recordatorio (con tilde: «recuérdame que mañana…» sí es un recordatorio).
_PREGUNTA_A_LA_MEMORIA = re.compile(
    r"^(?:recu[eé]rdame|recordarme)\s+(?P<q>(?:qué|cómo|cuál|cuáles|dónde|quién|quiénes|cuándo|cuánto|cuántos|"
    r"cuánta|cuántas)\b.+)$", re.IGNORECASE)

_HORA = re.compile(
    r"^(?:que hora (?:es|son|tenemos)|(?:me dices |dime |di )?la hora|tienes hora|hora actual)" + _FIN
)
_FECHA = re.compile(
    r"^(?:que dia es(?: hoy)?|que fecha es(?: hoy)?|a que (?:dia )?estamos(?: hoy)?|a cuantos estamos(?: hoy)?|"
    r"(?:en )?que (?:dia|fecha) (?:estamos|tenemos)(?: hoy)?|fecha de hoy|que dia de la semana es(?: hoy)?)" + _FIN
)

_DIA = r"(hoy|ahora mismo|ahora|pasado manana|manana|esta tarde|esta noche)"
_TIEMPO_INICIOS = [re.compile(p) for p in (
    r"^(?:que|como)\s+(?:tal\s+)?(?:(?:esta|anda)\s+)?(?:el\s+)?(?:tiempo|clima|temperatura)"
    r"(?:\s+(?:hace|hara|hay|tenemos|va a hacer|esta haciendo|tendremos))?",
    r"^(?:cuantos grados|que temperatura)\s+(?:hace|hay|tenemos|hara)",
    r"^(?:va a llover|llovera|llueve|esta lloviendo|hace frio|hace calor|hara frio|hara calor|va a hacer frio|"
    r"va a hacer calor|necesito paraguas|hace sol|va a nevar|nevara)",
    r"^(?:el\s+)?(?:tiempo|clima|pronostico(?: del tiempo)?|prevision(?: del tiempo)?|temperatura)\b",
)]
_TIEMPO_RESTO = re.compile(
    r"^(?:\s+(?:de|para))?(?:\s+" + _DIA + r")?(?:\s+(?:en|de|para|por)\s+(.+?))?(?:\s+" + _DIA + r")?" + _FIN
)

DISPARADORES_BUSQUEDA = sorted((plano(d) for d in (
    "busca en internet", "busca en google", "busca en la web", "busca en la red", "busca por internet",
    "busca información sobre", "busca información de", "busca información acerca de",
    "busca info sobre", "busca info de", "investiga sobre", "investiga acerca de",
    "consulta en internet", "busca", "investiga", "googlea",
)), key=len, reverse=True)
_SITIOS = (r"youtube|spotify|wikipedia|amazon|google maps|maps|github|twitter|reddit|netflix|twitch|bing|"
           r"google imagenes|imagenes")
_BUSCAR_EN_SITIO = re.compile(rf"^(.+?)\s+en\s+({_SITIOS})$")
_NOTICIAS_TEMA = re.compile(
    r"^(?:(?:dime|dame|cuentame|ponme|quiero)\s+)?(?:que\s+)?(?:las\s+)?(?:ultimas\s+)?noticias\s+(?:hay\s+)?(?:hoy\s+)?"
    r"(?:de|sobre|acerca de)\s+(?!(?:hoy|ultima hora|este dia)\b)(.+?)" + _FIN
)
_NOTICIAS_GENERALES = re.compile(
    r"^(?:(?:di|dime|dame|lee|leeme|que|cuales son|cuentame|pon|ponme|quiero|ensename|muestrame|hay)\s+)?"
    r"(?:las\s+)?(?:ultimas\s+|principales\s+)?noticias"
    r"(?:\s+hay)?(?:\s+(?:de\s+hoy|del\s+dia|de\s+ultima\s+hora|hoy))?" + _FIN
)

_LEER_PAGINA = re.compile(
    r"^(?:resume|resumeme|lee|leeme|que dice|de que va)\s+"
    r"(?:(?:la|esta)\s+(?:pagina|web)\s+|(?:el|este)\s+(?:articulo|enlace)\s+)?"
    r"((?:https?://)?(?:[\w-]+\.)+[a-z]{2,}\S*)" + _FIN
)

_PC = r"(?:(?:el|la|mi|este|esta)\s+)?(?:ordenador|pc|equipo|portatil|computadora|maquina|windows)"
_ACTIVAR = r"(?:(?:activa|pon|usa|cambia a|cambia al|pasa a|pasa al)\s+)?(?:el\s+)?"
_COMANDOS_SISTEMA = [(re.compile(patron + _FIN), comando) for patron, comando in (
    (rf"^(?:apaga|apagar)\s+{_PC}", "apagar"),
    (rf"^(?:reinicia|reiniciar)\s+{_PC}", "reiniciar"),
    (rf"^(?:(?:suspende|suspender|duerme|hiberna)\s+{_PC}|pon\s+{_PC}\s+en\s+(?:suspension|reposo))", "suspender"),
    (rf"^(?:bloquea|bloquear)(?:\s+(?:{_PC}|la\s+pantalla|la\s+sesion))?", "bloquear"),
    (r"^(?:cierra|cerrar)\s+(?:la\s+|mi\s+)?sesion(?:\s+de\s+windows)?", "cerrar_sesion"),
    (r"^(?:cancela|anula|aborta|para)\s+(?:el\s+)?(?:apagado|reinicio)", "cancelar_apagado"),
    (rf"^{_ACTIVAR}(?:modo|tema)\s+(?:oscuro|noche|nocturno)", "modo_oscuro"),
    (rf"^(?:{_ACTIVAR}(?:modo|tema)\s+(?:claro|dia|diurno)|(?:quita|desactiva)\s+(?:el\s+)?(?:modo|tema)\s+oscuro)",
     "modo_claro"),
    (r"^(?:(?:(?:haz|toma|saca)\s+)?(?:una\s+)?(?:captura|pantallazo|screenshot)(?:\s+de\s+(?:la\s+)?pantalla)?|"
     r"captura\s+(?:la\s+)?pantalla)", "captura"),
    (r"^(?:vacia|vaciar|limpia)\s+(?:la\s+)?papelera(?:\s+de\s+reciclaje)?", "vaciar_papelera"),
    (r"^(?:(?:muestra|ensena|ve a|vuelve a)\s+(?:el\s+|al\s+)?escritorio|minimiza\s+todo|"
     r"minimiza\s+(?:todas\s+)?las\s+ventanas)", "mostrar_escritorio"),
)]

_ARTICULO = re.compile(r"^(?:el|la|los|las|mi|mis|un|una)\s+")

# --- Recordatorios, temporizadores, alarmas y agenda (tools/recordatorios.py) ---
_TEMPORIZADOR = re.compile(
    r"^(?:(?:pon(?:me)?|crea|inicia|programa|activa|haz)\s+(?:un|una)\s+)?(?:temporizador|cuenta\s+atras)"
    r"\s+(?:de|para|por|durante)\s+(.+)$")
_ALARMA = re.compile(
    r"^(?:(?:pon(?:me)?|crea|programa|activa)\s+(?:una\s+|la\s+|otra\s+)?alarma|despiertame|levantame)\b"
    r"\s*(?:para\s+)?(.*)$")
_RECORDATORIO = re.compile(
    r"^(?:recuerdame|avisame|no\s+me\s+dejes\s+olvidar|"
    r"(?:pon(?:me)?|crea|programa|anade)\s+un\s+recordatorio(?:\s+(?:para|de))?)\s+(.+)$")
_APUNTAR_AGENDA = re.compile(
    r"^(?:(?:apunta(?:me)?|anota(?:me)?|pon(?:me)?|anade|mete|guarda)\s+en\s+(?:la|mi)\s+agenda|agendame|agenda)"
    r"\s+(?:que\s+)?(.+)$")
_CUANDO_AGENDA = (r"(?:hoy|manana|pasado\s+manana|esta\s+semana|la\s+semana\s+que\s+viene|para\s+hoy|"
                  r"para\s+manana|(?:el|este|el\s+proximo)\s+(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo))")
_CONSULTAR_AGENDA = re.compile(
    r"^(?:que\s+(?:tengo|hay)\s+(?P<cuando>" + _CUANDO_AGENDA + r")(?:\s+en\s+(?:la|mi)\s+agenda)?"
    r"|que\s+hay\s+en\s+(?:la|mi)\s+agenda(?:\s+(?P<cuando2>" + _CUANDO_AGENDA + r"))?"
    r"|(?:mi\s+agenda|la\s+agenda)(?:\s+(?:de|para)\s+(?P<cuando3>" + _CUANDO_AGENDA + r"))?"
    r"|tengo\s+algo\s+(?P<cuando4>" + _CUANDO_AGENDA + r")"
    r"|que\s+(?P<tipo>recordatorios|alarmas|temporizadores|citas)\s+tengo"
    r"|(?:tengo|hay)\s+(?:alguna?s?\s+)?(?P<tipo2>recordatorios?|alarmas?|temporizadores?|citas?)(?:\s+puest[oa]s?)?"
    r"|(?:dime|lista|ensename|muestrame|cuales\s+son)\s+(?:mis\s+|los\s+|las\s+)?(?P<tipo3>recordatorios|alarmas|"
    r"temporizadores|citas))$")
_CANCELAR_RECORDATORIO = re.compile(
    r"^(?:cancela|quita|borra|elimina|apaga|desactiva|anula)\s+(?P<todos>todos\s+los\s+|todas\s+las\s+)?"
    r"(?:el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?(?P<tipo>temporizador(?:es)?|alarmas?|recordatorios?|citas?|"
    r"cuenta\s+atras)(?:\s+(?:de|del|de\s+la|de\s+las|para|para\s+las?)\s+(?P<que>.+))?$")
_CUANTO_QUEDA = re.compile(
    r"^cuanto\s+(?:tiempo\s+)?(?:le\s+)?(?:queda|falta)(?:\s+(?:al|del|para\s+el|de\s+la|a\s+la)\s+"
    r"(?:temporizador|cuenta\s+atras))?$")
_POSPONER = re.compile(
    r"^(?:pospon(?:la|lo)?|aplaza(?:la|lo)?)(?:\s+(?:la\s+alarma|el\s+recordatorio|el\s+temporizador))?"
    r"(?:\s+(?:otros?\s+)?(?P<duracion>.+))?$")
_OTROS_MINUTOS = re.compile(
    r"^(?:dame|dejame(?:\s+dormir)?)\s+(?:otros\s+)?(?P<duracion>(?:\d+|un|una|dos|tres|cinco|diez|quince|veinte)"
    r"\s+minutos?)\s+mas$")
# --- Estado del equipo y brillo (tools/sistema.py) ---
_BRILLO = re.compile(
    r"^(?:(?P<sube>sube|subir|aumenta|mas)|(?P<baja>baja|bajar|reduce|menos)|pon|cambia|ajusta|deja)?\s*"
    r"(?:un\s+poco\s+)?(?:el\s+)?brillo(?:\s+de\s+la\s+pantalla)?"
    r"(?:\s+(?:al|a|en)\s+(?P<valor>\d{1,3})\s*(?:%|por\s*ciento)?|\s+al\s+(?P<extremo>maximo|minimo|tope))?$")
_BRILLO_CONSULTA = re.compile(r"^(?:que|cuanto)\s+brillo\s+(?:tengo|hay|tiene\s+la\s+pantalla)$|^como\s+esta\s+el\s+brillo$")
_BATERIA = re.compile(
    r"^(?:cuanta\s+bateria\s+(?:me\s+)?(?:queda|tengo|hay|le\s+queda(?:\s+al\s+(?:portatil|ordenador))?)|"
    r"(?:nivel|estado)\s+de\s+(?:la\s+)?bateria|(?:la\s+)?bateria|que\s+bateria\s+tengo|"
    r"como\s+(?:va|esta|anda)\s+la\s+bateria|(?:se\s+)?esta\s+cargando(?:\s+el\s+(?:portatil|ordenador))?|"
    r"me\s+queda\s+(?:mucha\s+)?bateria|tengo\s+(?:poca\s+)?bateria)$")
_EQUIPO = re.compile(
    r"^(?:como\s+(?:va|esta|anda)\s+(?:el\s+)?(?:ordenador|pc|equipo|sistema|portatil)|"
    r"(?:estado|rendimiento)\s+del\s+(?:ordenador|pc|equipo|sistema|portatil)|"
    r"(?:cuanta|que)\s+(?P<ram>memoria|ram)\s+(?:estoy\s+usando|uso|se\s+esta\s+usando|queda|libre|tengo\s+libre|"
    r"esta\s+usando\s+el\s+(?:ordenador|pc))|uso\s+de\s+(?:la\s+)?(?P<ram2>memoria|ram)|"
    r"(?:cuanto|que)\s+(?:uso\s+de\s+)?(?P<cpu>cpu|procesador)(?:\s+(?:estoy\s+usando|se\s+esta\s+usando|uso|tengo|hay))?|"
    r"uso\s+del?\s+(?P<cpu2>cpu|procesador)|"
    r"(?:cuanto|que)\s+(?P<disco>espacio|disco)\s+(?:libre\s+)?(?:me\s+queda|tengo|hay|queda)(?:\s+libre)?"
    r"(?:\s+en\s+el\s+disco)?|(?P<disco2>espacio)\s+libre(?:\s+en\s+(?:el\s+)?disco)?|"
    r"(?:como\s+va|uso\s+de|temperatura\s+de)\s+la\s+(?P<gpu>grafica|gpu|tarjeta\s+grafica))$")

# --- Leer la pantalla (tools/pantalla.py) ---
_SITIO_PANTALLA = r"(?:la\s+|esta\s+|mi\s+)?(?:pantalla|ventana)"
_PANTALLA = re.compile(
    r"^(?:que\s+(?:pone|hay|sale|dice|aparece)\s+(?:en\s+)?" + _SITIO_PANTALLA +
    r"|(?:lee|leeme|leer)\s+(?:lo\s+que\s+(?:pone|hay|sale)\s+en\s+)?" + _SITIO_PANTALLA +
    r"|(?:lee|leeme)(?:lo)?\s+esto|que\s+pone\s+aqui|que\s+(?:error|fallo|problema)\s+es\s+este"
    r"|que\s+(?:significa|quiere\s+decir)\s+este\s+(?:error|mensaje|aviso)"
    r"|como\s+(?:arreglo|soluciono|resuelvo)\s+este\s+(?:error|fallo|problema)"
    r"|(?:explica|explicame|resume|resumeme|traduce|traduceme|corrige|corrigeme)(?:lo)?\s+"
    r"(?:esto|lo\s+que\s+(?:pone|hay|sale)\s+en\s+" + _SITIO_PANTALLA + r"|" + _SITIO_PANTALLA + r")"
    r"|mira\s+" + _SITIO_PANTALLA + r"(?:\s+y\s+.+)?|que\s+estoy\s+viendo"
    # «mira la pantalla y dime…»: «mira» se quita antes como muletilla («mira, …»)
    r"|" + _SITIO_PANTALLA + r"\s+y\s+(?:dime|explicame|corrige(?:me)?|revisa|traduce(?:me)?|resume(?:me)?|lee(?:me)?)\b.*"
    r"|revisa\s+(?:lo\s+que\s+(?:he\s+escrito|pone|hay)(?:\s+en\s+" + _SITIO_PANTALLA + r")?|" + _SITIO_PANTALLA + r"))$")

# --- Archivos (tools/archivos.py) ---
_CLASE_ARCHIVO = (r"(?:archivos?|ficheros?|documentos?|pdfs?|fotos?|imagen(?:es)?|capturas?|videos?|canciones|cancion|"
                  r"presentacion(?:es)?|powerpoint|excel|word|hojas?\s+de\s+calculo|diapositivas|apuntes|trabajo)")
_ARTICULOS_ARCHIVO = r"(?:(?:el|la|los|las|mi|mis|un|una)\s+)?"
_BUSCAR_ARCHIVO = re.compile(r"^(?:busca(?:me)?|encuentra(?:me)?|localiza)\s+" + _ARTICULOS_ARCHIVO
                             + r"(?P<que>" + _CLASE_ARCHIVO + r"\b.*)$")
_DONDE_ARCHIVO = re.compile(r"^donde\s+(?:esta|estan|tengo|guarde|he\s+guardado|puse)\s+" + _ARTICULOS_ARCHIVO
                            + r"(?P<que>" + _CLASE_ARCHIVO + r"\b.*)$")
_ABRIR_ARCHIVO = re.compile(r"^(?:abre|abrir|muestrame|ensename)\s+" + _ARTICULOS_ARCHIVO
                            + r"(?P<que>" + _CLASE_ARCHIVO + r"\b.*)$")
_ABRIR_DESCARGA = re.compile(
    r"^(?:abre|abrir|muestrame|ensename)\s+(?:lo\s+ultimo|el\s+ultimo\s+(?:archivo|documento|fichero))\s+"
    r"que\s+(?:he|has)\s+descargado$|^(?:abre|abrir)\s+(?:mi\s+|la\s+)?ultima\s+descarga$")
_BUSCAR_EN_EL_PC = re.compile(r"^(?:busca(?:me)?|buscar|encuentra(?:me)?|localiza|mira|revisa|investiga|lee(?:me)?|"
                              r"explora|analiza)\b")
_EN_EL_ORDENADOR = re.compile(r"\ben\s+(?:mi|el|este)\s+(?:ordenador|pc|portatil|equipo|disco)\b|"
                              r"\ben\s+mis\s+(?:archivos|carpetas|documentos|cosas)\b")
_QUE_DESCARGUE = re.compile(r"^(?:que|cual)\s+(?:es\s+)?(?:lo\s+ultimo|el\s+ultimo\s+archivo)\s+que\s+(?:he\s+)?descargado$")
_ORDINALES = {"primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3, "cuarto": 4, "quinto": 5,
              "sexto": 6, "septimo": 7, "octavo": 8, "ultimo": -1}
_ABRIR_RESULTADO = re.compile(
    r"^(?:abre|abrir)\s+(?:el\s+)?(?:resultado\s+(?:numero\s+)?(?P<n>\d+)|(?P<ord>"
    + "|".join(_ORDINALES) + r")(?:\s+(?:archivo|resultado|documento))?)$")

# --- Resumen del día («buenos días») ---
_SALUDO_MANANA = re.compile(r"^[\s¡!¿?]*(?:jarvis?[\s,.:;!]*)?(?:buenos\s+dias|buen\s+dia)(?:[\s,.:;!]*jarvis?)?[\s.!?]*$")
_RESUMEN_DIA = re.compile(
    r"^(?:(?:dame|hazme|dime|quiero|leeme)\s+)?(?:el\s+|un\s+)?(?:resumen|briefing)"
    r"(?:\s+(?:del?\s+dia|de\s+hoy|de\s+la\s+manana|diario|matinal))?$"
    r"|^pon(?:me)?\s+al\s+dia$|^que\s+hay\s+(?:para|de)\s+hoy$|^como\s+(?:empieza|pinta|viene)\s+(?:el|mi)\s+dia$")

_TIPO_RECORDATORIO = {"temporizador": "temporizador", "cuenta atras": "temporizador", "alarma": "alarma",
                      "recordatorio": "recordatorio", "cita": "cita"}
_ABRIR = re.compile(
    r"^(?:abre|abrir|ejecuta|ejecutar|inicia|iniciar|lanza|lanzar|arranca|arrancar)\s+"
    r"(?:la aplicacion de\s+|la aplicacion\s+|el programa\s+|la app de\s+|la app\s+|el juego\s+)?(.+)$"
)
# «quiero jugar a Fall Guys», «vamos a jugar al Rocket League», «juega a Phasmophobia»: abrir ese juego.
_JUGAR = re.compile(
    r"^(?:(?:quiero|vamos a|voy a|me apetece|podemos|dejame)\s+)?"
    r"(?:jugar|juega|jugamos|echar una partida|echemos una partida)(?:\s+un rato)?\s+(?:al|a la|a los|a las|a)\s+(.+)$"
)
_CERRAR = re.compile(r"^(?:cierra|cerrar)\b\s*(.*)$")
_SALIR = re.compile(r"^sal(?:\s+de\s+(.+))?$")
_VENTANA_ACTIVA = {
    "", "esto", "esta ventana", "la ventana", "la ventana activa", "ventana", "ventana activa",
    "la aplicacion", "el programa", "esta aplicacion", "este programa", "la app", "aqui",
}
_CERRAR_RELLENO = re.compile(r"^(?:la aplicacion de\s+|la aplicacion\s+|el programa\s+|la app de\s+|la app\s+|"
                             r"la ventana de\s+|el juego\s+)")

_APPS_ESCRIBIBLES = (r"bloc de notas|notepad|word|wordpad|excel|powerpoint|navegador|chrome|edge|firefox|"
                     r"whatsapp|discord|telegram|notion|visual studio code|vs code|code|obsidian|onenote|"
                     r"teams|slack|outlook")
_ESCRIBIR = re.compile(r"^(?:escribe|escribir|teclea|teclear|dicta)\b\s*:?\s*(.+)$")
_ESCRIBIR_EN = re.compile(rf"^(.+?)\s+en\s+(?:(?:el|la|mi)\s+)?({_APPS_ESCRIBIBLES})$")

# --- Mensajes por aplicaciones de chat (tools/mensajes.py) ---
# «Escríbele "Hola" a elPixel12», «dile a Pepe que llego tarde», «mándale un mensaje a Ana por Discord
# diciendo que ya voy», «en Discord escríbele hola a Pepe». Pasó: «escríbele "Hola" a elPixel12»
# acababa en «escribir: le "Hola" a elPixel12» en la ventana activa.
APPS_DE_MENSAJES = ("discord", "whatsapp", "telegram", "slack", "teams", "instagram", "messenger", "signal")
_APPS_MENSAJES = "|".join(APPS_DE_MENSAJES)
_APP_DELANTE = re.compile(rf"^(?:en|por|desde)\s+(?:el\s+)?(?P<app>{_APPS_MENSAJES})\b[\s,:]*")
_APP_EN_MEDIO = re.compile(
    rf"[\s,]+(?:por|en|desde|a\s+traves\s+de|via|usando|con)\s+(?:el\s+)?(?P<app>{_APPS_MENSAJES})\b")
_VERBO_LE = r"(?:escribele|escribeles|mandale|mandales|enviale|enviales|dile|diles|avisale|avisales|avisa)"
_VERBO_SIN_LE = r"(?:escribe|manda|envia)"
_OBJETO_MENSAJE = (r"(?:(?:un|el|este|otro|un\s+nuevo)\s+(?:mensaje(?:\s+privado)?|mensajito|md|dm|privado|wasap|"
                   r"whatsapp)\s+)")
_DELIMITADOR_MENSAJE = (r"(?:diciendole|diciendoles|diciendo|que\s+dice|que\s+ponga|con\s+el\s+texto|de\s+que|"
                        r"que)")
_VERBO_MENSAJE = re.compile(rf"^(?:(?P<le>{_VERBO_LE})|(?P<sin>{_VERBO_SIN_LE}))\s+(?P<objeto>{_OBJETO_MENSAJE})?")
# Detrás del verbo: «a Pepe que hola», «a Pepe: hola», «a Pepe hola» (el contacto es una palabra, como
# los nombres de usuario de Discord) o «hola a Pepe».
_CUERPO_A_QUIEN = re.compile(
    rf"^(?:a|para)\s+(?P<contacto>(?:(?:mi|mis|tu|su)\s+)?@?[^\s:,]+)"
    rf"(?:\s*:\s*|[\s,]+(?:{_DELIMITADOR_MENSAJE}\s+)?)(?P<texto>.+)$")
_CUERPO_TEXTO_PRIMERO = re.compile(r"^(?P<texto>.+?)\s+(?:a|para)\s+(?P<contacto>@?[^\s:,]+)$")
_MENSAJE_CON_COMILLAS = re.compile(r"[\"“«](?P<texto>[^\"“”«»]+)[\"”»]|(?<!\w)'(?P<texto2>[^']+)'(?!\w)")

# --- Leer mensajes («¿cuál es el último mensaje que me ha escrito elPixel12 por Discord?») ---
# Pasó: acababa en la IA, que miró la pantalla y contestó con el último mensaje que había mandado ella.
_QUIEN = r"(?P<contacto>@?[^\s,.;:!?¿¡\"«»]+)"
_PIDE_LEER = r"(?:(?:cual|que)\s+(?:es|fue|era)\s+|dime\s+|leeme\s+|lee\s+|ensename\s+|muestrame\s+|di\s+)?"
_MENSAJE_PALABRA = r"(?:mensa[jg]e|md|dm|privado)"
_LEER_MENSAJES = [(re.compile(patron), mios, cantidad) for patron, mios, cantidad in (
    # «el último mensaje que le he mandado a X»: el mío
    (rf"^{_PIDE_LEER}(?:el\s+)?ultimo\s+{_MENSAJE_PALABRA}\s+que\s+le\s+(?:he|has|hemos)\s+(?:mandado|enviado|escrito|dicho)"
     rf"\s+a\s+{_QUIEN}$", True, 1),
    # «el último mensaje que me ha escrito X», «el último mensaje de X»
    (rf"^{_PIDE_LEER}(?:el\s+)?ultimo\s+{_MENSAJE_PALABRA}\s+(?:que\s+me\s+(?:ha|han)\s+(?:escrito|mandado|enviado|dicho)"
     rf"\s+|de\s+|del\s+){_QUIEN}$", False, 1),
    # «los últimos 3 mensajes de X», «léeme la conversación con X»
    (rf"^{_PIDE_LEER}(?:los\s+)?ultimos\s+(?:(?P<n>\d{{1,2}}|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+)?"
     rf"{_MENSAJE_PALABRA}s?\s+(?:de|con)\s+{_QUIEN}$", False, 5),
    (rf"^(?:leeme|lee|ensename|muestrame|dime)\s+(?:la\s+conversacion|el\s+chat|los\s+mensajes)\s+(?:con|de)\s+{_QUIEN}$",
     False, 5),
    # «¿qué me ha dicho X?», «¿me ha contestado X?», «¿ha respondido X?»
    (rf"^que\s+me\s+(?:ha|han)\s+(?:dicho|escrito|contestado|respondido|mandado|enviado|puesto)\s+{_QUIEN}$", False, 1),
    (rf"^(?:ya\s+)?me\s+(?:ha|han)\s+(?:escrito|contestado|respondido|mandado\s+algo|dicho\s+algo)\s+{_QUIEN}$", False, 1),
    (rf"^(?:ya\s+)?(?:ha|han)\s+(?:contestado|respondido|escrito)\s+{_QUIEN}$", False, 1),
)]
_NUMEROS_PEQUENOS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
                     "diez": 10}


def interpretar_lectura_de_mensajes(frase) -> dict | None:
    """«¿Cuál es el último mensaje que me ha escrito elPixel12 por Discord?» ->
    {"target": "elPixel12", "app": "discord", "cantidad": 1, "mios": False}; None si no es eso."""
    frase = frase if isinstance(frase, _Frase) else _Frase(frase)
    app, resto = _quitar_app_de_mensajes(frase)
    resto = _Frase(resto.texto.rstrip(" ?!."))
    for patron, mios, cantidad in _LEER_MENSAJES:
        coincidencia = patron.match(resto.plano)
        if coincidencia:
            if "n" in coincidencia.groupdict() and coincidencia.group("n"):
                numero = coincidencia.group("n")
                cantidad = int(numero) if numero.isdigit() else _NUMEROS_PEQUENOS[numero]
            contacto = resto.grupo(coincidencia, "contacto").lstrip("@")
            return {"target": contacto, "app": app, "cantidad": max(1, min(cantidad, 10)), "mios": mios}
    return None

_PULSAR = re.compile(
    r"^(?:pulsa|presiona|dale a|aprieta)\s+(?:la\s+tecla\s+|las\s+teclas\s+|el\s+boton\s+(?:de\s+)?)?(.+)$"
)
MAPA_TECLAS = {
    "intro": "enter", "enter": "enter", "entrar": "enter", "retorno": "enter",
    "espacio": "space", "barra espaciadora": "space",
    "escape": "esc", "esc": "esc",
    "tabulador": "tab", "tab": "tab",
    "atras": "backspace", "borrar": "backspace", "borra": "backspace", "retroceso": "backspace",
    "suprimir": "delete", "supr": "delete",
    "arriba": "up", "abajo": "down", "izquierda": "left", "derecha": "right",
    "inicio": "home", "fin": "end",
    "control": "ctrl", "ctrl": "ctrl", "alt": "alt",
    "mayusculas": "shift", "mayus": "shift", "shift": "shift",
    "windows": "win", "win": "win",
    "imprimir pantalla": "printscreen", "impr pant": "printscreen",
    "pagina arriba": "pageup", "pagina abajo": "pagedown", "re pag": "pageup", "av pag": "pagedown",
}

_MEDIA = [(re.compile(patron), objetivo, confianza) for patron, objetivo, confianza in (
    (r"\b(?:siguiente|proxima)\s+(?:cancion|pista|tema)\b|\b(?:cancion|pista)\s+siguiente\b|"
     r"^(?:pasa|salta|cambia)\s+(?:de\s+|la\s+)?cancion" + _FIN, "siguiente", 0.9),
    (r"\banterior\s+(?:cancion|pista|tema)\b|\b(?:cancion|pista)\s+anterior\b", "anterior", 0.9),
    (r"^(?:sube|subir|aumenta|mas)\s+(?:un poco\s+)?(?:el\s+)?volumen\b", "subir_volumen", 0.85),
    (r"^(?:baja|bajar|reduce|menos)\s+(?:un poco\s+)?(?:el\s+)?volumen\b", "bajar_volumen", 0.85),
    (r"^(?:silencia|silenciar|mute|mutea|quita el sonido|sin sonido)"
     r"(?:\s+(?:el\s+)?(?:volumen|sonido|audio|ordenador|pc))?" + _FIN, "silenciar", 0.85),
)]

# --- Música (tools/media.py): lo que suena se consulta y se controla de verdad ---
_EN_APP = r"(?:\s+(?:en|de)\s+(?:spotify|youtube))?"
_MUSICA_SONANDO = re.compile(
    r"^(?:(?:que|cual) (?:es )?(?:la |esta )?(?:cancion|musica|tema)"
    r" (?:es esta|esta sonando|suena|sonaba|estaba sonando|estoy escuchando|estaba escuchando|es)"
    r"|que (?:esta sonando|suena|sonaba|estaba sonando|estoy escuchando|estaba escuchando|tengo puesto)"
    r"|(?:como se llama|de quien es|quien canta) (?:esta|esto|la)(?: (?:cancion|musica|tema))?"
    r"(?: que (?:suena|esta sonando|sonaba|estaba sonando))?)" + _EN_APP + _FIN
)
_MUSICA_PAUSA = re.compile(
    r"^(?:(?:pausa|pausar|pon pausa a|para|deten|quita|apaga)(?: (?:la|el|esta|este))?"
    r" (?:musica|cancion|reproduccion|tema|video)"
    r"|(?:pausa|pausar|para|deten) (?:spotify|youtube)"
    r"|pausa|pausala|pausalo|pon pausa)" + _EN_APP + _FIN
)
_MUSICA_REANUDAR = re.compile(
    r"^(?:(?:reproduce|reproducir|reanuda|reanudar|continua|sigue con|vuelve a poner|vuelve a reproducir|pon"
    r"|dale al play a|dale play a|quita la pausa a)(?: (?:la|el|tu|mi))? (?:musica|cancion|reproduccion|tema|lista|playlist)"
    r"(?: (?:que (?:estaba sonando|sonaba|estaba escuchando|tenia puesta)|de antes|de nuevo|otra vez))?"
    r"|(?:reproduce|pon|vuelve a poner|vuelve a reproducir) (?:la )?ultima (?:cancion|musica|tema)"
    r"(?: que (?:estaba sonando|sonaba|estaba escuchando|escuche|puse|pusiste))?"
    r"|(?:reproduce|pon|vuelve a poner) (?:lo|la musica|la cancion) que (?:estaba sonando|sonaba|estaba escuchando)"
    r"|(?:pon|reproduce) (?:algo de |un poco de )?musica"
    r"|reanuda|reanudar|continua|play|dale al play|dale play|reproduce|reproducir|quita la pausa|sigue sonando"
    r"|(?:reproduce|reanuda|dale al play en|dale play en) (?:spotify|youtube))" + _EN_APP + _FIN
)
_MUSICA_PONER = re.compile(
    r"^(?:reproduce|reproducir|reproduceme|pon|quiero escuchar|quiero oir|me apetece escuchar|me apetece oir)"
    r"\s+(.+?)(?:\s+en\s+(spotify|youtube))?" + _FIN
)
# «pon la canción X», «pon algo de Queen»: está claro que es música, no una aplicación.
_PISTA_MUSICAL = re.compile(
    r"^(?:la cancion|una cancion|esa cancion|el tema|la de|el de|musica de|canciones de|temas de|algo de|un poco de|"
    r"el disco|el album|la playlist|la lista|el video de|la musica de|musica)\b"
)
_QUITAR_PISTA = re.compile(
    r"^(?:(?:la|una|esa) cancion(?: de)?|el tema(?: de)?|la de|el de|canciones de|temas de|algo de|un poco de|"
    r"la musica de|musica de|el video(?: de)?)\s+"
)
# «pon el volumen…», «pon que mañana…», «pon en pantalla completa»: no son canciones.
_NO_ES_MUSICA = re.compile(
    r"^(?:que|en|a|al)\b|^(?:el |la |un |una )?(?:volumen|brillo|alarma|temporizador|recordatorio|modo|"
    r"pantalla completa|subtitulos|captura)\b"
)

_CONECTOR = re.compile(r"\s*,?\s+(?:y luego|y despues|y a continuacion|luego|despues|a continuacion|y)\s+")


# ==========================================
# UTILIDADES DE EXTRACCIÓN
# ==========================================

def _quitar_prefijo(frase: _Frase, prefijos: list):
    """Si la frase empieza por uno de los prefijos, devuelve lo que queda detrás; si no, None."""
    for prefijo in prefijos:
        if frase.empieza(prefijo):
            return frase.desde(len(prefijo))
    return None


def _sin_articulo(texto: str) -> str:
    coincidencia = _ARTICULO.match(plano(texto))
    return texto[coincidencia.end():] if coincidencia else texto


def _interpretar_teclas(dicho: str) -> str:
    dicho = plano(dicho).strip(" ?!.")
    if dicho in MAPA_TECLAS:
        return MAPA_TECLAS[dicho]
    partes = [p for p in re.split(r"\s*\+\s*|\s+mas\s+|\s+y\s+|\s+", dicho) if p]
    if len(partes) > 1 and all(p in MAPA_TECLAS or len(p) == 1 or re.fullmatch(r"f\d{1,2}", p) for p in partes):
        return "+".join(MAPA_TECLAS.get(p, p) for p in partes)
    return MAPA_TECLAS.get(dicho, dicho)


def _quitar_app_de_mensajes(frase: _Frase) -> tuple:
    """«En Discord escríbele…», «…por Discord»: devuelve (aplicación, frase sin esa mención)."""
    coincidencia = _APP_DELANTE.match(frase.plano)
    if coincidencia:
        return coincidencia.group("app"), frase.desde(coincidencia.end())
    coincidencia = _APP_EN_MEDIO.search(frase.plano)
    if coincidencia:
        return coincidencia.group("app"), _Frase(frase.texto[:coincidencia.start()] + " "
                                                 + frase.texto[coincidencia.end():])
    return "", frase


def _texto_del_mensaje(texto: str) -> str:
    texto = texto.strip().strip("\"'“”«»").strip(" ,;:")
    return texto[:-1].rstrip() if texto.endswith(".") and not texto.endswith("..") else texto


def interpretar_mensaje(frase) -> dict | None:
    """«Escríbele "Hola" a elPixel12 por Discord» -> {"target": "elPixel12", "text": "Hola", "app": "discord"}.

    None si la frase no pide mandar un mensaje a alguien. Sin «le» («escribe hola a todos») solo
    cuenta si dice «un mensaje» o la aplicación: si no, es escribir en la ventana activa.
    """
    frase = frase if isinstance(frase, _Frase) else _Frase(frase)
    app, resto = _quitar_app_de_mensajes(frase)
    verbo = _VERBO_MENSAJE.match(resto.plano)
    if not verbo or (verbo.group("sin") and not (verbo.group("objeto") or app)):
        return None
    if not app and re.search(r"\b(?:wasap|whatsapp)\b", verbo.group("objeto") or ""):
        app = "whatsapp"                                               # «mándale un wasap a…»
    cuerpo = resto.desde(verbo.end())
    comillas = _MENSAJE_CON_COMILLAS.search(cuerpo.texto)
    if comillas:
        texto = comillas.group("texto") or comillas.group("texto2") or ""
        fuera = _Frase(cuerpo.texto[:comillas.start()] + " " + cuerpo.texto[comillas.end():])
        quien = re.search(r"(?:^|\s)(?:a|para)\s+(?P<contacto>.+?)[\s.,:;!?]*$", fuera.plano)
        contacto = fuera.grupo(quien, "contacto") if quien else ""
    else:
        coincidencia = _CUERPO_A_QUIEN.match(cuerpo.plano) or _CUERPO_TEXTO_PRIMERO.match(cuerpo.plano)
        if not coincidencia:
            return None
        contacto, texto = cuerpo.grupo(coincidencia, "contacto"), cuerpo.grupo(coincidencia, "texto")
        texto = re.sub(r"^que\s+", "", texto, flags=re.IGNORECASE)        # «dile que llego tarde a Pepe»
    contacto = contacto.strip(" .,:;!?¿¡\"'«»").lstrip("@").strip()
    texto = _texto_del_mensaje(texto)
    if not contacto or not texto:
        return None
    return {"target": contacto, "text": texto, "app": app}


def _tramos_entre_comillas(texto: str) -> list:
    return [coincidencia.span() for coincidencia in _MENSAJE_CON_COMILLAS.finditer(texto)]


def _limpiar_consulta(resto: _Frase) -> str:
    consulta = re.sub(r"\b(?:en internet|en la web|en google|en la red|por internet)\b", "", resto.texto,
                      flags=re.IGNORECASE)
    consulta = re.sub(r"^\s*(?:sobre|acerca de|informaci[oó]n (?:de|sobre|acerca de)|info (?:de|sobre))\s+", "",
                      consulta.strip(), flags=re.IGNORECASE)
    return _objetivo(consulta)


# ==========================================
# CLASIFICACIÓN DE UNA ÚNICA ORDEN
# ==========================================

def _clasificar_simple(frase: _Frase, con_activacion: str = "") -> dict:
    crudo = frase.texto
    p = frase.plano
    if not p:
        # «Buenos días, Jarvis» se queda vacía al quitar la activación (saludo + nombre).
        if con_activacion and _SALUDO_MANANA.match(con_activacion):
            return _intencion(raw=crudo, intent="briefing", action="greeting", confidence=0.95)
        return _intencion(raw=crudo)

    def hecho(**campos):
        return _intencion(raw=crudo, **campos)

    # 1. SISTEMA — apagar a JARVIS / parada de emergencia
    if _DESPEDIDA.match(p) or (con_activacion and _DESPEDIDA.match(con_activacion)):
        return hecho(intent="system", action="shutdown_jarvis", confidence=1.0)
    if _PARADA.match(p):
        return hecho(intent="system", action="stop", confidence=1.0)

    # 1b. RESUMEN DEL DÍA («buenos días»: el tiempo, lo que tienes hoy y titulares)
    if _SALUDO_MANANA.match(p) or (con_activacion and _SALUDO_MANANA.match(con_activacion)):
        return hecho(intent="briefing", action="greeting", confidence=0.95)
    if _RESUMEN_DIA.match(p):
        return hecho(intent="briefing", action="summary", confidence=0.95)

    # 2. SEGURIDAD — palabra de seguridad o modo confirmación
    if any(frase.empieza(f) for f in FRASES_SEGURIDAD_MODO_CONFIRMACION):
        return hecho(intent="security_setup", action="set_confirm_mode", confidence=1.0)
    coincidencia = _SEGURIDAD_PALABRA.match(p)
    if coincidencia:
        palabra = _objetivo(frase.grupo(coincidencia, 1))
        return hecho(intent="security_setup", action="set_word", text=palabra,
                     confidence=0.9 if palabra else 0.4)

    # 3. AJUSTES — modo voz/texto, idioma, conversación
    coincidencia = _MODO.match(p)
    if coincidencia:
        modo = "voz" if coincidencia.group(1) in ("voz", "hablado") else "texto"
        return hecho(intent="settings", action="set_mode", target=modo, confidence=0.95)
    coincidencia = _IDIOMA.match(p)
    if coincidencia:
        return hecho(intent="settings", action="set_language", target=coincidencia.group(1), confidence=0.9)
    if _CONVERSACION_NUEVA.match(p):
        return hecho(intent="conversation", action="new", confidence=0.95)
    if _HISTORIAL_BORRAR.match(p):
        return hecho(intent="history", action="delete_all", confidence=0.95)
    if _CONVERSACION_BORRAR.match(p):
        return hecho(intent="history", action="delete_current", confidence=0.95)

    # 3b. RECORDATORIOS, TEMPORIZADORES, ALARMAS Y AGENDA (antes que la memoria: comparten verbos)
    coincidencia = _PREGUNTA_A_LA_MEMORIA.match(crudo)
    if coincidencia:
        return hecho(intent="memory_query", action="query", query=_objetivo(coincidencia.group("q")),
                     confidence=0.85)
    coincidencia = _TEMPORIZADOR.match(p)
    if coincidencia:
        return hecho(intent="reminder", action="create", tipo="temporizador",
                     target=_objetivo(frase.grupo(coincidencia, 1)), confidence=0.95)
    coincidencia = _ALARMA.match(p)
    if coincidencia:
        return hecho(intent="reminder", action="create", tipo="alarma",
                     target=_objetivo(frase.grupo(coincidencia, 1)), confidence=0.95)
    coincidencia = _RECORDATORIO.match(p) or _APUNTAR_AGENDA.match(p)
    if coincidencia:
        tipo = "cita" if coincidencia.re is _APUNTAR_AGENDA else "recordatorio"
        return hecho(intent="reminder", action="create", tipo=tipo,
                     target=_objetivo(frase.grupo(coincidencia, 1)), confidence=0.95)
    coincidencia = _CONSULTAR_AGENDA.match(p)
    if coincidencia:
        grupos = coincidencia.groupdict()
        cuando = next((grupos[g] for g in ("cuando", "cuando2", "cuando3", "cuando4") if grupos.get(g)), "")
        tipo = next((grupos[g] for g in ("tipo", "tipo2", "tipo3") if grupos.get(g)), "")
        return hecho(intent="reminder", action="list", target=cuando,
                     tipo=_TIPO_RECORDATORIO.get(re.sub(r"(?:es|s)$", "", tipo), ""), confidence=0.95)
    coincidencia = _CANCELAR_RECORDATORIO.match(p)
    if coincidencia:
        tipo = re.sub(r"(?:es|s)$", "", coincidencia.group("tipo"))
        que = frase.grupo(coincidencia, "que") if coincidencia.group("que") else ""
        return hecho(intent="reminder", action="cancel", tipo=_TIPO_RECORDATORIO.get(tipo, ""),
                     target=_objetivo(que), todos=bool(coincidencia.group("todos")), confidence=0.95)
    if _CUANTO_QUEDA.match(p):
        return hecho(intent="reminder", action="status", confidence=0.9)
    coincidencia = _POSPONER.match(p) or _OTROS_MINUTOS.match(p)
    if coincidencia:
        return hecho(intent="reminder", action="snooze", target=coincidencia.group("duracion") or "",
                     confidence=0.9)

    # 3c. ESTADO DEL EQUIPO Y BRILLO (antes que la memoria: «¿cuánta memoria estoy usando?» es la RAM)
    if _BATERIA.match(p):
        return hecho(intent="system_info", action="battery", confidence=0.95)
    coincidencia = _EQUIPO.match(p)
    if coincidencia:
        grupos = {g: v for g, v in coincidencia.groupdict().items() if v}
        foco = next((f for f in ("ram", "cpu", "disco", "gpu") if f in grupos or f + "2" in grupos), "")
        return hecho(intent="system_info", action="status", target=foco, confidence=0.95)
    if _BRILLO_CONSULTA.match(p):
        return hecho(intent="system_info", action="brightness", target="?", confidence=0.95)
    coincidencia = _BRILLO.match(p)
    if coincidencia and (coincidencia.group("sube") or coincidencia.group("baja") or coincidencia.group("valor")
                         or coincidencia.group("extremo")):
        extremo = coincidencia.group("extremo")
        objetivo = (coincidencia.group("valor") or ("max" if extremo in ("maximo", "tope") else "min" if extremo else "")
                    or ("+" if coincidencia.group("sube") else "-"))
        return hecho(intent="system_info", action="brightness", target=objetivo, confidence=0.95)

    # 3d. LEER LA PANTALLA (la petición entera va al modelo junto con el texto de la pantalla)
    if _PANTALLA.match(p):
        return hecho(intent="screen", action="read", target=crudo.strip(), confidence=0.95)

    # 3e. ARCHIVOS (antes que «busca» en Internet y que «abre» una aplicación)
    coincidencia = _ABRIR_RESULTADO.match(p)
    if coincidencia:
        numero = int(coincidencia.group("n")) if coincidencia.group("n") else _ORDINALES[coincidencia.group("ord")]
        return hecho(intent="files", action="open_index", target=str(numero), confidence=0.9)
    if _ABRIR_DESCARGA.match(p):
        return hecho(intent="files", action="open_recent", confidence=0.95)
    if _QUE_DESCARGUE.match(p):
        return hecho(intent="files", action="recent", confidence=0.95)
    # 3f. BUSCAR EN EL ORDENADOR, no en Internet («busca en mi ordenador el código de mi TFG y dime con qué
    # está hecho»): lo resuelve la IA con sus funciones de archivos. Pasó: acababa en una búsqueda web.
    if _BUSCAR_EN_EL_PC.match(p) and (_EN_EL_ORDENADOR.search(p) or re.search(r"\b(?:codigo|proyecto)\b", p)) \
            and not _BUSCAR_ARCHIVO.match(p):
        return hecho(intent="chat", action="files_llm", confidence=0.0)
    for patron, accion in ((_ABRIR_ARCHIVO, "open"), (_BUSCAR_ARCHIVO, "search"), (_DONDE_ARCHIVO, "search")):
        coincidencia = patron.match(p)
        if coincidencia:
            from tools.archivos import interpretar_busqueda
            que = frase.grupo(coincidencia, "que").rstrip("?!. ")
            palabras, _tipo = interpretar_busqueda(que)
            # «abre el Excel» o «abre Word» son la aplicación: sin nada más que buscar no es un archivo.
            if palabras or accion == "search":
                return hecho(intent="files", action=accion, target=que, confidence=0.9)

    # 4. MEMORIA — editar (antes que el resto: "cambia/modifica" son verbos propios)
    coincidencia = _MEMORIA_EDITAR.match(p)
    if coincidencia:
        objetivo = _objetivo(frase.grupo(coincidencia, 1))
        nuevo_valor = _objetivo(frase.grupo(coincidencia, 2))
        return hecho(intent="memory_edit", action="edit", target=objetivo, text=nuevo_valor,
                     confidence=0.9 if (objetivo and nuevo_valor) else 0.4)

    # 5. MEMORIA — listar todo
    if _MEMORIA_LISTAR.match(p):
        return hecho(intent="memory_query", action="list", confidence=1.0)

    # 6. MEMORIA — borrar (antes que guardar: comparten estructura "olvida que...")
    if _MEMORIA_BORRAR_TODO.match(p):
        return hecho(intent="memory_delete", action="delete_all", confidence=0.95)
    resto = _quitar_prefijo(frase, PREFIJOS_MEMORIA_BORRAR)
    if resto is not None:
        objetivo = _objetivo(resto.texto)
        return hecho(intent="memory_delete", action="delete", target=objetivo,
                     confidence=0.9 if objetivo else 0.5)

    # 7. MEMORIA — consultar
    resto = _quitar_prefijo(frase, PREFIJOS_MEMORIA_CONSULTAR)
    if resto is not None:
        consulta = _objetivo(resto.texto)
        return hecho(intent="memory_query", action="query", query=consulta,
                     confidence=0.9 if consulta else 0.5)

    # 8. MEMORIA — guardar
    resto = _quitar_prefijo(frase, PREFIJOS_MEMORIA_GUARDAR)
    if resto is not None:
        contenido = _limpiar(resto.texto)
        if not contenido:
            return hecho(intent="memory_save", action="save", confidence=0.4)
        if _CONTENIDO_DIFUSO.search(plano(contenido)):
            return hecho(intent="chat", action="memory_save_llm", confidence=0.0)
        return hecho(intent="memory_save", action="save", target=contenido, confidence=1.0)
    if re.match(r"^(?:guarda|apunta|anota|recuerda|memoriza|anade|agrega|mete)(?:lo|la|los|las)\b", p):
        return hecho(intent="chat", action="memory_save_llm", confidence=0.0)     # «guárdalo en tu memoria»

    # 9. INFORMACIÓN LOCAL — hora y fecha (no necesitan ni Internet ni modelo)
    if _HORA.match(p):
        return hecho(intent="info", action="time", confidence=0.95)
    if _FECHA.match(p):
        return hecho(intent="info", action="date", confidence=0.95)

    # 10. TIEMPO METEOROLÓGICO
    for inicio in _TIEMPO_INICIOS:
        coincidencia = inicio.match(p)
        if not coincidencia:
            continue
        resto_tiempo = _TIEMPO_RESTO.match(p[coincidencia.end():])
        if resto_tiempo:
            desplazamiento = coincidencia.end()
            ciudad = ""
            if resto_tiempo.span(2)[0] >= 0:
                a, b = resto_tiempo.span(2)
                ciudad = _objetivo(crudo[desplazamiento + a:desplazamiento + b])
            dia = resto_tiempo.group(1) or resto_tiempo.group(3) or "hoy"
            accion = "tomorrow" if "manana" in dia else "today"
            return hecho(intent="weather", action=accion, target=ciudad, confidence=0.9)
        break

    # 11. INTERNET — búsqueda web, en un sitio concreto o de noticias
    resto = _quitar_prefijo(frase, DISPARADORES_BUSQUEDA)
    if resto is not None:
        consulta = _limpiar_consulta(resto)
        en_sitio = _BUSCAR_EN_SITIO.match(plano(consulta))
        if en_sitio:
            return hecho(intent="search_web", action="site", target=en_sitio.group(2),
                         query=_objetivo(consulta[:en_sitio.end(1)]), confidence=0.9)
        accion = "news" if re.match(r"^(?:las\s+)?(?:ultimas\s+)?noticias\b", plano(consulta)) else "search"
        return hecho(intent="search_web", action=accion, query=consulta or crudo,
                     confidence=0.95 if consulta else 0.5)
    coincidencia = _NOTICIAS_TEMA.match(p)
    if coincidencia:
        return hecho(intent="search_web", action="news", query=_objetivo(frase.grupo(coincidencia, 1)),
                     confidence=0.9)
    if _NOTICIAS_GENERALES.match(p):
        return hecho(intent="search_web", action="news", query="noticias de hoy", confidence=0.85)
    coincidencia = _LEER_PAGINA.match(p)
    if coincidencia:
        return hecho(intent="search_web", action="read", target=frase.grupo(coincidencia, 1).rstrip("?!"),
                     confidence=0.9)

    # 12. CONTROL PC — comandos de sistema (antes que abrir/cerrar: "cierra sesión")
    for patron, comando in _COMANDOS_SISTEMA:
        if patron.match(p):
            return hecho(intent="pc", action="system_command", target=comando, confidence=0.95)

    # 13. CONTROL PC — abrir aplicación, carpeta o web
    coincidencia = _ABRIR.match(p)
    if coincidencia:
        objetivo = _sin_articulo(_objetivo(frase.grupo(coincidencia, 1)))
        return hecho(intent="pc", action="open_app", target=objetivo, confidence=0.95 if objetivo else 0.3)
    coincidencia = _JUGAR.match(p)
    if coincidencia:
        objetivo = _sin_articulo(_objetivo(frase.grupo(coincidencia, 1)))
        return hecho(intent="pc", action="open_app", target=objetivo, confidence=0.9 if objetivo else 0.3)

    # 14. CONTROL PC — cerrar aplicación / ventana
    coincidencia = _CERRAR.match(p) or _SALIR.match(p)
    if coincidencia:
        objetivo = _objetivo(frase.grupo(coincidencia, 1)) if coincidencia.span(1)[0] >= 0 else ""
        if plano(objetivo) in _VENTANA_ACTIVA:
            objetivo = "active"
        else:
            relleno = _CERRAR_RELLENO.match(plano(objetivo))
            if relleno:
                objetivo = objetivo[relleno.end():]
            objetivo = _sin_articulo(objetivo)
        return hecho(intent="pc", action="close_app", target=objetivo, confidence=0.9)

    # 14a. MENSAJES — leer lo que te ha escrito alguien (o lo último que le mandaste)
    lectura = interpretar_lectura_de_mensajes(frase)
    if lectura:
        return hecho(intent="message", action="read", confidence=0.9, **lectura)

    # 14b. MENSAJES — mandar un mensaje a alguien por una aplicación de chat (Discord...)
    mensaje = interpretar_mensaje(frase)
    if mensaje:
        return hecho(intent="message", action="send", confidence=0.9, **mensaje)
    coincidencia = re.match(rf"^{_VERBO_LE}\s+(.+)$", p)
    if coincidencia and p.startswith("escribele"):
        # «Escríbele hola» sin decir a quién: se escribe en la ventana en la que estabas (su chat).
        return hecho(intent="pc", action="type_text", text=_texto_del_mensaje(frase.grupo(coincidencia, 1)),
                     confidence=0.8)

    # 15. CONTROL PC — escribir texto (opcionalmente en una aplicación concreta)
    coincidencia = _ESCRIBIR.match(p)
    if coincidencia:
        texto = frase.grupo(coincidencia, 1)
        destino = ""
        en_app = _ESCRIBIR_EN.match(plano(texto))
        if en_app:
            destino = en_app.group(2)
            texto = _limpiar(texto[:en_app.end(1)])
        return hecho(intent="pc", action="type_text", text=texto, target=destino,
                     confidence=0.9 if texto else 0.3)

    # 16. CONTROL PC — pulsar tecla o combinación
    coincidencia = _PULSAR.match(p)
    if coincidencia:
        return hecho(intent="pc", action="press_key", key=_interpretar_teclas(frase.grupo(coincidencia, 1)),
                     confidence=0.9)

    # 17. MÚSICA — qué suena (se lee de verdad en Windows, nunca se supone)
    app_musica = "spotify" if "spotify" in p else "youtube" if "youtube" in p else ""
    if _MUSICA_SONANDO.match(p):
        return hecho(intent="music", action="now_playing", target=app_musica, confidence=0.95)

    # 18. CONTROL PC — siguiente/anterior canción y volumen
    for patron, objetivo, confianza in _MEDIA:
        if patron.search(p):
            return hecho(intent="pc", action="media", target=objetivo, confidence=confianza)

    # 19. MÚSICA — pausar y reanudar de forma explícita (no la tecla que alterna)
    if _MUSICA_PAUSA.match(p):
        return hecho(intent="music", action="pause", target=app_musica, confidence=0.9)
    if _MUSICA_REANUDAR.match(p):
        return hecho(intent="music", action="resume", target=app_musica, confidence=0.9)

    # 20. MÚSICA — poner una canción, un artista o un estilo
    coincidencia = _MUSICA_PONER.match(p)
    if coincidencia and not _NO_ES_MUSICA.match(coincidencia.group(1)):
        consulta = frase.grupo(coincidencia, 1)
        pista = _QUITAR_PISTA.match(plano(consulta))
        if pista:
            consulta = consulta[pista.end():]
        consulta = _objetivo(consulta)
        servicio = coincidencia.group(2) or ""
        if consulta and (not p.startswith("pon ") or _PISTA_MUSICAL.match(coincidencia.group(1))):
            return hecho(intent="music", action="play_song", query=consulta, target=servicio, confidence=0.9)
        # «pon X» a secas puede ser una aplicación («pon Netflix») o una canción («pon Despacito»):
        # se intenta abrir y, si no existe esa aplicación, se pone como canción.
        objetivo = _sin_articulo(consulta)
        if objetivo and len(objetivo.split()) <= 6:
            return hecho(intent="pc", action="open_app", target=objetivo, confidence=0.7, alternativa="musica")

    # 21. CHAT NORMAL (se envía al núcleo conversacional)
    return hecho(intent="chat", action="unknown", confidence=0.0)


# ==========================================
# API PÚBLICA
# ==========================================

def interpretar(comando: str) -> dict:
    """
    Punto de entrada del motor de intenciones.
    Soporta órdenes encadenadas ("abre spotify y busca lo-fi beats"), devolviendo
    intent="multi" con la lista de sub-intenciones en "steps".
    """
    if not comando or not comando.strip():
        return _intencion()

    original = _Frase(comando)
    frase = _preparar(original)
    if not frase:
        # «Buenos días, Jarvis» se queda vacía al quitar la activación (saludo + nombre).
        if _SALUDO_MANANA.match(original.plano):
            return _intencion(raw=original.texto, intent="briefing", action="greeting", confidence=0.95)
        return _intencion(raw=original.texto)

    # Secuencias: se parte por conectores y solo se acepta si al menos dos partes
    # son órdenes reconocidas; así no se trocean frases naturales como
    # "busca hoteles y restaurantes en Madrid".
    # Lo que va entre comillas es texto literal (el mensaje que mandar): ahí no se parte.
    comillas = _tramos_entre_comillas(frase.texto)
    cortes = [c for c in _CONECTOR.finditer(frase.plano)
              if not any(inicio < c.start() < fin for inicio, fin in comillas)]
    if cortes:
        partes, inicio = [], 0
        for corte in cortes:
            partes.append((inicio, frase.texto[inicio:corte.start()]))
            inicio = corte.end()
        partes.append((inicio, frase.texto[inicio:]))
        partes = [(inicio, parte) for inicio, parte in ((i, _Frase(x)) for i, x in partes) if parte]
        if len(partes) >= 2:
            pasos = []
            for inicio, parte in partes:
                paso = _clasificar_simple(_preparar(parte), parte.plano)
                if paso["intent"] == "message":
                    # El mensaje puede llevar «y» («dile que voy y vuelvo»): desde aquí, todo es el mensaje.
                    cola = _Frase(frase.texto[inicio:])
                    pasos.append(_clasificar_simple(_preparar(cola), cola.plano))
                    break
                pasos.append(paso)
            for anterior, paso in zip(pasos, pasos[1:]):
                # «Abre Discord y escríbele hola a Pepe»: el mensaje va por la aplicación que se acaba de abrir.
                if (paso["intent"] == "message" and not paso.get("app") and anterior["intent"] == "pc"
                        and anterior["action"] == "open_app" and plano(anterior["target"]) in APPS_DE_MENSAJES):
                    paso["app"] = plano(anterior["target"])
            accionables = [paso for paso in pasos if paso["intent"] != "chat"]
            if len(accionables) >= 2:
                resultado = _intencion(intent="multi", action="sequence", steps=pasos,
                                       confidence=min(paso["confidence"] for paso in pasos), raw=frase.texto)
                registro.debug("Secuencia de %d pasos", len(pasos))
                return resultado

    resultado = _clasificar_simple(frase, original.plano)
    # No se registra el texto: podría contener la palabra de seguridad o datos personales.
    registro.debug("Intención %s/%s (%.2f)", resultado["intent"], resultado["action"], resultado["confidence"])
    return resultado
