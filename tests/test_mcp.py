"""
Pruebas del cliente MCP (core/mcp.py) con un servidor MCP de mentira (tests/servidor_mcp_falso.py)
que habla JSON-RPC por stdio como uno de verdad.

Ejecutar con:  pytest tests/test_mcp.py -v
"""
import json
import sys
from pathlib import Path

import pytest

from core.mcp import GestorMCP, nombre_de_funcion

SERVIDOR = str(Path(__file__).with_name("servidor_mcp_falso.py"))


def configurar(tmp_path, **servidores):
    ruta = tmp_path / "mcp.json"
    ruta.write_text(json.dumps({"servidores": servidores}), encoding="utf-8")
    return ruta


@pytest.fixture
def gestor(tmp_path):
    g = GestorMCP(configurar(tmp_path, notas={"comando": sys.executable, "argumentos": [SERVIDOR]}))
    g.arrancar()
    yield g
    g.cerrar()


def test_sin_fichero_no_arranca_nada(tmp_path):
    g = GestorMCP(tmp_path / "no_existe.json")
    g.arrancar()
    assert g.servidores == {} and g.definiciones() == [] and g.estado() == []


def test_descubre_las_herramientas(gestor):
    assert gestor.nombres() == {"mcp_notas_hora", "mcp_notas_leer_nota", "mcp_notas_borrar_todo", "mcp_notas_falla"}
    estado = gestor.estado()[0]
    assert estado["servidor"] == "notas" and estado["activo"]
    assert {h["nombre"]: h["permitida"] for h in estado["herramientas"]} == {
        "hora": True, "leer_nota": False, "borrar_todo": False, "falla": True}


def test_al_modelo_solo_se_le_ofrecen_las_de_lectura(gestor):
    definiciones = {d["function"]["name"]: d for d in gestor.definiciones()}
    assert set(definiciones) == {"mcp_notas_hora", "mcp_notas_falla"}
    assert definiciones["mcp_notas_hora"]["function"]["description"] == "[notas] Dice la hora"


def test_llamar_herramientas(gestor):
    assert gestor.llamar("mcp_notas_hora", {}) == (True, "Son las 10:30.")
    assert gestor.llamar("mcp_notas_falla", {}) == (False, "No se puede.")
    correcto, texto = gestor.llamar("mcp_notas_borrar_todo", {})
    assert not correcto and texto.startswith("Rechazado")                     # bloqueada por defecto
    assert gestor.llamar("mcp_otro_cosa", {})[0] is False


def test_permitir_herramientas_concretas(tmp_path):
    g = GestorMCP(configurar(tmp_path, notas={"comando": sys.executable, "argumentos": [SERVIDOR],
                                              "herramientas_permitidas": ["leer_nota"]}))
    g.arrancar()
    try:
        assert g.llamar("mcp_notas_leer_nota", {"titulo": "compra"}) == (True, "Nota «compra»: comprar pan.")
        assert g.llamar("mcp_notas_borrar_todo", {})[0] is False                  # esta sigue bloqueada
    finally:
        g.cerrar()


def test_un_servidor_que_no_arranca_no_rompe_nada(tmp_path):
    g = GestorMCP(configurar(tmp_path, roto={"comando": "programa_que_no_existe_jarvis"},
                             apagado={"comando": sys.executable, "argumentos": [SERVIDOR], "activo": False}))
    g.arrancar()
    assert g.servidores == {} and g.estado()[0]["servidor"] == "roto" and "error" in g.estado()[0]


def test_al_cerrar_se_paran_los_servidores(gestor):
    servidor = gestor.servidores["notas"]
    gestor.cerrar()
    assert not servidor.vivo and gestor.nombres() == set()


def test_nombres_de_funcion_validos():
    assert nombre_de_funcion("mis notas", "leer-nota") == "mcp_mis_notas_leer_nota"
    assert len(nombre_de_funcion("x" * 80, "y")) == 64
