"""
Control del ordenador
======================
Las «manos» de JARVIS en Windows. Todo lo que hay aquí son acciones concretas
de un catálogo cerrado; nunca se ejecutan comandos arbitrarios (sección 9 del
plan técnico). Quién decide SI algo se ejecuta es tools/security.py.

  - Abrir: cualquier programa del menú Inicio (incluidas las apps de la
    Microsoft Store), los juegos de Steam y Epic (aunque no tengan acceso
    directo), carpetas personales y webs conocidas.
  - Cerrar: la ventana activa o las ventanas de una aplicación (cierre
    ordenado: la aplicación puede pedir que guardes tu trabajo).
  - Escribir: pega texto en la ventana en la que estabas, o en una aplicación.
  - Teclas, multimedia y volumen.
  - Comandos de sistema: apagar, reiniciar, suspender, bloquear, cerrar sesión,
    modo oscuro/claro, captura de pantalla, vaciar la papelera...

Con la variable de entorno JARVIS_SIMULAR=1 ninguna acción se ejecuta de
verdad (se registra y se da por hecha): lo usan las pruebas.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from core.config import dir_datos
from core.text import normalizar

registro = logging.getLogger("jarvis.ordenador")

ES_WINDOWS = os.name == "nt"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CADUCIDAD_INDICE = 12 * 3600          # el índice de aplicaciones se rehace cada 12 h
VERSION_INDICE = 2                    # súbela si cambia lo que se indexa: los índices guardados antiguos se rehacen
WM_CLOSE = 0x0010


@dataclass
class Resultado:
    ok: bool
    mensaje: str
    datos: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.mensaje


def simulando() -> bool:
    return os.environ.get("JARVIS_SIMULAR") == "1"


def _pyautogui():
    import pyautogui
    pyautogui.PAUSE = 0.03
    return pyautogui


# ==========================================
# ÍNDICE DE APLICACIONES
# ==========================================
_INDICE = []
_INDICE_FECHA = 0.0
_CERROJO_INDICE = threading.Lock()

_NO_INDEXAR = re.compile(
    r"\b(?:uninstall|desinstalar|readme|leeme|help|ayuda|manual|documentation|documentacion|website|sitio web|"
    r"license|licencia|release notes|changelog|installer|instalador|repair|reparar|updater|actualizador|"
    r"crash|faq|support|soporte)\b"
)

# nombre dicho -> (nombre con el que buscar en el índice, orden de respaldo si no aparece)
ALIAS = {
    "notepad": ("bloc de notas", "notepad.exe"), "bloc de notas": (None, "notepad.exe"),
    "calc": ("calculadora", "calc.exe"), "calculadora": (None, "calc.exe"),
    "cmd": ("simbolo del sistema", "cmd.exe"), "simbolo del sistema": (None, "cmd.exe"),
    "consola": ("simbolo del sistema", "cmd.exe"),
    "powershell": ("windows powershell", "powershell.exe"), "terminal": (None, "wt.exe"),
    "explorador de archivos": (None, "explorer.exe"), "explorador": ("explorador de archivos", "explorer.exe"),
    "paint": (None, "mspaint.exe"), "administrador de tareas": (None, "taskmgr.exe"),
    "panel de control": (None, "control.exe"),
    "editor del registro": (None, "regedit.exe"), "regedit": ("editor del registro", "regedit.exe"),
    "configuracion": (None, "ms-settings:"), "ajustes": ("configuracion", "ms-settings:"),
    "ajustes de windows": ("configuracion", "ms-settings:"),
    "configuracion de windows": ("configuracion", "ms-settings:"),
    "vs code": ("visual studio code", "code"), "vscode": ("visual studio code", "code"),
    "code": ("visual studio code", "code"),
    "word": (None, "winword.exe"), "excel": (None, "excel.exe"), "powerpoint": (None, "powerpnt.exe"),
    "navegador": (None, "https://www.google.com"), "internet": (None, "https://www.google.com"),
    "tienda": ("microsoft store", "ms-windows-store:"), "camara": (None, "microsoft.windows.camera:"),
    "fotos": (None, "ms-photos:"), "reloj": (None, "ms-clock:"), "steam": (None, "steam://open/main"),
}

SITIOS_WEB = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "netflix": "https://www.netflix.com", "twitch": "https://www.twitch.tv", "github": "https://github.com",
    "wikipedia": "https://es.wikipedia.org", "whatsapp web": "https://web.whatsapp.com",
    "whatsapp": "https://web.whatsapp.com", "instagram": "https://www.instagram.com",
    "twitter": "https://x.com", "x": "https://x.com", "amazon": "https://www.amazon.es",
    "google maps": "https://www.google.com/maps", "maps": "https://www.google.com/maps",
    "mapas": "https://www.google.com/maps", "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com", "facebook": "https://www.facebook.com",
    "tiktok": "https://www.tiktok.com", "spotify": "https://open.spotify.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "outlook": "https://outlook.live.com", "telegram": "https://web.telegram.org",
    "discord": "https://discord.com/app", "prime video": "https://www.primevideo.com",
    "disney plus": "https://www.disneyplus.com", "hbo": "https://play.max.com", "max": "https://play.max.com",
    "google calendar": "https://calendar.google.com", "traductor": "https://translate.google.es",
}

NOMBRES_WEB = {
    "youtube": "YouTube", "github": "GitHub", "whatsapp web": "WhatsApp Web", "whatsapp": "WhatsApp Web",
    "linkedin": "LinkedIn", "tiktok": "TikTok", "hbo": "Max", "x": "X", "twitter": "X",
    "google maps": "Google Maps", "maps": "Google Maps", "mapas": "Google Maps",
    "google drive": "Google Drive", "drive": "Google Drive", "prime video": "Prime Video",
    "disney plus": "Disney+", "google calendar": "Google Calendar", "traductor": "el traductor de Google",
}

# carpeta dicha -> valor en el registro de Windows («User Shell Folders») y nombre por defecto
CARPETAS = {
    "descargas": ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
    "documentos": ("Personal", "Documents"),
    "escritorio": ("Desktop", "Desktop"),
    "imagenes": ("My Pictures", "Pictures"),
    "musica": ("My Music", "Music"),
    "videos": ("My Video", "Videos"),
}
_PATRON_CARPETA = re.compile(
    r"^(?:(?:la\s+)?carpeta\s+(?:de\s+)?(?:mis\s+)?|mis\s+)(descargas|documentos|escritorio|imagenes|musica|videos)$"
    r"|^(descargas|documentos)$"
)
_PATRON_DOMINIO = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$")
_RELLENO_OBJETIVO = re.compile(r"^(?:el|la|los|las|mi|mis|un|una|la aplicacion de|la aplicacion|la app de|la app|"
                               r"el programa|el juego|la pagina de|la pagina|la web de|la web)\s+")


def carpeta_conocida(nombre: str) -> Path:
    """Ruta real de una carpeta personal (respeta carpetas movidas a OneDrive)."""
    valor, por_defecto = CARPETAS[nombre]
    if ES_WINDOWS:
        try:
            import winreg
            clave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as abierta:
                ruta = winreg.ExpandEnvironmentStrings(winreg.QueryValueEx(abierta, valor)[0])
            if os.path.isdir(ruta):
                return Path(ruta)
        except OSError:
            pass
    return Path.home() / por_defecto


def _entrada(nombre: str, tipo: str, destino: str) -> dict:
    return {"nombre": nombre, "clave": normalizar(nombre), "tipo": tipo, "destino": destino}


def _apps_menu_inicio() -> list:
    """Accesos directos del menú Inicio y del escritorio (método de la v4)."""
    carpetas = []
    for variable in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            carpetas.append(os.path.join(base, r"Microsoft\Windows\Start Menu\Programs"))
    for variable in ("USERPROFILE", "PUBLIC"):
        base = os.environ.get(variable)
        if base:
            carpetas.append(os.path.join(base, "Desktop"))
    carpetas.append(str(carpeta_conocida("escritorio")))

    entradas = []
    for carpeta in carpetas:
        if not os.path.isdir(carpeta):
            continue
        for raiz, _, ficheros in os.walk(carpeta):
            for fichero in ficheros:
                if fichero.lower().endswith((".lnk", ".url", ".exe")):
                    entradas.append(_entrada(os.path.splitext(fichero)[0], "ruta", os.path.join(raiz, fichero)))
    return entradas


def _apps_get_startapps() -> list:
    """Todas las aplicaciones que Windows muestra en Inicio, incluidas las de la Store."""
    if not ES_WINDOWS:
        return []
    orden = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", orden],
            capture_output=True, timeout=25, creationflags=SIN_VENTANA,
        ).stdout.decode("utf-8", errors="replace").strip()
        datos = json.loads(salida) if salida else []
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        registro.warning("No se pudo consultar Get-StartApps: %s", error)
        return []
    if isinstance(datos, dict):
        datos = [datos]
    return [_entrada(app["Name"], "appid", app["AppID"]) for app in datos
            if isinstance(app, dict) and app.get("Name") and app.get("AppID")]


# ==========================================
# JUEGOS (Steam y Epic, aunque no tengan acceso directo)
# ==========================================
_NO_SON_JUEGOS = re.compile(
    r"redistributable|steamworks|proton|steam linux runtime|steamvr|dedicated server|soundtrack|\bsdk\b|benchmark",
    re.IGNORECASE)
_MARCAS = re.compile(r"[®™©]")


def _nombre_de_juego(nombre: str) -> str:
    """«Rocket League®» -> «Rocket League»."""
    return re.sub(r"\s+", " ", _MARCAS.sub("", nombre or "")).strip()


def _valor_vdf(texto: str, clave: str) -> str | None:
    """Un valor de los ficheros de texto de Steam («"name"  "Tomb Raider"»)."""
    encontrado = re.search(r'"' + re.escape(clave) + r'"\s+"((?:[^"\\]|\\.)*)"', texto)
    return encontrado.group(1).replace("\\\\", "\\") if encontrado else None


def _carpeta_de_steam() -> Path | None:
    candidatas = []
    if ES_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as clave:
                candidatas.append(winreg.QueryValueEx(clave, "SteamPath")[0])
        except OSError:
            pass
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(variable)
        if base:
            candidatas.append(os.path.join(base, "Steam"))
    for candidata in candidatas:
        if candidata and os.path.isdir(os.path.join(candidata, "steamapps")):
            return Path(candidata)
    return None


def bibliotecas_de_steam(carpeta: Path) -> list:
    """La carpeta de Steam y las demás bibliotecas (por ejemplo, juegos instalados en otro disco)."""
    rutas = [Path(carpeta)]
    for nombre in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
        try:
            texto = (Path(carpeta) / nombre).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ruta in re.findall(r'"path"\s+"((?:[^"\\]|\\.)*)"', texto):
            ruta = Path(ruta.replace("\\\\", "\\"))
            if all(os.path.normcase(str(ruta)) != os.path.normcase(str(vista)) for vista in rutas):
                rutas.append(ruta)
    return [ruta for ruta in rutas if (ruta / "steamapps").is_dir()]


def juegos_de_steam(carpeta: Path | None = None) -> list:
    """Juegos instalados en Steam; se abren con steam://rungameid/<id>, como su acceso directo."""
    carpeta = carpeta or _carpeta_de_steam()
    if not carpeta:
        return []
    juegos = []
    for biblioteca in bibliotecas_de_steam(carpeta):
        for manifiesto in sorted((biblioteca / "steamapps").glob("appmanifest_*.acf")):
            try:
                texto = manifiesto.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            identificador, nombre, estado = (_valor_vdf(texto, clave) for clave in ("appid", "name", "StateFlags"))
            if not identificador or not nombre or _NO_SON_JUEGOS.search(nombre):
                continue
            if estado and estado.isdigit() and not int(estado) & 4:
                continue                                  # a medio descargar o actualizar: aún no se puede jugar
            juegos.append(_entrada(_nombre_de_juego(nombre), "protocolo", f"steam://rungameid/{identificador}"))
    return juegos


def juegos_de_epic(carpeta: Path | None = None) -> list:
    """Juegos instalados con Epic Games; se abren con el mismo enlace que usan sus accesos directos."""
    if carpeta is None:
        base = os.environ.get("PROGRAMDATA")
        if not base:
            return []
        carpeta = Path(base) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    juegos = []
    for manifiesto in sorted(Path(carpeta).glob("*.item")):
        try:
            datos = json.loads(manifiesto.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(datos, dict) or datos.get("bIsIncompleteInstall"):
            continue
        categorias = datos.get("AppCategories") or []
        if categorias and "games" not in categorias:
            continue                                      # complementos o motores, no juegos
        partes = [datos.get(clave) for clave in ("CatalogNamespace", "CatalogItemId", "AppName")]
        nombre = _nombre_de_juego(datos.get("DisplayName") or "")
        if not nombre or not all(isinstance(parte, str) and parte for parte in partes):
            continue
        enlace = ("com.epicgames.launcher://apps/" + "%3A".join(urllib.parse.quote(parte, safe="") for parte in partes)
                  + "?action=launch&silent=true")
        juegos.append(_entrada(nombre, "protocolo", enlace))
    return juegos


def _juegos_instalados() -> list:
    try:
        return juegos_de_steam() + juegos_de_epic()
    except Exception:
        registro.exception("No se pudieron leer las bibliotecas de juegos")
        return []


def indexar_aplicaciones(forzar: bool = False) -> list:
    """Construye (o recupera de caché) el índice de aplicaciones instaladas."""
    global _INDICE, _INDICE_FECHA
    with _CERROJO_INDICE:
        if _INDICE and not forzar and time.time() - _INDICE_FECHA < CADUCIDAD_INDICE:
            return _INDICE

        cache = dir_datos() / "apps.json"
        if not forzar and not _INDICE and cache.exists():
            try:
                guardado = json.loads(cache.read_text(encoding="utf-8"))
                if (guardado.get("version") == VERSION_INDICE and guardado.get("apps")
                        and time.time() - guardado.get("fecha", 0) < CADUCIDAD_INDICE):
                    _INDICE, _INDICE_FECHA = guardado["apps"], guardado["fecha"]
                    return _INDICE
            except (OSError, ValueError):
                pass

        registro.info("Indexando aplicaciones instaladas...")
        vistas, indice = set(), []
        for entrada in _apps_menu_inicio() + _apps_get_startapps():
            if not entrada["clave"] or entrada["clave"] in vistas or _NO_INDEXAR.search(entrada["clave"]):
                continue
            vistas.add(entrada["clave"])
            indice.append(entrada)
        # Los juegos sin acceso directo (los que ya lo tienen no se repiten). Sin el filtro de arriba:
        # un juego puede llamarse «Help Wanted» y no por eso es una página de ayuda.
        for entrada in _juegos_instalados():
            if entrada["clave"] and entrada["clave"] not in vistas:
                vistas.add(entrada["clave"])
                indice.append(entrada)
        _INDICE, _INDICE_FECHA = indice, time.time()
        try:
            cache.write_text(json.dumps({"version": VERSION_INDICE, "fecha": _INDICE_FECHA, "apps": indice},
                                        ensure_ascii=False),
                             encoding="utf-8")
        except OSError:
            pass
        registro.info("Índice listo: %d aplicaciones.", len(indice))
        return _INDICE


def listar_aplicaciones() -> list:
    return sorted({entrada["nombre"] for entrada in indexar_aplicaciones()}, key=str.lower)


def _buscar_en_indice(clave: str, difuso: bool = True) -> dict | None:
    indice = indexar_aplicaciones()
    if not clave:
        return None
    # 1) nombre exacto
    for entrada in indice:
        if entrada["clave"] == clave:
            return entrada
    # 2) todas las palabras dichas están en el nombre ("visual studio" -> "Visual Studio Code")
    palabras = set(clave.split())
    if len(clave) >= 3:
        candidatas = [e for e in indice if palabras <= set(e["clave"].split())]
        if candidatas:
            return min(candidatas, key=lambda e: (len(e["clave"].split()), len(e["clave"])))
        # 3) el nombre empieza por lo dicho ("photo" -> "Photoshop")
        candidatas = [e for e in indice if e["clave"].startswith(clave)]
        if candidatas:
            return min(candidatas, key=lambda e: len(e["clave"]))
    if not difuso:
        return None
    # 4) parecido ortográfico, para errores leves de transcripción ("spotifai")
    claves = [e["clave"] for e in indice]
    parecidas = difflib.get_close_matches(clave, claves, n=1, cutoff=0.78)
    if parecidas:
        return next(e for e in indice if e["clave"] == parecidas[0])
    return None


def resolver_objetivo(texto: str) -> dict | None:
    """Traduce lo que ha dicho la usuaria en algo que se puede abrir.

    Devuelve {"nombre", "tipo", "destino"} o None. El orden importa: primero
    carpetas personales, luego aplicaciones instaladas y por último webs.
    """
    clave = normalizar(texto or "")
    for _ in range(2):
        clave = _RELLENO_OBJETIVO.sub("", clave)
    if not clave:
        return None

    carpeta = _PATRON_CARPETA.match(clave)
    if carpeta:
        nombre = carpeta.group(1) or carpeta.group(2)
        return {"nombre": f"la carpeta {nombre.capitalize()}", "tipo": "carpeta",
                "destino": str(carpeta_conocida(nombre))}

    exacta = next((e for e in indexar_aplicaciones() if e["clave"] == clave), None)
    if exacta:
        return exacta

    if clave in ALIAS:
        canonico, respaldo = ALIAS[clave]
        encontrada = _buscar_en_indice(canonico or clave, difuso=False)
        if encontrada:
            return encontrada
        tipo = "url" if respaldo.startswith("http") else "comando"
        return {"nombre": (canonico or clave).capitalize(), "tipo": tipo, "destino": respaldo}

    encontrada = _buscar_en_indice(clave, difuso=False)
    if encontrada:
        return encontrada
    if clave in SITIOS_WEB:
        return {"nombre": NOMBRES_WEB.get(clave, clave.title()), "tipo": "url", "destino": SITIOS_WEB[clave]}
    encontrada = _buscar_en_indice(clave, difuso=True)
    if encontrada:
        return encontrada

    dominio = (texto or "").strip().lower()
    if _PATRON_DOMINIO.match(dominio):
        url = dominio if dominio.startswith("http") else f"https://{dominio}"
        return {"nombre": dominio, "tipo": "url", "destino": url}
    return None


def sugerencias(texto: str, cantidad: int = 3) -> list:
    """Nombres de aplicaciones parecidos a lo dicho (para el «¿quizá querías...?»)."""
    indice = indexar_aplicaciones()
    parecidas = difflib.get_close_matches(normalizar(texto or ""), [e["clave"] for e in indice],
                                          n=cantidad, cutoff=0.5)
    return [next(e["nombre"] for e in indice if e["clave"] == clave) for clave in parecidas]


def buscar_aplicacion(nombre: str) -> tuple:
    """Compatibilidad con la v4: devuelve (nombre encontrado, destino) o (None, None)."""
    encontrada = resolver_objetivo(nombre)
    return (encontrada["nombre"], encontrada["destino"]) if encontrada else (None, None)


def _lanzar(entrada: dict) -> bool:
    if simulando():
        registro.info("[SIMULACIÓN] abrir %s (%s)", entrada["nombre"], entrada["destino"])
        return True
    tipo, destino = entrada["tipo"], entrada["destino"]
    try:
        if tipo == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{destino}"])
        elif tipo == "url":
            webbrowser.open(destino)
        else:                                   # ruta, comando, carpeta o protocolo (ms-settings:)
            os.startfile(destino)
        return True
    except OSError as error:
        if tipo == "comando":
            try:
                subprocess.Popen([destino], shell=False)
                return True
            except OSError:
                pass
        registro.warning("No se pudo abrir %s: %s", destino, error)
        return False


def abrir_aplicacion(nombre: str) -> tuple:
    """Abre una aplicación, carpeta o web. Devuelve (éxito, nombre mostrado)."""
    encontrada = resolver_objetivo(nombre)
    if not encontrada:
        return False, nombre
    return _lanzar(encontrada), encontrada["nombre"]


# ==========================================
# VENTANAS Y FOCO
# ==========================================
_PIDS_PROPIOS = {os.getpid()}
_ultima_ventana_externa = 0
_seguimiento_activo = False
_CLASES_DEL_SISTEMA = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

ALIAS_PROCESO = {
    "bloc de notas": "notepad", "calculadora": "calculatorapp", "visual studio code": "code", "vs code": "code",
    "vscode": "code", "google chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge",
    "word": "winword", "powerpoint": "powerpnt", "terminal": "windowsterminal",
    "simbolo del sistema": "cmd", "paint": "mspaint", "configuracion": "systemsettings",
    "administrador de tareas": "taskmgr", "explorador de archivos": "explorer", "explorador": "explorer",
}


def registrar_pid_propio(pid: int) -> None:
    """Marca un proceso como parte de JARVIS (p. ej. su ventana) para no cerrarlo ni escribir en él."""
    _PIDS_PROPIOS.add(int(pid))


def _user32():
    return ctypes.windll.user32


def _pid_de(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _clase_de(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _titulo_de(hwnd: int) -> str:
    largo = _user32().GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(largo + 1)
    _user32().GetWindowTextW(hwnd, buffer, largo + 1)
    return buffer.value


def _es_propia(hwnd: int) -> bool:
    return bool(hwnd) and _pid_de(hwnd) in _PIDS_PROPIOS


def _es_ventana_de_aplicacion(hwnd: int) -> bool:
    usuario = _user32()
    return bool(hwnd and usuario.IsWindow(hwnd) and usuario.IsWindowVisible(hwnd)
                and usuario.GetWindowTextLengthW(hwnd) > 0 and _clase_de(hwnd) not in _CLASES_DEL_SISTEMA)


def _ventanas() -> list:
    """Ventanas de aplicación visibles: [{"hwnd", "titulo", "pid", "proceso"}]."""
    if not ES_WINDOWS:
        return []
    import psutil
    encontradas = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visitar(hwnd, _):
        if _es_ventana_de_aplicacion(hwnd):
            pid = _pid_de(hwnd)
            try:
                proceso = psutil.Process(pid).name()
            except Exception:
                proceso = ""
            encontradas.append({"hwnd": hwnd, "titulo": _titulo_de(hwnd), "pid": pid, "proceso": proceso})
        return True

    _user32().EnumWindows(visitar, 0)
    return encontradas


def _ventanas_de(nombre: str) -> list:
    """Ventanas (ajenas a JARVIS) que pertenecen a la aplicación indicada."""
    clave = normalizar(nombre or "")
    for _ in range(2):
        clave = _RELLENO_OBJETIVO.sub("", clave)
    if not clave:
        return []
    proceso_buscado = ALIAS_PROCESO.get(clave, clave.replace(" ", ""))
    patron_titulo = re.compile(rf"\b{re.escape(clave)}\b") if len(clave) >= 4 else None
    coincidentes = []
    for ventana in _ventanas():
        if ventana["pid"] in _PIDS_PROPIOS:
            continue
        proceso = normalizar(os.path.splitext(ventana["proceso"])[0]).replace(" ", "")
        if proceso in (proceso_buscado, clave.replace(" ", "")) or (
                patron_titulo and patron_titulo.search(normalizar(ventana["titulo"]))):
            coincidentes.append(ventana)
    return coincidentes


def _vigilar_foco() -> None:
    global _ultima_ventana_externa
    while _seguimiento_activo:
        try:
            hwnd = _user32().GetForegroundWindow()
            if hwnd and not _es_propia(hwnd) and _es_ventana_de_aplicacion(hwnd):
                _ultima_ventana_externa = hwnd
        except Exception:
            pass
        time.sleep(0.3)


def iniciar_seguimiento_foco() -> None:
    """Recuerda cuál fue la última ventana ajena a JARVIS que tuvo el foco.

    Así, cuando la orden se escribe en la propia ventana de JARVIS ("escribe
    hola"), el texto va a la aplicación en la que estabas y no al chat.
    """
    global _seguimiento_activo
    if not ES_WINDOWS or _seguimiento_activo or simulando():
        return
    _seguimiento_activo = True
    threading.Thread(target=_vigilar_foco, name="jarvis-foco", daemon=True).start()


def _enfocar(hwnd: int) -> bool:
    usuario = _user32()
    if usuario.IsIconic(hwnd):
        usuario.ShowWindow(hwnd, 9)             # SW_RESTORE
    # Windows solo deja traer una ventana al frente tras una pulsación: se simula Alt.
    usuario.keybd_event(0x12, 0, 0, 0)
    usuario.SetForegroundWindow(hwnd)
    usuario.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.25)
    return usuario.GetForegroundWindow() == hwnd


def _ventana_destino() -> int:
    """Ventana que debe recibir teclas/texto: la activa, salvo que sea la del propio JARVIS."""
    actual = _user32().GetForegroundWindow()
    if actual and not _es_propia(actual):
        return actual
    if _ultima_ventana_externa and _es_ventana_de_aplicacion(_ultima_ventana_externa):
        return _ultima_ventana_externa
    return 0


def ventana_externa_activa() -> dict:
    """La ventana en la que estaba la usuaria (la activa o, si es la de JARVIS, la anterior).

    {"hwnd", "titulo", "proceso"}, o {} si no hay ninguna (o en simulación).
    """
    if not ES_WINDOWS or simulando():
        return {}
    hwnd = _ventana_destino()
    if not hwnd:
        return {}
    import psutil
    try:
        proceso = psutil.Process(_pid_de(hwnd)).name()
    except Exception:
        proceso = ""
    return {"hwnd": hwnd, "titulo": _titulo_de(hwnd), "proceso": proceso}


def _preparar_destino(aplicacion: str = "") -> tuple:
    """Deja en primer plano la ventana que va a recibir la escritura. Devuelve (ok, error)."""
    if aplicacion:
        ventanas = _ventanas_de(aplicacion)
        if not ventanas:
            abierta, _ = abrir_aplicacion(aplicacion)
            if not abierta:
                return False, f"No he podido abrir {aplicacion}."
            limite = time.time() + 8
            while time.time() < limite and not ventanas:
                time.sleep(0.4)
                ventanas = _ventanas_de(aplicacion)
            if not ventanas:
                return False, f"He abierto {aplicacion}, pero no encuentro su ventana para escribir."
            time.sleep(0.6)
        hwnd = ventanas[0]["hwnd"]
    else:
        hwnd = _ventana_destino()
        if not hwnd:
            return False, ("No sé en qué ventana hacerlo. Haz clic en la aplicación de destino y pídemelo de "
                           "nuevo, o dime en cuál: «escribe hola en el bloc de notas».")
    if _user32().GetForegroundWindow() != hwnd and not _enfocar(hwnd):
        return False, "No he podido traer al frente la ventana de destino."
    return True, ""


# ==========================================
# ACCIONES
# ==========================================
def cerrar_ventana() -> bool:
    """Cierra la ventana activa (nunca la del propio JARVIS)."""
    if simulando():
        registro.info("[SIMULACIÓN] cerrar la ventana activa")
        return True
    if not ES_WINDOWS:
        return False
    hwnd = _ventana_destino()
    if not hwnd:
        return False
    return bool(_user32().PostMessageW(hwnd, WM_CLOSE, 0, 0))


def cerrar_aplicacion(nombre: str) -> Resultado:
    """Pide a todas las ventanas de una aplicación que se cierren (cierre ordenado)."""
    if simulando():
        registro.info("[SIMULACIÓN] cerrar %s", nombre)
        return Resultado(True, f"He cerrado {nombre}.", {"ventanas": 1})
    ventanas = _ventanas_de(nombre)
    if not ventanas:
        return Resultado(False, f"No encuentro ninguna ventana de {nombre} abierta.")
    for ventana in ventanas:
        _user32().PostMessageW(ventana["hwnd"], WM_CLOSE, 0, 0)
    return Resultado(True, f"He cerrado {nombre}.", {"ventanas": len(ventanas)})


def escribir_texto(texto: str, aplicacion: str = "") -> Resultado:
    """Pega `texto` en la ventana de destino (se usa el portapapeles y luego se restaura)."""
    if not texto:
        return Resultado(False, "No hay ningún texto que escribir.")
    if simulando():
        registro.info("[SIMULACIÓN] escribir %r en %s", texto, aplicacion or "la ventana activa")
        return Resultado(True, "He escrito el texto solicitado.")
    preparado, error = _preparar_destino(aplicacion)
    if not preparado:
        return Resultado(False, error)
    pyautogui = _pyautogui()
    try:
        import pyperclip
        anterior = pyperclip.paste()
        pyperclip.copy(texto)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyperclip.copy(anterior)
    except Exception as error:
        registro.debug("Portapapeles no disponible (%s); se teclea carácter a carácter.", error)
        try:
            pyautogui.write(texto, interval=0.02)
        except Exception:
            return Resultado(False, "No he podido escribir el texto.")
    return Resultado(True, "He escrito el texto solicitado.")


def pulsar_tecla(tecla: str) -> Resultado:
    """Pulsa una tecla o combinación ("enter", "ctrl+c"). Solo teclas conocidas."""
    partes = [parte.strip() for parte in (tecla or "").lower().split("+") if parte.strip()]
    pyautogui = _pyautogui()
    if not partes or any(parte not in pyautogui.KEYBOARD_KEYS for parte in partes):
        return Resultado(False, f"No conozco la tecla «{tecla}».")
    if simulando():
        registro.info("[SIMULACIÓN] pulsar %s", "+".join(partes))
        return Resultado(True, f"He pulsado {'+'.join(partes)}.")
    preparado, error = _preparar_destino()
    if not preparado:
        return Resultado(False, error)
    try:
        if len(partes) > 1:
            pyautogui.hotkey(*partes)
        else:
            pyautogui.press(partes[0])
    except Exception:
        return Resultado(False, f"No he podido pulsar {tecla}.")
    return Resultado(True, f"He pulsado {'+'.join(partes)}.")


MULTIMEDIA = {
    "siguiente": ("nexttrack", 1, "Siguiente pista."),
    "anterior": ("prevtrack", 1, "Pista anterior."),
    "subir_volumen": ("volumeup", 5, "Volumen subido."),
    "bajar_volumen": ("volumedown", 5, "Volumen bajado."),
    "silenciar": ("volumemute", 1, "Sonido silenciado (o restaurado, si ya lo estaba)."),
    "play_pausa": ("playpause", 1, "Reproducción pausada o reanudada."),
}


def control_multimedia(objetivo: str) -> Resultado:
    if objetivo not in MULTIMEDIA:
        return Resultado(False, "No reconozco esa orden multimedia.")
    tecla, pulsaciones, mensaje = MULTIMEDIA[objetivo]
    if simulando():
        registro.info("[SIMULACIÓN] multimedia: %s", objetivo)
        return Resultado(True, mensaje)
    try:
        _pyautogui().press(tecla, presses=pulsaciones)
    except Exception:
        return Resultado(False, "No he podido enviar la orden multimedia.")
    return Resultado(True, mensaje)


# ==========================================
# COMANDOS DE SISTEMA (catálogo cerrado)
# ==========================================
ESPERA_APAGADO = 30   # segundos de margen para poder decir «cancela el apagado»


def _orden(*argumentos) -> int:
    return subprocess.run(list(argumentos), capture_output=True, creationflags=SIN_VENTANA).returncode


def _apagar() -> Resultado:
    _orden("shutdown", "/s", "/t", str(ESPERA_APAGADO), "/c", "JARVIS va a apagar el equipo.")
    return Resultado(True, f"El ordenador se apagará en {ESPERA_APAGADO} segundos. "
                           "Di «cancela el apagado» si te arrepientes.")


def _reiniciar() -> Resultado:
    _orden("shutdown", "/r", "/t", str(ESPERA_APAGADO), "/c", "JARVIS va a reiniciar el equipo.")
    return Resultado(True, f"El ordenador se reiniciará en {ESPERA_APAGADO} segundos. "
                           "Di «cancela el apagado» si te arrepientes.")


def _cancelar_apagado() -> Resultado:
    if _orden("shutdown", "/a") == 0:
        return Resultado(True, "Apagado cancelado.")
    return Resultado(False, "No había ningún apagado programado.")


def _suspender() -> Resultado:
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=SIN_VENTANA)
    return Resultado(True, "Suspendiendo el ordenador.")


def _bloquear() -> Resultado:
    ctypes.windll.user32.LockWorkStation()
    return Resultado(True, "Ordenador bloqueado.")


def _cerrar_sesion() -> Resultado:
    _orden("shutdown", "/l")
    return Resultado(True, "Cerrando la sesión.")


def _tema(oscuro: bool) -> Resultado:
    import winreg
    clave = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    valor = 0 if oscuro else 1
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave, 0, winreg.KEY_SET_VALUE) as abierta:
        winreg.SetValueEx(abierta, "AppsUseLightTheme", 0, winreg.REG_DWORD, valor)
        winreg.SetValueEx(abierta, "SystemUsesLightTheme", 0, winreg.REG_DWORD, valor)
    # Avisa a las aplicaciones abiertas de que el tema ha cambiado.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200, None)
    return Resultado(True, "Modo oscuro activado." if oscuro else "Modo claro activado.")


def _captura() -> Resultado:
    carpeta = carpeta_conocida("imagenes") / "JARVIS"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"captura_{time.strftime('%Y%m%d_%H%M%S')}.png"
    _pyautogui().screenshot().save(str(ruta))
    return Resultado(True, f"Captura guardada en Imágenes\\JARVIS como {ruta.name}.", {"ruta": str(ruta)})


def _vaciar_papelera() -> Resultado:
    # 0x7 = sin confirmación, sin barra de progreso y sin sonido (la confirmación ya la ha pedido JARVIS).
    codigo = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
    if codigo == 0:
        return Resultado(True, "Papelera vaciada.")
    return Resultado(True, "La papelera ya estaba vacía.")


def _mostrar_escritorio() -> Resultado:
    _pyautogui().hotkey("win", "d")
    return Resultado(True, "Mostrando el escritorio.")


COMANDOS_SISTEMA = {
    "apagar": _apagar,
    "reiniciar": _reiniciar,
    "cancelar_apagado": _cancelar_apagado,
    "suspender": _suspender,
    "bloquear": _bloquear,
    "cerrar_sesion": _cerrar_sesion,
    "modo_oscuro": lambda: _tema(True),
    "modo_claro": lambda: _tema(False),
    "captura": _captura,
    "vaciar_papelera": _vaciar_papelera,
    "mostrar_escritorio": _mostrar_escritorio,
}


def ejecutar_comando_sistema(comando: str) -> Resultado:
    if comando not in COMANDOS_SISTEMA:
        return Resultado(False, "Ese comando no está en mi catálogo de acciones permitidas.")
    if simulando():
        registro.info("[SIMULACIÓN] comando de sistema: %s", comando)
        return Resultado(True, f"Comando «{comando}» ejecutado (simulación).")
    if not ES_WINDOWS:
        return Resultado(False, "Los comandos de sistema solo están disponibles en Windows.")
    try:
        return COMANDOS_SISTEMA[comando]()
    except Exception as error:
        registro.exception("Fallo al ejecutar el comando de sistema %s", comando)
        return Resultado(False, f"No he podido completar esa acción ({type(error).__name__}).")


# ==========================================
# PUNTO DE ENTRADA
# ==========================================
def ejecutar_intencion(intencion: dict) -> Resultado:
    """Ejecuta una intención de tipo "pc" ya autorizada por la capa de seguridad."""
    accion = intencion.get("action", "")
    objetivo = intencion.get("target", "")

    if accion == "open_app":
        encontrada = resolver_objetivo(objetivo)
        if not encontrada:
            parecidas = sugerencias(objetivo)
            pista = f" ¿Quizá te referías a {', '.join(parecidas)}?" if parecidas else ""
            return Resultado(False, f"No encuentro nada llamado «{objetivo}» en este ordenador.{pista}",
                             {"sugerencias": parecidas})
        if not _lanzar(encontrada):
            return Resultado(False, f"No he podido abrir {encontrada['nombre']}.")
        return Resultado(True, f"He abierto {encontrada['nombre']}.",
                         {"nombre": encontrada["nombre"], "tipo": encontrada["tipo"]})

    if accion == "close_app":
        if objetivo in ("active", "activa", "la ventana activa", ""):
            if cerrar_ventana():
                return Resultado(True, "He cerrado la ventana activa.")
            return Resultado(False, "No encuentro ninguna ventana que cerrar.")
        return cerrar_aplicacion(objetivo)

    if accion == "type_text":
        return escribir_texto(intencion.get("text", ""), objetivo)

    if accion == "press_key":
        return pulsar_tecla(intencion.get("key", "enter"))

    if accion in ("media", "media_keys"):
        if objetivo in ("siguiente", "anterior"):
            # Por los controles multimedia de Windows: así se sabe qué canción ha quedado sonando.
            from tools import media
            return media.controlar(objetivo)
        return control_multimedia(objetivo)

    if accion == "system_command":
        return ejecutar_comando_sistema(objetivo)

    return Resultado(False, "No reconozco esa orden de control del sistema.")
