"""
Pruebas del módulo de memoria persistente (tools/memory.py).
Usa una base de datos SQLite temporal en cada test para no tocar data/jarvis.db.

Ejecutar con:  pytest tests/test_memory.py -v
"""
import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from tools.memory import MemoriaLargoPlazo


@pytest.fixture
def memoria():
    tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_db_")
    m = MemoriaLargoPlazo(db_path=tmp_dir)
    yield m
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ==========================================
# GUARDAR
# ==========================================
def test_guardar_recuerdo_basico(memoria):
    respuesta = memoria.guardar_recuerdo("recuerda que me gusta el rock")
    assert "almacenado" in respuesta.lower()
    recuerdos = memoria.listar_recuerdos()
    assert len(recuerdos) == 1
    assert recuerdos[0]["documento"] == "me gusta el rock"


def test_guardar_recuerdo_vacio(memoria):
    respuesta = memoria.guardar_recuerdo("recuerda")
    assert "no he detectado" in respuesta.lower()
    assert memoria.listar_recuerdos() == []


@pytest.mark.parametrize("texto,categoria_esperada", [
    ("me gusta el rock", "preferencias"),
    ("mi cumpleaños es en marzo", "personales"),
    ("tengo que entregar el TFG el viernes", "tareas"),
    ("la torre eiffel mide 330 metros", "información"),
])
def test_categorizacion_automatica(memoria, texto, categoria_esperada):
    memoria.guardar_recuerdo(texto)
    recuerdos = memoria.listar_recuerdos()
    assert recuerdos[0]["categoria"] == categoria_esperada


# ==========================================
# CONSULTAR (semántico)
# ==========================================
def test_buscar_contexto_relevante(memoria):
    memoria.guardar_recuerdo("recuerda que mi color favorito es el morado")
    resultado = memoria.buscar_contexto("cuál es mi color favorito")
    assert "morado" in resultado


def test_buscar_contexto_sin_recuerdos(memoria):
    assert memoria.buscar_contexto("cualquier cosa") == ""


# ==========================================
# LISTAR
# ==========================================
def test_listar_por_categoria(memoria):
    memoria.guardar_recuerdo("recuerda que me gusta el rock")
    memoria.guardar_recuerdo("recuerda que mi cumpleaños es en marzo")
    solo_preferencias = memoria.listar_recuerdos(categoria="preferencias")
    assert len(solo_preferencias) == 1
    assert solo_preferencias[0]["documento"] == "me gusta el rock"


# ==========================================
# EDITAR
# ==========================================
def test_editar_recuerdo_existente(memoria):
    memoria.guardar_recuerdo("recuerda que mi color favorito es el morado")
    respuesta = memoria.editar_recuerdo("mi color favorito", "verde")
    assert "actualizado" in respuesta.lower()
    recuerdos = memoria.listar_recuerdos()
    assert len(recuerdos) == 1
    # Cambio respecto a la v2: al editar se conserva de qué trata el recuerdo.
    # Antes quedaba solo "verde", que por sí solo ya no significaba nada.
    assert recuerdos[0]["documento"] == "mi color favorito es verde"


def test_editar_recuerdo_inexistente(memoria):
    respuesta = memoria.editar_recuerdo("algo que no existe", "nuevo valor")
    assert "no encuentro" in respuesta.lower()


# ==========================================
# BORRAR (soft-delete)
# ==========================================
def test_borrar_recuerdo_existente(memoria):
    memoria.guardar_recuerdo("recuerda que me gusta el rock")
    respuesta = memoria.borrar_recuerdo("me gusta el rock")
    assert "he olvidado" in respuesta.lower()
    assert memoria.listar_recuerdos() == []


def test_borrar_no_devuelve_resultados_en_busqueda(memoria):
    memoria.guardar_recuerdo("recuerda que me gusta el rock")
    memoria.borrar_recuerdo("me gusta el rock")
    assert memoria.buscar_contexto("qué música me gusta") == ""


def test_borrar_recuerdo_inexistente(memoria):
    respuesta = memoria.borrar_recuerdo("algo que nunca guardé")
    assert "no encuentro" in respuesta.lower()
