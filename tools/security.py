"""
Capa de seguridad de JARVIS
============================
Cubre la sección 9 del plan técnico («Seguridad y privacidad»):
  - Catálogo cerrado de acciones delicadas.
  - Confirmación / palabra de seguridad antes de ejecutarlas.
  - Separación entre lo que se interpreta y lo que se ejecuta
    (este módulo NUNCA ejecuta nada; solo decide SI algo puede ejecutarse).
  - Registro de acciones para poder auditarlas.

La palabra de seguridad se guarda SIEMPRE como hash con sal (PBKDF2-SHA256),
nunca en texto plano, en data/seguridad.json.

Modos de funcionamiento, elegidos por la usuaria:
  "confirmar" -> basta con decir "sí", "confirmo", "adelante"...
  "palabra"   -> hay que decir (o escribir) la palabra de seguridad elegida

Si todavía no se ha configurado nada, JARVIS usa "confirmar" por defecto
(a prueba de fallos: nunca ejecuta una acción delicada sin que alguien responda).
Una vez elegida una palabra, cambiarla o quitarla también exige decirla.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from pathlib import Path

from core.config import dir_datos
from core.text import normalizar

registro = logging.getLogger("jarvis.seguridad")

ITERACIONES = 200_000
LONGITUD_MINIMA = 4

# ==========================================
# CATÁLOGO DE ACCIONES DELICADAS
# ==========================================
# (intent, action) que SIEMPRE requieren confirmación.
ACCIONES_SENSIBLES = {
    ("pc", "close_app"),
    ("pc", "type_text"),
    ("message", "send"),                 # mandar un mensaje a alguien (Discord...)
    ("memory_delete", "delete"),
    ("memory_delete", "delete_all"),
    ("history", "delete_all"),
    ("history", "delete_current"),
}

# Comandos de sistema que requieren confirmación (el resto, como bloquear o
# hacer una captura, no pueden causar pérdida de datos).
COMANDOS_SISTEMA_SENSIBLES = {"apagar", "reiniciar", "suspender", "cerrar_sesion", "vaciar_papelera"}

# Combinaciones de teclas que cierran o borran cosas.
TECLAS_SENSIBLES = {"alt+f4", "ctrl+w", "ctrl+q", "delete", "shift+delete", "ctrl+shift+delete", "ctrl+alt+delete"}

# Aplicaciones que, aunque abrir no sea delicado por defecto, SÍ deben pedir
# confirmación: herramientas de Windows capaces de hacer cambios profundos.
APLICACIONES_SENSIBLES = [
    "cmd", "powershell", "simbolo del sistema", "terminal", "regedit", "editor del registro",
    "panel de control", "administrador de tareas", "taskmgr", "diskpart", "administracion de discos",
    "configuracion del sistema", "msconfig", "administrador de dispositivos",
]
_PATRON_APP_SENSIBLE = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in APLICACIONES_SENSIBLES) + r")\b")

FRASES_CONFIRMACION = [
    "si", "confirmo", "confirmado", "confirmar", "adelante", "afirmativo", "hazlo", "correcto", "vale",
    "de acuerdo", "por supuesto", "claro", "ok", "okay", "dale", "procede", "si confirmo", "si hazlo",
]
FRASES_CANCELACION = [
    "cancela", "cancelar", "cancelalo", "no", "negativo", "detente", "para", "olvidalo", "dejalo",
    "mejor no", "aborta", "abortar", "ni se te ocurra",
]
MAXIMO_PALABRAS_RESPUESTA = 5

DESCRIPCION_COMANDOS = {
    "apagar": "apagar el ordenador",
    "reiniciar": "reiniciar el ordenador",
    "suspender": "suspender el ordenador",
    "bloquear": "bloquear el ordenador",
    "cerrar_sesion": "cerrar la sesión de Windows",
    "cancelar_apagado": "cancelar el apagado",
    "modo_oscuro": "activar el modo oscuro",
    "modo_claro": "activar el modo claro",
    "captura": "hacer una captura de pantalla",
    "vaciar_papelera": "vaciar la papelera de reciclaje",
    "mostrar_escritorio": "mostrar el escritorio",
}


def _derivar(palabra_normalizada: str, sal: bytes, iteraciones: int = ITERACIONES) -> str:
    return hashlib.pbkdf2_hmac("sha256", palabra_normalizada.encode("utf-8"), sal, iteraciones).hex()


def _hash_antiguo(palabra: str) -> str:
    """Formato de la v4 (sha256 sin sal). Solo para leer configuraciones antiguas."""
    return hashlib.sha256(palabra.strip().lower().encode("utf-8")).hexdigest()


def _coincide_frase(texto: str, frases: list) -> bool:
    limpio = normalizar(texto)
    if not limpio or len(limpio.split()) > MAXIMO_PALABRAS_RESPUESTA:
        return False
    return any(limpio == frase or limpio.startswith(frase + " ") for frase in frases)


class GestorSeguridad:
    def __init__(self, config_path: str | Path | None = None, log_path: str | Path | None = None):
        self.config_path = str(config_path) if config_path else str(dir_datos() / "seguridad.json")
        self.log_path = str(log_path) if log_path else str(dir_datos() / "logs" / "acciones.jsonl")
        self._config = self._cargar_config()
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CONFIGURACIÓN (persistente)
    # ------------------------------------------------------------------
    def _cargar_config(self) -> dict:
        ruta = Path(self.config_path)
        if ruta.exists():
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
                if isinstance(datos, dict):
                    return datos
            except (OSError, ValueError):
                registro.warning("No se pudo leer la configuración de seguridad; se usa el modo confirmación.")
        return {"modo": None, "palabra_hash": None, "configurada": False}

    def _guardar_config(self) -> None:
        try:
            ruta = Path(self.config_path)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            registro.error("No se pudo guardar la configuración de seguridad: %s", error)

    @property
    def modo_configurado(self) -> bool:
        return bool(self._config.get("configurada"))

    @property
    def modo(self) -> str:
        """Modo efectivo: "palabra" si hay palabra de seguridad; si no, "confirmar"."""
        if self._config.get("modo") == "palabra" and self._config.get("palabra_hash"):
            return "palabra"
        return "confirmar"

    def estado(self) -> dict:
        return {"configurada": self.modo_configurado, "modo": self.modo}

    def configurar_palabra(self, palabra: str) -> str:
        limpia = normalizar(palabra or "")
        if not limpia:
            return "No he entendido qué palabra de seguridad quieres usar."
        if len(limpia) < LONGITUD_MINIMA:
            return f"Elige una palabra de seguridad de al menos {LONGITUD_MINIMA} letras."
        if limpia in FRASES_CONFIRMACION or limpia in FRASES_CANCELACION:
            return "Esa palabra es una orden habitual. Elige otra menos corriente como palabra de seguridad."
        sal = secrets.token_bytes(16)
        self._config = {
            "modo": "palabra", "configurada": True,
            "palabra_hash": _derivar(limpia, sal), "sal": sal.hex(), "iteraciones": ITERACIONES,
        }
        self._guardar_config()
        return "Palabra de seguridad configurada. La pediré antes de cualquier acción delicada."

    def configurar_modo_confirmacion(self) -> str:
        self._config = {"modo": "confirmar", "palabra_hash": None, "configurada": True}
        self._guardar_config()
        return "Entendido. A partir de ahora solo te pediré confirmación antes de acciones delicadas."

    # ------------------------------------------------------------------
    # DECISIÓN: ¿esta acción necesita permiso?
    # ------------------------------------------------------------------
    def es_sensible(self, intent: str, action: str, target: str = "") -> bool:
        if (intent, action) in ACCIONES_SENSIBLES:
            return True
        if intent == "pc":
            if action == "open_app" and target and _PATRON_APP_SENSIBLE.search(normalizar(target)):
                return True
            if action == "system_command" and target in COMANDOS_SISTEMA_SENSIBLES:
                return True
            if action == "press_key" and (target or "").lower().replace(" ", "") in TECLAS_SENSIBLES:
                return True
        # Con palabra de seguridad activa, cambiarla o quitarla exige conocerla.
        if intent == "security_setup" and self.modo == "palabra":
            return True
        return False

    def generar_prompt_confirmacion(self, intent: str, action: str, target: str = "", texto: str = "") -> str:
        descripcion = self._describir_accion(intent, action, target, texto)
        if self.modo == "palabra":
            return (f"Eso es una acción delicada: {descripcion}. "
                    "Dime la palabra de seguridad para continuar, o di 'cancela'.")
        return f"Eso es una acción delicada: {descripcion}. ¿Confirmas? Di 'confirmo' o 'cancela'."

    @staticmethod
    def _describir_accion(intent: str, action: str, target: str = "", texto: str = "") -> str:
        if intent == "pc" and action == "close_app":
            return "cerrar la ventana activa" if target in ("active", "activa", "") else f"cerrar {target}"
        if intent == "pc" and action == "type_text":
            vista_previa = (texto[:40] + "…") if len(texto) > 40 else texto
            destino = f" en {target}" if target else ""
            return f'escribir "{vista_previa}"{destino}'
        if intent == "pc" and action == "open_app":
            return f"abrir {target}"
        if intent == "pc" and action == "system_command":
            return DESCRIPCION_COMANDOS.get(target, target)
        if intent == "pc" and action == "press_key":
            return f"pulsar {target}"
        if intent == "memory_delete" and action == "delete_all":
            return "borrar TODOS mis recuerdos"
        if intent == "memory_delete":
            return f"borrar de mi memoria «{target}»"
        if intent == "history" and action == "delete_current":
            return "borrar esta conversación"
        if intent == "history":
            return "borrar todo el historial de conversaciones"
        if intent == "security_setup":
            return "cambiar la configuración de seguridad"
        return f"{action} {target}".strip()

    def describir(self, intencion: dict) -> str:
        """Descripción legible de una intención (para tarjetas de confirmación y el registro)."""
        if intencion.get("intent") == "message":
            from tools.mensajes import nombre_de
            texto = intencion.get("text", "")
            vista_previa = (texto[:80] + "…") if len(texto) > 80 else texto
            por = f" por {nombre_de(intencion['app'])}" if intencion.get("app") else ""
            return f"mandar «{vista_previa}» a {intencion.get('target', '')}{por}"
        objetivo = intencion.get("key") if intencion.get("action") == "press_key" else intencion.get("target", "")
        return self._describir_accion(intencion.get("intent", ""), intencion.get("action", ""),
                                      objetivo or "", intencion.get("text", ""))

    # ------------------------------------------------------------------
    # VERIFICACIÓN DE LA RESPUESTA DE LA USUARIA
    # ------------------------------------------------------------------
    def verificar_palabra(self, texto: str) -> bool:
        """True si `texto` es la palabra de seguridad configurada."""
        if self.modo != "palabra":
            return False
        dicho = normalizar(texto or "")
        if not dicho:
            return False
        # Se acepta la palabra sola o precedida de una fórmula natural.
        candidatos = {dicho}
        for inicio in ("la palabra de seguridad es ", "la palabra es ", "mi palabra es ", "es "):
            if dicho.startswith(inicio):
                candidatos.add(dicho[len(inicio):])
        guardado = self._config.get("palabra_hash") or ""
        sal = self._config.get("sal")
        for candidato in candidatos:
            if sal:
                calculado = _derivar(candidato, bytes.fromhex(sal), int(self._config.get("iteraciones", ITERACIONES)))
            else:
                calculado = _hash_antiguo(candidato)
            if hmac.compare_digest(calculado, guardado):
                self._recordar_huella(candidato)
                return True
        return False

    def _recordar_huella(self, palabra: str) -> None:
        """Huella rápida de la palabra, solo en memoria mientras JARVIS está abierto (nunca en disco),
        con una clave aleatoria: sirve para notar al momento si aparece dentro de cualquier mensaje."""
        clave = secrets.token_bytes(32)
        self._huella = (clave, hmac.new(clave, palabra.encode("utf-8"), "sha256").digest(), len(palabra.split()))

    def contiene_palabra(self, texto: str, comprobar_corto: bool = True) -> bool:
        """True si el mensaje lleva la palabra de seguridad aunque nadie la haya pedido.

        Pasó: se escribió la palabra en la conversación sin una confirmación pendiente y acabó
        guardada en el historial y en manos del modelo. Los mensajes cortos se comprueban con el
        hash (lento a propósito, así que solo si `comprobar_corto`); dentro de los largos se busca
        con la huella rápida, que existe desde la primera vez que se dice bien en la sesión.
        """
        if self.modo != "palabra":
            return False
        palabras = normalizar(texto or "").split()
        if not palabras:
            return False
        if comprobar_corto and len(palabras) <= 3 and self.verificar_palabra(" ".join(palabras)):
            return True
        huella = getattr(self, "_huella", None)
        if huella:
            clave, esperada, largo = huella
            for inicio in range(len(palabras) - largo + 1):
                trozo = " ".join(palabras[inicio:inicio + largo]).encode("utf-8")
                if hmac.compare_digest(hmac.new(clave, trozo, "sha256").digest(), esperada):
                    return True
        return False

    def verificar_respuesta(self, texto: str) -> bool:
        if not (texto or "").strip():
            return False
        if self.modo == "palabra":
            return self.verificar_palabra(texto)
        # Modo "confirmar" o sin configurar todavía: basta una afirmación clara y breve.
        return _coincide_frase(texto, FRASES_CONFIRMACION)

    def es_cancelacion(self, texto: str) -> bool:
        return _coincide_frase(texto, FRASES_CANCELACION)

    @staticmethod
    def es_afirmacion(texto: str) -> bool:
        """Un «sí» claro a una pregunta que no es delicada (p. ej. «¿lo guardo en mi memoria?»)."""
        return _coincide_frase(texto, FRASES_CONFIRMACION + ["guardalo", "guardala", "recuerdalo", "apuntalo"])

    # ------------------------------------------------------------------
    # AUDITORÍA
    # ------------------------------------------------------------------
    def registrar_accion(self, intent: str, action: str, target: str, confirmada: bool, ejecutada: bool,
                         **extra) -> dict:
        entrada = {
            "timestamp": time.time(), "intent": intent, "action": action, "target": target,
            "confirmada": confirmada, "ejecutada": ejecutada,
        }
        entrada.update({clave: valor for clave, valor in extra.items() if valor not in (None, "")})
        try:
            with open(self.log_path, "a", encoding="utf-8") as fichero:
                fichero.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError as error:
            registro.error("No se pudo registrar la acción en el log: %s", error)
        return entrada

    def ultimas_acciones(self, cantidad: int = 10, solo_ejecutadas: bool = True) -> list:
        """Las acciones más recientes del registro de auditoría (la más nueva primero)."""
        try:
            with open(self.log_path, "r", encoding="utf-8") as fichero:
                lineas = fichero.readlines()[-400:]
        except OSError:
            return []
        acciones = []
        for linea in reversed(lineas):
            try:
                entrada = json.loads(linea)
            except ValueError:
                continue
            if solo_ejecutadas and not entrada.get("ejecutada"):
                continue
            acciones.append(entrada)
            if len(acciones) >= cantidad:
                break
        return acciones
