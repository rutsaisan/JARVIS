"""
Bus de eventos de JARVIS
=========================
Desacopla el núcleo de la interfaz: el orquestador, la voz y las herramientas
publican lo que ocurre ("estado", "mensaje", "accion"...) y cualquier interfaz
(la ventana, la consola, las pruebas) se suscribe sin que el núcleo sepa quién
escucha.

Tipos de evento que se emiten:
    estado        valor = reposo | escuchando | procesando | hablando | error
    mensaje       un mensaje nuevo de la conversación (usuario o asistente)
    fragmento     trozo de respuesta mientras el modelo la va generando
    confirmacion  hay (o deja de haber) una acción delicada esperando permiso
    accion        se ha registrado una acción de herramienta
    sistema       cambia la salud de un componente (ia, voz, internet, memoria)
    nivel         volumen del micrófono (0..1) para animar la onda
    recuerdos     la memoria ha cambiado
    ajustes       se han modificado ajustes
    conversacion  cambia la conversación activa
    apagar        el asistente se cierra
"""
from __future__ import annotations

import queue
import threading
import time


class BusEventos:
    def __init__(self):
        self._colas = []
        self._funciones = []
        self._cerrojo = threading.Lock()

    # --- Suscripción por cola (la usa el servidor para enviar eventos a la ventana) ---
    def suscribir(self, capacidad: int = 400) -> queue.Queue:
        cola = queue.Queue(maxsize=capacidad)
        with self._cerrojo:
            self._colas.append(cola)
        return cola

    def desuscribir(self, cola: queue.Queue) -> None:
        with self._cerrojo:
            if cola in self._colas:
                self._colas.remove(cola)

    # --- Suscripción por función (consola, pruebas) ---
    def escuchar(self, funcion) -> None:
        with self._cerrojo:
            self._funciones.append(funcion)

    def dejar_de_escuchar(self, funcion) -> None:
        with self._cerrojo:
            if funcion in self._funciones:
                self._funciones.remove(funcion)

    # --- Publicación ---
    def emitir(self, tipo: str, /, **datos) -> None:
        # «tipo» es solo posicional: así los datos pueden llevar su propio campo "tipo".
        evento = {"tipo": tipo, "datos": datos, "t": time.time()}
        with self._cerrojo:
            colas = list(self._colas)
            funciones = list(self._funciones)
        for cola in colas:
            try:
                cola.put_nowait(evento)
            except queue.Full:
                # Un oyente lento no debe frenar al asistente: se descarta lo más antiguo.
                try:
                    cola.get_nowait()
                    cola.put_nowait(evento)
                except (queue.Empty, queue.Full):
                    pass
        for funcion in funciones:
            try:
                funcion(evento)
            except Exception:
                pass


bus = BusEventos()
