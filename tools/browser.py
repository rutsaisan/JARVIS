"""
Internet: búsqueda y navegación
================================
Herramienta web aislada del núcleo conversacional (plan técnico, sección 5).

  - buscar_resultados()  -> lista de resultados (título, enlace, resumen, fuente); si la
                            web no da nada claro, se completa con Wikipedia
  - buscar_en_internet() -> los mismos resultados como texto para el modelo (API de la v4)
  - abrir_url() / abrir_busqueda() -> navegación en el navegador por defecto
  - leer_pagina()        -> texto principal de una página, para resumirla
  - hay_internet()       -> comprobación rápida de conexión

Lo que llega de Internet es solo texto que el modelo lee: nunca se ejecuta ni
puede lanzar herramientas (las órdenes solo salen de lo que dice la usuaria).
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import threading
import urllib.parse
import warnings
import webbrowser

from core.text import palabras_clave

registro = logging.getLogger("jarvis.internet")

REGION = "es-es"
WIKIPEDIA = "https://es.wikipedia.org"
# Wikipedia pide que los programas se identifiquen; no se envía ningún dato de la usuaria.
CABECERAS_WIKIPEDIA = {"User-Agent": "JARVIS-asistente-local/1.0 (proyecto educativo)"}
COBERTURA_SUFICIENTE = 0.5   # si ningún resultado cubre la mitad de la consulta, se mira también Wikipedia
MENSAJE_SIN_RESULTADOS = "No se encontraron resultados relevantes en la red."
MENSAJE_ERROR = "Error al intentar conectar con los servicios de búsqueda web."

BUSQUEDA_EN_SITIO = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "wikipedia": "https://es.wikipedia.org/w/index.php?search={q}",
    "amazon": "https://www.amazon.es/s?k={q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "github": "https://github.com/search?q={q}",
    "twitter": "https://x.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "twitch": "https://www.twitch.tv/search?term={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "imagenes": "https://www.google.com/search?tbm=isch&q={q}",
    "google imagenes": "https://www.google.com/search?tbm=isch&q={q}",
}
NOMBRE_SITIO = {
    "youtube": "YouTube", "spotify": "Spotify", "wikipedia": "Wikipedia", "amazon": "Amazon",
    "google maps": "Google Maps", "maps": "Google Maps", "github": "GitHub", "twitter": "X",
    "reddit": "Reddit", "netflix": "Netflix", "twitch": "Twitch", "bing": "Bing", "google": "Google",
    "imagenes": "Google Imágenes", "google imagenes": "Google Imágenes",
}


class ErrorBusqueda(Exception):
    """La búsqueda no se pudo completar (sin conexión, servicio caído...)."""


# ==========================================
# BÚSQUEDA CON UN NAVEGADOR SIN VENTANA
# ==========================================
# Medido en noviembre de 2026 desde este PC: el paquete duckduckgo_search solo consulta ya Bing,
# y Bing devuelve basura a los programas («precio del bitcoin hoy» -> «¿Qué es un precio?»).
# Google, Startpage y Mojeek piden JavaScript o un captcha; DuckDuckGo y Brave bloquean las
# peticiones sin navegador. Con Edge sin ventana (el que trae Windows) y un perfil propio de JARVIS
# (nunca el tuyo), DuckDuckGo da resultados buenos en 2-4 s. Las noticias salen del RSS de Google
# Noticias, que no necesita navegador.
AGENTE_NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
ESPERA_NAVEGADOR = 25            # segundos como mucho por búsqueda
TIEMPO_VIRTUAL = 4000            # ms de «tiempo de página» para que DuckDuckGo pinte los resultados
_cerrojo_navegador = threading.Lock()


def navegador_sin_ventana() -> str:
    """Ruta de Edge (o de Chrome) para usarlo sin ventana; "" si no hay ninguno."""
    if os.name != "nt":
        return ""
    try:
        import winreg
        for nombre in ("msedge.exe", "chrome.exe"):
            for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(raiz, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{nombre}") as clave:
                        ruta = winreg.QueryValue(clave, None)
                    if ruta and os.path.isfile(ruta):
                        return ruta
                except OSError:
                    continue
    except ImportError:
        pass
    for ruta in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.isfile(ruta):
            return ruta
    return ""


def _html_con_navegador(url: str) -> str:
    """El HTML de una página ya pintada por el navegador sin ventana ("" si falla o tarda demasiado)."""
    ejecutable = navegador_sin_ventana()
    if not ejecutable:
        return ""
    from core.config import dir_datos
    perfil = dir_datos() / "navegador_busquedas"           # perfil propio: ni tus cookies ni tu historial
    orden = [ejecutable, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-sync", "--disable-default-apps", "--mute-audio",
             f"--user-data-dir={perfil}", f"--user-agent={AGENTE_NAVEGADOR}", "--lang=es-ES",
             f"--virtual-time-budget={TIEMPO_VIRTUAL}", "--dump-dom", url]
    with _cerrojo_navegador:                                # un mismo perfil no admite dos a la vez
        try:
            proceso = subprocess.Popen(orden, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            registro.info("No se pudo abrir el navegador sin ventana (%s).", type(error).__name__)
            return ""
        try:
            salida, _ = proceso.communicate(timeout=ESPERA_NAVEGADOR)
        except subprocess.TimeoutExpired:
            _matar_arbol(proceso.pid)
            registro.info("El navegador sin ventana ha tardado demasiado.")
            return ""
    return salida.decode("utf-8", "replace")


def _matar_arbol(pid: int) -> None:
    try:
        import psutil
        padre = psutil.Process(pid)
        for hijo in padre.children(recursive=True):
            hijo.kill()
        padre.kill()
    except Exception:
        pass


def _resultados_de_duckduckgo(html: str) -> list:
    from bs4 import BeautifulSoup
    pagina = BeautifulSoup(html, "html.parser")
    resultados = []
    for articulo in pagina.select("article[data-testid='result']"):
        enlace = articulo.select_one("a[data-testid='result-title-a']")
        if not enlace:
            continue
        url = enlace.get("href", "")
        if not url.startswith("http") or "duckduckgo.com/y.js" in url or "bing.com/aclick" in url:
            continue                                         # anuncios
        resumen = articulo.select_one("[data-result='snippet']")
        resultados.append({"titulo": enlace.get_text(" ", strip=True), "url": url,
                           "resumen": resumen.get_text(" ", strip=True) if resumen else "",
                           "fuente": _dominio(url), "fecha": ""})
    return resultados


def buscar_con_navegador(consulta: str) -> list:
    """Resultados de DuckDuckGo pintados por Edge sin ventana ([] si no se puede)."""
    url = "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": consulta, "kl": REGION})
    html = _html_con_navegador(url)
    return _resultados_de_duckduckgo(html) if html else []


def noticias_de_google(consulta: str, maximo: int = 5) -> list:
    """Noticias recientes del RSS de Google Noticias en español ([] si falla)."""
    import requests
    from bs4 import BeautifulSoup
    try:
        respuesta = requests.get("https://news.google.com/rss/search",
                                 params={"q": consulta, "hl": "es", "gl": "ES", "ceid": "ES:es"},
                                 headers={"User-Agent": AGENTE_NAVEGADOR}, timeout=10)
        respuesta.raise_for_status()
        canal = BeautifulSoup(respuesta.text, "xml")
    except Exception as error:
        registro.info("Google Noticias no responde (%s).", type(error).__name__)
        return []
    noticias = []
    for item in canal.find_all("item")[:maximo]:
        titulo = item.title.get_text(strip=True) if item.title else ""
        medio = item.source.get_text(strip=True) if item.source else ""
        if medio and titulo.endswith(f" - {medio}"):
            titulo = titulo[:-len(medio) - 3]
        fecha = item.pubDate.get_text(strip=True) if item.pubDate else ""
        noticias.append({"titulo": titulo, "url": item.link.get_text(strip=True) if item.link else "",
                         "resumen": f"{medio}, {fecha}".strip(", "), "fuente": medio or "news.google.com",
                         "fecha": fecha})
    return [n for n in noticias if n["titulo"] and n["url"]]


def _clase_ddgs():
    try:
        from ddgs import DDGS                    # nombre nuevo del paquete
    except ImportError:
        from duckduckgo_search import DDGS       # nombre con el que se instaló en la v4
    return DDGS


def _dominio(url: str) -> str:
    return urllib.parse.urlparse(url or "").netloc.replace("www.", "")


# ==========================================
# BÚSQUEDA
# ==========================================
def cobertura(consulta: str, resultado: dict) -> float:
    """Qué parte de las palabras clave de la consulta aparece en el título o el resumen (0..1)."""
    claves = set(palabras_clave(consulta))
    if not claves:
        return 1.0
    texto = set(palabras_clave(f"{resultado.get('titulo', '')} {resultado.get('resumen', '')}"))
    return len(claves & texto) / len(claves)


def claves_de_nombres(consulta: str) -> set:
    """Palabras clave de lo que parecen nombres propios de la consulta («Rosalía», «Nicki Nicole»)."""
    return set(palabras_clave(" ".join(p for p in re.findall(r"[^\W\d_][\w'-]*", consulta or "") if p[:1].isupper())))


def ordenar_por_relevancia(consulta: str, resultados: list) -> list:
    """Quita los resultados que no tienen que ver con la consulta y deja primero los más afines.

    El buscador devuelve a veces páginas sin relación (Gmail al buscar a una cantante, la
    película «Origen» al buscar el origen de Rosalía); dárselas al modelo solo lo confunde
    y acaba rellenando con datos inventados. Si la consulta nombra algo, el resultado tiene
    que mencionarlo.
    """
    nombres = claves_de_nombres(consulta)
    puntuados = []
    for posicion, resultado in enumerate(resultados):
        if nombres and not nombres & set(palabras_clave(f"{resultado.get('titulo', '')} {resultado.get('resumen', '')}")):
            continue
        puntuados.append((cobertura(consulta, resultado), posicion, resultado))
    puntuados.sort(key=lambda x: (-x[0], x[1]))
    return [r for puntos, _, r in puntuados if puntos > 0]


def _misma_pagina(url: str) -> str:
    """Para no repetir un artículo: la versión móvil de Wikipedia es la misma página."""
    url = urllib.parse.unquote(url or "")
    return re.sub(r"://(\w+)\.m\.wikipedia\.org", r"://\1.wikipedia.org", url).rstrip("/").lower()


def buscar_en_wikipedia(consulta: str, maximo: int = 2) -> list:
    """Artículos de Wikipedia en español sobre la consulta, con su primer párrafo ([] si falla)."""
    import requests
    try:
        respuesta = requests.get(f"{WIKIPEDIA}/w/api.php", params={
            "action": "query", "generator": "search", "gsrsearch": consulta, "gsrlimit": maximo,
            "prop": "extracts|info", "exintro": 1, "explaintext": 1, "exsentences": 3, "exlimit": maximo,
            "inprop": "url", "format": "json", "formatversion": 2}, headers=CABECERAS_WIKIPEDIA, timeout=8)
        respuesta.raise_for_status()
        paginas = respuesta.json().get("query", {}).get("pages", [])
    except Exception as error:
        registro.info("Wikipedia no responde (%s).", type(error).__name__)
        return []
    paginas = sorted((p for p in paginas if isinstance(p, dict) and p.get("title")), key=lambda p: p.get("index", 0))
    return [{"titulo": p["title"],
             "url": p.get("fullurl") or f"{WIKIPEDIA}/wiki/{urllib.parse.quote(p['title'].replace(' ', '_'), safe='()')}",
             "resumen": " ".join((p.get("extract") or "").split())[:600],
             "fuente": "es.wikipedia.org", "fecha": ""} for p in paginas]


def _completar_con_wikipedia(consulta: str, resultados: list) -> list:
    """Añade delante lo que diga Wikipedia si la web no ha dado nada claro o si se busca algo con
    nombre propio (una persona, un grupo, un lugar...): su primer párrafo es lo más fiable."""
    if (resultados and cobertura(consulta, resultados[0]) >= COBERTURA_SUFICIENTE
            and not claves_de_nombres(consulta)):
        return resultados
    extra = ordenar_por_relevancia(consulta, buscar_en_wikipedia(consulta))
    if not extra:
        return resultados
    nuevas = {_misma_pagina(r["url"]) for r in extra}
    return extra + [r for r in resultados if _misma_pagina(r["url"]) not in nuevas]


def buscar_resultados(consulta: str, max_resultados: int = 5, tipo: str = "web") -> list:
    """Busca en DuckDuckGo. `tipo` es "web" o "noticias". Lanza ErrorBusqueda si falla.

    En las búsquedas generales se descartan los resultados sin relación con la consulta y,
    si no queda nada claro, se completa con Wikipedia (personas, lugares, conceptos...).
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    registro.info("Buscando en la red (%s).", tipo)
    if not _simulando():
        if tipo == "noticias":
            noticias = noticias_de_google(consulta, max_resultados)
            if noticias:
                return noticias
        else:
            resultados = [r for r in buscar_con_navegador(consulta) if r["titulo"]]
            if resultados:
                return _completar_con_wikipedia(consulta, ordenar_por_relevancia(consulta, resultados))[:max_resultados]
        registro.info("Sin resultados del navegador sin ventana: se prueba la búsqueda de respaldo.")
    crudos = []
    # El paquete avisa en cada uso de su cambio de nombre y altera los filtros de
    # avisos de Python: se silencia mientras dura la búsqueda y luego se restaura.
    mostrar_original = warnings.showwarning
    warnings.showwarning = lambda *argumentos, **opciones: None
    try:
        with warnings.catch_warnings():
            with _clase_ddgs()() as ddgs:
                if tipo == "noticias":
                    try:
                        crudos = list(ddgs.news(consulta, region=REGION, max_results=max_resultados))
                    except Exception as error:
                        registro.info("La búsqueda de noticias ha fallado (%s); se usa la búsqueda general.",
                                      type(error).__name__)
                    if not crudos:
                        general = consulta if "noticias" in consulta.lower() else f"noticias {consulta}"
                        crudos = list(ddgs.text(general, region=REGION, max_results=max_resultados))
                else:
                    crudos = list(ddgs.text(consulta, region=REGION, max_results=max_resultados))
    except Exception as error:
        registro.warning("Fallo en la búsqueda: %s", type(error).__name__)
        respaldo = ordenar_por_relevancia(consulta, buscar_en_wikipedia(consulta)) if tipo == "web" else []
        if respaldo:
            return respaldo[:max_resultados]
        raise ErrorBusqueda(str(error)) from error
    finally:
        warnings.showwarning = mostrar_original

    resultados = []
    for crudo in crudos:
        url = crudo.get("href") or crudo.get("url") or ""
        resultados.append({
            "titulo": (crudo.get("title") or "").strip(),
            "url": url,
            "resumen": (crudo.get("body") or "").strip(),
            "fuente": crudo.get("source") or _dominio(url),
            "fecha": crudo.get("date") or "",
        })
    resultados = [r for r in resultados if r["titulo"] and r["url"].startswith("http")]
    if tipo == "web":
        resultados = _completar_con_wikipedia(consulta, ordenar_por_relevancia(consulta, resultados))
    return resultados[:max_resultados]


def formatear_contexto(resultados: list) -> str:
    """Texto estructurado con los resultados, listo para dárselo al modelo."""
    return "\n".join(f"- {r['titulo']} ({r['fuente']}): {r['resumen']}" for r in resultados)


def buscar_en_internet(consulta: str, max_resultados: int = 3) -> str:
    """API de la v4: búsqueda web rápida devuelta como texto para el modelo."""
    try:
        resultados = buscar_resultados(consulta, max_resultados)
    except ErrorBusqueda:
        return MENSAJE_ERROR
    return formatear_contexto(resultados) if resultados else MENSAJE_SIN_RESULTADOS


# ==========================================
# NAVEGACIÓN
# ==========================================
def _simulando() -> bool:
    return os.environ.get("JARVIS_SIMULAR") == "1"


def abrir_url(url: str) -> bool:
    """Abre una dirección en el navegador por defecto. Solo se admiten http y https."""
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return False
    if _simulando():
        registro.info("[SIMULACIÓN] abrir %s", url)
        return True
    try:
        return bool(webbrowser.open(url))
    except Exception as error:
        registro.warning("No se pudo abrir %s: %s", url, error)
        return False


def url_busqueda(sitio: str, consulta: str) -> str | None:
    plantilla = BUSQUEDA_EN_SITIO.get((sitio or "").lower())
    if not plantilla:
        return None
    return plantilla.format(q=urllib.parse.quote_plus(consulta or ""))


def abrir_busqueda(sitio: str, consulta: str) -> tuple:
    """Abre la búsqueda de `consulta` dentro de un sitio. Devuelve (éxito, nombre del sitio, url)."""
    sitio = (sitio or "").lower()
    nombre = NOMBRE_SITIO.get(sitio, sitio.title())
    url = url_busqueda(sitio, consulta)
    if not url:
        return False, nombre, ""
    if sitio == "spotify" and not _simulando():
        # Si la aplicación de Spotify está instalada, se busca directamente en ella.
        try:
            os.startfile(f"spotify:search:{urllib.parse.quote(consulta)}")
            return True, nombre, url
        except (OSError, AttributeError):
            pass
    return abrir_url(url), nombre, url


# ==========================================
# LECTURA DE PÁGINAS
# ==========================================
_REDES_LOCALES = re.compile(r"^(?:localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|\[?::1)")
TAMANO_MAXIMO = 2_000_000


def leer_pagina(url: str, max_caracteres: int = 6000) -> str:
    """Descarga una página pública y devuelve su texto principal ("" si no se puede)."""
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        url = f"https://{url}"
    anfitrion = urllib.parse.urlparse(url).hostname or ""
    if not anfitrion or _REDES_LOCALES.match(anfitrion):
        return ""                                # nunca se leen direcciones de la red local
    try:
        import requests
        from bs4 import BeautifulSoup
        respuesta = requests.get(url, timeout=8, stream=True,
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; AsistenteLocal/1.0)"})
        respuesta.raise_for_status()
        if "html" not in respuesta.headers.get("Content-Type", "html"):
            return ""
        contenido = respuesta.raw.read(TAMANO_MAXIMO, decode_content=True)
        sopa = BeautifulSoup(contenido, "html.parser")
    except Exception as error:
        registro.warning("No se pudo leer %s: %s", url, error)
        return ""
    for etiqueta in sopa(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
        etiqueta.decompose()
    principal = sopa.find("article") or sopa.find("main") or sopa.body or sopa
    texto = re.sub(r"\s+", " ", principal.get_text(" ", strip=True))
    titulo = sopa.title.get_text(strip=True) if sopa.title else ""
    return (f"{titulo}\n{texto}" if titulo else texto)[:max_caracteres]


# ==========================================
# CONEXIÓN
# ==========================================
def hay_internet(espera: float = 2.0) -> bool:
    for servidor, puerto in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((servidor, puerto), timeout=espera):
                return True
        except OSError:
            continue
    return False
