"""
Pruebas del módulo de música (tools/media.py) sin tocar el ordenador ni Internet:
la salida de los controles multimedia de Windows, la búsqueda en YouTube y la
comprobación de que una canción suena de verdad se simulan.

Ejecutar con:  pytest tests/test_media.py -v
"""
import json

from tools import media


def sesion(app, estado, actual=False, titulo="Canción", objetivo=False):
    return {"app": app, "id_app": app, "titulo": titulo, "artista": "Artista", "album": "", "estado": estado,
            "actual": actual, "objetivo": objetivo}


# ==========================================
# LO QUE DEVUELVE WINDOWS
# ==========================================
def test_nombre_de_la_aplicacion():
    assert media.nombre_aplicacion("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify"
    assert media.nombre_aplicacion("Spotify.exe") == "Spotify"
    assert media.nombre_aplicacion("MSEdge") == "Edge"
    assert media.nombre_aplicacion("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic") == "Reproductor multimedia"
    assert media.nombre_aplicacion("C:\\Programas\\MiReproductor.exe") == "MiReproductor"
    assert media.nombre_aplicacion("") == "un reproductor"


def test_interpretar_la_salida_del_guion():
    # PowerShell 5.1 convierte una lista de un elemento en un objeto suelto.
    salida = json.dumps({"hecho": True, "sesiones": {
        "app": "Spotify.exe", "titulo": " MONEY MACHINE ", "artista": "Nicki Nicole", "album": "NAIKI",
        "estado": "Playing", "actual": True, "objetivo": True}})
    datos = media.interpretar_salida("aviso previo\r\n" + salida)
    assert datos == {"hecho": True, "sesiones": [{
        "app": "Spotify", "id_app": "Spotify.exe", "titulo": "MONEY MACHINE", "artista": "Nicki Nicole",
        "album": "NAIKI", "estado": "sonando", "actual": True, "objetivo": True}]}
    assert media.interpretar_salida('{"hecho": null, "sesiones": []}') == {"hecho": None, "sesiones": []}
    assert media.interpretar_salida("") is None
    assert media.interpretar_salida("{roto") is None


def test_estados_desconocidos():
    datos = media.interpretar_salida('{"hecho": null, "sesiones": [{"app": "VLC", "estado": "Raro"}, 5]}')
    assert datos["sesiones"][0]["estado"] == "desconocido" and len(datos["sesiones"]) == 1


def test_elegir_de_que_sesion_hablar():
    spotify, opera = sesion("Spotify", "pausa", actual=True), sesion("Opera", "sonando")
    assert media.elegir_sesion([spotify, opera]) is opera                  # la que suena
    assert media.elegir_sesion([spotify, opera], "spotify") is spotify     # la que se ha pedido
    assert media.elegir_sesion([sesion("Chrome", "parada"), spotify]) is spotify   # la última activa
    assert media.elegir_sesion([]) is None


def test_describir_lo_que_suena():
    assert media.describir(None) == "No hay ninguna música abierta ahora mismo."
    assert media.describir(sesion("Spotify", "sonando", titulo="MONEY MACHINE")) == \
        "Ahora suena «MONEY MACHINE» de Artista en Spotify."
    assert media.describir(sesion("Spotify", "pausa", titulo="X")) == "En Spotify está en pausa «X» de Artista."
    assert "no dice qué" in media.describir(sesion("Opera", "sonando", titulo=""))


# ==========================================
# CONTROL DE LA REPRODUCCIÓN
# ==========================================
def test_control_en_simulacion():
    assert media.controlar("pausar").ok
    assert not media.controlar("bailar").ok


def test_reanudar_dice_lo_que_suena(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    llamadas = []

    def ejecutar(accion, preferida=""):
        llamadas.append((accion, preferida))
        return {"hecho": True, "sesiones": [sesion("Opera", "pausa"),
                                            sesion("Spotify", "sonando", titulo="MONEY MACHINE", objetivo=True)]}
    monkeypatch.setattr(media, "_ejecutar", ejecutar)
    resultado = media.controlar("reproducir", "Spotify")
    assert llamadas == [("reproducir", "Spotify")]
    assert resultado.ok and resultado.mensaje == "Ahora suena «MONEY MACHINE» de Artista en Spotify."
    assert resultado.datos["sesion"]["app"] == "Spotify"


def test_reanudar_sin_musica_abierta(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    monkeypatch.setattr(media, "_ejecutar", lambda accion, preferida="": {"hecho": None, "sesiones": []})
    monkeypatch.setattr(media.time, "sleep", lambda segundos: None)
    resultado = media.controlar("reproducir", "spotify")
    assert not resultado.ok and "Dime qué quieres que ponga" in resultado.mensaje


def test_el_reproductor_rechaza_la_orden(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    monkeypatch.setattr(media, "_ejecutar", lambda accion, preferida="": {
        "hecho": False, "sesiones": [sesion("Spotify", "pausa", objetivo=True)]})
    resultado = media.controlar("pausar")
    assert not resultado.ok and resultado.mensaje == "Spotify no ha aceptado la orden."


def test_sin_controles_de_windows_no_alterna_a_ciegas(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    monkeypatch.setattr(media, "_ejecutar", lambda accion, preferida="": None)
    # La tecla play/pausa alterna: si no se sabe el estado, podría hacer lo contrario de lo pedido.
    assert not media.controlar("pausar").ok
    assert not media.controlar("reproducir").ok


def test_el_guion_solo_admite_acciones_conocidas():
    assert media._ejecutar("formatear") is None
    assert "__ACCION__" in media._GUION and "__PREFERIDA__" in media._GUION


# ==========================================
# YOUTUBE
# ==========================================
def test_videos_de_una_pagina_de_resultados():
    datos = {"contents": {"lista": [
        {"videoRenderer": {"videoId": "abcdefghijk", "title": {"runs": [{"text": "Queen - Bohemian Rhapsody"}]},
                           "ownerText": {"runs": [{"text": "Queen Official"}]}, "lengthText": {"simpleText": "5:59"}}},
        {"channelRenderer": {"title": {"simpleText": "Queen"}}},
        {"videoRenderer": {"videoId": "bbbbbbbbbbb", "title": {"runs": [{"text": "Otra"}]}}}]}}
    html = "<script>var ytInitialData = " + json.dumps(datos) + ";</script><script>otra()</script>"
    videos = media.videos_de_pagina(html, "queen")
    assert videos[0] == {"id": "abcdefghijk", "titulo": "Queen - Bohemian Rhapsody", "canal": "Queen Official",
                         "duracion": "5:59"}
    assert [v["id"] for v in videos] == ["abcdefghijk", "bbbbbbbbbbb"]
    assert len(media.videos_de_pagina(html, "queen", maximo=1)) == 1


def test_otra_variante_de_la_pagina_toma_el_primer_video():
    html = '<html>{"x":1,"videoId":"zzzzzzzzzzz"} {"videoId":"yyyyyyyyyyy"}</html>'
    assert media.videos_de_pagina(html, "Despacito") == [
        {"id": "zzzzzzzzzzz", "titulo": "Despacito", "canal": "", "duracion": ""}]
    assert media.videos_de_pagina("<html></html>", "x") == []


def test_titulos_parecidos():
    assert media.parecidos("Queen - Bohemian Rhapsody", "Queen - Bohemian Rhapsody (Official Video) - YouTube")
    assert media.parecidos("Bohemian Rhapsody", "Bohemian Rhapsody - YouTube")
    assert not media.parecidos("Queen - Bohemian Rhapsody", "Despacito - Luis Fonsi")
    assert not media.parecidos("", "algo")


# ==========================================
# PONER UNA CANCIÓN
# ==========================================
VIDEO = {"id": "abcdefghijk", "titulo": "Queen - Bohemian Rhapsody", "canal": "Queen", "duracion": "5:59"}


def test_poner_en_simulacion_no_abre_nada(monkeypatch):
    monkeypatch.setattr(media, "buscar_en_youtube", lambda consulta, maximo=5: [VIDEO])
    abiertos = []
    monkeypatch.setattr(media.webbrowser, "open", abiertos.append)
    resultado = media.reproducir("  bohemian   rhapsody ")
    assert resultado.ok and "simulación" in resultado.mensaje
    assert resultado.datos["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert resultado.datos["consulta"] == "bohemian rhapsody"
    assert abiertos == []


def test_poner_comprueba_que_suena_de_verdad(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    monkeypatch.setattr(media, "buscar_en_youtube", lambda consulta, maximo=5: [VIDEO])
    monkeypatch.setattr(media.time, "sleep", lambda segundos: None)
    acciones, abiertos = [], []
    monkeypatch.setattr(media, "_ejecutar", lambda accion, preferida="": acciones.append(accion))
    monkeypatch.setattr(media.webbrowser, "open", abiertos.append)
    monkeypatch.setattr(media, "estado", lambda: [sesion("Opera", "sonando",
                                                         titulo="Queen - Bohemian Rhapsody - YouTube")])
    resultado = media.reproducir("bohemian rhapsody")
    assert acciones == ["pausar_todo"]                      # que no se solape con otra música
    assert abiertos == ["https://www.youtube.com/watch?v=abcdefghijk"]
    assert resultado.ok and resultado.datos["confirmado"] is True
    assert resultado.mensaje == "Sonando «Queen - Bohemian Rhapsody» en YouTube."


def test_si_no_se_puede_comprobar_lo_dice(monkeypatch):
    monkeypatch.setattr(media, "simulando", lambda: False)
    monkeypatch.setattr(media, "buscar_en_youtube", lambda consulta, maximo=5: [VIDEO])
    monkeypatch.setattr(media.time, "sleep", lambda segundos: None)
    monkeypatch.setattr(media, "_ejecutar", lambda accion, preferida="": None)
    monkeypatch.setattr(media.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(media, "estado", lambda: [])
    resultado = media.reproducir("bohemian rhapsody")
    assert resultado.ok and resultado.datos["confirmado"] is False
    assert "dale al play" in resultado.mensaje              # no afirma que suena si no lo ha visto


def test_cancion_no_encontrada(monkeypatch):
    monkeypatch.setattr(media, "buscar_en_youtube", lambda consulta, maximo=5: [])
    resultado = media.reproducir("xyzzy")
    assert not resultado.ok and resultado.mensaje == "No he encontrado «xyzzy» en YouTube."
    assert not media.reproducir("   ").ok


def test_en_spotify_abre_la_busqueda_y_dice_la_verdad():
    resultado = media.reproducir("Money Machine", "spotify")
    assert resultado.ok and resultado.datos["confirmado"] is False
    assert "dale al play" in resultado.mensaje
