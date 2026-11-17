"""
Buscar y abrir archivos
=======================
«Abre el PDF del TFG», «busca la foto de la playa», «¿dónde está el documento de inglés?»,
«abre lo último que he descargado».

1. Se busca en el índice de búsqueda de Windows (el mismo que usa el Explorador): es
   instantáneo. Se consulta con Search.CollatorDSO, sin instalar nada.
2. Si el índice no responde, se recorren las carpetas personales (Escritorio, Documentos,
   Descargas, Imágenes, Música, Vídeos, OneDrive) con un límite de tiempo.

Solo se buscan y abren archivos de tu carpeta de usuario. Los programas y scripts (.exe, .bat,
.ps1, .lnk…) nunca se abren desde aquí: para abrir programas está «abre X», que pasa por la
lista de aplicaciones y por la capa de seguridad.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from core.text import normalizar
from tools.computer import Resultado, simulando

registro = logging.getLogger("jarvis.archivos")

MAXIMO_RESULTADOS = 8
TIEMPO_RECORRIDO = 3.0          # segundos como mucho recorriendo carpetas (si el índice no responde)
PROFUNDIDAD_MAXIMA = 6

# Lo que nunca se abre desde la búsqueda de archivos (programas, scripts, accesos directos...).
NO_SE_ABREN = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
               ".msi", ".msp", ".scr", ".lnk", ".url", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw", ".sh",
               ".dll", ".sys", ".pif", ".appref-ms", ".application", ".gadget", ".inf", ".msc"}
TIPOS = {
    "pdf": {".pdf"},
    "foto": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".tif", ".tiff"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"},
    "cancion": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"},
    "documento": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages"},
    "word": {".doc", ".docx", ".odt", ".rtf"},
    "presentacion": {".ppt", ".pptx", ".odp", ".key"},
    "excel": {".xls", ".xlsx", ".csv", ".ods"},
    "codigo": {".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".html", ".htm", ".css", ".scss", ".java", ".kt", ".cs",
               ".c", ".cpp", ".h", ".hpp", ".go", ".rb", ".swift", ".dart", ".vue", ".sql", ".json", ".xml", ".yml",
               ".yaml", ".sh", ".ps1", ".bat", ".ipynb", ".r", ".lua", ".rs", ".twig", ".blade.php"},
}
_PALABRAS_DE_TIPO = {
    "pdf": "pdf", "pdfs": "pdf", "foto": "foto", "fotos": "foto", "imagen": "foto", "imagenes": "foto",
    "captura": "foto", "video": "video", "videos": "video", "cancion": "cancion", "canciones": "cancion",
    "audio": "cancion", "documento": "documento", "documentos": "documento", "word": "word",
    "presentacion": "presentacion", "presentaciones": "presentacion", "powerpoint": "presentacion",
    "diapositivas": "presentacion", "excel": "excel", "hoja de calculo": "excel", "hojas de calculo": "excel",
    "codigo": "codigo", "codigo fuente": "codigo", "script": "codigo", "scripts": "codigo",
}
_VACIAS = set("""el la los las un una unos unas de del al a en con por para que se llama llamado llamada mi mis tu
tus su sus lo archivo archivos fichero ficheros carpeta y o e sobre donde esta estan tengo guarde guardado guardada
este esta ese esa aquel aquella""".split())
_NO_RECORRER = {"appdata", "node_modules", ".git", "__pycache__", ".venv", "venv", "$recycle.bin", ".cache",
                "site-packages", ".idea", ".vscode", "temp", "tmp"}
_CARPETAS = ("Desktop", "Escritorio", "Documents", "Documentos", "Downloads", "Descargas", "Pictures", "Imágenes",
             "Music", "Música", "Videos", "Vídeos", "OneDrive")


def interpretar_busqueda(texto: str) -> tuple:
    """«el PDF del TFG» -> (["tfg"], "pdf"). Devuelve (palabras a buscar, tipo o "")."""
    plano = normalizar(texto or "")
    tipo = ""
    for frase, valor in sorted(_PALABRAS_DE_TIPO.items(), key=lambda par: -len(par[0])):
        if re.search(rf"\b{frase}\b", plano):
            tipo = tipo or valor
            plano = re.sub(rf"\b{frase}\b", " ", plano)
    palabras = [p for p in plano.split() if p not in _VACIAS and len(p) > 1]
    return palabras, tipo


def _extension_valida(ruta: str, tipo: str) -> bool:
    extension = os.path.splitext(ruta)[1].lower()
    return extension in TIPOS[tipo] if tipo in TIPOS else True


def _dato(ruta: str, fecha: float | None = None, tamano: int | None = None) -> dict:
    try:
        estado = os.stat(ruta)
        fecha = fecha or estado.st_mtime
        tamano = tamano if tamano is not None else estado.st_size
    except OSError:
        pass
    return {"nombre": os.path.basename(ruta), "ruta": ruta, "carpeta": os.path.basename(os.path.dirname(ruta)),
            "fecha": fecha or 0, "tamano": tamano or 0}


# ==========================================
# 1) ÍNDICE DE BÚSQUEDA DE WINDOWS
# ==========================================
def _ruta_de_url(url) -> str:
    """«file:C:/Users/Ruth/Documents/TFG.pdf» -> la ruta de Windows de ese archivo."""
    import urllib.parse
    url = str(url or "")
    if not url.lower().startswith("file:"):
        return ""
    ruta = urllib.parse.unquote(url[5:])
    if re.match(r"^/*[a-zA-Z]:", ruta):              # «file:C:/…» o «file:///C:/…»
        ruta = ruta.lstrip("/")
    return os.path.normpath(ruta)

def _buscar_en_indice(palabras: list, tipo: str, maximo: int) -> list | None:
    """None si el índice no responde (entonces se recorren las carpetas)."""
    if os.name != "nt":
        return None
    try:
        import pythoncom
        from win32com.client import Dispatch
    except ImportError:
        return None
    pythoncom.CoInitialize()                       # cada hilo que usa COM tiene que inicializarlo
    try:
        conexion = Dispatch("ADODB.Connection")
        conexion.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        ambito = "file:" + str(Path.home()).replace("\\", "/")
        condiciones = [f"SCOPE='{ambito}'", "System.ItemType <> 'Directory'"]
        for palabra in palabras:
            limpia = re.sub(r"[^a-z0-9ñ]", "", palabra)
            if limpia:
                condiciones.append(f"System.FileName LIKE '%{limpia}%'")
        if tipo in TIPOS:
            condiciones.append("(" + " OR ".join(f"System.FileExtension = '{e}'" for e in sorted(TIPOS[tipo])) + ")")
        # System.ItemUrl es la ruta real; System.ItemPathDisplay trae los nombres traducidos
        # («Documentos» en vez de «Documents») y no existe en el disco.
        consulta = (f"SELECT TOP {maximo * 3} System.ItemUrl, System.DateModified, System.Size "
                    f"FROM SystemIndex WHERE {' AND '.join(condiciones)} ORDER BY System.DateModified DESC")
        registros = Dispatch("ADODB.Recordset")
        registros.Open(consulta, conexion)
        resultados = []
        while not registros.EOF:
            ruta = _ruta_de_url(registros.Fields.Item("System.ItemUrl").Value)
            if ruta and os.path.isfile(ruta):
                tamano = registros.Fields.Item("System.Size").Value
                resultados.append(_dato(ruta, tamano=int(tamano) if tamano is not None else None))
            registros.MoveNext()
        registros.Close()
        conexion.Close()
        return resultados
    except Exception as error:
        registro.info("El índice de búsqueda de Windows no responde (%s): se recorren las carpetas.",
                      type(error).__name__)
        return None
    finally:
        pythoncom.CoUninitialize()


# ==========================================
# 2) RECORRIDO DE LAS CARPETAS PERSONALES
# ==========================================
def carpetas_personales() -> list:
    casa = Path.home()
    vistas, carpetas = set(), []
    for nombre in _CARPETAS:
        carpeta = casa / nombre
        if carpeta.is_dir() and carpeta.resolve() not in vistas:
            vistas.add(carpeta.resolve())
            carpetas.append(carpeta)
    return carpetas


def _recorrer(palabras: list, tipo: str, maximo: int, carpetas: list | None = None) -> list:
    limite = time.monotonic() + TIEMPO_RECORRIDO
    resultados = []
    for base in carpetas or carpetas_personales():
        for raiz, directorios, ficheros in os.walk(base):
            if time.monotonic() > limite:
                return resultados
            profundidad = len(Path(raiz).relative_to(base).parts)
            directorios[:] = [] if profundidad >= PROFUNDIDAD_MAXIMA else [
                d for d in directorios if not d.startswith(".") and d.lower() not in _NO_RECORRER]
            for fichero in ficheros:
                nombre = normalizar(fichero)
                if all(palabra in nombre for palabra in palabras) and _extension_valida(fichero, tipo):
                    resultados.append(_dato(os.path.join(raiz, fichero)))
    return resultados


def buscar(texto: str, maximo: int = MAXIMO_RESULTADOS, carpetas: list | None = None) -> list:
    """Archivos de la carpeta de usuario cuyo nombre encaja con lo dicho, los más recientes primero."""
    palabras, tipo = interpretar_busqueda(texto)
    if not palabras and not tipo:
        return []
    resultados = None if carpetas else _buscar_en_indice(palabras, tipo, maximo)
    if resultados is None:
        resultados = _recorrer(palabras, tipo, maximo, carpetas)
    if not resultados and len(palabras) > 1:
        # «informe del Club de Ajedrez de Sevilla»: ningún nombre lleva TODAS las palabras.
        # Se prueba con las más distintivas (las largas) por separado y se ordena por cuántas lleva.
        for palabra in sorted((p for p in palabras if len(p) >= 4), key=len, reverse=True)[:3]:
            sueltos = None if carpetas else _buscar_en_indice([palabra], tipo, maximo)
            resultados.extend(sueltos if sueltos is not None else _recorrer([palabra], tipo, maximo, carpetas))
    # Primero los que tienen todas las palabras en el nombre; luego, los más recientes.
    def orden(dato):
        nombre = normalizar(dato["nombre"])
        return (-sum(palabra in nombre for palabra in palabras), -dato["fecha"])
    unicos = {dato["ruta"].lower(): dato for dato in resultados}
    return sorted(unicos.values(), key=orden)[:maximo]


def buscar_carpetas(texto: str, maximo: int = 5) -> list:
    """Carpetas de tus carpetas personales cuyo nombre lleva lo dicho («la carpeta del TFG», «ClubApp»)."""
    palabras, _ = interpretar_busqueda(texto)
    palabras = [p for p in palabras if p not in ("codigo", "proyecto", "carpeta", "carpetas")]
    if not palabras:
        return []
    # Primero las que llevan todas las palabras; si no hay, las que llevan alguna de las más distintivas.
    largas = sorted((p for p in palabras if len(p) >= 4), key=len, reverse=True)[:3]
    limite = time.monotonic() + TIEMPO_RECORRIDO
    todas, alguna = [], []
    for base in carpetas_personales():
        for raiz, directorios, _ficheros in os.walk(base):
            if time.monotonic() > limite or len(todas) >= maximo:
                return (todas or alguna)[:maximo]
            if _carpeta_de_jarvis(raiz) or len(Path(raiz).relative_to(base).parts) >= PROFUNDIDAD_MAXIMA:
                directorios[:] = []
                continue
            directorios[:] = [d for d in directorios if not d.startswith(".") and d.lower() not in _RUIDO_DE_PROYECTO]
            for directorio in directorios:
                nombre = normalizar(directorio).replace(" ", "")
                if all(palabra in nombre for palabra in palabras):
                    todas.append(_dato(os.path.join(raiz, directorio)))
                elif len(palabras) > 1 and any(palabra in nombre for palabra in largas):
                    alguna.append(_dato(os.path.join(raiz, directorio)))
    return (todas or alguna)[:maximo]


def ultimos_descargados(cantidad: int = 1, carpeta: Path | None = None) -> list:
    carpeta = carpeta or next((c for c in (Path.home() / "Downloads", Path.home() / "Descargas") if c.is_dir()), None)
    if not carpeta:
        return []
    ficheros = [f for f in carpeta.iterdir() if f.is_file() and not f.name.startswith(".")
                and f.suffix.lower() not in (".crdownload", ".part", ".tmp", ".partial", ".download")]
    ficheros.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [_dato(str(f)) for f in ficheros[:cantidad]]


def se_puede_abrir(ruta: str) -> bool:
    return os.path.splitext(ruta)[1].lower() not in NO_SE_ABREN


def abrir(ruta: str) -> Resultado:
    nombre = os.path.basename(ruta)
    if not se_puede_abrir(ruta):
        return Resultado(False, f"«{nombre}» es un programa o un script, y por seguridad no se abre desde la búsqueda "
                                "de archivos. Si es una aplicación, dime «abre» y su nombre.")
    if not os.path.isfile(ruta):
        return Resultado(False, f"«{nombre}» ya no está donde estaba.")
    if simulando():
        registro.info("[SIMULACIÓN] abrir archivo %s", ruta)
        return Resultado(True, f"He abierto «{nombre}».", {"ruta": ruta})
    try:
        os.startfile(ruta)
    except OSError as error:
        registro.warning("No se pudo abrir %s: %s", ruta, error)
        return Resultado(False, f"No he podido abrir «{nombre}».")
    return Resultado(True, f"He abierto «{nombre}».", {"ruta": ruta})


def describir(dato: dict) -> str:
    fecha = time.strftime("%d/%m/%Y", time.localtime(dato["fecha"])) if dato.get("fecha") else ""
    return f"{dato['nombre']} (en {dato['carpeta']}{', ' + fecha if fecha else ''})"


# ==========================================
# LEER ARCHIVOS, VER CARPETAS Y BUSCAR DENTRO
# ==========================================
# «Lee el informe del TFG», «¿qué hay en C:\Users\Ruth\Documents\FP\ClubApp_app?», «busca en mi
# proyecto dónde uso session_start». Pasó: sin esto, la IA abría el Word en pantalla para leerlo
# con el reconocimiento de texto, y del código de una carpeta no veía nada.
# Solo lectura, solo dentro de la carpeta de usuario, y nunca lo que suele guardar contraseñas.
MAXIMO_LECTURA = 6000            # caracteres de un archivo que se le dan al modelo
MAXIMO_TAMANO = 30 * 1024 * 1024
MAXIMO_ENTRADAS = 60             # elementos de una carpeta que se enseñan
TIEMPO_BUSQUEDA_CONTENIDO = 4.0  # segundos como mucho buscando un texto dentro de los archivos
MAXIMO_TAMANO_TEXTO = 2 * 1024 * 1024
EXTENSIONES_TEXTO = (TIPOS["codigo"] | {".txt", ".md", ".csv", ".log", ".ini", ".cfg", ".conf", ".toml", ".tex",
                                        ".rst", ".env.example", ".gitignore", ".htaccess"})
_RUIDO_DE_PROYECTO = _NO_RECORRER | {"vendor", "dist", "build", "out", "bin", "obj", "target", ".next", ".nuxt",
                                     "coverage", ".gradle", ".dart_tool"}
# Archivos que suelen guardar contraseñas, claves o tokens: no se leen nunca (las plantillas
# como «.env.example» sí; el código que trata contraseñas, como «recuperar_password.php», también).
_SECRETOS = re.compile(
    r"^\.env(?!\.(?:example|sample|dist|template)$)(?:\..*)?$|\.(?:pem|key|pfx|p12|kdbx|keystore|jks|ppk)$|"
    r"^id_(?:rsa|dsa|ecdsa|ed25519)|^(?:secretos|seguridad|credentials?|secrets?)\.json$|^\.?(?:netrc|npmrc|pypirc)$|"
    r"^(?:mis[_ ]?)?(?:passwords?|contrasenas?|claves)\.(?:txt|csv|xlsx?|docx?|json|md)$",
    re.IGNORECASE)
_CARPETAS_PRIVADAS = {"appdata", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config"}


def es_secreto(nombre: str) -> bool:
    from core.text import plano
    return bool(_SECRETOS.search(plano(nombre or "")))


def _carpeta_de_jarvis(raiz: str) -> bool:
    """Los datos de JARVIS (ajustes, seguridad, registros) quedan fuera de cualquier recorrido."""
    from core.config import dir_datos
    try:
        Path(raiz).resolve().relative_to(dir_datos().resolve())
        return True
    except (ValueError, OSError):
        return False


def _limpiar_ruta(texto: str) -> str:
    """Lo que dice el modelo o la usuaria («"C:\\Users\\Ruth\\Documents\\FP"», «~/Documents») a una ruta."""
    texto = str(texto or "").strip().strip("\"'«»“”`").strip()
    texto = texto.replace("\\\\", "\\").replace("/", os.sep)
    return os.path.normpath(os.path.expanduser(texto)) if texto else ""


def ruta_permitida(ruta: str) -> tuple:
    """(se puede, motivo). Dentro de la carpeta de usuario, fuera de AppData y de los datos de JARVIS."""
    try:
        real = Path(ruta).resolve()
    except (OSError, ValueError):
        return False, "Esa ruta no es válida."
    casa = Path.home().resolve()
    try:
        partes = real.relative_to(casa).parts
    except ValueError:
        return False, "Solo miro dentro de tu carpeta de usuario."
    if any(parte.lower() in _CARPETAS_PRIVADAS for parte in partes):
        return False, "Esa carpeta es de configuración privada de los programas: no la miro."
    from core.config import dir_datos
    try:
        real.relative_to(dir_datos().resolve())
        return False, "Esa es la carpeta de datos de JARVIS (tus ajustes y la seguridad): no la leo."
    except ValueError:
        pass
    if real.is_file() and es_secreto(real.name):
        return False, f"«{real.name}» suele guardar contraseñas o claves: no lo leo."
    return True, ""


def _resolver(texto: str, carpeta: bool) -> str:
    """La ruta tal cual si existe; si no, una carpeta o un archivo con ese nombre en tus carpetas."""
    ruta = _limpiar_ruta(texto)
    if not ruta:
        return ""
    if os.path.isabs(ruta) and (os.path.isdir(ruta) if carpeta else os.path.isfile(ruta)):
        return ruta
    nombre = normalizar(os.path.basename(ruta) or ruta)
    if not nombre:
        return ""
    limite = time.monotonic() + TIEMPO_RECORRIDO
    for base in carpetas_personales():
        for raiz, directorios, ficheros in os.walk(base):
            if time.monotonic() > limite:
                return ""
            if _carpeta_de_jarvis(raiz):
                directorios[:] = []
                continue
            directorios[:] = [d for d in directorios if not d.startswith(".") and d.lower() not in _RUIDO_DE_PROYECTO]
            for candidato in (directorios if carpeta else ficheros):
                if normalizar(candidato) == nombre:
                    return os.path.join(raiz, candidato)
    return ""


def _texto_de_xml(contenido: bytes, etiqueta: str, parrafo: str) -> str:
    """Texto de un XML de Office: une los <etiqueta> y separa por <parrafo>."""
    texto = contenido.decode("utf-8", "replace")
    trozos = []
    for bloque in re.split(rf"</{parrafo}>", texto):
        partes = re.findall(rf"<{etiqueta}(?:\s[^>]*)?>([^<]*)</{etiqueta}>", bloque)
        if partes:
            trozos.append("".join(partes))
    import html
    return html.unescape("\n".join(trozos))


def _leer_office(ruta: str, extension: str) -> str:
    import zipfile
    with zipfile.ZipFile(ruta) as zip_:
        nombres = zip_.namelist()
        if extension == ".docx":
            return _texto_de_xml(zip_.read("word/document.xml"), "w:t", "w:p")
        if extension == ".pptx":
            diapositivas = sorted((n for n in nombres if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                                  key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
            return "\n\n".join(f"[Diapositiva {i}]\n" + _texto_de_xml(zip_.read(n), "a:t", "a:p")
                               for i, n in enumerate(diapositivas, start=1))
        # .xlsx: textos compartidos y valores de la primera hoja, fila a fila
        compartidos = []
        if "xl/sharedStrings.xml" in nombres:
            xml = zip_.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            compartidos = ["".join(re.findall(r"<t(?:\s[^>]*)?>([^<]*)</t>", si))
                           for si in re.findall(r"<si>(.*?)</si>", xml, re.DOTALL)]
        hoja = next((n for n in sorted(nombres) if re.match(r"xl/worksheets/sheet\d+\.xml$", n)), None)
        if not hoja:
            return ""
        filas = []
        for fila in re.findall(r"<row[^>]*>(.*?)</row>", zip_.read(hoja).decode("utf-8", "replace"), re.DOTALL):
            valores = []
            for atributos, interior in re.findall(r"<c([^>]*)>(.*?)</c>", fila, re.DOTALL):
                valor = re.search(r"<v>([^<]*)</v>", interior) or re.search(r"<t[^>]*>([^<]*)</t>", interior)
                valor = valor.group(1) if valor else ""
                if 't="s"' in atributos and valor.isdigit() and int(valor) < len(compartidos):
                    valor = compartidos[int(valor)]
                valores.append(valor)
            if any(valores):
                filas.append(" | ".join(valores))
        import html
        return html.unescape("\n".join(filas))


def _leer_pdf(ruta: str) -> str:
    from pypdf import PdfReader
    lector = PdfReader(ruta)
    trozos, total = [], 0
    for numero, pagina in enumerate(lector.pages, start=1):
        texto = (pagina.extract_text() or "").strip()
        if texto:
            trozos.append(f"[Página {numero}]\n{texto}")
            total += len(texto)
        if total > MAXIMO_LECTURA * 2:
            break
    return "\n\n".join(trozos)


def leer(texto: str) -> dict:
    """Contenido de un archivo: {"ok", "mensaje", "nombre", "ruta", "texto", "recortado"}."""
    ruta = _resolver(texto, carpeta=False)
    if not ruta:
        return {"ok": False, "mensaje": f"No encuentro el archivo «{texto}». Dime la ruta completa o búscalo antes."}
    nombre = os.path.basename(ruta)
    permitido, motivo = ruta_permitida(ruta)
    if not permitido:
        return {"ok": False, "mensaje": motivo}
    extension = os.path.splitext(nombre)[1].lower()
    try:
        if os.path.getsize(ruta) > MAXIMO_TAMANO:
            return {"ok": False, "mensaje": f"«{nombre}» es demasiado grande para leerlo entero."}
        if extension in (".docx", ".pptx", ".xlsx"):
            contenido = _leer_office(ruta, extension)
        elif extension == ".pdf":
            contenido = _leer_pdf(ruta)
        elif extension in EXTENSIONES_TEXTO or nombre.lower().endswith(tuple(EXTENSIONES_TEXTO)):
            with open(ruta, "rb") as fichero:
                crudo = fichero.read(MAXIMO_TAMANO_TEXTO)
            try:
                contenido = crudo.decode("utf-8")
            except UnicodeDecodeError:
                contenido = crudo.decode("cp1252", "replace")
        else:
            return {"ok": False, "mensaje": f"No sé leer archivos «{extension or 'sin extensión'}» como «{nombre}»."}
    except Exception as error:
        registro.info("No se pudo leer %s: %s", nombre, type(error).__name__)
        return {"ok": False, "mensaje": f"No he podido leer «{nombre}»."}
    contenido = contenido.strip()
    if not contenido:
        return {"ok": False, "mensaje": f"«{nombre}» no tiene texto que se pueda leer (quizá son solo imágenes)."}
    return {"ok": True, "mensaje": "", "nombre": nombre, "ruta": ruta, "texto": contenido[:MAXIMO_LECTURA],
            "recortado": len(contenido) > MAXIMO_LECTURA, "caracteres": len(contenido)}


def _tamano_legible(bytes_: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024 or unidad == "GB":
            return f"{bytes_:.0f} {unidad}" if unidad == "B" else f"{bytes_:.1f} {unidad}"
        bytes_ /= 1024
    return ""


def explorar(texto: str) -> dict:
    """Qué hay en una carpeta, con un nivel de subcarpetas: {"ok", "mensaje", "ruta", "texto", "archivos"}."""
    ruta = _resolver(texto, carpeta=True)
    if not ruta:
        return {"ok": False, "mensaje": f"No encuentro la carpeta «{texto}». Dime la ruta completa."}
    permitido, motivo = ruta_permitida(ruta)
    if not permitido:
        return {"ok": False, "mensaje": motivo}
    lineas, archivos, extensiones, omitidas = [], [], {}, []

    def entradas(carpeta: str) -> list:
        try:
            return sorted(os.scandir(carpeta), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return []

    for entrada in entradas(ruta):
        if len(lineas) >= MAXIMO_ENTRADAS:
            lineas.append("… (hay más)")
            break
        if (entrada.name.startswith(".") or entrada.name.lower() in _RUIDO_DE_PROYECTO
                or (entrada.is_dir() and _carpeta_de_jarvis(entrada.path))):
            omitidas.append(entrada.name)
            continue
        if entrada.is_dir():
            hijos = [h for h in entradas(entrada.path) if not h.name.startswith(".")
                     and h.name.lower() not in _RUIDO_DE_PROYECTO]
            muestra = ", ".join(h.name + ("/" if h.is_dir() else "") for h in hijos[:8])
            lineas.append(f"[carpeta] {entrada.name}/  ({len(hijos)} elementos{': ' + muestra if muestra else ''}"
                          f"{', …' if len(hijos) > 8 else ''})")
            for hijo in hijos:
                if hijo.is_file():
                    extension = os.path.splitext(hijo.name)[1].lower() or "(sin extensión)"
                    extensiones[extension] = extensiones.get(extension, 0) + 1
        else:
            try:
                estado = entrada.stat()
                tamano, fecha = estado.st_size, estado.st_mtime
            except OSError:
                tamano, fecha = 0, 0
            extension = os.path.splitext(entrada.name)[1].lower() or "(sin extensión)"
            extensiones[extension] = extensiones.get(extension, 0) + 1
            lineas.append(f"{len(archivos) + 1}) {entrada.name}  ({_tamano_legible(tamano)}, "
                          f"{time.strftime('%d/%m/%Y', time.localtime(fecha)) if fecha else '?'})")
            archivos.append(_dato(entrada.path, fecha=fecha, tamano=tamano))
    resumen = ", ".join(f"{n} {e}" for e, n in sorted(extensiones.items(), key=lambda par: -par[1])[:8])
    cabecera = f"Carpeta {ruta}" + (f" — tipos de archivo: {resumen}" if resumen else "")
    if omitidas:
        cabecera += f" (no muestro {', '.join(omitidas[:6])}: son internas o de dependencias)"
    if not lineas:
        lineas = ["(vacía)"]
    return {"ok": True, "mensaje": "", "ruta": ruta, "texto": cabecera + "\n" + "\n".join(lineas),
            "archivos": archivos}


def _fragmento(linea: str, texto: str) -> str:
    linea = " ".join(linea.split())
    posicion = plano_simple(linea).find(plano_simple(texto))
    inicio = max(0, posicion - 60)
    return ("…" if inicio else "") + linea[inicio:inicio + 180]


def plano_simple(texto: str) -> str:
    from core.text import plano
    return plano(texto or "")


def buscar_contenido(texto: str, carpeta: str = "", maximo: int = MAXIMO_RESULTADOS) -> list:
    """Archivos de texto o código que CONTIENEN `texto` (en una carpeta o en las personales), con el trozo."""
    buscado = plano_simple(texto).strip()
    if len(buscado) < 2:
        return []
    if carpeta:
        ruta = _resolver(carpeta, carpeta=True)
        if not ruta or not ruta_permitida(ruta)[0]:
            return []
        bases = [Path(ruta)]
    else:
        bases = carpetas_personales()
    limite = time.monotonic() + TIEMPO_BUSQUEDA_CONTENIDO
    resultados = []
    for base in bases:
        for raiz, directorios, ficheros in os.walk(base):
            if time.monotonic() > limite or len(resultados) >= maximo:
                return resultados
            if _carpeta_de_jarvis(raiz):
                directorios[:] = []
                continue
            directorios[:] = [d for d in directorios if not d.startswith(".") and d.lower() not in _RUIDO_DE_PROYECTO]
            for fichero in ficheros:
                extension = os.path.splitext(fichero)[1].lower()
                if extension not in EXTENSIONES_TEXTO or es_secreto(fichero):
                    continue
                camino = os.path.join(raiz, fichero)
                try:
                    if os.path.getsize(camino) > MAXIMO_TAMANO_TEXTO:
                        continue
                    with open(camino, "r", encoding="utf-8", errors="replace") as abierto:
                        for numero, linea in enumerate(abierto, start=1):
                            if buscado in plano_simple(linea):
                                dato = _dato(camino)
                                dato.update(linea=numero, fragmento=_fragmento(linea, texto))
                                resultados.append(dato)
                                break
                except OSError:
                    continue
                if len(resultados) >= maximo:
                    return resultados
    return resultados
