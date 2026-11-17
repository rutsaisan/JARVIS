"""
Mandar mensajes por aplicaciones de chat
========================================
«Escríbele "Hola" a elPixel12 por Discord», «dile a Pepe que llego tarde», «abre Discord y
escríbele hola a Ana».

De momento solo Discord, que tiene un buscador de conversaciones con el teclado (Ctrl+K). Se trae
al frente Discord (o se abre) y se comprueban dos cosas antes de escribir nada:
  1. Que el buscador está abierto de verdad: según la accesibilidad de Windows, el foco tiene que
     estar en su casilla (un «cuadro combinado»). Si no, el nombre del contacto acabaría escrito y
     enviado en el chat que estuviera abierto.
  2. Que, tras elegirla, está abierta SU conversación: el título de la ventana lo dice
     («@elPixel12 - Discord»). Si no aparece, no se escribe nada: es mejor no mandar un mensaje que
     mandarlo a otra persona o a un canal.

Mandar un mensaje es una acción delicada: antes pasa siempre por la confirmación (o la palabra de
seguridad), que dice qué se va a mandar, a quién y por dónde.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import time

from core.text import plano
from tools import computer
from tools.computer import Resultado, simulando

registro = logging.getLogger("jarvis.mensajes")

ESPERA_ARRANQUE = 30.0         # segundos como mucho hasta que aparece la ventana principal
ESPERA_TRAS_ARRANCAR = 4.0     # recién abierto, Discord tarda un poco más en responder al teclado
ESPERA_TRAS_TRAER = 0.5        # recién puesta delante la ventana
ESPERA_BUSCADOR = 2.0          # como mucho, tras Ctrl+K, hasta que el buscador tiene el foco
ESPERA_RESULTADOS = 1.5        # tras escribir el nombre, hasta que salen las conversaciones
ESPERA_CONVERSACION = 1.5      # tras elegirla, hasta que el título de la ventana cambia

APPS = {
    "discord": {"nombre": "Discord", "buscador": ("ctrl", "k")},
}
NOMBRES = {"discord": "Discord", "whatsapp": "WhatsApp", "telegram": "Telegram", "slack": "Slack",
           "teams": "Teams", "instagram": "Instagram", "messenger": "Messenger", "signal": "Signal"}


def soportada(app: str) -> bool:
    return app in APPS


def nombre_de(app: str) -> str:
    return NOMBRES.get(app, (app or "").capitalize())


def es_su_conversacion(titulo: str, contacto: str) -> bool:
    """«@elPixel12 - Discord» es la conversación con elPixel12; «#general | Servidor» o «@elPixel123», no."""
    titulo, contacto = plano(titulo or ""), plano(contacto or "").strip().lstrip("@")
    return bool(contacto) and re.search(rf"@\s*{re.escape(contacto)}(?![\w.])", titulo) is not None


def _ventana_principal(app: str) -> int:
    """La ventana principal de la aplicación (no la del actualizador), o 0 si no está abierta."""
    for ventana in computer._ventanas_de(app):
        titulo = plano(ventana["titulo"])
        if "updater" not in titulo and "actualiz" not in titulo:
            return ventana["hwnd"]
    return 0


def _traer_al_frente(app: str) -> tuple:
    """Deja la aplicación delante, abriéndola si hace falta. Devuelve (hwnd, error)."""
    hwnd = _ventana_principal(app)
    if not hwnd:
        abierta, _ = computer.abrir_aplicacion(app)
        if not abierta:
            return 0, f"No he podido abrir {nombre_de(app)}."
        limite = time.time() + ESPERA_ARRANQUE
        while time.time() < limite and not hwnd:
            time.sleep(0.5)
            hwnd = _ventana_principal(app)
        if not hwnd:
            return 0, f"He abierto {nombre_de(app)}, pero su ventana no ha aparecido a tiempo. Pídemelo otra vez."
        time.sleep(ESPERA_TRAS_ARRANCAR)
    if computer._user32().GetForegroundWindow() != hwnd and not computer._enfocar(hwnd):
        return 0, f"No he podido traer {nombre_de(app)} al frente."
    return hwnd, ""


def _pegar(pyautogui, texto: str) -> None:
    import pyperclip
    pyperclip.copy(texto)
    pyautogui.hotkey("ctrl", "v")


def _esperar_buscador(espera: float = ESPERA_BUSCADOR) -> str | None:
    """Mira cada poco si el buscador ya tiene el foco; devuelve el último papel visto."""
    limite = time.monotonic() + espera
    rol = rol_del_foco()
    while rol not in ("combobox", None) and time.monotonic() < limite:
        time.sleep(0.25)
        rol = rol_del_foco()
    return rol


def _cerrar_buscador(pyautogui, intentos: int = 3) -> None:
    """Esc hasta que el buscador ya no tiene el foco (con el nombre escrito, el primer Esc a veces solo lo borra)."""
    for _ in range(intentos):
        pyautogui.press("esc")
        time.sleep(0.35)
        if rol_del_foco() != "combobox":
            return


def rol_del_foco() -> str | None:
    """El papel (ARIA) de lo que tiene el foco, según la accesibilidad de Windows («combobox» en el
    buscador de Discord, «document» o «textbox» en el chat). None si no se puede saber."""
    try:
        import comtypes.client
        import pythoncom
    except ImportError:
        return None
    pythoncom.CoInitialize()                       # cada hilo que usa COM tiene que inicializarlo
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        automatizacion = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elemento = automatizacion.GetFocusedElement()
        rol = (elemento.CurrentAriaRole or "").lower()
        return rol or ("combobox" if elemento.CurrentControlType == UIA.UIA_ComboBoxControlTypeId else "")
    except Exception as error:
        registro.info("No se ha podido leer el foco con la accesibilidad de Windows (%s).", type(error).__name__)
        return None
    finally:
        pythoncom.CoUninitialize()


@contextlib.contextmanager
def _portapapeles_intacto():
    """Lo que tenías copiado vuelve a su sitio al terminar (para escribir se usa el portapapeles)."""
    import pyperclip
    try:
        anterior = pyperclip.paste()
    except Exception:
        anterior = None
    try:
        yield
    finally:
        if anterior is not None:
            try:
                pyperclip.copy(anterior)
            except Exception:
                pass


def _abrir_conversacion(app: str, contacto: str, pyautogui) -> tuple:
    """Deja abierta en la aplicación la conversación con `contacto`. Devuelve (hwnd, error).

    Solo escribe el nombre si el buscador tiene el foco, y solo da la conversación por buena si el
    título de la ventana dice que es la suya.
    """
    nombre_app = nombre_de(app)
    hwnd, error = _traer_al_frente(app)
    if not hwnd:
        return 0, error
    time.sleep(ESPERA_TRAS_TRAER)              # recién puesta delante, a veces se perdía la primera tecla
    rol = None
    for _ in range(2):                         # si no se abre, otra vez desde cero (Esc lo cierra si acaso)
        # Primero Esc: si el buscador ya estaba abierto, Ctrl+K lo CERRARÍA y el nombre del contacto
        # acabaría escrito (y enviado) en el chat que estuviera abierto.
        pyautogui.press("esc")
        time.sleep(0.3)
        pyautogui.hotkey(*APPS[app]["buscador"])                 # buscador de conversaciones
        rol = _esperar_buscador()
        if rol == "combobox" or rol is None:
            break
    if rol != "combobox":
        # Solo se escribe el nombre si el foco está de verdad en el buscador (un «cuadro combinado»).
        pyautogui.press("esc")
        registro.info("El buscador de %s no tiene el foco (%s): no se escribe nada.", nombre_app, rol)
        return 0, (f"No he conseguido abrir el buscador de conversaciones de {nombre_app}, así que no he escrito "
                   "nada." + (" (Falta el paquete comtypes: pip install comtypes.)" if rol is None else ""))
    _pegar(pyautogui, contacto)
    time.sleep(ESPERA_RESULTADOS)
    pyautogui.press("enter")
    time.sleep(ESPERA_CONVERSACION)
    if not es_su_conversacion(computer._titulo_de(hwnd), contacto):
        _cerrar_buscador(pyautogui)
        registro.info("No se ha encontrado la conversación pedida en %s: no se escribe nada.", nombre_app)
        return 0, (f"No encuentro una conversación con {contacto} en {nombre_app}, así que no he escrito nada. "
                   "Comprueba cómo se llama ahí (su nombre de usuario).")
    return hwnd, ""


def _comprobaciones(app: str, contacto: str, que: str) -> str:
    """Qué impide hacerlo («mandar mensajes», «leer mensajes»), o "" si nada."""
    if not soportada(app):
        return f"Todavía no sé {que} por {nombre_de(app)}; de momento, solo por Discord."
    if not contacto:
        return "Me falta el nombre de la persona."
    if os.name != "nt" and not simulando():
        return "Esto solo funciona en Windows."
    return ""


def enviar(app: str, contacto: str, texto: str) -> Resultado:
    """Manda `texto` a `contacto` por `app`. Solo escribe si encuentra SU conversación."""
    app = (app or "").lower()
    contacto, texto = (contacto or "").strip().lstrip("@"), (texto or "").strip()
    problema = _comprobaciones(app, contacto, "mandar mensajes") or ("" if texto else "Me falta qué decirle.")
    if problema:
        return Resultado(False, problema)
    nombre_app = nombre_de(app)
    if simulando():
        registro.info("[SIMULACIÓN] mensaje por %s a %s: %r", nombre_app, contacto, texto)
        return Resultado(True, f"Mensaje enviado a {contacto} por {nombre_app}.",
                         {"app": app, "contacto": contacto, "texto": texto})
    pyautogui = computer._pyautogui()
    try:
        with _portapapeles_intacto():
            hwnd, error = _abrir_conversacion(app, contacto, pyautogui)
            if not hwnd:
                return Resultado(False, error)
            if computer._user32().GetForegroundWindow() != hwnd:
                return Resultado(False, f"{nombre_app} ha dejado de estar delante antes de escribir: no he mandado nada.")
            _pegar(pyautogui, texto)
            time.sleep(0.3)
            pyautogui.press("enter")
            time.sleep(0.3)
    except Exception as error:
        registro.warning("Fallo al mandar el mensaje por %s: %s", nombre_app, type(error).__name__)
        return Resultado(False, f"Algo ha fallado al escribir en {nombre_app}; no sé si el mensaje ha salido. "
                                "Míralo en la conversación.")
    registro.info("Mensaje enviado por %s.", nombre_app)
    return Resultado(True, f"Mensaje enviado a {contacto} por {nombre_app}.",
                     {"app": app, "contacto": contacto, "texto": texto})


# ==========================================
# LEER LA CONVERSACIÓN
# ==========================================
# «¿Cuál es el último mensaje que me ha escrito elPixel12?». Pasó: la IA miró la pantalla y
# contestó con el último mensaje que había mandado ELLA. Ahora se abre la conversación (con las
# mismas comprobaciones que para escribir) y se leen los mensajes con la accesibilidad de Windows,
# que da de cada uno el autor, la fecha y el texto, sin reconocimiento de imagen. Discord, por dentro:
#   li#chat-messages-<canal>-<id>             un mensaje (del más antiguo al más reciente)
#     #message-username-<id> > botón          el autor (los seguidos de la misma persona no lo repiten)
#     .hiddenVisually > texto                 la fecha completa («lunes, 17 de noviembre de 2026 16:34»)
#     #message-content-<id>                   el texto (sin lo oculto ni la marca «(editado)»)
MAXIMO_MENSAJES = 30
_FECHA = re.compile(r"\d{1,2}:\d{2}")


def _uia():
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA
    return UIA, comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)


def _hijos(walker, elemento) -> list:
    hijos, hijo = [], walker.GetFirstChildElement(elemento)
    while hijo:
        hijos.append(hijo)
        hijo = walker.GetNextSiblingElement(hijo)
    return hijos


def _leer_mensaje(walker, item, autor_anterior: str) -> dict:
    """Autor, fecha y texto de un mensaje, recorriéndolo una sola vez.

    Ojo: la cabecera (autor y hora) y los «time» son de tipo texto aunque tengan hijos, así que se
    mira dentro de todo lo que tiene hijos y solo se recoge el nombre de las hojas.
    """
    datos = {"autor": "", "fecha": "", "partes": [], "editado": False}

    def recorrer(elemento, en_contenido=False, oculto=False, en_hora=False):
        identificador = elemento.CurrentAutomationId or ""
        clase = elemento.CurrentClassName or ""
        if "buttonContainer" in clase:                       # la barra de reacciones al pasar el ratón
            return
        if identificador.startswith("message-username-"):
            nombres = [h.CurrentName.strip() for h in _hijos(walker, elemento) if (h.CurrentName or "").strip()]
            datos["autor"] = nombres[0] if nombres else ""
            return
        en_contenido = en_contenido or identificador.startswith("message-content-")
        oculto = oculto or "hiddenVisually" in clase
        en_hora = en_hora or (en_contenido and (elemento.CurrentLocalizedControlType or "") == "time")
        hijos = _hijos(walker, elemento)
        if not hijos:
            texto = elemento.CurrentName or ""
            if oculto and not datos["fecha"] and _FECHA.search(texto):
                datos["fecha"] = texto.strip()
            elif en_contenido and en_hora:
                datos["editado"] = True                      # «(editado)» va dentro de un «time»
            elif en_contenido and not oculto:
                datos["partes"].append(texto)
            return
        for hijo in hijos:
            recorrer(hijo, en_contenido, oculto, en_hora)

    recorrer(item)
    texto = "".join(datos["partes"]).strip()
    return {"autor": datos["autor"] or autor_anterior, "fecha": datos["fecha"],
            "texto": texto or "(sin texto: una imagen, un archivo o un sticker)", "editado": datos["editado"]}


def mensajes_visibles(hwnd: int, maximo: int = MAXIMO_MENSAJES) -> list:
    """Los mensajes que la aplicación tiene en pantalla, del más antiguo al más reciente:
    [{"autor", "fecha", "texto", "editado"}]. [] si no se pueden leer."""
    import pythoncom
    pythoncom.CoInitialize()
    try:
        UIA, automatizacion = _uia()
        walker = automatizacion.RawViewWalker
        raiz = automatizacion.ElementFromHandle(hwnd)
        listas = raiz.FindAll(UIA.TreeScope_Descendants, automatizacion.CreatePropertyCondition(
            UIA.UIA_ControlTypePropertyId, UIA.UIA_ListControlTypeId))
        for i in range(listas.Length):
            # Los mensajes no cuelgan directamente de la lista (hay un nivel en medio): se buscan por su id.
            todos = listas.GetElement(i).FindAll(UIA.TreeScope_Descendants, automatizacion.CreateTrueCondition())
            items = [e for e in (todos.GetElement(j) for j in range(todos.Length))
                     if (e.CurrentAutomationId or "").startswith("chat-messages-")]
            if not items:
                continue
            items = items[-maximo:]
            leidos, autor = [], ""
            for item in items:
                mensaje = _leer_mensaje(walker, item, autor)
                autor = mensaje["autor"]
                leidos.append(mensaje)
            # Los primeros pueden ser continuación de alguien de más arriba: sin autor, no sirven.
            return [m for m in leidos if m["autor"]]
        return []
    except Exception as error:
        registro.info("No se han podido leer los mensajes (%s).", type(error).__name__)
        return []
    finally:
        pythoncom.CoUninitialize()


def nombre_en_el_titulo(titulo: str) -> str:
    """«@elPixel12 - Discord» -> «elPixel12» (como se ve en la conversación)."""
    coincidencia = re.match(r"^\s*@(.+?)\s+-\s+\S+", titulo or "")
    return coincidencia.group(1).strip() if coincidencia else ""


def leer(app: str, contacto: str) -> Resultado:
    """Abre la conversación con `contacto` y devuelve sus últimos mensajes (no escribe nada)."""
    app = (app or "").lower()
    contacto = (contacto or "").strip().lstrip("@")
    problema = _comprobaciones(app, contacto, "leer mensajes")
    if problema:
        return Resultado(False, problema)
    nombre_app = nombre_de(app)
    if simulando():
        registro.info("[SIMULACIÓN] leer la conversación con %s en %s", contacto, nombre_app)
        return Resultado(True, "", {"app": app, "contacto": contacto, "mensajes": [
            {"autor": contacto, "fecha": "", "texto": "(mensaje de prueba)", "editado": False}]})
    pyautogui = computer._pyautogui()
    try:
        with _portapapeles_intacto():
            hwnd, error = _abrir_conversacion(app, contacto, pyautogui)
        if not hwnd:
            return Resultado(False, error.replace("no he escrito nada", "no he leído nada"))
        visto_como = nombre_en_el_titulo(computer._titulo_de(hwnd)) or contacto
        time.sleep(0.8)                                          # que se pinten los mensajes
        lista = mensajes_visibles(hwnd)
    except Exception as error:
        registro.warning("Fallo al leer la conversación en %s: %s", nombre_app, type(error).__name__)
        return Resultado(False, f"Algo ha fallado al leer la conversación en {nombre_app}.")
    if not lista:
        return Resultado(False, f"He abierto la conversación con {visto_como}, pero no consigo leer sus mensajes.")
    return Resultado(True, "", {"app": app, "contacto": visto_como, "mensajes": lista})


def resumen_de_conversacion(mensajes: list, contacto: str, cantidad: int = 1, mios: bool = False) -> str:
    """La respuesta a «¿cuál es el último mensaje de X?» (o «…los últimos 3», o «el último que le mandé»)."""
    def suyo(m):
        return plano(m["autor"]).strip() == plano(contacto).strip()

    def frase(m):
        cuando = f" ({m['fecha']})" if m.get("fecha") else ""
        return f"«{m['texto']}»{cuando}{' (editado)' if m.get('editado') else ''}"

    if cantidad > 1:
        lineas = [f"{m['autor'] if suyo(m) else 'Tú'}: {frase(m)}" for m in mensajes[-cantidad:]]
        return f"Los últimos mensajes con {contacto}:\n" + "\n".join(lineas)
    if mios:
        tuyos = [m for m in mensajes if not suyo(m)]
        return (f"Lo último que le mandaste a {contacto} fue {frase(tuyos[-1])}." if tuyos
                else f"No veo ningún mensaje tuyo reciente en la conversación con {contacto}.")
    suyos = [m for m in mensajes if suyo(m)]
    if not suyos:
        return f"No veo ningún mensaje reciente de {contacto} en vuestra conversación."
    texto = f"Lo último que te ha escrito {contacto} es {frase(suyos[-1])}."
    if not suyo(mensajes[-1]):
        texto += f" Todavía no ha contestado a tu último mensaje, {frase(mensajes[-1])}."
    return texto
