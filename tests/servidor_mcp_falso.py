"""
Servidor MCP de mentira para las pruebas (tests/test_mcp.py): habla JSON-RPC por stdio,
un mensaje por línea, como un servidor MCP de verdad.

Herramientas:
    hora        de solo lectura (readOnlyHint): devuelve una hora fija
    leer_nota   sin anotaciones: se trata como «puede cambiar cosas»
    borrar_todo destructiva
    falla       devuelve isError
"""
import json
import sys

HERRAMIENTAS = [
    {"name": "hora", "description": "Dice la hora", "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}},
    {"name": "leer_nota", "description": "Lee una nota",
     "inputSchema": {"type": "object", "properties": {"titulo": {"type": "string"}}, "required": ["titulo"]}},
    {"name": "borrar_todo", "description": "Borra todo", "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"destructiveHint": True}},
    {"name": "falla", "description": "Siempre falla", "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}},
]


def responder(identificador, resultado):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": identificador, "result": resultado}) + "\n")
    sys.stdout.flush()


for linea in sys.stdin:
    mensaje = json.loads(linea)
    metodo, identificador = mensaje.get("method"), mensaje.get("id")
    if identificador is None:
        continue                                                     # notificaciones
    if metodo == "initialize":
        sys.stdout.write("esto no es JSON y se ignora\n")
        responder(identificador, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "falso", "version": "1"}})
    elif metodo == "tools/list":
        responder(identificador, {"tools": HERRAMIENTAS})
    elif metodo == "tools/call":
        nombre, argumentos = mensaje["params"]["name"], mensaje["params"].get("arguments") or {}
        if nombre == "hora":
            responder(identificador, {"content": [{"type": "text", "text": "Son las 10:30."}]})
        elif nombre == "leer_nota":
            responder(identificador, {"content": [{"type": "text", "text": f"Nota «{argumentos.get('titulo')}»: comprar pan."}]})
        elif nombre == "falla":
            responder(identificador, {"content": [{"type": "text", "text": "No se puede."}], "isError": True})
        else:
            responder(identificador, {"content": [{"type": "text", "text": "Borrado."}]})
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": identificador,
                                     "error": {"code": -32601, "message": "Método desconocido"}}) + "\n")
        sys.stdout.flush()
