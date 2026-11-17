"""
Pruebas de los juegos de Steam y Epic: se leen sus bibliotecas (aquí, unas de mentira en una
carpeta temporal) para poder abrir cada juego por su nombre aunque no tenga acceso directo.

Ejecutar con:  pytest tests/test_juegos.py -v
"""
import json
import time

import pytest

from core.config import dir_datos
from tools import computer
from tools.intent_engine import interpretar


def manifiesto_steam(carpeta, identificador, nombre, estado="4"):
    (carpeta / "steamapps").mkdir(parents=True, exist_ok=True)
    (carpeta / "steamapps" / f"appmanifest_{identificador}.acf").write_text(
        '"AppState"\n{\n'
        f'\t"appid"\t\t"{identificador}"\n\t"name"\t\t"{nombre}"\n\t"StateFlags"\t\t"{estado}"\n'
        f'\t"installdir"\t\t"{nombre}"\n}}\n', encoding="utf-8")


@pytest.fixture
def steam(tmp_path):
    """Steam con dos bibliotecas: la suya y otra en «otro disco»."""
    principal, otra = tmp_path / "Steam", tmp_path / "Otro disco" / "SteamLibrary"
    manifiesto_steam(principal, 739630, "Phasmophobia")
    manifiesto_steam(principal, 228980, "Steamworks Common Redistributables")
    manifiesto_steam(principal, 1234, "Juego a medio descargar", estado="1026")
    manifiesto_steam(otra, 3156800, "The WereCleaner")
    rutas = [str(principal).replace("\\", "\\\\"), str(otra).replace("\\", "\\\\")]
    (principal / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n' + "".join(
            f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{ruta}"\n\t\t"label"\t\t""\n\t}}\n' for i, ruta in enumerate(rutas))
        + "}\n", encoding="utf-8")
    return principal


@pytest.fixture
def epic(tmp_path):
    carpeta = tmp_path / "Manifests"
    carpeta.mkdir()
    manifiestos = {
        "rocket": {"DisplayName": "Rocket League®", "AppName": "Sugar", "CatalogNamespace": "9773aa1a",
                   "CatalogItemId": "530145df", "bIsIncompleteInstall": False,
                   "AppCategories": ["public", "games", "applications"]},
        "plugin": {"DisplayName": "Un complemento", "AppName": "Plug", "CatalogNamespace": "aa", "CatalogItemId": "bb",
                   "AppCategories": ["plugins", "engines"]},
        "a_medias": {"DisplayName": "Fall Guys", "AppName": "0a2d9f", "CatalogNamespace": "50118b",
                     "CatalogItemId": "38ec48", "bIsIncompleteInstall": True, "AppCategories": ["games"]},
    }
    for nombre, datos in manifiestos.items():
        (carpeta / f"{nombre}.item").write_text(json.dumps(datos), encoding="utf-8")
    (carpeta / "roto.item").write_text("{esto no es json", encoding="utf-8")
    return carpeta


# ==========================================
# LECTURA DE LAS BIBLIOTECAS
# ==========================================
def test_bibliotecas_de_steam_en_varios_discos(steam):
    assert [ruta.name for ruta in computer.bibliotecas_de_steam(steam)] == ["Steam", "SteamLibrary"]


def test_juegos_de_steam(steam):
    juegos = {j["nombre"]: j for j in computer.juegos_de_steam(steam)}
    assert sorted(juegos) == ["Phasmophobia", "The WereCleaner"]      # sin redistribuibles ni descargas a medias
    assert juegos["Phasmophobia"]["destino"] == "steam://rungameid/739630"
    assert juegos["Phasmophobia"]["tipo"] == "protocolo" and juegos["Phasmophobia"]["clave"] == "phasmophobia"


def test_juegos_de_epic(epic):
    juegos = computer.juegos_de_epic(epic)
    assert [j["nombre"] for j in juegos] == ["Rocket League"]           # sin «®», sin complementos ni a medias
    assert juegos[0]["destino"] == "com.epicgames.launcher://apps/9773aa1a%3A530145df%3ASugar?action=launch&silent=true"


def test_sin_steam_ni_epic_no_pasa_nada(tmp_path):
    assert computer.juegos_de_epic(tmp_path / "no existe") == []
    assert computer.bibliotecas_de_steam(tmp_path / "no existe") == []


# ==========================================
# EN EL ÍNDICE DE APLICACIONES
# ==========================================
@pytest.fixture
def indice(monkeypatch, steam, epic):
    acceso = computer._entrada("Phasmophobia", "ruta", r"C:\Users\Ruth\Desktop\Phasmophobia.url")
    monkeypatch.setattr(computer, "_apps_menu_inicio", lambda: [acceso, computer._entrada("Spotify", "ruta", "s.lnk")])
    monkeypatch.setattr(computer, "_apps_get_startapps", lambda: [])
    monkeypatch.setattr(computer, "_juegos_instalados",
                        lambda: computer.juegos_de_steam(steam) + computer.juegos_de_epic(epic))
    monkeypatch.setattr(computer, "_INDICE", [])
    monkeypatch.setattr(computer, "_INDICE_FECHA", 0.0)
    (dir_datos() / "apps.json").unlink(missing_ok=True)
    yield computer.indexar_aplicaciones(forzar=True)
    (dir_datos() / "apps.json").unlink(missing_ok=True)


def test_los_juegos_entran_en_el_indice_sin_repetirse(indice):
    nombres = [entrada["nombre"] for entrada in indice]
    assert nombres == ["Phasmophobia", "Spotify", "The WereCleaner", "Rocket League"]
    assert indice[0]["tipo"] == "ruta"                                 # si ya tenía acceso directo, se usa ese


def test_abrir_un_juego_por_su_nombre(indice):
    assert computer.resolver_objetivo("Rocket League")["destino"].startswith("com.epicgames.launcher://")
    assert computer.resolver_objetivo("the werecleaner")["destino"] == "steam://rungameid/3156800"
    assert computer.resolver_objetivo("el Rocket")["nombre"] == "Rocket League"      # con una parte basta
    ok, nombre = computer.abrir_aplicacion("Rocket League")                           # en simulación
    assert ok and nombre == "Rocket League"


def test_un_indice_guardado_antiguo_se_rehace(monkeypatch, indice):
    # El de la versión anterior no tenía juegos: aunque sea reciente, no se usa.
    (dir_datos() / "apps.json").write_text(json.dumps({"fecha": time.time(), "apps": [
        computer._entrada("Spotify", "ruta", "s.lnk")]}), encoding="utf-8")
    monkeypatch.setattr(computer, "_INDICE", [])
    nombres = [entrada["nombre"] for entrada in computer.indexar_aplicaciones()]
    assert "Rocket League" in nombres
    guardado = json.loads((dir_datos() / "apps.json").read_text(encoding="utf-8"))
    assert guardado["version"] == computer.VERSION_INDICE


# ==========================================
# ÓRDENES
# ==========================================
@pytest.mark.parametrize("frase,juego", [
    ("quiero jugar a Fall Guys", "Fall Guys"), ("vamos a jugar al Rocket League", "Rocket League"),
    ("juega a Phasmophobia", "Phasmophobia"), ("echemos una partida al Fall Guys", "Fall Guys"),
    ("déjame jugar un rato a Schedule I", "Schedule I"), ("abre el juego Phasmophobia", "Phasmophobia"),
])
def test_jugar_a_un_juego_lo_abre(frase, juego):
    r = interpretar(frase)
    assert (r["intent"], r["action"], r["target"]) == ("pc", "open_app", juego)


@pytest.mark.parametrize("frase", ["me gusta jugar al fútbol", "¿a qué juegas tú?", "mi hermano juega al pádel"])
def test_hablar_de_jugar_no_abre_nada(frase):
    assert interpretar(frase)["action"] != "open_app"
