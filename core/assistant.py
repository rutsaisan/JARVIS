"""
Asistente: el montaje de todas las piezas
==========================================
Crea y conecta los módulos de JARVIS (ajustes, memoria, historial, seguridad,
cerebro, voz y orquestador) y ofrece a cualquier interfaz —la ventana, la
consola— una única puerta de entrada:

    asistente.enviar("abre Spotify")     una frase escrita
    asistente.pulsar_microfono()         hablar sin decir «Jarvis»
    asistente.detener()                  parada de emergencia
    asistente.estado()                   foto del estado para pintar la interfaz

También decide qué hacer con lo que oye el micrófono (política de escucha):

  - En reposo solo atiende si se dice la palabra de activación.
    «Jarvis, abre Spotify» se ejecuta directamente; «Jarvis» a secas abre una
    ventana de escucha.
  - Tras cada respuesta queda unos segundos atento (ventana de fluidez) para
    seguir la conversación sin repetir «Jarvis».
  - Si hay una confirmación pendiente, cualquier frase se toma como respuesta.
  - Mientras habla, solo reacciona a su nombre: se calla y escucha.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from core.brain import Cerebro
from core.config import Ajustes, dir_datos
from core.database import BaseDatos
from core.embeddings import Embeddings
from core.events import bus as bus_global
from core.history import Historial
from core.orchestrator import Orquestador
from core.personality import frase
from tools.intent_engine import interpretar
from tools.memory import MemoriaLargoPlazo
from tools.security import GestorSeguridad

registro = logging.getLogger("jarvis.asistente")

VERSION = "1.0"
LONGITUD_MAXIMA_FRASE = 2000
PERIODO_SALUD = 30               # segundos entre comprobaciones de Internet y de Ollama


class Asistente:
    def __init__(self, bus=None, con_voz: bool = True):
        self.bus = bus or bus_global
        self.ajustes = Ajustes()
        base = BaseDatos(dir_datos() / "jarvis.db")
        self.embeddings = Embeddings(self.ajustes.obtener("memoria.modelo_embeddings"),
                                     self.ajustes.obtener("ia.host"))
        self.memoria = MemoriaLargoPlazo(base_datos=base, embeddings=self.embeddings, indexar_en_segundo_plano=True)
        self.historial = Historial(base, self.embeddings)
        self.seguridad = GestorSeguridad()
        self.cerebro = Cerebro(self.ajustes)

        self.habla = None
        self.escucha = None
        if con_voz:
            try:
                from voice.listener import Escucha
                from voice.speaker import Habla
                self.habla = Habla(self.ajustes, self.bus)
                self.habla.al_empezar = self._al_empezar_a_hablar
                self.habla.al_terminar = self._al_terminar_de_hablar
                self.escucha = Escucha(self.ajustes, self._al_oir, self._al_evento_de_voz, self.bus)
                self.escucha.atento = self._atento
                threading.Thread(target=self._configurar_activacion, name="jarvis-activacion", daemon=True).start()
            except Exception as error:
                registro.warning("La voz no está disponible: %s", error)

        self.orquestador = Orquestador(self.ajustes, self.memoria, self.historial, self.seguridad,
                                       self.cerebro, self.habla, bus=self.bus)
        self.orquestador.al_apagar = self._al_pedir_apagado
        self.al_cerrar = None            # la interfaz pone aquí cómo cerrar la aplicación

        self.salud = {"ia": "iniciando", "voz": "apagada", "internet": "comprobando", "memoria": "disponible"}
        self.estado_actual = "reposo"
        self._trabajos = queue.Queue()
        self._cerrando = threading.Event()
        self._ventana_hasta = 0.0
        self._solo_acuse = False
        self._iniciado = False

        self.bus.escuchar(self._al_evento_del_bus)
        self.ajustes.al_cambiar(self._al_cambiar_ajustes)

    # ------------------------------------------------------------------
    # CICLO DE VIDA
    # ------------------------------------------------------------------
    def iniciar(self) -> None:
        if self._iniciado:
            return
        self._iniciado = True
        threading.Thread(target=self._trabajar, name="jarvis-orquestador", daemon=True).start()
        threading.Thread(target=self._arranque, name="jarvis-arranque", daemon=True).start()
        threading.Thread(target=self._vigilar_salud, name="jarvis-salud", daemon=True).start()
        from tools.recordatorios import Avisador
        self.avisador = Avisador(self.orquestador.recordatorios, self.orquestador.avisar)
        self.avisador.start()
        threading.Thread(target=self._vigilar_calendario, name="jarvis-calendario", daemon=True).start()
        threading.Thread(target=self._arrancar_mcp, name="jarvis-mcp", daemon=True).start()
        self._aplicar_modo()

    def cerrar(self, rapido: bool = False) -> None:
        """Detiene la voz y el Ollama arrancado por JARVIS.

        Con `rapido=True` no se cierran las conexiones abiertas (pueden tardar si hay
        una petición en curso): se usa cuando el proceso va a terminar acto seguido.
        """
        self._cerrando.set()
        if self.escucha:
            self.escucha.detener()
        if self.habla:
            self.habla.detener()
        if self.orquestador.mcp is not None:
            self.orquestador.mcp.cerrar()
        self.cerebro.cerrar(cerrar_conexiones=not rapido)
        if not rapido:
            self.embeddings.cerrar()

    def _al_pedir_apagado(self) -> None:
        if self.al_cerrar:
            self.al_cerrar()

    # ------------------------------------------------------------------
    # PUERTA DE ENTRADA
    # ------------------------------------------------------------------
    @property
    def modo(self) -> str:
        return self.ajustes.obtener("modo", "texto")

    def enviar(self, texto: str, origen: str = "texto") -> bool:
        """Encola una frase para que el orquestador la atienda. No bloquea."""
        texto = (texto or "").strip()[:LONGITUD_MAXIMA_FRASE]
        if not texto:
            return False
        if self._es_parada(texto):
            self.orquestador.detener()           # la parada no espera su turno en la cola
        self._trabajos.put((texto, origen))
        return True

    def detener(self) -> None:
        """Parada de emergencia: calla, deja de generar y cancela lo pendiente."""
        while True:
            try:
                self._trabajos.get_nowait()
            except queue.Empty:
                break
        self.orquestador.detener()

    def pulsar_microfono(self) -> bool:
        """Escucha una frase sin exigir la palabra de activación."""
        if not self.escucha:
            return False
        if self.habla and self.habla.ocupada:
            self.habla.detener()
        self.escucha.escuchar_una_vez()
        self.bus.emitir("estado", valor="escuchando")
        return True

    def estado(self) -> dict:
        return {
            "version": VERSION,
            "usuario": self.ajustes.obtener("usuario.nombre"),
            "modo": self.modo,
            "estado": self.estado_actual,
            "salud": dict(self.salud),
            "seguridad": self.seguridad.estado(),
            "primera_vez": not self.seguridad.modo_configurado,
            "pendiente": self.orquestador.estado_pendiente(),
            "conversacion": self.orquestador.conversacion_id,
            "recuerdos": self.memoria.contar(),
            "voz_disponible": self.escucha is not None,
        }

    @staticmethod
    def _es_parada(texto: str) -> bool:
        intencion = interpretar(texto)
        return intencion["intent"] == "system" and intencion["action"] == "stop"

    # ------------------------------------------------------------------
    # HILOS DE FONDO
    # ------------------------------------------------------------------
    def _trabajar(self) -> None:
        while not self._cerrando.is_set():
            try:
                texto, origen = self._trabajos.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.orquestador.procesar(texto, origen)
            except Exception:
                registro.exception("Error al atender una frase")
            if self.modo == "voz" and not (self.habla and self.habla.ocupada):
                self._abrir_ventana()

    def _arranque(self) -> None:
        self._comprobar_internet()
        self._preparar_ia()
        try:
            self.memoria.reindexar()
        except Exception:
            registro.exception("No se pudo reindexar la memoria")
        try:
            from tools import computer
            computer.indexar_aplicaciones()
            computer.iniciar_seguimiento_foco()
        except Exception:
            registro.exception("No se pudo preparar el control del ordenador")
        self._resumir_pendientes()
        if self.ajustes.obtener("resumen.automatico", False) and time.localtime().tm_hour < 12:
            try:
                self.orquestador.resumen_automatico()
            except Exception:
                registro.exception("No se pudo dar el resumen de buenos días")

    def _preparar_ia(self) -> None:
        self._fijar_salud("ia", "cargando")
        if not self.cerebro.asegurar_servidor():
            self._fijar_salud("ia", "sin_conexion")
            return
        instalados = self.cerebro.modelos()
        elegido = self.cerebro.modelo
        if instalados and not any(m == elegido or m.split(":")[0] == elegido for m in instalados):
            self._fijar_salud("ia", "sin_modelo")
            return
        self._fijar_salud("ia", "activa" if self.cerebro.precargar() else "error")
        self.embeddings.calentar()               # deja cargado también el modelo de memoria

    def _resumir_pendientes(self) -> None:
        """Resume conversaciones antiguas que quedaron sin resumen (para poder recordarlas)."""
        if self.salud["ia"] != "activa" or not self.ajustes.obtener("memoria.guardar_historial"):
            return
        try:
            for conversacion_id in self.historial.sin_resumen()[:3]:
                if self._cerrando.is_set() or conversacion_id == self.orquestador.conversacion_id:
                    continue
                self.orquestador.resumir_conversacion(conversacion_id)
        except Exception:
            registro.exception("No se pudieron resumir las conversaciones pendientes")

    def _configurar_activacion(self) -> None:
        """Detector dedicado de «Hey Jarvis» si está elegido e instalado (voice/activacion.py)."""
        if self.escucha is None:
            return
        from voice.activacion import crear
        self.escucha.detector = crear(self.ajustes)

    def _arrancar_mcp(self) -> None:
        """Arranca los servidores MCP de data/mcp.json (si existe). Sin fichero no se arranca nada."""
        from core.mcp import GestorMCP
        gestor = GestorMCP(dir_datos() / "mcp.json")
        try:
            gestor.arrancar()
        except Exception:
            registro.exception("No se pudieron arrancar las herramientas MCP")
        if self._cerrando.is_set():
            gestor.cerrar()
            return
        self.orquestador.mcp = gestor

    def sincronizar_calendario(self) -> int:
        """Lee el calendario iCal de Ajustes (si hay) y lo pasa a la agenda. Devuelve los eventos nuevos."""
        direccion = (self.ajustes.obtener("agenda.calendario") or "").strip()
        if not direccion:
            return 0
        from tools.recordatorios import descargar_ics, leer_ics
        try:
            eventos = leer_ics(descargar_ics(direccion))
        except Exception as error:
            registro.warning("No se pudo leer el calendario: %s", type(error).__name__)
            return 0
        nuevos = self.orquestador.recordatorios.sincronizar_calendario(
            eventos, int(self.ajustes.obtener("agenda.aviso_minutos", 15)))
        registro.info("Calendario: %d eventos, %d nuevos.", len(eventos), nuevos)
        return nuevos

    def _vigilar_calendario(self) -> None:
        while not self._cerrando.is_set():
            self.sincronizar_calendario()
            self._cerrando.wait(30 * 60)

    def _comprobar_internet(self) -> None:
        try:
            from tools.browser import hay_internet
            self._fijar_salud("internet", "conectado" if hay_internet() else "sin_conexion")
        except Exception:
            self._fijar_salud("internet", "sin_conexion")

    def _vigilar_salud(self) -> None:
        while not self._cerrando.wait(PERIODO_SALUD):
            self._comprobar_internet()
            if self.salud["ia"] in ("activa", "sin_conexion", "error"):
                disponible = self.cerebro.disponible()
                if disponible and self.salud["ia"] != "activa":
                    self._preparar_ia()
                elif not disponible:
                    self._fijar_salud("ia", "sin_conexion")
            try:
                self.memoria.reindexar()         # vectores de recuerdos guardados mientras Ollama no respondía
            except Exception:
                registro.exception("No se pudo reindexar la memoria")

    def _fijar_salud(self, componente: str, estado: str) -> None:
        if self.salud.get(componente) != estado:
            self.bus.emitir("sistema", componente=componente, estado=estado)

    # ------------------------------------------------------------------
    # REACCIÓN A EVENTOS Y AJUSTES
    # ------------------------------------------------------------------
    def _al_evento_del_bus(self, evento: dict) -> None:
        if evento["tipo"] == "sistema":
            self.salud[evento["datos"].get("componente", "")] = evento["datos"].get("estado", "")
        elif evento["tipo"] == "estado":
            self.estado_actual = evento["datos"].get("valor", "reposo")

    def _al_cambiar_ajustes(self, claves: list) -> None:
        if "modo" in claves:
            self._aplicar_modo()
        if self.modo == "voz" and self.escucha and {"voz.motor_escucha", "voz.modelo_whisper"} & set(claves):
            threading.Thread(target=self._preparar_transcriptor, name="jarvis-whisper", daemon=True).start()
        if "voz.motor_activacion" in claves:
            threading.Thread(target=self._configurar_activacion, name="jarvis-activacion", daemon=True).start()
        if {"agenda.calendario", "agenda.aviso_minutos"} & set(claves):
            threading.Thread(target=self.sincronizar_calendario, name="jarvis-calendario-ya", daemon=True).start()
        if {"ia.modelo", "ia.host"} & set(claves):
            threading.Thread(target=self._preparar_ia, name="jarvis-modelo", daemon=True).start()
        for clave in ("memoria.aprender_automaticamente", "memoria.sugerir_recuerdos", "memoria.guardar_historial"):
            if clave in claves:
                # Queda constancia de cuándo se concede o se retira cada permiso.
                self.historial.registrar_consentimiento(clave.split(".")[1], bool(self.ajustes.obtener(clave)))
        self.bus.emitir("ajustes", claves=claves)

    def _aplicar_modo(self) -> None:
        if not self.escucha:
            return
        if self.modo == "voz":
            self.escucha.iniciar()
            threading.Thread(target=self._preparar_transcriptor, name="jarvis-whisper", daemon=True).start()
        else:
            self.escucha.detener()
            self._ventana_hasta = 0.0

    def _preparar_transcriptor(self) -> None:
        self.escucha.transcriptor.preparar()
        if self.escucha.activa:
            self._fijar_salud("voz", "escuchando")

    # ------------------------------------------------------------------
    # POLÍTICA DE ESCUCHA
    # ------------------------------------------------------------------
    def _abrir_ventana(self) -> None:
        self._ventana_hasta = time.time() + float(self.ajustes.obtener("voz.ventana_fluidez", 8))

    def _atento(self) -> bool:
        return self.orquestador.pendiente is not None or time.time() < self._ventana_hasta

    def _al_empezar_a_hablar(self) -> None:
        if self.escucha:
            self.escucha.eco(True)

    def _al_terminar_de_hablar(self, habia_sonado: bool) -> None:
        self._solo_acuse = False
        if self.escucha:
            self.escucha.eco(False)
            if self.escucha.esperando_pulsacion:
                # Se pulsó el micrófono mientras hablaba: al callar, sigue a la escucha.
                self.bus.emitir("estado", valor="escuchando")
        if self.modo == "voz":
            self._abrir_ventana()

    def _al_evento_de_voz(self, nombre: str) -> None:
        """Refleja en el estado lo que hace el micrófono, solo cuando JARVIS está atento."""
        if self.habla and self.habla.ocupada:
            return
        pulsado = bool(self.escucha and self.escucha.esperando_pulsacion)
        if nombre == "inicio_voz" and (pulsado or self._atento()):
            self.bus.emitir("estado", valor="escuchando")
        elif nombre == "fin_voz" and self.estado_actual == "escuchando":
            self.bus.emitir("estado", valor="procesando")
        elif nombre == "activacion":
            self.bus.emitir("estado", valor="escuchando")
        elif nombre in ("sin_texto", "sin_voz") and self.estado_actual in ("escuchando", "procesando"):
            self.bus.emitir("estado", valor="reposo")

    def _al_oir(self, texto: str, forzada: bool = False, durante_habla: bool = False) -> None:
        """Decide qué hacer con una frase transcrita (se llama desde el hilo de escucha)."""
        from voice.listener import separar_activacion
        dicha, resto = separar_activacion(texto, self.ajustes.obtener("voz.palabra_activacion", "jarvis"))
        # El texto no se registra: puede contener la palabra de seguridad.
        registro.debug("Frase oída (activación=%s, pulsación=%s, durante_habla=%s)", dicha, forzada, durante_habla)

        if durante_habla:
            # Lo oído lleva mezclada la voz de JARVIS: solo sirve para interrumpirle.
            if not dicha:
                return
            registro.info("Interrupción por voz.")
            if resto and self._es_parada(resto):
                self.detener()
            elif self.habla:
                self.habla.detener()
            self._abrir_ventana()
            return

        if dicha and not resto:
            # Solo ha dicho «Jarvis» (o «Hey Jarvis» y una pausa): acuse breve y ventana de escucha
            # abierta, también si había pulsado el micrófono o ya estaba atento.
            self._abrir_ventana()
            self._solo_acuse = True
            if self.habla:
                self.habla.decir(frase("activacion", int(self.ajustes.obtener("personalidad.sarcasmo", 2)),
                                       nombre=self.ajustes.obtener("usuario.nombre") or ""))
            else:
                self.bus.emitir("estado", valor="escuchando")
        elif forzada or self._atento():
            self.enviar(resto if dicha else texto, "voz")
        elif dicha:
            self.enviar(resto, "voz")
        elif self.estado_actual in ("escuchando", "procesando"):
            self.bus.emitir("estado", valor="reposo")         # no iba dirigido a JARVIS
