"""
Registro de herramientas
=========================
Catálogo cerrado de lo que JARVIS puede hacer en el mundo real (plan técnico,
sección 3: «Registro de herramientas»). Cada herramienta declara qué hace,
ejemplos de uso y su política de confirmación. La usuaria puede activar o
desactivar cada una desde el panel de Herramientas; una herramienta
desactivada no se ejecuta aunque la orden se entienda.
"""
from __future__ import annotations

HERRAMIENTAS = [
    {
        "id": "apps", "nombre": "Abrir aplicación", "icono": "app",
        "descripcion": "Abre cualquier programa instalado, carpetas personales y webs conocidas.",
        "ejemplos": ["abre Spotify", "abre Visual Studio Code", "abre la carpeta de descargas", "abre YouTube"],
        "confirmacion": "Solo para herramientas del sistema (cmd, PowerShell, registro…).",
    },
    {
        "id": "musica", "nombre": "Música", "icono": "musica",
        "descripcion": "Pone canciones o artistas, reanuda o pausa lo que suena y te dice qué está sonando "
                       "(lo lee de Windows: nunca lo supone).",
        "ejemplos": ["pon Bohemian Rhapsody de Queen", "reproduce la última canción", "¿qué canción está sonando?",
                     "pausa la música"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "busqueda", "nombre": "Buscar en Internet", "icono": "buscar",
        "descripcion": "Busca información y noticias, o abre la búsqueda en un sitio concreto.",
        "ejemplos": ["busca recetas de pasta", "¿qué noticias hay de tecnología?", "busca lo-fi beats en YouTube"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "tiempo", "nombre": "Consultar el tiempo", "icono": "sol",
        "descripcion": "Tiempo actual y previsión de mañana para cualquier ciudad.",
        "ejemplos": ["¿qué tiempo hace en Sevilla?", "¿va a llover mañana?"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "escritura", "nombre": "Escribir en aplicaciones", "icono": "teclado",
        "descripcion": "Escribe texto en la ventana en la que estabas o en una aplicación concreta.",
        "ejemplos": ["escribe Hola, ¿qué tal?", "escribe Lista de la compra en el bloc de notas"],
        "confirmacion": "Siempre pide confirmación.",
    },
    {
        "id": "mensajes", "nombre": "Mensajes de Discord", "icono": "teclado",
        "descripcion": "Manda mensajes a tus contactos por Discord y te lee lo que te han escrito: busca su "
                       "conversación y solo escribe si la encuentra de verdad.",
        "ejemplos": ["escríbele \"hola\" a elPixel12 por Discord", "abre Discord y dile a Pepe que ya voy",
                     "¿qué me ha dicho elPixel12 por Discord?", "léeme los últimos 3 mensajes de Pepe en Discord"],
        "confirmacion": "Mandar siempre pide confirmación (dice qué se manda y a quién); leer, no.",
    },
    {
        "id": "comandos", "nombre": "Ejecutar comandos", "icono": "terminal",
        "descripcion": "Apagar, reiniciar, suspender, bloquear, modo oscuro, captura de pantalla, papelera…",
        "ejemplos": ["apaga el ordenador", "activa el modo oscuro", "haz una captura de pantalla"],
        "confirmacion": "Pide confirmación para apagar, reiniciar, suspender, cerrar sesión y vaciar la papelera.",
    },
    {
        "id": "cerrar", "nombre": "Cerrar aplicación", "icono": "cerrar",
        "descripcion": "Cierra la ventana activa o las ventanas de una aplicación.",
        "ejemplos": ["cierra Spotify", "cierra esta ventana"],
        "confirmacion": "Siempre pide confirmación.",
    },
    {
        "id": "teclas", "nombre": "Teclas y volumen", "icono": "teclado",
        "descripcion": "Pulsa teclas, pasa de canción y controla el volumen.",
        "ejemplos": ["pulsa intro", "siguiente canción", "sube el volumen", "pulsa control c"],
        "confirmacion": "Solo para combinaciones que cierran o borran (Alt+F4, Suprimir…).",
    },
    {
        "id": "recordatorios", "nombre": "Recordatorios y agenda", "icono": "reloj",
        "descripcion": "Temporizadores, alarmas, recordatorios y tu agenda (también la de un calendario iCal, "
                       "como Google Calendar). Avisa en voz alta aunque estés con otra cosa.",
        "ejemplos": ["pon un temporizador de 10 minutos", "despiértame mañana a las 7",
                     "recuérdame el jueves a las 5 ir al dentista", "¿qué tengo hoy?", "cancela la alarma"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "archivos", "nombre": "Buscar y abrir archivos", "icono": "tarea",
        "descripcion": "Busca archivos en tus carpetas (con el índice de búsqueda de Windows) y los abre. "
                       "Los programas y scripts no se abren desde aquí.",
        "ejemplos": ["abre el PDF del TFG", "busca la foto de la playa", "¿dónde está el documento de inglés?",
                     "abre lo último que he descargado"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "pantalla", "nombre": "Leer la pantalla", "icono": "app",
        "descripcion": "Lee el texto de la ventana en la que estabas (con el reconocimiento de texto de Windows) "
                       "para explicarlo, resumirlo, traducirlo o decirte qué error es. Todo en tu ordenador.",
        "ejemplos": ["¿qué pone en la pantalla?", "¿qué error es este?", "tradúceme lo que pone en la pantalla",
                     "resume esto"],
        "confirmacion": "No requiere confirmación. La captura no se guarda.",
    },
    {
        "id": "equipo", "nombre": "Estado del equipo y brillo", "icono": "info",
        "descripcion": "Batería, procesador, memoria, disco y gráfica, y el brillo de la pantalla del portátil.",
        "ejemplos": ["¿cuánta batería me queda?", "¿cómo va el ordenador?", "sube el brillo", "pon el brillo al 40"],
        "confirmacion": "No requiere confirmación.",
    },
    {
        "id": "mcp", "nombre": "Herramientas MCP", "icono": "herramientas",
        "descripcion": "Herramientas de servidores MCP locales que configures en data/mcp.json (por ejemplo, para "
                       "leer tus documentos). La IA solo usa las de lectura, salvo que permitas otras.",
        "ejemplos": ["(depende de los servidores que conectes)"],
        "confirmacion": "Las que pueden cambiar cosas están bloqueadas salvo que lo permitas en data/mcp.json.",
    },
    {
        "id": "memoria", "nombre": "Memoria", "icono": "memoria",
        "descripcion": "Guarda, consulta, modifica y borra recuerdos autorizados.",
        "ejemplos": ["recuerda que me gusta el té", "¿qué recuerdas de mí?", "olvida que me gusta el té"],
        "confirmacion": "Pide confirmación para borrar.",
    },
]

_POR_ID = {herramienta["id"]: herramienta for herramienta in HERRAMIENTAS}

_ACCIONES_PC = {
    "open_app": "apps", "close_app": "cerrar", "type_text": "escritura",
    "press_key": "teclas", "media": "teclas", "media_keys": "teclas", "system_command": "comandos",
}


def herramienta_de(intencion: dict) -> str | None:
    """Id de la herramienta que atiende una intención, o None si no usa ninguna."""
    intent, action = intencion.get("intent", ""), intencion.get("action", "")
    if intent == "pc":
        return _ACCIONES_PC.get(action)
    if intent == "search_web":
        return "busqueda"
    if intent == "weather":
        return "tiempo"
    if intent == "music":
        return "musica"
    if intent.startswith("memory_"):
        return "memoria"
    if intent == "reminder":
        return "recordatorios"
    if intent == "system_info":
        return "equipo"
    if intent == "screen":
        return "pantalla"
    if intent == "files":
        return "archivos"
    if intent == "mcp":
        return "mcp"
    if intent == "message":
        return "mensajes"
    return None


def nombre_de(herramienta_id: str) -> str:
    return _POR_ID.get(herramienta_id, {}).get("nombre", herramienta_id)


def catalogo(activas: dict | None = None) -> list:
    """El catálogo completo, con el estado (activa o no) de cada herramienta."""
    activas = activas or {}
    return [dict(herramienta, activa=bool(activas.get(herramienta["id"], True))) for herramienta in HERRAMIENTAS]
