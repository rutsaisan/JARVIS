"""
Pruebas de la búsqueda en Internet sin conexión: el buscador y Wikipedia se simulan.

El buscador devuelve a veces páginas que no tienen nada que ver (la película «Origen» al
buscar el origen de Rosalía). Con resultados así el modelo acababa inventándose el dato,
así que se filtran y, si hace falta, se completa con Wikipedia.

Ejecutar con:  pytest tests/test_busqueda.py -v
"""
import pytest

from tools import browser


def resultado(titulo, resumen="", url=None):
    return {"titulo": titulo, "resumen": resumen, "url": url or f"https://ejemplo.es/{titulo.replace(' ', '-')}",
            "fuente": "ejemplo.es", "fecha": ""}


WIKI_ROSALIA = resultado("Rosalía (cantante)", "Rosalía Vila Tobella (San Esteban Sasroviras, Barcelona, 25 de "
                         "septiembre de 1992) es una cantante española.",
                         "https://es.wikipedia.org/wiki/Rosal%C3%ADa_(cantante)")


class BuscadorFalso:
    """Sustituye a DDGS: devuelve lo que se le diga o falla."""

    def __init__(self, crudos=(), fallo=None):
        self.crudos, self.fallo, self.consultas = list(crudos), fallo, []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False

    def text(self, consulta, region=None, max_results=5):
        self.consultas.append(consulta)
        if self.fallo:
            raise self.fallo
        return [{"title": r["titulo"], "href": r["url"], "body": r["resumen"]} for r in self.crudos]

    news = text


@pytest.fixture
def buscador(monkeypatch):
    def preparar(crudos=(), fallo=None, wikipedia=()):
        falso = BuscadorFalso(crudos, fallo)
        monkeypatch.setattr(browser, "_clase_ddgs", lambda: falso)
        falso.wikipedia = []
        monkeypatch.setattr(browser, "buscar_en_wikipedia",
                            lambda consulta, maximo=2: falso.wikipedia.append(consulta) or list(wikipedia))
        return falso
    return preparar


# ==========================================
# RELEVANCIA
# ==========================================
def test_cobertura():
    assert browser.cobertura("precio del bitcoin", resultado("Precio del Bitcoin hoy", "68.000 dólares")) == 1.0
    assert browser.cobertura("Nicki Nicole cantante", resultado("Gmail", "Correo de Google")) == 0
    assert browser.cobertura("", resultado("lo que sea")) == 1.0


def test_nombres_propios_de_la_consulta():
    assert browser.claves_de_nombres("origen de Rosalía") == {"rosali"}
    assert browser.claves_de_nombres("precio actual de Bitcoin") == {"bitcoin"}
    assert browser.claves_de_nombres("chiste corto") == set()


def test_fuera_lo_que_no_tiene_que_ver():
    resultados = [resultado("Gmail", "Correo electrónico"), resultado("RAE - precio", "Valor pecuniario"),
                  resultado("Precio del Bitcoin hoy", "Cotización BTC en tiempo real")]
    assert [r["titulo"] for r in browser.ordenar_por_relevancia("precio del bitcoin", resultados)] == [
        "Precio del Bitcoin hoy", "RAE - precio"]
    # Si la consulta nombra algo, el resultado tiene que mencionarlo.
    assert [r["titulo"] for r in browser.ordenar_por_relevancia("precio del Bitcoin", resultados)] == [
        "Precio del Bitcoin hoy"]


def test_la_pelicula_origen_no_cuenta_como_el_origen_de_rosalia():
    pelicula = resultado("Origen (2010) - Filmaffinity", "Origen es una película de Christopher Nolan")
    assert browser.ordenar_por_relevancia("origen de Rosalía", [pelicula, WIKI_ROSALIA]) == [WIKI_ROSALIA]


def test_la_version_movil_de_wikipedia_es_la_misma_pagina():
    assert browser._misma_pagina("https://es.m.wikipedia.org/wiki/Rosalía_(cantante)") == \
        browser._misma_pagina("https://es.wikipedia.org/wiki/Rosal%C3%ADa_(cantante)")


# ==========================================
# BÚSQUEDA CON WIKIPEDIA DE COMPLEMENTO
# ==========================================
def test_resultados_claros_no_consultan_wikipedia(buscador):
    falso = buscador([resultado("Receta de paella valenciana", "Cómo hacer paella valenciana")])
    resultados = browser.buscar_resultados("receta de paella valenciana")
    assert [r["titulo"] for r in resultados] == ["Receta de paella valenciana"]
    assert falso.wikipedia == []


def test_resultados_sin_relacion_se_cambian_por_wikipedia(buscador):
    pelicula = resultado("Origen (2010) - Filmaffinity", "Origen es una película de Christopher Nolan")
    falso = buscador([pelicula], wikipedia=[WIKI_ROSALIA])
    assert browser.buscar_resultados("origen de Rosalía") == [WIKI_ROSALIA]
    assert falso.wikipedia == ["origen de Rosalía"]


def test_con_nombre_propio_wikipedia_va_primero_y_sin_repetir(buscador):
    movil = resultado("Rosalía (cantante) - Wikipedia", "Rosalía ha sido acusada de apropiación cultural",
                      "https://es.m.wikipedia.org/wiki/Rosalía_(cantante)")
    noticia = resultado("Rosalía en EL PAÍS", "Rosalía en la gala de anoche")
    buscador([movil, noticia], wikipedia=[WIKI_ROSALIA])
    assert browser.buscar_resultados("Rosalía cantante") == [WIKI_ROSALIA, noticia]


def test_si_el_buscador_falla_se_usa_wikipedia(buscador):
    buscador(fallo=RuntimeError("202 Ratelimit"), wikipedia=[WIKI_ROSALIA])
    assert browser.buscar_resultados("Rosalía") == [WIKI_ROSALIA]


def test_si_falla_todo_se_avisa(buscador):
    buscador(fallo=RuntimeError("sin conexión"))
    with pytest.raises(browser.ErrorBusqueda):
        browser.buscar_resultados("Rosalía")


def test_las_noticias_no_se_filtran_ni_usan_wikipedia(buscador):
    falso = buscador([resultado("Última hora", "Lo que pasa hoy")], wikipedia=[WIKI_ROSALIA])
    assert [r["titulo"] for r in browser.buscar_resultados("Rosalía", tipo="noticias")] == ["Última hora"]
    assert falso.wikipedia == []


def test_wikipedia_se_lee_bien_y_sin_datos_de_la_usuaria(monkeypatch):
    peticiones = []

    class Respuesta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query": {"pages": [
                {"index": 2, "title": "Rosalía de Castro", "fullurl": "https://es.wikipedia.org/wiki/Rosal%C3%ADa_de_Castro",
                 "extract": "María Rosalía Rita de Castro fue una poeta."},
                {"index": 1, "title": "Rosalía (cantante)", "extract": "Rosalía Vila Tobella\n es una cantante."}]}}

    import requests
    monkeypatch.setattr(requests, "get", lambda url, params=None, headers=None, timeout=None:
                        peticiones.append((url, params, headers)) or Respuesta())
    resultados = browser.buscar_en_wikipedia("Rosalía")
    assert [r["titulo"] for r in resultados] == ["Rosalía (cantante)", "Rosalía de Castro"]     # por relevancia
    assert resultados[0]["resumen"] == "Rosalía Vila Tobella es una cantante."
    assert resultados[0]["url"] == "https://es.wikipedia.org/wiki/Rosal%C3%ADa_(cantante)"
    url, parametros, cabeceras = peticiones[0]
    assert url == "https://es.wikipedia.org/w/api.php" and parametros["gsrsearch"] == "Rosalía"
    assert cabeceras == {"User-Agent": "JARVIS-asistente-local/1.0 (proyecto educativo)"}


def test_wikipedia_caida_no_rompe_nada(monkeypatch):
    import requests

    def fallar(*argumentos, **opciones):
        raise requests.ConnectionError("sin red")
    monkeypatch.setattr(requests, "get", fallar)
    assert browser.buscar_en_wikipedia("Rosalía") == []
