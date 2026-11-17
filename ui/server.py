"""
Servidor local de la interfaz
==============================
Puente entre la ventana (HTML/CSS/JS en ui/web) y el núcleo de JARVIS.
Expone una API en http://127.0.0.1:<puerto> y un canal de eventos (SSE) por
el que la interfaz recibe en directo lo que ocurre: estados, mensajes,
acciones, nivel del micrófono...

Seguridad (este servidor puede abrir programas y escribir en el equipo, así
que no debe poder usarlo nadie más que la ventana de JARVIS):
  - Solo escucha en 127.0.0.1: no es accesible desde la red.
  - Cada arranque genera un testigo aleatorio. La ventana entra con él y
    recibe una cookie de sesión; sin ella, la API responde 401.
  - Se comprueba la cabecera Host (protección frente a «DNS rebinding») y
    el origen de las peticiones que modifican algo (protección CSRF).
  - Las operaciones delicadas hechas desde la interfaz (borrar recuerdos o
    conversaciones, cambiar la seguridad) exigen la palabra de seguridad si
    está configurada, con bloqueo temporal tras varios fallos.

Acceso desde el móvil (opcional, desactivado por defecto; Ajustes → «Usar JARVIS desde el móvil»):
  - Una segunda entrada, en otro puerto, que escucha en la red de casa. Solo atiende a
    direcciones privadas (192.168.x.x, 10.x.x.x...): nunca a Internet.
  - Se entra con un PIN de 6 cifras que solo se ve en los Ajustes del ordenador; tras 5 fallos
    se bloquea unos minutos. La sesión del móvil dura mientras JARVIS esté abierto.
  - Desde el móvil no se puede cambiar la seguridad, las claves ni ver el PIN, y todo lo
    delicado sigue pidiendo confirmación (o la palabra de seguridad) como siempre.
"""
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import logging
import queue
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.serving import make_server

from core.config import guardar_secreto, leer_secreto
from tools import registry

registro = logging.getLogger("jarvis.servidor")

CARPETA_WEB = Path(__file__).resolve().parent / "web"
COOKIE = "jarvis_sesion"
COOKIE_MOVIL = "jarvis_movil"
MAXIMO_FALLOS_PIN = 5
BLOQUEO_PIN = 300                # segundos de bloqueo del acceso móvil tras 5 PIN incorrectos
# Lo que solo se puede hacer desde la propia ventana del ordenador, nunca desde el móvil.
SOLO_EN_EL_ORDENADOR = {"/api/movil", "/api/movil/pin", "/api/clave", "/api/seguridad", "/entrar"}


def ip_privada(direccion: str) -> bool:
    try:
        ip = ipaddress.ip_address((direccion or "").split("%")[0])
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def direcciones_de_la_red() -> list:
    """Direcciones IPv4 privadas de este ordenador (sin preguntar a nadie de fuera)."""
    import psutil
    import socket
    direcciones = []
    for interfaz, datos in psutil.net_if_addrs().items():
        estado = psutil.net_if_stats().get(interfaz)
        if estado is not None and not estado.isup:
            continue
        for dato in datos:
            if dato.family == socket.AF_INET and ip_privada(dato.address) and not dato.address.startswith(("127.", "169.254.")):
                direcciones.append(dato.address)
    return sorted(set(direcciones), key=lambda d: (not d.startswith("192.168."), d))


def nuevo_pin() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


_PAGINA_PIN = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>JARVIS</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05020b;color:#f1e6ff;
font:16px "Segoe UI",system-ui,sans-serif}form{width:min(320px,86vw);padding:28px;border:1px solid rgba(208,136,255,.78);
background:#0b0418;box-shadow:0 0 30px rgba(180,75,255,.35)}h1{margin:0 0 6px;font:600 15px Bahnschrift,sans-serif;
letter-spacing:.3em;color:#b44bff}p{margin:0 0 18px;color:#b9a3d9;font-size:14px}input{width:100%;box-sizing:border-box;
padding:12px;font-size:24px;letter-spacing:.4em;text-align:center;background:#030108;color:#fff;
border:1px solid rgba(190,100,255,.45)}button{margin-top:14px;width:100%;padding:12px;font-size:15px;color:#fff;
background:#8a2cff;border:0}.error{color:#ff4d8d;margin-top:12px;font-size:14px}</style></head><body>
<form method="post" action="/movil/entrar"><h1>J.A.R.V.I.S.</h1><p>Escribe el PIN que ves en el ordenador,
en Ajustes → Móvil.</p><input name="pin" inputmode="numeric" autocomplete="one-time-code" maxlength="6"
autofocus required>__ERROR__<button type="submit">Entrar</button></form></body></html>"""
MAXIMO_FALLOS = 5
BLOQUEO = 60                     # segundos de bloqueo tras demasiadas palabras incorrectas
CABECERAS_SEGURAS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
}


class Servidor:
    def __init__(self, asistente, puerto: int = 0, testigo: str = "", puerto_movil: int | None = None,
                 escuchar_movil: bool = True):
        self.asistente = asistente
        self.testigo = testigo or secrets.token_urlsafe(24)
        self.testigo_movil = secrets.token_urlsafe(24)       # la sesión del móvil (tras el PIN)
        self._fallos_palabra = 0
        self._bloqueado_hasta = 0.0
        self._fallos_pin = 0
        self._pin_bloqueado_hasta = 0.0
        self.app = self._crear_app()
        self._http = make_server("127.0.0.1", puerto, self.app, threaded=True)
        self.puerto = self._http.server_port
        self._hilo = None
        self._http_movil = None
        self.puerto_movil = None
        if asistente.ajustes.obtener("movil.activo", False):
            self._preparar_movil(asistente.ajustes.obtener("movil.puerto", 8770) if puerto_movil is None
                                 else puerto_movil, escuchar_movil)

    def _preparar_movil(self, puerto: int, escuchar: bool = True) -> None:
        """La entrada para el móvil: escucha en la red de casa (solo atiende a direcciones privadas)."""
        if not leer_secreto("movil_pin"):
            guardar_secreto("movil_pin", nuevo_pin())
        if not escuchar:                     # pruebas: las reglas de acceso sin abrir el puerto en la red
            self.puerto_movil = puerto
            return
        try:
            self._http_movil = make_server("0.0.0.0", puerto, self.app, threaded=True)
            self.puerto_movil = self._http_movil.server_port
        except OSError as error:
            registro.warning("No se pudo abrir la entrada para el móvil en el puerto %s: %s", puerto, error)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}"

    @property
    def url_de_entrada(self) -> str:
        return f"{self.url}/entrar?t={self.testigo}"

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._http.serve_forever, name="jarvis-servidor", daemon=True)
        self._hilo.start()
        registro.info("Interfaz disponible en %s", self.url)
        if self._http_movil is not None:
            threading.Thread(target=self._http_movil.serve_forever, name="jarvis-servidor-movil", daemon=True).start()
            registro.info("Acceso desde el móvil en el puerto %s", self.puerto_movil)

    def detener(self) -> None:
        for servidor in (self._http, self._http_movil):
            try:
                if servidor is not None:
                    servidor.shutdown()
            except Exception:
                pass

    def estado_movil(self) -> dict:
        return {"activo": self.puerto_movil is not None, "pin": leer_secreto("movil_pin") if self.puerto_movil else "",
                "direcciones": [f"http://{ip}:{self.puerto_movil}" for ip in direcciones_de_la_red()]
                if self.puerto_movil else []}

    # ------------------------------------------------------------------
    # AUTORIZACIÓN DE OPERACIONES DELICADAS HECHAS DESDE LA INTERFAZ
    # ------------------------------------------------------------------
    def _permiso(self, datos: dict):
        """None si la operación está autorizada; si no, la respuesta de error que hay que devolver."""
        seguridad = self.asistente.seguridad
        if seguridad.modo != "palabra":
            return None                          # en modo confirmación, el diálogo de la interfaz basta
        if time.time() < self._bloqueado_hasta:
            return jsonify(error="Demasiados intentos. Espera un minuto antes de volver a probar."), 429
        if seguridad.verificar_palabra(str(datos.get("palabra", ""))):
            self._fallos_palabra = 0
            return None
        self._fallos_palabra += 1
        if self._fallos_palabra >= MAXIMO_FALLOS:
            self._fallos_palabra = 0
            self._bloqueado_hasta = time.time() + BLOQUEO
        return jsonify(error="La palabra de seguridad no es correcta.", pide_palabra=True), 403

    # ------------------------------------------------------------------
    # APLICACIÓN
    # ------------------------------------------------------------------
    def _crear_app(self) -> Flask:
        app = Flask(__name__, static_folder=str(CARPETA_WEB), static_url_path="/web")
        app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        asistente = self.asistente

        def cuerpo() -> dict:
            datos = request.get_json(silent=True)
            return datos if isinstance(datos, dict) else {}

        def en_segundo_plano(funcion, *argumentos) -> None:
            threading.Thread(target=funcion, args=argumentos, name="jarvis-peticion", daemon=True).start()

        def recuerdo_publico(recuerdo: dict) -> dict:
            return {"id": recuerdo["id"], "texto": recuerdo["texto"], "categoria": recuerdo["categoria"],
                    "origen": recuerdo["origen"], "fecha": recuerdo["fecha_creacion"],
                    "actualizado": recuerdo["fecha_actualizacion"]}

        def activacion_disponible() -> bool:
            from voice.activacion import disponible
            return disponible()

        def ajustes_publicos() -> dict:
            from voice.speaker import VOCES_EDGE, motores_disponibles
            return {"valores": asistente.ajustes.todo(), "voces_edge": VOCES_EDGE,
                    "motores_habla": motores_disponibles(),
                    "clave_elevenlabs": bool(leer_secreto("elevenlabs_api_key")),
                    "modelos": asistente.cerebro.modelos(),
                    "activacion_disponible": activacion_disponible(),
                    "whisper_descargado": bool(asistente.escucha and
                                               asistente.escucha.transcriptor.modelo_descargado())}

        # ---------------- Control de acceso ----------------
        def desde_el_movil() -> bool:
            return (self.puerto_movil is not None and request.host.rsplit(":", 1)[-1] == str(self.puerto_movil)
                    and ip_privada(request.remote_addr))

        @app.before_request
        def comprobar_acceso():
            if desde_el_movil():
                return comprobar_movil()
            if request.host not in (f"127.0.0.1:{self.puerto}", f"localhost:{self.puerto}"):
                return "Acceso no permitido.", 403
            if request.path == "/entrar":
                return None
            if not hmac.compare_digest(request.cookies.get(COOKIE, ""), self.testigo):
                if request.path.startswith("/api/"):
                    return jsonify(error="Sesión no válida."), 401
                return "Abre JARVIS desde su acceso directo (python jarvis.py).", 401
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                origen = request.headers.get("Origin")
                if origen and origen not in (f"http://127.0.0.1:{self.puerto}", f"http://localhost:{self.puerto}"):
                    return jsonify(error="Origen no permitido."), 403
            return None

        def comprobar_movil():
            if request.path in ("/movil", "/movil/entrar"):
                return None
            if request.path in SOLO_EN_EL_ORDENADOR:
                return jsonify(error="Esto solo se puede hacer desde el ordenador."), 403
            if not hmac.compare_digest(request.cookies.get(COOKIE_MOVIL, ""), self.testigo_movil):
                if request.path.startswith("/api/"):
                    return jsonify(error="Sesión no válida."), 401
                return redirect("/movil")
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                origen = request.headers.get("Origin")
                if origen and origen != f"http://{request.host}":
                    return jsonify(error="Origen no permitido."), 403
            return None

        @app.get("/movil")
        def pagina_movil():
            if not desde_el_movil():
                return "Acceso no permitido.", 403
            return Response(_PAGINA_PIN.replace("__ERROR__", ""), mimetype="text/html")

        @app.post("/movil/entrar")
        def entrar_movil():
            if not desde_el_movil():
                return "Acceso no permitido.", 403
            origen = request.headers.get("Origin")
            if origen and origen != f"http://{request.host}":
                return "Origen no permitido.", 403

            def error(mensaje: str, codigo: int):
                pagina = _PAGINA_PIN.replace("__ERROR__", f'<div class="error">{html.escape(mensaje)}</div>')
                return Response(pagina, status=codigo, mimetype="text/html")

            if time.time() < self._pin_bloqueado_hasta:
                return error("Demasiados intentos. Espera unos minutos.", 429)
            pin = leer_secreto("movil_pin")
            if not pin or not hmac.compare_digest(str(request.form.get("pin", "")).strip(), pin):
                self._fallos_pin += 1
                if self._fallos_pin >= MAXIMO_FALLOS_PIN:
                    self._fallos_pin = 0
                    self._pin_bloqueado_hasta = time.time() + BLOQUEO_PIN
                    registro.warning("Acceso móvil bloqueado: demasiados PIN incorrectos.")
                return error("PIN incorrecto.", 403)
            self._fallos_pin = 0
            registro.info("Nueva sesión desde el móvil (%s).", request.remote_addr)
            respuesta = redirect("/")
            respuesta.set_cookie(COOKIE_MOVIL, self.testigo_movil, httponly=True, samesite="Strict")
            return respuesta

        @app.after_request
        def cabeceras(respuesta):
            for nombre, valor in CABECERAS_SEGURAS.items():
                respuesta.headers.setdefault(nombre, valor)
            if not request.path.startswith("/api/eventos"):
                respuesta.headers["Cache-Control"] = "no-store"
            return respuesta

        @app.errorhandler(404)
        def no_encontrado(_error):
            return jsonify(error="No encontrado."), 404

        @app.errorhandler(Exception)
        def error_interno(error):
            from werkzeug.exceptions import HTTPException
            if isinstance(error, HTTPException):
                return jsonify(error=error.description), error.code
            registro.exception("Error al atender %s %s", request.method, request.path)
            return jsonify(error="Error interno de JARVIS."), 500

        # ---------------- Páginas ----------------
        @app.get("/entrar")
        def entrar():
            if not hmac.compare_digest(request.args.get("t", ""), self.testigo):
                return "Enlace de acceso no válido.", 403
            respuesta = redirect("/")
            respuesta.set_cookie(COOKIE, self.testigo, httponly=True, samesite="Strict")
            return respuesta

        @app.get("/")
        def inicio():
            return send_from_directory(CARPETA_WEB, "index.html")

        # ---------------- Estado y eventos ----------------
        @app.get("/api/estado")
        def estado():
            datos = asistente.estado()
            datos.update(
                mensajes=asistente.orquestador.sesion[-200:],
                acciones=asistente.seguridad.ultimas_acciones(8),
                herramientas=registry.catalogo(asistente.ajustes.obtener("herramientas")),
                ajustes=ajustes_publicos(),
            )
            return jsonify(datos)

        @app.get("/api/eventos")
        def eventos():
            cola = asistente.bus.suscribir()

            def flujo():
                try:
                    yield "retry: 1500\n\n"
                    while True:
                        try:
                            evento = cola.get(timeout=15)
                            yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
                        except queue.Empty:
                            yield ": sigo aquí\n\n"
                finally:
                    asistente.bus.desuscribir(cola)

            return Response(flujo(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # ---------------- Conversar ----------------
        @app.post("/api/mensaje")
        def mensaje():
            return jsonify(ok=asistente.enviar(str(cuerpo().get("texto", "")), "texto"))

        @app.post("/api/detener")
        def detener():
            asistente.detener()
            return jsonify(ok=True)

        @app.post("/api/microfono")
        def microfono():
            return jsonify(ok=asistente.pulsar_microfono())

        @app.post("/api/confirmar")
        def confirmar():
            datos = cuerpo()
            en_segundo_plano(asistente.orquestador.resolver_confirmacion, bool(datos.get("aceptar")),
                             str(datos.get("palabra", "")))
            return jsonify(ok=True)

        @app.post("/api/sugerencia")
        def sugerencia():
            en_segundo_plano(asistente.orquestador.resolver_sugerencia, bool(cuerpo().get("aceptar")))
            return jsonify(ok=True)

        @app.post("/api/probar_voz")
        def probar_voz():
            if not asistente.habla:
                return jsonify(error="La voz no está disponible en este equipo."), 409
            asistente.habla.detener()
            nombre = asistente.ajustes.obtener("usuario.nombre") or ""
            asistente.habla.decir(f"Hola, {nombre}. Soy Jarvis, y así es como sueno.")
            return jsonify(ok=True)

        # ---------------- Ajustes ----------------
        @app.get("/api/ajustes")
        def leer_ajustes():
            return jsonify(ajustes_publicos())

        @app.post("/api/ajustes")
        def cambiar_ajustes():
            aplicadas, rechazadas = asistente.ajustes.actualizar(cuerpo())
            return jsonify(aplicadas=aplicadas, rechazadas=rechazadas, ajustes=ajustes_publicos())

        @app.post("/api/clave")
        def clave():
            datos = cuerpo()
            if datos.get("servicio") != "elevenlabs":
                return jsonify(error="Servicio desconocido."), 400
            guardado = guardar_secreto("elevenlabs_api_key", str(datos.get("clave", ""))[:200])
            return jsonify(ok=guardado, configurada=bool(leer_secreto("elevenlabs_api_key")))

        @app.post("/api/seguridad")
        def seguridad():
            datos = cuerpo()
            # Si ya hay palabra de seguridad, cambiarla o quitarla exige la actual.
            denegado = self._permiso({"palabra": datos.get("palabra_actual", "")})
            if denegado:
                return denegado
            if datos.get("modo") == "palabra":
                respuesta = asistente.seguridad.configurar_palabra(str(datos.get("palabra", "")))
                correcto = asistente.seguridad.modo == "palabra" and "configurada" in respuesta
            else:
                respuesta = asistente.seguridad.configurar_modo_confirmacion()
                correcto = True
            if correcto:
                asistente.seguridad.registrar_accion("security_setup", "interfaz", "", confirmada=True,
                                                     ejecutada=True, descripcion="Cambiaste la configuración de seguridad")
                asistente.bus.emitir("ajustes", seguridad=asistente.seguridad.estado())
            return jsonify(ok=correcto, mensaje=respuesta, seguridad=asistente.seguridad.estado()), (200 if correcto else 400)

        # ---------------- Memoria ----------------
        @app.get("/api/recuerdos")
        def recuerdos():
            lista = asistente.memoria.listar_recuerdos(request.args.get("categoria") or None,
                                                       request.args.get("q") or None)
            return jsonify(recuerdos=[recuerdo_publico(r) for r in lista],
                           conteo=asistente.memoria.conteo_por_categoria(), total=asistente.memoria.contar())

        @app.post("/api/recuerdos")
        def crear_recuerdo():
            datos = cuerpo()
            resultado = asistente.memoria.guardar(str(datos.get("texto", ""))[:500], datos.get("categoria"))
            if resultado["ok"]:
                asistente.seguridad.registrar_accion("memory_save", "interfaz", "", confirmada=True, ejecutada=True,
                                                     descripcion="Guardaste un recuerdo", herramienta="memoria")
                asistente.bus.emitir("recuerdos")
            return jsonify(ok=resultado["ok"], mensaje=resultado["mensaje"]), (200 if resultado["ok"] else 400)

        @app.put("/api/recuerdos/<int:recuerdo_id>")
        def editar_recuerdo(recuerdo_id: int):
            datos = cuerpo()
            texto = datos.get("texto")
            correcto = asistente.memoria.actualizar(recuerdo_id, str(texto)[:500] if texto is not None else None,
                                                    datos.get("categoria"))
            if not correcto:
                return jsonify(error="No se ha podido guardar. Recuerda que no almaceno contraseñas ni claves."), 400
            asistente.bus.emitir("recuerdos")
            return jsonify(ok=True)

        @app.delete("/api/recuerdos/<int:recuerdo_id>")
        def borrar_recuerdo(recuerdo_id: int):
            denegado = self._permiso(cuerpo())
            if denegado:
                return denegado
            borrado = asistente.memoria.borrar_por_id(recuerdo_id)
            if borrado:
                asistente.seguridad.registrar_accion("memory_delete", "interfaz", "", confirmada=True, ejecutada=True,
                                                     descripcion="Borraste un recuerdo", herramienta="memoria")
                asistente.bus.emitir("recuerdos")
            return jsonify(ok=borrado)

        @app.delete("/api/recuerdos")
        def borrar_recuerdos():
            denegado = self._permiso(cuerpo())
            if denegado:
                return denegado
            cantidad = asistente.memoria.borrar_todo()
            asistente.seguridad.registrar_accion("memory_delete", "interfaz_todo", "", confirmada=True, ejecutada=True,
                                                 descripcion="Borraste todos los recuerdos", herramienta="memoria")
            asistente.bus.emitir("recuerdos")
            return jsonify(ok=True, borrados=cantidad)

        # ---------------- Conversaciones ----------------
        @app.get("/api/conversaciones")
        def conversaciones():
            return jsonify(conversaciones=asistente.historial.listar(request.args.get("q") or None),
                           activa=asistente.orquestador.conversacion_id)

        @app.post("/api/conversaciones")
        def nueva_conversacion():
            asistente.orquestador.nueva_conversacion()
            return jsonify(ok=True)

        @app.post("/api/conversaciones/<int:conversacion_id>/abrir")
        def abrir_conversacion(conversacion_id: int):
            if not asistente.orquestador.abrir_conversacion(conversacion_id):
                return jsonify(error="Esa conversación ya no existe."), 404
            return jsonify(ok=True, mensajes=asistente.orquestador.sesion)

        @app.delete("/api/conversaciones/<int:conversacion_id>")
        def borrar_conversacion(conversacion_id: int):
            denegado = self._permiso(cuerpo())
            if denegado:
                return denegado
            borrada = asistente.historial.borrar(conversacion_id)
            if conversacion_id == asistente.orquestador.conversacion_id:
                asistente.orquestador.nueva_conversacion(resumir=False)
            asistente.bus.emitir("historial")
            return jsonify(ok=borrada)

        @app.delete("/api/conversaciones")
        def borrar_conversaciones():
            denegado = self._permiso(cuerpo())
            if denegado:
                return denegado
            cantidad = asistente.historial.borrar_todo()
            asistente.orquestador.nueva_conversacion(resumir=False)
            asistente.seguridad.registrar_accion("history", "interfaz_todo", "", confirmada=True, ejecutada=True,
                                                 descripcion="Borraste el historial")
            asistente.bus.emitir("historial")
            return jsonify(ok=True, borradas=cantidad)

        # ---------------- Herramientas ----------------
        @app.get("/api/acciones")
        def acciones():
            cantidad = min(max(request.args.get("n", 20, type=int), 1), 100)
            return jsonify(acciones=asistente.seguridad.ultimas_acciones(cantidad))

        @app.get("/api/aplicaciones")
        def aplicaciones():
            from tools.computer import listar_aplicaciones
            return jsonify(aplicaciones=listar_aplicaciones())

        @app.post("/api/buscar")
        def buscar():
            from tools.browser import ErrorBusqueda, buscar_resultados
            datos = cuerpo()
            consulta = str(datos.get("consulta", "")).strip()[:200]
            if not consulta:
                return jsonify(resultados=[])
            if not asistente.ajustes.obtener("herramientas.busqueda", True):
                return jsonify(error="La búsqueda en Internet está desactivada en Herramientas."), 409
            try:
                resultados = buscar_resultados(consulta, int(asistente.ajustes.obtener("internet.max_resultados", 5)),
                                               "noticias" if datos.get("tipo") == "noticias" else "web")
            except ErrorBusqueda:
                return jsonify(error="No he podido conectar con el buscador. Comprueba la conexión."), 502
            entrada = asistente.seguridad.registrar_accion(
                "search_web", "interfaz", consulta, confirmada=False, ejecutada=True,
                descripcion=f"Buscaste: {consulta}", herramienta="busqueda")
            asistente.bus.emitir("accion", **entrada)
            return jsonify(resultados=resultados)

        @app.get("/api/movil")
        def movil():
            return jsonify(self.estado_movil())

        @app.post("/api/movil/pin")
        def cambiar_pin():
            guardar_secreto("movil_pin", nuevo_pin())
            self.testigo_movil = secrets.token_urlsafe(24)      # las sesiones abiertas en el móvil se cierran
            return jsonify(self.estado_movil())

        @app.get("/api/mcp")
        def mcp():
            gestor = asistente.orquestador.mcp
            return jsonify(servidores=gestor.estado() if gestor else [])

        @app.post("/api/abrir")
        def abrir():
            from tools.browser import abrir_url
            return jsonify(ok=abrir_url(str(cuerpo().get("url", ""))))

        return app
