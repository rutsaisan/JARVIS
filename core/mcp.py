"""
Herramientas MCP (Model Context Protocol)
==========================================
MCP es un estándar abierto para dar herramientas a una IA. Aquí JARVIS hace de cliente: arranca
los servidores MCP locales que configures en data/mcp.json y le ofrece sus herramientas al
modelo de Ollama como funciones, igual que las suyas (core/funciones.py).

    {
      "servidores": {
        "documentos": {
          "comando": "npx",
          "argumentos": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/Ruth/Documents"],
          "activo": true,
          "permitir_cambios": false,
          "herramientas_permitidas": []
        }
      }
    }

Seguridad, por defecto:
  - Sin fichero no se arranca nada. JARVIS nunca instala servidores por su cuenta.
  - Solo se usan las herramientas que el servidor marca como de solo lectura (readOnlyHint).
    Las que pueden cambiar cosas quedan bloqueadas, salvo que el servidor tenga
    "permitir_cambios": true o la herramienta esté en "herramientas_permitidas".
  - Cada llamada queda en el registro de auditoría, y la herramienta «MCP» se puede
    desactivar desde el panel de Herramientas.

Transporte: stdio, JSON-RPC 2.0 con un mensaje JSON por línea (como manda la especificación).
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import re
import subprocess
import threading

registro = logging.getLogger("jarvis.mcp")

VERSION_PROTOCOLO = "2025-06-18"
TIEMPO_RESPUESTA = 30.0               # segundos de espera para cada petición
MAXIMO_HERRAMIENTAS = 12              # al modelo no se le ofrecen más (cada una ocupa contexto)
MAXIMO_DESCRIPCION = 240
MAXIMO_TEXTO = 4000
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErrorMCP(Exception):
    pass


def nombre_de_funcion(servidor: str, herramienta: str) -> str:
    """Nombre de la función para el modelo: mcp_<servidor>_<herramienta> (solo letras, números y _)."""
    limpio = re.sub(r"[^A-Za-z0-9_]", "_", f"mcp_{servidor}_{herramienta}")
    return limpio[:64]


class ServidorMCP:
    """Un servidor MCP local arrancado como proceso hijo y hablado por su entrada y salida estándar."""

    def __init__(self, nombre: str, comando: str, argumentos=(), entorno: dict | None = None,
                 permitir_cambios: bool = False, permitidas=()):
        self.nombre = nombre
        self.comando = comando
        self.argumentos = list(argumentos)
        self.entorno = dict(entorno or {})
        self.permitir_cambios = bool(permitir_cambios)
        self.permitidas = set(permitidas or ())
        self.herramientas = []                 # [{"name", "description", "inputSchema", "annotations"}]
        self._proceso = None
        self._ids = itertools.count(1)
        self._esperando = {}                   # id -> cola con la respuesta
        self._cerrojo = threading.Lock()

    # ---------- ciclo de vida ----------
    def arrancar(self) -> None:
        entorno = dict(os.environ, **{str(k): str(v) for k, v in self.entorno.items()})
        self._proceso = subprocess.Popen(
            [self.comando, *self.argumentos], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=entorno, creationflags=SIN_VENTANA)
        threading.Thread(target=self._leer, name=f"jarvis-mcp-{self.nombre}", daemon=True).start()
        self._peticion("initialize", {"protocolVersion": VERSION_PROTOCOLO, "capabilities": {},
                                      "clientInfo": {"name": "JARVIS", "version": "1.0"}})
        self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        resultado = self._peticion("tools/list", {})
        self.herramientas = [h for h in resultado.get("tools", []) if isinstance(h, dict) and h.get("name")]
        registro.info("Servidor MCP «%s»: %d herramientas.", self.nombre, len(self.herramientas))

    def cerrar(self) -> None:
        proceso, self._proceso = self._proceso, None
        if proceso and proceso.poll() is None:
            try:
                proceso.stdin.close()
                proceso.terminate()
                proceso.wait(timeout=3)
            except Exception:
                try:
                    proceso.kill()
                except OSError:
                    pass

    @property
    def vivo(self) -> bool:
        return self._proceso is not None and self._proceso.poll() is None

    # ---------- JSON-RPC ----------
    def _enviar(self, mensaje: dict) -> None:
        if not self.vivo:
            raise ErrorMCP(f"el servidor «{self.nombre}» no está en marcha")
        linea = (json.dumps(mensaje, ensure_ascii=False) + "\n").encode("utf-8")
        with self._cerrojo:
            self._proceso.stdin.write(linea)
            self._proceso.stdin.flush()

    def _peticion(self, metodo: str, parametros: dict, tiempo: float = TIEMPO_RESPUESTA) -> dict:
        identificador = next(self._ids)
        buzon = queue.Queue(maxsize=1)
        self._esperando[identificador] = buzon
        try:
            self._enviar({"jsonrpc": "2.0", "id": identificador, "method": metodo, "params": parametros})
            try:
                respuesta = buzon.get(timeout=tiempo)
            except queue.Empty:
                raise ErrorMCP(f"«{self.nombre}» no ha respondido a {metodo}") from None
        finally:
            self._esperando.pop(identificador, None)
        if respuesta is None:
            raise ErrorMCP(f"el servidor «{self.nombre}» se ha cerrado")
        if "error" in respuesta:
            raise ErrorMCP(str((respuesta["error"] or {}).get("message") or respuesta["error"]))
        return respuesta.get("result") or {}

    def _leer(self) -> None:
        proceso = self._proceso
        try:
            for linea in proceso.stdout:
                try:
                    mensaje = json.loads(linea.decode("utf-8", errors="replace"))
                except ValueError:
                    continue                                   # líneas que no son JSON: se ignoran
                if not isinstance(mensaje, dict):
                    continue
                if "id" in mensaje and ("result" in mensaje or "error" in mensaje):
                    buzon = self._esperando.get(mensaje["id"])
                    if buzon:
                        buzon.put(mensaje)
                elif "id" in mensaje and "method" in mensaje:
                    # Peticiones del servidor (roots, sampling...): JARVIS no las ofrece.
                    try:
                        self._enviar({"jsonrpc": "2.0", "id": mensaje["id"],
                                      "error": {"code": -32601, "message": "No disponible en JARVIS"}})
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            for buzon in list(self._esperando.values()):
                try:
                    buzon.put_nowait(None)
                except queue.Full:
                    pass

    # ---------- herramientas ----------
    def permitida(self, herramienta: dict) -> bool:
        anotaciones = herramienta.get("annotations") or {}
        return (bool(anotaciones.get("readOnlyHint")) or self.permitir_cambios
                or herramienta.get("name") in self.permitidas)

    def llamar(self, herramienta: str, argumentos: dict) -> tuple:
        """(correcto, texto) de una llamada a una herramienta."""
        resultado = self._peticion("tools/call", {"name": herramienta, "arguments": argumentos or {}})
        partes = []
        for contenido in resultado.get("content") or []:
            if not isinstance(contenido, dict):
                continue
            if contenido.get("type") == "text":
                partes.append(str(contenido.get("text", "")))
            elif contenido.get("type") == "resource":
                recurso = contenido.get("resource") or {}
                partes.append(str(recurso.get("text") or recurso.get("uri") or ""))
            else:
                partes.append(f"[{contenido.get('type', 'contenido')} sin texto]")
        if not partes and resultado.get("structuredContent") is not None:
            partes.append(json.dumps(resultado["structuredContent"], ensure_ascii=False))
        return not resultado.get("isError", False), "\n".join(partes)[:MAXIMO_TEXTO]


class GestorMCP:
    """Los servidores MCP configurados y sus herramientas, ofrecidas al modelo como funciones."""

    def __init__(self, ruta_configuracion):
        self.ruta = ruta_configuracion
        self.servidores = {}
        self.errores = {}                       # servidor -> por qué no arrancó
        self._funciones = {}                    # nombre de función -> (servidor, herramienta)

    def leer_configuracion(self) -> dict:
        try:
            with open(self.ruta, encoding="utf-8") as fichero:
                datos = json.load(fichero)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            registro.warning("No se pudo leer %s: %s", self.ruta, error)
            return {}
        servidores = datos.get("servidores") if isinstance(datos, dict) else None
        return servidores if isinstance(servidores, dict) else {}

    def arrancar(self) -> None:
        for nombre, conf in self.leer_configuracion().items():
            if not isinstance(conf, dict) or not conf.get("activo", True) or not conf.get("comando"):
                continue
            servidor = ServidorMCP(str(nombre), str(conf["comando"]), conf.get("argumentos") or [],
                                   conf.get("entorno") or {}, conf.get("permitir_cambios", False),
                                   conf.get("herramientas_permitidas") or [])
            try:
                servidor.arrancar()
            except Exception as error:
                self.errores[nombre] = f"{type(error).__name__}: {error}"
                registro.warning("El servidor MCP «%s» no ha arrancado: %s", nombre, error)
                servidor.cerrar()
                continue
            self.servidores[nombre] = servidor
            for herramienta in servidor.herramientas:
                self._funciones[nombre_de_funcion(nombre, herramienta["name"])] = (servidor, herramienta)

    def cerrar(self) -> None:
        for servidor in self.servidores.values():
            servidor.cerrar()
        self.servidores.clear()
        self._funciones.clear()

    def nombres(self) -> set:
        return set(self._funciones)

    def definiciones(self) -> list:
        """Definiciones para el modelo: solo las herramientas permitidas, como mucho MAXIMO_HERRAMIENTAS."""
        lista = []
        for nombre, (servidor, herramienta) in self._funciones.items():
            if not servidor.permitida(herramienta) or not servidor.vivo:
                continue
            esquema = herramienta.get("inputSchema") or {"type": "object", "properties": {}}
            descripcion = f"[{servidor.nombre}] {herramienta.get('description') or herramienta['name']}"
            lista.append({"type": "function", "function": {
                "name": nombre, "description": descripcion[:MAXIMO_DESCRIPCION], "parameters": esquema}})
            if len(lista) >= MAXIMO_HERRAMIENTAS:
                break
        return lista

    def llamar(self, nombre: str, argumentos: dict) -> tuple:
        """(correcto, texto). Nunca lanza: los fallos vuelven como texto para el modelo."""
        if nombre not in self._funciones:
            return False, "Error: esa herramienta MCP no existe."
        servidor, herramienta = self._funciones[nombre]
        if not servidor.permitida(herramienta):
            return False, ("Rechazado: esta herramienta puede cambiar cosas y no está permitida. Se permite en "
                           "data/mcp.json («permitir_cambios» o «herramientas_permitidas»).")
        try:
            return servidor.llamar(herramienta["name"], argumentos if isinstance(argumentos, dict) else {})
        except ErrorMCP as error:
            return False, f"Error de la herramienta MCP: {error}"
        except Exception as error:
            registro.exception("Fallo en la herramienta MCP %s", nombre)
            return False, f"Error de la herramienta MCP: {type(error).__name__}"

    def estado(self) -> list:
        """Para la interfaz: cada servidor con sus herramientas y si están permitidas."""
        estado = [{"servidor": nombre, "activo": servidor.vivo,
                   "herramientas": [{"nombre": h["name"], "permitida": servidor.permitida(h)}
                                    for h in servidor.herramientas]}
                  for nombre, servidor in self.servidores.items()]
        estado += [{"servidor": nombre, "activo": False, "error": error, "herramientas": []}
                   for nombre, error in self.errores.items()]
        return estado
