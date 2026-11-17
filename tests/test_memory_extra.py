"""
Pruebas añadidas en la versión final de la memoria (SQLite + privacidad).

Ejecutar con:  pytest tests/test_memory_extra.py -v
"""
import shutil
import tempfile

import numpy as np
import pytest

from tools.memory import MemoriaLargoPlazo, es_dato_sensible


@pytest.fixture
def memoria():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_mem_")
    yield MemoriaLargoPlazo(db_path=carpeta)
    shutil.rmtree(carpeta, ignore_errors=True)


# ==========================================
# PRIVACIDAD: nada de contraseñas ni datos bancarios
# ==========================================
@pytest.mark.parametrize("texto", [
    "mi contraseña del banco es 1234",
    "el PIN de la tarjeta es 4821",
    "mi IBAN es ES91 2100 0418 4502 0005 1332",
    "la clave del wifi es fresa2024",
    "el número de la tarjeta es 4111 1111 1111 1111",
])
def test_no_guarda_datos_sensibles(memoria, texto):
    resultado = memoria.guardar(texto)
    assert resultado["ok"] is False
    assert resultado["motivo"] == "sensible"
    assert memoria.contar() == 0


@pytest.mark.parametrize("texto", [
    "me gusta el rock", "mi cumpleaños es el 5 de mayo", "la clave del éxito es la constancia",
    "tengo 3 gatos",
])
def test_datos_normales_no_son_sensibles(texto):
    assert es_dato_sensible(texto) is False


# ==========================================
# GUARDAR
# ==========================================
def test_conserva_mayusculas_y_acentos(memoria):
    memoria.guardar_recuerdo("Recuerda que mi perro se llama Toby")
    assert memoria.listar_recuerdos()[0]["documento"] == "mi perro se llama Toby"


def test_no_duplica_recuerdos(memoria):
    memoria.guardar_recuerdo("me gusta el rock")
    resultado = memoria.guardar("Me gusta el rock.")
    assert resultado["motivo"] == "duplicado"
    assert memoria.contar() == 1


def test_clave_y_valor(memoria):
    memoria.guardar_recuerdo("mi color favorito es el morado")
    recuerdo = memoria.listar_recuerdos()[0]
    assert recuerdo["clave"] == "mi color favorito"
    assert recuerdo["valor"] == "el morado"


def test_categoria_no_se_confunde_con_subcadenas(memoria):
    # "felicitar" contiene "cita", pero no es una tarea.
    memoria.guardar_recuerdo("hay que felicitar a Marta")
    assert memoria.listar_recuerdos()[0]["categoria"] == "información"


# ==========================================
# BUSCAR
# ==========================================
def test_no_inyecta_recuerdos_irrelevantes(memoria):
    memoria.guardar_recuerdo("no tengo tiempo para nada")
    assert memoria.buscar_contexto("¿qué tal el tiempo?") == ""


def test_buscar_devuelve_relevancia_ordenada(memoria):
    memoria.guardar_recuerdo("mi color favorito es el morado")
    memoria.guardar_recuerdo("mi comida favorita es la paella")
    resultados = memoria.buscar("cuál es mi color favorito", n=2)
    assert resultados[0]["documento"] == "mi color favorito es el morado"
    assert 0 < resultados[0]["relevancia"] <= 1


def test_listar_con_busqueda(memoria):
    memoria.guardar_recuerdo("me gusta el rock")
    memoria.guardar_recuerdo("mi cumpleaños es en marzo")
    assert [r["documento"] for r in memoria.listar_recuerdos(buscar="ROCK")] == ["me gusta el rock"]


# ==========================================
# EDITAR Y BORRAR POR ID (panel de memoria)
# ==========================================
def test_actualizar_por_id(memoria):
    nuevo = memoria.guardar("me gusta el té")
    assert memoria.actualizar(nuevo["id"], texto="prefiero el té al café") is True
    assert memoria.obtener(nuevo["id"])["documento"] == "prefiero el té al café"


def test_actualizar_rechaza_datos_sensibles(memoria):
    nuevo = memoria.guardar("me gusta el té")
    assert memoria.actualizar(nuevo["id"], texto="mi contraseña es 1234") is False
    assert memoria.obtener(nuevo["id"])["documento"] == "me gusta el té"


def test_borrar_por_id_es_logico(memoria):
    nuevo = memoria.guardar("me gusta el té")
    assert memoria.borrar_por_id(nuevo["id"]) is True
    assert memoria.obtener(nuevo["id"]) is None
    assert memoria.contar() == 0
    # El dato sigue en la base de datos, marcado como inactivo (se puede auditar).
    assert memoria.bd.uno("SELECT COUNT(*) AS n FROM recuerdos")["n"] == 1


def test_borrar_todo(memoria):
    memoria.guardar_recuerdo("me gusta el rock")
    memoria.guardar_recuerdo("mi cumpleaños es en marzo")
    assert memoria.borrar_todo() == 2
    assert memoria.listar_recuerdos() == []


def test_editar_frase_completa_la_sustituye(memoria):
    memoria.guardar_recuerdo("trabajo en una tienda")
    memoria.editar_recuerdo("mi trabajo en la tienda", "ahora trabajo en una empresa de software")
    assert memoria.listar_recuerdos()[0]["documento"] == "ahora trabajo en una empresa de software"


# ==========================================
# BÚSQUEDA SEMÁNTICA (con un generador de vectores de mentira)
# ==========================================
class EmbeddingsFalsos:
    """Sustituye a Ollama: textos «de música» apuntan en una dirección y el resto en otra."""
    modelo = "falso"
    activo = True

    def vector(self, texto, tipo="documento"):
        musical = any(p in texto.lower() for p in ("rock", "música", "canción", "escuchar"))
        return np.array([1.0, 0.0] if musical else [0.0, 1.0], dtype=np.float32)

    def vectores(self, textos, tipo="documento"):
        return [self.vector(t, tipo) for t in textos]


def test_busqueda_semantica_encuentra_sin_palabras_comunes():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_sem_")
    try:
        memoria = MemoriaLargoPlazo(db_path=carpeta, embeddings=EmbeddingsFalsos())
        memoria.guardar_recuerdo("adoro el rock de los 80")
        memoria.guardar_recuerdo("mi cumpleaños es en marzo")
        # Ninguna palabra clave en común: solo lo encuentra el parecido semántico.
        assert "rock" in memoria.buscar_contexto("¿qué suelo escuchar?")
        assert memoria.buscar_contexto("¿cuándo nací?") == "" or "marzo" in memoria.buscar_contexto("¿cuándo nací?")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
