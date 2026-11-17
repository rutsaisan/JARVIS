"""
Música: lo que suena de verdad y el control de la reproducción
===============================================================
JARVIS no debe suponer qué música suena ni decir que ha puesto algo sin comprobarlo
(pasó: se inventó que sonaba «Stairway to Heaven»). Este módulo usa los controles
multimedia de Windows (GlobalSystemMediaTransportControls), los mismos que aparecen
junto al volumen, que conocen Spotify, los navegadores y la mayoría de reproductores:

    estado()                  sesiones abiertas: aplicación, título, artista y si suena o no
    sonando()                 la sesión que suena ahora (o la última que sonó)
    controlar(accion)         reproducir / pausar / siguiente / anterior, de forma explícita
                              (no la tecla «play/pausa», que alterna y puede hacer lo contrario)
    buscar_en_youtube(texto)  vídeos de YouTube de una canción o artista, sin cuenta ni API
    reproducir(texto)         pone esa canción y comprueba si de verdad empieza a sonar

Con JARVIS_SIMULAR=1 no se abre ni se controla nada (lo usan las pruebas y las demos).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser

from core.text import normalizar
from tools.computer import Resultado, simulando

registro = logging.getLogger("jarvis.musica")

ES_WINDOWS = os.name == "nt"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACCIONES = {"estado", "reproducir", "pausar", "siguiente", "anterior", "pausar_todo"}
ESTADOS = {"Playing": "sonando", "Paused": "pausa", "Stopped": "parada", "Changing": "cambiando",
           "Opened": "abierta", "Closed": "cerrada"}

# Guion de PowerShell que habla con los controles multimedia de Windows (WinRT).
# Se lanza aparte en cada consulta (~0,8 s): así no hace falta ningún paquete extra.
_GUION = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$accion = '__ACCION__'
$preferida = '__PREFERIDA__'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$comoTarea = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Esperar($operacion, [Type]$tipo) {
    $tarea = $comoTarea.MakeGenericMethod($tipo).Invoke($null, @($operacion))
    $null = $tarea.Wait(5000)
    $tarea.Result
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$gestor = Esperar ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$sesiones = @($gestor.GetSessions())
$actual = $gestor.GetCurrentSession()
$objetivo = $actual
if ($preferida) {
    $elegida = $sesiones | Where-Object { $_.SourceAppUserModelId -like "*$preferida*" } | Select-Object -First 1
    if ($elegida) { $objetivo = $elegida }
}
$hecho = $null
if ($accion -eq 'pausar_todo') {
    foreach ($s in $sesiones) {
        if ($s.GetPlaybackInfo().PlaybackStatus -eq 'Playing') { $null = Esperar ($s.TryPauseAsync()) ([bool]) }
    }
    $hecho = $true
} elseif ($accion -ne 'estado' -and $objetivo) {
    switch ($accion) {
        'reproducir' { $hecho = Esperar ($objetivo.TryPlayAsync()) ([bool]) }
        'pausar'     { $hecho = Esperar ($objetivo.TryPauseAsync()) ([bool]) }
        'siguiente'  { $hecho = Esperar ($objetivo.TrySkipNextAsync()) ([bool]) }
        'anterior'   { $hecho = Esperar ($objetivo.TrySkipPreviousAsync()) ([bool]) }
    }
    Start-Sleep -Milliseconds 900
}
$lista = @(foreach ($s in $sesiones) {
    $p = Esperar ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    [pscustomobject]@{
        app = $s.SourceAppUserModelId; titulo = $p.Title; artista = $p.Artist; album = $p.AlbumTitle
        estado = $s.GetPlaybackInfo().PlaybackStatus.ToString()
        actual = [bool]($actual -and $s.SourceAppUserModelId -eq $actual.SourceAppUserModelId)
        objetivo = [bool]($objetivo -and $s.SourceAppUserModelId -eq $objetivo.SourceAppUserModelId)
    }
})
ConvertTo-Json -Compress -Depth 4 -InputObject ([pscustomobject]@{ hecho = $hecho; sesiones = $lista })
"""

_APLICACIONES = [("spotify", "Spotify"), ("opera", "Opera"), ("chrome", "Chrome"), ("msedge", "Edge"),
                 ("microsoftedge", "Edge"), ("firefox", "Firefox"), ("brave", "Brave"), ("vlc", "VLC"),
                 ("zunemusic", "Reproductor multimedia"), ("mediaplayer", "Reproductor multimedia"),
                 ("youtube", "YouTube"), ("applemusic", "Apple Music"), ("deezer", "Deezer")]


def nombre_aplicacion(identificador: str) -> str:
    """«SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify» -> «Spotify»."""
    plano_id = (identificador or "").lower()
    for clave, nombre in _APLICACIONES:
        if clave in plano_id:
            return nombre
    parte = re.split(r"[!\\/]", identificador or "")[-1]
    return re.sub(r"\.exe$", "", parte, flags=re.IGNORECASE).strip() or "un reproductor"


def _sesion(cruda: dict) -> dict:
    return {
        "app": nombre_aplicacion(cruda.get("app") or ""),
        "id_app": cruda.get("app") or "",
        "titulo": (cruda.get("titulo") or "").strip(),
        "artista": (cruda.get("artista") or "").strip(),
        "album": (cruda.get("album") or "").strip(),
        "estado": ESTADOS.get(cruda.get("estado") or "", "desconocido"),
        "actual": bool(cruda.get("actual")),
        "objetivo": bool(cruda.get("objetivo")),
    }


def interpretar_salida(texto: str) -> dict | None:
    """Convierte la salida JSON del guion en {"hecho": bool|None, "sesiones": [...]}."""
    texto = (texto or "").strip()
    if "{" not in texto:
        return None
    try:
        datos = json.loads(texto[texto.index("{"):])
    except ValueError:
        return None
    sesiones = datos.get("sesiones") or []
    if isinstance(sesiones, dict):                 # PowerShell 5.1 «desenvuelve» las listas de un elemento
        sesiones = [sesiones]
    return {"hecho": datos.get("hecho"), "sesiones": [_sesion(s) for s in sesiones if isinstance(s, dict)]}


def _ejecutar(accion: str, preferida: str = "") -> dict | None:
    if accion not in ACCIONES or not ES_WINDOWS:
        return None
    preferida = re.sub(r"[^a-z0-9]", "", normalizar(preferida))[:20]
    guion = _GUION.replace("__ACCION__", accion).replace("__PREFERIDA__", preferida)
    codificado = base64.b64encode(guion.encode("utf-16-le")).decode("ascii")
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", codificado],
            capture_output=True, timeout=20, creationflags=SIN_VENTANA)
    except (OSError, subprocess.SubprocessError) as error:
        registro.warning("No se pudo consultar la música de Windows: %s", error)
        return None
    if salida.returncode != 0:
        registro.warning("Controles multimedia no disponibles: %s",
                         salida.stderr.decode("utf-8", errors="replace").strip()[:200])
        return None
    return interpretar_salida(salida.stdout.decode("utf-8", errors="replace"))


# ==========================================
# LO QUE SUENA
# ==========================================
def estado() -> list:
    """Sesiones multimedia abiertas ([] si no hay ninguna o no se pueden leer)."""
    if simulando():
        return []
    resultado = _ejecutar("estado")
    return resultado["sesiones"] if resultado else []


def elegir_sesion(sesiones: list, preferida: str = "") -> dict | None:
    """La sesión de la que hablar: la de la aplicación pedida, la que suena, o la última activa."""
    if not sesiones:
        return None
    if preferida:
        for sesion in sesiones:
            if normalizar(preferida) in normalizar(sesion["app"] + " " + sesion["id_app"]):
                return sesion
    for sesion in sesiones:
        if sesion["estado"] == "sonando":
            return sesion
    for sesion in sesiones:
        if sesion["actual"]:
            return sesion
    return sesiones[0]


def sonando(preferida: str = "") -> dict | None:
    return elegir_sesion(estado(), preferida)


def describir(sesion: dict | None) -> str:
    """Frase con lo que suena, para decirla en voz alta."""
    if not sesion:
        return "No hay ninguna música abierta ahora mismo."
    if not sesion["titulo"]:
        return f"{sesion['app']} está abierto, pero no dice qué está reproduciendo."
    cancion = f"«{sesion['titulo']}»" + (f" de {sesion['artista']}" if sesion["artista"] else "")
    if sesion["estado"] == "sonando":
        return f"Ahora suena {cancion} en {sesion['app']}."
    if sesion["estado"] == "pausa":
        return f"En {sesion['app']} está en pausa {cancion}."
    return f"En {sesion['app']} tienes {cancion}."


# ==========================================
# CONTROL
# ==========================================
_TECLAS = {"siguiente": "nexttrack", "anterior": "prevtrack"}
_HECHO = {"reproducir": "reanudado", "pausar": "pausado", "siguiente": "siguiente", "anterior": "anterior"}


def controlar(accion: str, preferida: str = "") -> Resultado:
    """Reproduce, pausa, avanza o retrocede en la sesión pedida (o la activa), y dice cómo queda."""
    if accion not in _HECHO:
        return Resultado(False, "No conozco esa orden de música.")
    if simulando():
        registro.info("[SIMULACIÓN] música: %s", accion)
        return Resultado(True, f"Música: {_HECHO[accion]} (simulación).", {"simulado": True})
    resultado = _ejecutar(accion, preferida)
    if resultado is not None and resultado["hecho"] is None and preferida:
        time.sleep(2.0)                                # la aplicación se acaba de abrir: espera a que aparezca
        resultado = _ejecutar(accion, preferida)
    if resultado is None:
        # Sin acceso a los controles de Windows: solo las teclas que no alternan son seguras.
        if accion in _TECLAS:
            from tools.computer import control_multimedia
            return control_multimedia("siguiente" if accion == "siguiente" else "anterior")
        return Resultado(False, "No puedo acceder a los controles de música de Windows.")
    if resultado["hecho"] is None:
        if accion == "reproducir":
            return Resultado(False, "No hay ninguna música abierta que reanudar. Dime qué quieres que ponga.")
        return Resultado(False, "No hay ninguna música abierta ahora mismo.")
    sesion = next((s for s in resultado["sesiones"] if s["objetivo"]), None) or elegir_sesion(resultado["sesiones"])
    datos = {"sesion": sesion}
    if not resultado["hecho"]:
        return Resultado(False, f"{sesion['app'] if sesion else 'El reproductor'} no ha aceptado la orden.", datos)
    if accion == "pausar":
        return Resultado(True, "Música en pausa.", datos)
    return Resultado(True, describir(sesion), datos)


# ==========================================
# PONER UNA CANCIÓN
# ==========================================
_CABECERAS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}
_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+1"}   # evita la página de consentimiento de cookies


def _texto_de(nodo: dict) -> str:
    if not isinstance(nodo, dict):
        return ""
    return nodo.get("simpleText") or "".join(parte.get("text", "") for parte in nodo.get("runs", []))


def videos_de(datos_iniciales: dict, maximo: int = 5) -> list:
    """Vídeos de la estructura ytInitialData de una página de resultados de YouTube."""
    videos = []

    def recorrer(nodo):
        if len(videos) >= maximo:
            return
        if isinstance(nodo, dict):
            video = nodo.get("videoRenderer")
            if isinstance(video, dict) and video.get("videoId"):
                videos.append({"id": video["videoId"], "titulo": _texto_de(video.get("title")),
                               "canal": _texto_de(video.get("ownerText")), "duracion": _texto_de(video.get("lengthText"))})
            for valor in nodo.values():
                recorrer(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)

    recorrer(datos_iniciales)
    return videos


def videos_de_pagina(html: str, consulta: str, maximo: int = 5) -> list:
    """Vídeos de una página de resultados. YouTube sirve a veces otra variante de la página:
    si no trae la estructura conocida, se toma al menos el primer identificador de vídeo."""
    coincidencia = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
    if coincidencia:
        try:
            videos = videos_de(json.loads(coincidencia.group(1)), maximo)
            if videos:
                return videos
        except ValueError:
            pass
    identificador = re.search(r'"videoId":"([\w-]{11})"', html)
    if identificador:
        return [{"id": identificador.group(1), "titulo": consulta, "canal": "", "duracion": ""}]
    return []


def buscar_en_youtube(consulta: str, maximo: int = 5) -> list:
    """Busca en YouTube y devuelve [{"id", "titulo", "canal", "duracion"}] ([] si no se puede)."""
    import requests
    for intento in (1, 2):
        try:
            respuesta = requests.get("https://www.youtube.com/results",
                                     params={"search_query": consulta, "hl": "es", "gl": "ES"},
                                     headers=_CABECERAS, cookies=_COOKIES, timeout=10)
            respuesta.raise_for_status()
            videos = videos_de_pagina(respuesta.text, consulta, maximo)
            if videos:
                return videos
            registro.info("YouTube no ha devuelto vídeos (intento %d).", intento)
        except Exception as error:
            registro.warning("No se pudo buscar en YouTube: %s", type(error).__name__)
        time.sleep(0.6)
    return []


def parecidos(a: str, b: str) -> bool:
    """¿Dos títulos hablan de lo mismo? (los navegadores a veces añaden « - YouTube» y similares)."""
    pa, pb = set(normalizar(a).split()), set(normalizar(b).split())
    if not pa or not pb:
        return False
    return len(pa & pb) / min(len(pa), len(pb)) >= 0.6


def reproducir(consulta: str, servicio: str = "youtube") -> Resultado:
    """Pone una canción (o artista, o tipo de música) y comprueba si empieza a sonar."""
    consulta = re.sub(r"\s+", " ", consulta or "").strip()
    if not consulta:
        return Resultado(False, "¿Qué quieres que ponga?")

    if servicio == "spotify":
        # Spotify no deja que otra aplicación elija y reproduzca una canción sin su API:
        # se abre la búsqueda y se dice la verdad.
        if not simulando():
            try:
                os.startfile("spotify:search:" + urllib.parse.quote(consulta))
            except (OSError, AttributeError):
                return Resultado(False, "No he podido abrir Spotify.")
        return Resultado(True, f"Te he buscado «{consulta}» en Spotify. Elige el resultado y dale al play.",
                         {"servicio": "spotify", "consulta": consulta, "confirmado": False})

    videos = buscar_en_youtube(consulta)
    if not videos:
        return Resultado(False, f"No he encontrado «{consulta}» en YouTube.")
    video = videos[0]
    url = f"https://www.youtube.com/watch?v={video['id']}"
    datos = {"servicio": "youtube", "consulta": consulta, "titulo": video["titulo"], "canal": video["canal"],
             "url": url, "confirmado": False}
    if simulando():
        registro.info("[SIMULACIÓN] reproducir %s", url)
        return Resultado(True, f"Sonando «{video['titulo']}» en YouTube (simulación).", dict(datos, simulado=True))

    _ejecutar("pausar_todo")                           # que no se solape con Spotify u otra música
    webbrowser.open(url)
    for _ in range(3):                                 # el navegador publica el título del vídeo al empezar
        time.sleep(1.5)
        if any(s["estado"] == "sonando" and parecidos(s["titulo"], video["titulo"]) for s in estado()):
            datos["confirmado"] = True
            return Resultado(True, f"Sonando «{video['titulo']}» en YouTube.", datos)
    return Resultado(True, f"He abierto «{video['titulo']}» en YouTube; si no empieza sola, dale al play.", datos)
