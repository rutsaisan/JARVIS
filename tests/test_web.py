"""
Comprobaciones estáticas de la interfaz web (ui/web), sin abrir ningún navegador:

  - la hoja de estilos no tiene comentarios ni llaves sin cerrar (un comentario sin cerrar se
    traga en silencio todas las reglas que vienen detrás: pasó una vez con el reactor);
  - todo lo que app.js busca por id, por clase del reactor o por clave de texto existe;
  - los ficheros a los que apunta la hoja de estilos existen;
  - app.js no tiene errores de sintaxis (si Node.js está instalado).

Ejecutar con:  pytest tests/test_web.py -v
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "ui" / "web"


def leer(nombre: str) -> str:
    return (WEB / nombre).read_text(encoding="utf-8")


def css_sin_comentarios() -> str:
    return re.sub(r"/\*.*?\*/", "", leer("styles.css"), flags=re.S)


# ==========================================
# HOJA DE ESTILOS
# ==========================================
def test_comentarios_y_llaves_equilibrados():
    css = leer("styles.css")
    assert css.count("/*") == css.count("*/"), "hay un comentario sin cerrar"
    limpio = css_sin_comentarios()
    assert "/*" not in limpio and "*/" not in limpio, "hay un comentario sin cerrar"
    assert limpio.count("{") == limpio.count("}"), "hay llaves sin cerrar"


@pytest.mark.parametrize("regla", [
    ".marco {", ".lateral {", ".heroe {", ".orbe {", ".capa {", ".pregunta {", ".onda {",
    ".compositor {", ".mensaje {", ".recuerdo {", ".herramienta {", ".ajustes {", ".modal {", ".peticion {",
])
def test_reglas_principales_activas(regla):
    assert regla in css_sin_comentarios(), f"la regla {regla!r} no existe o está dentro de un comentario"


def test_ficheros_que_usa_la_hoja_de_estilos():
    for fichero in re.findall(r'url\("/web/([^"]+)"\)', leer("styles.css")):
        assert (WEB / fichero).exists(), f"falta ui/web/{fichero}"


def test_sin_animaciones_infinitas():
    # Ver el comentario del reactor en styles.css: en la ventana nativa cualquier animación CSS
    # infinita mantiene al compositor redibujando sin parar y dispara el consumo de procesador.
    assert not re.findall(r"animation:[^;]*infinite", css_sin_comentarios())


# ==========================================
# HTML ↔ JAVASCRIPT
# ==========================================
def test_los_ids_que_usa_app_js_existen():
    html, js = leer("index.html"), leer("app.js")
    en_html = set(re.findall(r'\bid="([\w-]+)"', html))
    creados_en_js = set(re.findall(r'\bid: "([\w-]+)"', js))
    usados = set(re.findall(r'\$\$?\(\s*["\']#([\w-]+)', js))
    faltan = usados - en_html - creados_en_js
    assert not faltan, f"app.js busca ids que no existen en index.html: {sorted(faltan)}"


def test_los_anillos_del_reactor_existen():
    html, js = leer("index.html"), leer("app.js")
    clases = set(re.findall(r'\$\("\.(r-[\w-]+)"', js))
    assert clases, "el motor del HUD debería buscar los anillos por su clase"
    for clase in clases:
        assert re.search(rf'class="capa {re.escape(clase)}"', html), f"falta la capa .{clase} en index.html"


def test_los_textos_que_usa_app_js_existen():
    js, textos = leer("app.js"), leer("textos.es.js")
    definidas = set(re.findall(r'"([^"]+)"\s*:', textos))
    usadas = {c for c in re.findall(r'\bt\(\s*"([^"]+)"', js) if not c.endswith(".")}
    faltan = usadas - definidas
    assert not faltan, f"faltan textos en textos.es.js: {sorted(faltan)}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js no está instalado")
def test_sintaxis_de_app_js():
    resultado = subprocess.run(["node", "--check", str(WEB / "app.js")], capture_output=True, text=True)
    assert resultado.returncode == 0, resultado.stderr
