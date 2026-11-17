"""
Pruebas de la actualización de ajustes.json entre versiones.

ajustes.json guarda todos los valores, también los que nadie ha tocado. Cuando cambia un
valor por defecto (la voz de Google, llama3...), los ficheros antiguos se actualizan, pero
solo en lo que la usuaria no haya cambiado a mano.

Ejecutar con:  pytest tests/test_config.py -v
"""
import json

from core.config import AJUSTES_POR_DEFECTO, VERSION_AJUSTES, Ajustes


def escribir(carpeta, datos):
    ruta = carpeta / "ajustes.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


def test_los_ajustes_antiguos_se_actualizan(tmp_path):
    ruta = escribir(tmp_path, {
        "usuario": {"nombre": "Ruth"},
        "ia": {"modelo": "llama3", "temperatura": 0.7},
        "voz": {"motor_habla": "google", "voz_edge": "es-ES-AlvaroNeural", "genero": "femenino"}})
    ajustes = Ajustes(str(ruta))
    assert ajustes.obtener("ia.modelo") == "granite4.1:8b"           # llama3 no sabe usar herramientas
    assert ajustes.obtener("ia.temperatura") == 0.3                 # menos creatividad, menos inventos
    assert ajustes.obtener("voz.motor_habla") == "edge"              # voz neuronal, menos robótica
    assert ajustes.obtener("voz.voz_edge") == ""                     # automática según el género
    assert ajustes.obtener("usuario.nombre") == "Ruth"               # lo demás se conserva
    assert ajustes.obtener("memoria.aprender_automaticamente") is True
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    assert guardado["version"] == VERSION_AJUSTES and guardado["ia"]["modelo"] == "granite4.1:8b"


def test_lo_elegido_a_mano_se_respeta(tmp_path):
    ruta = escribir(tmp_path, {
        "ia": {"modelo": "qwen3:8b", "temperatura": 0.3},
        "voz": {"motor_habla": "windows", "voz_edge": "es-MX-DaliaNeural"}})
    ajustes = Ajustes(str(ruta))
    assert ajustes.obtener("ia.modelo") == "qwen3:8b"
    assert ajustes.obtener("ia.temperatura") == 0.3
    assert ajustes.obtener("voz.motor_habla") == "windows"
    assert ajustes.obtener("voz.voz_edge") == "es-MX-DaliaNeural"


def test_los_ajustes_ya_actualizados_no_se_tocan(tmp_path):
    # Si después de actualizar vuelve a elegir Google o llama3, es a propósito.
    ruta = escribir(tmp_path, {"version": VERSION_AJUSTES, "ia": {"modelo": "llama3"},
                               "voz": {"motor_habla": "google"}})
    ajustes = Ajustes(str(ruta))
    assert ajustes.obtener("ia.modelo") == "llama3"
    assert ajustes.obtener("voz.motor_habla") == "google"


def test_valores_por_defecto_nuevos():
    assert AJUSTES_POR_DEFECTO["version"] == VERSION_AJUSTES
    assert AJUSTES_POR_DEFECTO["voz"]["motor_habla"] == "edge"
    assert AJUSTES_POR_DEFECTO["musica"]["servicio"] == "youtube"
    assert AJUSTES_POR_DEFECTO["herramientas"]["musica"] is True


def test_el_servicio_de_musica_solo_admite_los_conocidos(tmp_path):
    ajustes = Ajustes(str(tmp_path / "ajustes.json"))
    ajustes.establecer("musica.servicio", "spotify")
    try:
        ajustes.establecer("musica.servicio", "napster")
    except ValueError:
        pass
    assert ajustes.obtener("musica.servicio") == "spotify"
