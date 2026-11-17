"""
Pruebas del orquestador: el recorrido completo de una frase, con el modelo de
lenguaje, la voz y las herramientas sustituidos por dobles de prueba.

Aquí se comprueban las garantías de seguridad del asistente: qué se ejecuta,
qué queda en espera de permiso y qué queda registrado.

Ejecutar con:  pytest tests/test_orchestrator.py -v
"""
import os
import shutil
import tempfile

import pytest

from core.brain import ErrorCerebro
from core.config import Ajustes
from core.database import BaseDatos
from core.events import BusEventos
from core.history import Historial
from core.orchestrator import MASCARA, Orquestador, _LocutorPorFrases
from tools.computer import Resultado
from tools.memory import MemoriaLargoPlazo
from tools.security import GestorSeguridad


# ==========================================
# DOBLES DE PRUEBA
# ==========================================
class CerebroFalso:
    def __init__(self):
        self.llamadas = []
        self.fallar = False
        self.respuesta = "Respuesta de prueba. Con dos frases para la voz."
        self.pedir = []                 # funciones que «pide el modelo»: [(nombre, argumentos), ...]
        self.resultados_funciones = []  # lo que JARVIS le devolvió
        self.datos_extraidos = []       # lo que «extrae el modelo» al aprender
        self.extracciones = []

    def pensar(self, texto, historial=None, modo="texto", contexto_memoria="", resumen_previo="",
               al_fragmento=None, cancelar=None, funciones=None):
        self.llamadas.append({"texto": texto, "historial": list(historial or []), "modo": modo,
                              "contexto_memoria": contexto_memoria, "resumen_previo": resumen_previo,
                              "funciones": [d["function"]["name"] for d in funciones.definiciones()] if funciones else [],
                              "previas": [previa[:2] for previa in funciones.previas] if funciones else []})
        if self.fallar:
            raise ErrorCerebro("No puedo acceder ahora mismo a mi núcleo local.", "sin_conexion")
        for nombre, argumentos in (self.pedir if funciones is not None else []):
            self.resultados_funciones.append(funciones.ejecutar(nombre, argumentos))
        if al_fragmento:
            for palabra in self.respuesta.split(" "):
                al_fragmento(palabra + " ")
        return self.respuesta

    def resumir(self, mensajes):
        return "Resumen de prueba."

    def extraer_datos(self, texto, nombre="la usuaria"):
        self.extracciones.append(texto)
        return list(self.datos_extraidos)


class HablaFalsa:
    def __init__(self):
        self.dicho = []
        self.detenciones = 0
        self.ocupada = False

    def decir(self, texto):
        self.dicho.append(texto)

    def detener(self):
        self.detenciones += 1


class HerramientasFalsas:
    def __init__(self):
        self.pc = []
        self.busquedas = []
        self.sitios = []
        self.resultados = [{"titulo": "Título uno", "url": "https://ejemplo.es/1", "resumen": "Resumen uno",
                            "fuente": "ejemplo.es", "fecha": ""}]
        self.fallo_busqueda = False

    def ejecutar_pc(self, intencion):
        self.pc.append(dict(intencion))
        if intencion["action"] == "open_app":
            if intencion["target"] == "inexistente":
                return Resultado(False, "No encuentro nada llamado «inexistente» en este ordenador.")
            return Resultado(True, f"He abierto {intencion['target']}.", {"nombre": intencion["target"].title()})
        return Resultado(True, "Hecho.")

    def buscar(self, consulta, maximo=5, tipo="web"):
        self.busquedas.append((consulta, tipo))
        if self.fallo_busqueda:
            raise RuntimeError("sin conexión")
        return list(self.resultados)

    def abrir_busqueda(self, sitio, consulta):
        self.sitios.append((sitio, consulta))
        return True, sitio.title(), f"https://{sitio}.com/?q={consulta}"

    def leer_pagina(self, url):
        return "Contenido de la página de prueba."

    # --- archivos ---
    archivos = [{"nombre": "TFG final.pdf", "ruta": "C:/u/Documents/TFG final.pdf", "carpeta": "Documents",
                 "fecha": 1794600000, "tamano": 1000},
                {"nombre": "TFG borrador.pdf", "ruta": "C:/u/Documents/TFG borrador.pdf", "carpeta": "Documents",
                 "fecha": 1794500000, "tamano": 900}]

    def buscar_archivos(self, texto):
        self.buscados = getattr(self, "buscados", []) + [texto]
        return [dict(a) for a in self.archivos] if "tfg" in texto.lower() else []

    def abrir_archivo(self, ruta):
        self.abiertos = getattr(self, "abiertos", []) + [ruta]
        return Resultado(True, f"He abierto «{ruta.rsplit('/', 1)[-1]}».", {"ruta": ruta})

    def ultimos_descargados(self, cantidad=1):
        return [{"nombre": "factura.pdf", "ruta": "C:/u/Downloads/factura.pdf", "carpeta": "Downloads",
                 "fecha": 1794600000, "tamano": 10}]

    def buscar_carpetas(self, texto):
        return [{"nombre": "TFG", "ruta": "C:/u/Documents/TFG", "carpeta": "Documents", "fecha": 0, "tamano": 0}] \
            if "tfg" in texto.lower() else []

    def leer_archivo(self, ruta):
        self.leidos = getattr(self, "leidos", []) + [ruta]
        if ruta.endswith(".env"):
            return {"ok": False, "mensaje": "«.env» suele guardar contraseñas o claves: no lo leo."}
        return {"ok": True, "nombre": ruta.rsplit("/", 1)[-1], "ruta": ruta, "texto": "Índice del TFG",
                "recortado": False, "caracteres": 14}

    def explorar_carpeta(self, ruta):
        return {"ok": True, "ruta": ruta, "texto": f"Carpeta {ruta}\n1) index.php  (1.0 KB, 01/11/2026)",
                "archivos": [{"nombre": "index.php", "ruta": f"{ruta}/index.php", "carpeta": "TFG", "fecha": 0,
                              "tamano": 1000}]}

    def buscar_en_archivos(self, texto, carpeta=""):
        return [{"nombre": "login.php", "ruta": "C:/u/Documents/TFG/login.php", "carpeta": "TFG", "fecha": 0,
                 "tamano": 10, "linea": 3, "fragmento": "session_start();"}] if texto == "session_start" else []

    # --- mensajes ---
    app_activa = ""

    def enviar_mensaje(self, app, contacto, texto):
        self.mensajes = getattr(self, "mensajes", []) + [(app, contacto, texto)]
        return Resultado(True, f"Mensaje enviado a {contacto} por Discord.", {})

    def app_de_mensajes_activa(self):
        return self.app_activa

    conversacion = [
        {"autor": "elPixel12", "fecha": "lunes, 16 de noviembre de 2026 18:00", "texto": "¿jugamos luego?", "editado": False},
        {"autor": "Ruth", "fecha": "lunes, 17 de noviembre de 2026 16:34", "texto": "hola", "editado": False},
        {"autor": "elPixel12", "fecha": "lunes, 17 de noviembre de 2026 16:40", "texto": "qué pasa", "editado": True},
    ]

    def leer_mensajes(self, app, contacto):
        self.lecturas = getattr(self, "lecturas", []) + [(app, contacto)]
        if contacto == "nadie":
            return Resultado(False, "No encuentro una conversación con nadie en Discord, así que no he leído nada.")
        return Resultado(True, "", {"app": app, "contacto": contacto, "mensajes": [dict(m) for m in self.conversacion]})

    # --- pantalla ---
    pantalla = Resultado(True, "He leído 2 líneas de la pantalla.",
                         {"texto": "Error 404\nNo se ha encontrado la página", "titulo": "Navegador", "lineas": 2})

    def leer_pantalla(self):
        return self.pantalla

    # --- equipo ---
    brillo_actual = 70

    def estado_equipo(self):
        return {"cpu": 12, "ram": 63, "ram_total_gb": 16.0, "disco": "C:", "disco_libre_gb": 120, "disco_uso": 75,
                "encendido_horas": 3.5, "gpu": None,
                "bateria": {"porcentaje": 76, "enchufado": False, "segundos_restantes": 11400}}

    def brillo(self):
        return self.brillo_actual

    def poner_brillo(self, valor):
        self.brillo_actual = valor
        return Resultado(True, f"Brillo al {valor} %.", {"brillo": valor})

    # --- música ---
    sesion = {"app": "Spotify", "id_app": "Spotify", "titulo": "MONEY MACHINE", "artista": "Nicki Nicole",
              "album": "NAIKI", "estado": "pausa", "actual": True, "objetivo": True}

    def musica_sonando(self, preferida=""):
        self.musica = getattr(self, "musica", []) + [("sonando", preferida)]
        return dict(self.sesion) if self.sesion else None

    def musica_controlar(self, accion, preferida=""):
        self.musica = getattr(self, "musica", []) + [(accion, preferida)]
        if not self.sesion:
            return Resultado(False, "No hay ninguna música abierta ahora mismo.")
        sesion = dict(self.sesion, estado="pausa" if accion == "pausar" else "sonando")
        return Resultado(True, "Música en pausa." if accion == "pausar" else
                         f"Ahora suena «{sesion['titulo']}» de {sesion['artista']} en Spotify.", {"sesion": sesion})

    def musica_reproducir(self, consulta, servicio="youtube"):
        self.musica = getattr(self, "musica", []) + [("poner", consulta, servicio)]
        if consulta == "nada":
            return Resultado(False, "No he encontrado «nada» en YouTube.")
        return Resultado(True, f"Sonando «{consulta} (vídeo)» en YouTube.",
                         {"servicio": servicio, "titulo": f"{consulta} (vídeo)", "url": "https://youtu.be/x",
                          "confirmado": True})

    def tiempo(self, ciudad, dia="hoy"):
        if ciudad == "Atlantis":
            raise RuntimeError("ciudad_desconocida")
        return {"ciudad": ciudad, "pais": "España", "dia": dia, "temperatura": 22, "sensacion": 22,
                "humedad": 50, "viento": 10, "maxima": 25, "minima": 15, "prob_lluvia": 0,
                "descripcion": "cielos despejados", "icono": "sol"}


@pytest.fixture
def jarvis():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_orq_")
    ajustes = Ajustes(os.path.join(carpeta, "ajustes.json"))
    ajustes.establecer("personalidad.sarcasmo", 0)           # frases neutras: resultados estables
    # Las pruebas antiguas de «¿lo guardo en mi memoria?» son del modo sin aprendizaje automático;
    # las del aprendizaje lo activan explícitamente.
    ajustes.establecer("memoria.aprender_automaticamente", False)
    base = BaseDatos(os.path.join(carpeta, "jarvis.db"))
    orquestador = Orquestador(
        ajustes=ajustes,
        memoria=MemoriaLargoPlazo(base_datos=base),
        historial=Historial(base),
        seguridad=GestorSeguridad(os.path.join(carpeta, "seguridad.json"),
                                  os.path.join(carpeta, "logs", "acciones.jsonl")),
        cerebro=CerebroFalso(),
        habla=HablaFalsa(),
        herramientas=HerramientasFalsas(),
        bus=BusEventos(),
    )
    orquestador.eventos = []
    orquestador.bus.escuchar(orquestador.eventos.append)
    orquestador.aprender_en_segundo_plano = False
    yield orquestador
    shutil.rmtree(carpeta, ignore_errors=True)


def textos(respuestas):
    return " | ".join(r["texto"] for r in respuestas)


def acciones_pc(jarvis):
    return [(i["action"], i.get("target") or i.get("key") or i.get("text")) for i in jarvis.herramientas.pc]


# ==========================================
# CONVERSACIÓN
# ==========================================
def test_la_conversacion_va_al_modelo_con_su_contexto(jarvis):
    jarvis.procesar("hola, ¿cómo estás?")
    respuestas = jarvis.procesar("explícame una cosa")
    assert respuestas[0]["texto"] == jarvis.cerebro.respuesta
    segunda = jarvis.cerebro.llamadas[1]
    assert segunda["texto"] == "explícame una cosa"
    assert [m["role"] for m in segunda["historial"]] == ["user", "assistant"]      # el turno anterior
    assert jarvis.herramientas.pc == []                                           # conversar no ejecuta nada


def test_la_conversacion_se_guarda_en_el_historial(jarvis):
    jarvis.procesar("hola Jarvis")
    conversaciones = jarvis.historial.listar()
    assert len(conversaciones) == 1 and conversaciones[0]["titulo"] == "hola Jarvis"
    assert [m["rol"] for m in jarvis.historial.mensajes(jarvis.conversacion_id)] == ["usuario", "asistente"]


def test_sin_permiso_de_historial_no_se_guarda_nada(jarvis):
    jarvis.ajustes.establecer("memoria.guardar_historial", False)
    jarvis.procesar("hola Jarvis")
    assert jarvis.historial.listar() == []
    assert len(jarvis.sesion) == 2                       # pero la conversación en curso sí se ve


def test_fallo_del_modelo_da_un_mensaje_claro(jarvis):
    jarvis.cerebro.fallar = True
    respuestas = jarvis.procesar("hola")
    assert respuestas[0]["tipo"] == "error" and "núcleo local" in respuestas[0]["texto"]


def test_pregunta_ofrece_buscar_en_internet(jarvis):
    respuestas = jarvis.procesar("¿cuál es la mejor ruta de senderismo cerca de Sevilla?")
    assert respuestas[0]["sugerencias"][0]["etiqueta"] == "Buscar en Internet"
    assert respuestas[0]["sugerencias"][0]["comando"].startswith("busca en internet cuál es la mejor ruta")


def test_recuerdos_relevantes_llegan_al_modelo(jarvis):
    jarvis.memoria.guardar_recuerdo("mi color favorito es el morado")
    jarvis.procesar("¿cuál es mi color favorito?")
    assert "morado" in jarvis.cerebro.llamadas[0]["contexto_memoria"]


# ==========================================
# ACCIONES SIN RIESGO
# ==========================================
def test_abrir_una_aplicacion_no_pide_permiso(jarvis):
    respuestas = jarvis.procesar("abre Spotify")
    assert acciones_pc(jarvis) == [("open_app", "Spotify")]
    assert respuestas[0]["texto"] == "He abierto Spotify."
    assert jarvis.cerebro.llamadas == []                 # una acción no pasa por el modelo
    registro = jarvis.seguridad.ultimas_acciones(1)[0]
    assert registro["descripcion"] == "Abriste: Spotify" and registro["confirmada"] is False


def test_aplicacion_no_encontrada(jarvis):
    respuestas = jarvis.procesar("abre inexistente")
    assert respuestas[0]["tipo"] == "aviso" and "No encuentro" in respuestas[0]["texto"]
    assert jarvis.seguridad.ultimas_acciones(5) == []    # lo que no se ejecuta no figura como hecho


def test_hora_y_fecha_no_usan_el_modelo(jarvis):
    assert jarvis.procesar("¿qué hora es?")[0]["texto"].startswith("Son las ")
    assert jarvis.procesar("¿qué día es hoy?")[0]["texto"].startswith("Hoy es ")
    assert jarvis.cerebro.llamadas == []


def test_herramienta_desactivada_no_se_ejecuta(jarvis):
    jarvis.ajustes.establecer("herramientas.apps", False)
    respuestas = jarvis.procesar("abre Spotify")
    assert jarvis.herramientas.pc == []
    assert "desactivada" in respuestas[0]["texto"]


# ==========================================
# ACCIONES DELICADAS: CONFIRMACIÓN
# ==========================================
def test_accion_delicada_espera_confirmacion(jarvis):
    respuestas = jarvis.procesar("cierra Spotify")
    assert jarvis.herramientas.pc == []                  # todavía no se ha tocado nada
    assert respuestas[0]["tipo"] == "confirmacion" and "cerrar Spotify" in respuestas[0]["texto"]
    assert jarvis.estado_pendiente()["tipo"] == "accion"

    jarvis.procesar("confirmo")
    assert acciones_pc(jarvis) == [("close_app", "Spotify")]
    assert jarvis.pendiente is None
    assert jarvis.seguridad.ultimas_acciones(1)[0]["confirmada"] is True


def test_cancelar_no_ejecuta(jarvis):
    jarvis.procesar("apaga el ordenador")
    respuestas = jarvis.procesar("cancela")
    assert jarvis.herramientas.pc == []
    assert "cancelada" in respuestas[0]["texto"].lower()
    assert jarvis.pendiente is None


def test_respuesta_ambigua_no_confirma_y_hay_maximo_de_intentos(jarvis):
    jarvis.procesar("cierra Spotify")
    jarvis.procesar("bueno, no sé")
    jarvis.procesar("a ver")
    respuestas = jarvis.procesar("mmm")
    assert jarvis.herramientas.pc == []
    assert "Demasiados intentos" in respuestas[0]["texto"]
    assert jarvis.pendiente is None


def test_confirmacion_caducada_no_se_ejecuta(jarvis):
    jarvis.procesar("cierra Spotify")
    jarvis.pendiente["expira"] = 0                       # ha pasado el tiempo
    jarvis.procesar("confirmo")
    assert jarvis.herramientas.pc == []                  # ese «confirmo» ya no confirma nada


def test_confirmar_desde_la_interfaz(jarvis):
    jarvis.procesar("vacía la papelera")
    jarvis.resolver_confirmacion(True)
    assert acciones_pc(jarvis) == [("system_command", "vaciar_papelera")]


def test_cancelar_desde_la_interfaz(jarvis):
    jarvis.procesar("vacía la papelera")
    jarvis.resolver_confirmacion(False)
    assert jarvis.herramientas.pc == []


def test_abrir_herramientas_del_sistema_pide_permiso(jarvis):
    jarvis.procesar("abre powershell")
    assert jarvis.herramientas.pc == [] and jarvis.pendiente is not None


# ==========================================
# PALABRA DE SEGURIDAD
# ==========================================
def test_palabra_de_seguridad_completa(jarvis):
    respuestas = jarvis.procesar("mi palabra de seguridad es fénix dorado")
    assert "configurada" in respuestas[0]["texto"].lower()

    respuestas = jarvis.procesar("cierra Spotify")
    assert "palabra de seguridad" in respuestas[0]["texto"]
    jarvis.procesar("confirmo")                          # con palabra, un «confirmo» no basta
    assert jarvis.herramientas.pc == []
    jarvis.procesar("Fénix dorado.")
    assert acciones_pc(jarvis) == [("close_app", "Spotify")]


def test_la_palabra_nunca_aparece_en_la_conversacion_ni_en_el_historial(jarvis):
    jarvis.procesar("mi palabra de seguridad es fénix dorado")
    jarvis.procesar("cierra Spotify")
    jarvis.procesar("fénix dorado")
    todo = " ".join(m["texto"] for m in jarvis.sesion)
    todo += " ".join(m["texto"] for m in jarvis.historial.mensajes(jarvis.conversacion_id))
    todo += open(jarvis.seguridad.log_path, encoding="utf-8").read()
    assert "nix dorado" not in todo.lower()
    assert MASCARA in todo


def test_tres_palabras_incorrectas_cancelan(jarvis):
    jarvis.procesar("mi palabra de seguridad es fénix dorado")
    jarvis.procesar("apaga el ordenador")
    for intento in ("gato", "perro", "pez"):
        respuestas = jarvis.procesar(intento)
    assert jarvis.herramientas.pc == []
    assert "Demasiados intentos" in respuestas[0]["texto"]


def test_cambiar_la_palabra_exige_la_actual(jarvis):
    jarvis.procesar("mi palabra de seguridad es fénix dorado")
    jarvis.procesar("usa modo confirmación")             # intento de saltarse la palabra
    assert jarvis.seguridad.modo == "palabra"
    jarvis.procesar("no la sé")
    jarvis.procesar("cancela")
    assert jarvis.seguridad.modo == "palabra"

    jarvis.procesar("usa modo confirmación")
    jarvis.procesar("fénix dorado")
    assert jarvis.seguridad.modo == "confirmar"


def test_interfaz_con_palabra_incorrecta(jarvis):
    jarvis.procesar("mi palabra de seguridad es fénix dorado")
    jarvis.procesar("cierra Spotify")
    jarvis.resolver_confirmacion(True, "otra cosa")
    assert jarvis.herramientas.pc == []
    jarvis.resolver_confirmacion(True, "fénix dorado")
    assert acciones_pc(jarvis) == [("close_app", "Spotify")]


# ==========================================
# SECUENCIAS
# ==========================================
def test_secuencia_se_detiene_en_el_paso_delicado_y_continua_al_confirmar(jarvis):
    jarvis.procesar("abre el bloc de notas, luego escribe Hola y después pulsa intro")
    assert acciones_pc(jarvis) == [("open_app", "bloc de notas")]        # escribir espera permiso
    jarvis.procesar("confirmo")
    assert acciones_pc(jarvis) == [("open_app", "bloc de notas"), ("type_text", "Hola"), ("press_key", "enter")]


def test_cancelar_una_secuencia_anula_el_resto(jarvis):
    jarvis.procesar("cierra Spotify y abre Chrome")
    respuestas = jarvis.procesar("cancela")
    assert jarvis.herramientas.pc == []
    assert "resto de la secuencia" in respuestas[0]["texto"]


def test_el_texto_escrito_no_va_al_registro(jarvis):
    jarvis.procesar("escribe mi diario secreto")
    jarvis.procesar("confirmo")
    assert "diario secreto" not in open(jarvis.seguridad.log_path, encoding="utf-8").read()


# ==========================================
# MEMORIA
# ==========================================
def test_guardar_y_listar_recuerdos(jarvis):
    respuestas = jarvis.procesar("Recuerda que mi perro se llama Toby")
    assert "almacenado" in respuestas[0]["texto"].lower()
    respuestas = jarvis.procesar("¿qué recuerdas de mí?")
    assert "mi perro se llama Toby" in respuestas[0]["texto"]
    assert respuestas[0]["datos"]["recuerdos"][0]["texto"] == "mi perro se llama Toby"


def test_borrar_recuerdo_muestra_cual_y_pide_permiso(jarvis):
    jarvis.procesar("recuerda que me gusta el rock")
    respuestas = jarvis.procesar("olvida lo del rock")
    assert "me gusta el rock" in respuestas[0]["texto"]                  # dice exactamente qué va a borrar
    assert jarvis.memoria.contar() == 1
    jarvis.procesar("sí")
    assert jarvis.memoria.contar() == 0


def test_borrar_recuerdo_inexistente_no_pide_permiso(jarvis):
    respuestas = jarvis.procesar("olvida que soy astronauta")
    assert "No encuentro" in respuestas[0]["texto"]
    assert jarvis.pendiente is None


def test_consulta_de_memoria_sin_resultados_pasa_al_modelo(jarvis):
    respuestas = jarvis.procesar("¿qué sabes sobre los agujeros negros?")
    assert respuestas[0]["texto"] == jarvis.cerebro.respuesta


def test_no_guarda_contrasenas(jarvis):
    respuestas = jarvis.procesar("recuerda que mi contraseña del correo es gato123")
    assert "sensible" in respuestas[0]["texto"]
    assert jarvis.memoria.contar() == 0


def test_sugerencia_de_recuerdo_aceptada(jarvis):
    respuestas = jarvis.procesar("Me gusta el anime")
    assert respuestas[0]["datos"]["sugerir_recuerdo"] == "Me gusta el anime"
    assert jarvis.memoria.contar() == 0                  # todavía no se ha autorizado
    jarvis.procesar("sí")
    assert jarvis.memoria.listar_recuerdos()[0]["texto"] == "Me gusta el anime"
    assert jarvis.memoria.listar_recuerdos()[0]["origen"] == "sugerido"


def test_sugerencia_de_recuerdo_rechazada(jarvis):
    jarvis.procesar("prefiero el té al café")
    jarvis.procesar("no")
    assert jarvis.memoria.contar() == 0


def test_sugerencia_ignorada_no_guarda_y_atiende_lo_nuevo(jarvis):
    jarvis.procesar("prefiero el té al café")
    jarvis.procesar("abre Spotify")
    assert jarvis.memoria.contar() == 0
    assert acciones_pc(jarvis) == [("open_app", "Spotify")]


def test_sugerencias_desactivadas(jarvis):
    jarvis.ajustes.establecer("memoria.sugerir_recuerdos", False)
    respuestas = jarvis.procesar("me gusta el anime")
    assert "sugerir_recuerdo" not in respuestas[0]["datos"]
    assert jarvis.pendiente is None


def test_botones_de_sugerencia(jarvis):
    jarvis.procesar("vivo en Valencia")
    jarvis.resolver_sugerencia(True)
    assert jarvis.memoria.contar() == 1


# ==========================================
# INTERNET Y TIEMPO
# ==========================================
def test_busqueda_resume_los_resultados_con_el_modelo(jarvis):
    respuestas = jarvis.procesar("busca noticias sobre inteligencia artificial")
    assert jarvis.herramientas.busquedas == [("noticias sobre inteligencia artificial", "noticias")]
    assert "Título uno" in jarvis.cerebro.llamadas[0]["texto"]           # el modelo recibe los resultados
    assert jarvis.cerebro.llamadas[0]["historial"] == []
    assert respuestas[0]["tipo"] == "busqueda"
    assert respuestas[0]["datos"]["resultados"][0]["url"] == "https://ejemplo.es/1"
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"].startswith("Buscaste: ")


def test_busqueda_sin_modelo_muestra_los_titulos(jarvis):
    jarvis.cerebro.fallar = True
    respuestas = jarvis.procesar("busca recetas de pasta")
    assert "Título uno" in respuestas[0]["texto"] and respuestas[0]["tipo"] == "busqueda"


def test_busqueda_sin_conexion(jarvis):
    jarvis.herramientas.fallo_busqueda = True
    respuestas = jarvis.procesar("busca recetas de pasta")
    assert respuestas[0]["tipo"] == "error" and "buscador" in respuestas[0]["texto"]


def test_busqueda_en_un_sitio(jarvis):
    respuestas = jarvis.procesar("busca lo-fi beats en YouTube")
    assert jarvis.herramientas.sitios == [("youtube", "lo-fi beats")]
    assert "lo-fi beats" in respuestas[0]["texto"]


def test_tiempo(jarvis):
    respuestas = jarvis.procesar("¿qué tiempo hace hoy en Madrid?")
    assert respuestas[0]["tipo"] == "tiempo" and respuestas[0]["datos"]["temperatura"] == 22
    assert "Madrid" in respuestas[0]["texto"]


def test_tiempo_sin_ciudad_pregunta_y_luego_usa_la_de_ajustes(jarvis):
    assert "ciudad" in jarvis.procesar("¿qué tiempo hace?")[0]["texto"]
    jarvis.ajustes.establecer("internet.ciudad", "Sevilla")
    assert "Sevilla" in jarvis.procesar("¿qué tiempo hace?")[0]["texto"]


def test_tiempo_ciudad_desconocida(jarvis):
    assert "No encuentro ninguna ciudad" in jarvis.procesar("el tiempo en Atlantis")[0]["texto"]


# ==========================================
# MODO VOZ, PARADA Y APAGADO
# ==========================================
def test_en_modo_texto_no_habla_y_en_modo_voz_si(jarvis):
    jarvis.procesar("abre Spotify")
    assert jarvis.habla.dicho == []
    jarvis.procesar("modo voz")
    assert jarvis.ajustes.obtener("modo") == "voz"
    jarvis.habla.dicho.clear()
    jarvis.procesar("abre Spotify")
    assert jarvis.habla.dicho == ["He abierto Spotify."]


def test_en_modo_voz_la_respuesta_se_lee_una_sola_vez_y_por_frases(jarvis):
    jarvis.ajustes.establecer("modo", "voz")
    jarvis.cerebro.respuesta = ("Esta es la primera frase de la respuesta del modelo. "
                                "Y esta es la segunda frase, algo más larga que la primera.")
    jarvis.procesar("cuéntame algo")
    assert "".join(jarvis.habla.dicho).count("primera frase") == 1
    assert len(jarvis.habla.dicho) == 2


def test_parada_de_emergencia(jarvis):
    jarvis.procesar("cierra Spotify")
    jarvis.procesar("para")
    assert jarvis.habla.detenciones >= 1
    assert jarvis.pendiente is None and jarvis.herramientas.pc == []


def test_apagar_jarvis_avisa_a_la_aplicacion(jarvis):
    import threading
    apagado = threading.Event()
    jarvis.al_apagar = apagado.set
    respuestas = jarvis.procesar("apágate")
    assert "Hasta pronto" in respuestas[0]["texto"]
    assert apagado.wait(3)


# ==========================================
# CONVERSACIONES
# ==========================================
def test_nueva_conversacion_empieza_sin_contexto(jarvis):
    jarvis.procesar("hola")
    primera = jarvis.conversacion_id
    jarvis.procesar("nueva conversación")
    jarvis.procesar("¿sigues ahí?")
    assert jarvis.conversacion_id != primera
    assert jarvis.cerebro.llamadas[-1]["historial"] == [
        {"role": "assistant", "content": "Conversación nueva."}]


def test_retomar_una_conversacion_guardada(jarvis):
    jarvis.procesar("mi plan es estudiar Python")
    primera = jarvis.conversacion_id
    jarvis.nueva_conversacion(resumir=False)
    assert jarvis.sesion == []
    assert jarvis.abrir_conversacion(primera) is True
    assert jarvis.sesion[0]["texto"] == "mi plan es estudiar Python"
    jarvis.procesar("¿de qué hablábamos?")
    assert jarvis.cerebro.llamadas[-1]["historial"][0]["content"] == "mi plan es estudiar Python"


def test_borrar_el_historial_pide_permiso(jarvis):
    jarvis.procesar("hola")
    jarvis.procesar("borra el historial")
    assert len(jarvis.historial.listar()) == 1
    jarvis.procesar("confirmo")
    assert jarvis.historial.listar() == []


def test_resumen_de_conversacion(jarvis):
    for frase in ("hola", "¿qué tal?"):
        jarvis.procesar(frase)
    assert jarvis.resumir_conversacion(jarvis.conversacion_id) == "Resumen de prueba."
    assert jarvis.historial.obtener(jarvis.conversacion_id)["resumen"] == "Resumen de prueba."


# ==========================================
# EVENTOS PARA LA INTERFAZ
# ==========================================
def test_eventos_emitidos(jarvis):
    jarvis.procesar("abre Spotify")
    tipos = [evento["tipo"] for evento in jarvis.eventos]
    assert tipos[0] == "estado" and jarvis.eventos[0]["datos"]["valor"] == "procesando"
    assert "mensaje" in tipos and "accion" in tipos
    assert jarvis.eventos[-1]["datos"] == {"valor": "reposo"}


def test_la_respuesta_del_modelo_llega_a_trozos(jarvis):
    jarvis.procesar("hola")
    fragmentos = [e["datos"] for e in jarvis.eventos if e["tipo"] == "fragmento"]
    final = [e["datos"] for e in jarvis.eventos if e["tipo"] == "mensaje" and e["datos"]["rol"] == "asistente"][0]
    assert "".join(f["texto"] for f in fragmentos).strip() == final["texto"]
    assert {f["id"] for f in fragmentos} == {final["id"]}                # mismo mensaje, mismo id


# ==========================================
# LOCUTOR POR FRASES
# ==========================================
def test_locutor_agrupa_en_frases_completas():
    habla = HablaFalsa()
    locutor = _LocutorPorFrases(habla)
    for trozo in ["Hola, ", "Ruth. ", "Hoy hace un día ", "estupendo para programar. ", "¿Empezamos", "?"]:
        locutor.recibir(trozo)
    locutor.terminar()
    assert habla.dicho == ["Hola, Ruth. Hoy hace un día estupendo para programar.", "¿Empezamos?"]


# ==========================================
# MÚSICA (se mira y se controla de verdad; nunca se supone)
# ==========================================
def test_que_suena_se_lee_de_verdad_sin_el_modelo(jarvis):
    respuestas = jarvis.procesar("¿qué canción está sonando?")
    assert jarvis.herramientas.musica == [("sonando", "")]
    assert respuestas[0]["tipo"] == "musica"
    assert respuestas[0]["texto"] == "En Spotify está en pausa «MONEY MACHINE» de Nicki Nicole."
    assert respuestas[0]["datos"]["sesion"]["artista"] == "Nicki Nicole"
    assert jarvis.cerebro.llamadas == []                 # el modelo no puede inventárselo


def test_sin_musica_abierta_lo_dice(jarvis):
    jarvis.herramientas.sesion = None
    respuestas = jarvis.procesar("¿qué suena?")
    assert respuestas[0]["tipo"] == "aviso"
    assert respuestas[0]["texto"] == "No hay ninguna música abierta ahora mismo."


def test_reanudar_y_pausar_son_ordenes_explicitas(jarvis):
    respuestas = jarvis.procesar("podés reproducir la música")
    assert jarvis.herramientas.musica == [("reproducir", "")]
    assert respuestas[0]["tipo"] == "musica" and "MONEY MACHINE" in respuestas[0]["texto"]
    jarvis.procesar("pausa la música")
    assert jarvis.herramientas.musica[-1] == ("pausar", "")
    assert [a["descripcion"] for a in jarvis.seguridad.ultimas_acciones(2)] == ["Pausaste la música",
                                                                                "Reanudaste la música"]


def test_reanudar_sin_musica_abierta_no_finge(jarvis):
    jarvis.herramientas.sesion = None
    respuestas = jarvis.procesar("reanuda la música")
    assert respuestas[0]["tipo"] == "aviso"
    assert jarvis.seguridad.ultimas_acciones(5) == []    # no figura como hecho


def test_frase_real_abre_spotify_y_reanuda(jarvis):
    jarvis.procesar("Abrís, Spotify y Reproduce la última canción que estaba sonando.")
    assert acciones_pc(jarvis) == [("open_app", "Spotify")]
    assert jarvis.herramientas.musica == [("reproducir", "")]


def test_poner_una_cancion(jarvis):
    respuestas = jarvis.procesar("pon la canción Despacito")
    assert jarvis.herramientas.musica == [("poner", "Despacito", "youtube")]
    assert respuestas[0]["tipo"] == "musica" and respuestas[0]["texto"] == "Sonando «Despacito (vídeo)» en YouTube."
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Pusiste: Despacito"
    assert jarvis.cerebro.llamadas == []


def test_el_servicio_de_musica_se_elige_en_ajustes(jarvis):
    jarvis.ajustes.establecer("musica.servicio", "spotify")
    jarvis.procesar("pon algo de Queen")
    jarvis.procesar("reproduce Bohemian Rhapsody en YouTube")    # o se dice en la frase
    assert jarvis.herramientas.musica == [("poner", "Queen", "spotify"), ("poner", "Bohemian Rhapsody", "youtube")]


def test_cancion_no_encontrada(jarvis):
    respuestas = jarvis.procesar("pon la canción nada")
    assert respuestas[0]["tipo"] == "aviso" and "No he encontrado" in respuestas[0]["texto"]
    assert jarvis.seguridad.ultimas_acciones(5) == []


def test_pon_algo_que_no_es_una_aplicacion_es_una_cancion(jarvis):
    respuestas = jarvis.procesar("pon inexistente")
    assert acciones_pc(jarvis) == [("open_app", "inexistente")]          # primero mira si es un programa
    assert jarvis.herramientas.musica == [("poner", "inexistente", "youtube")]
    assert respuestas[0]["tipo"] == "musica"


def test_pon_una_aplicacion_la_abre(jarvis):
    respuestas = jarvis.procesar("pon Spotify")
    assert acciones_pc(jarvis) == [("open_app", "Spotify")]
    assert getattr(jarvis.herramientas, "musica", []) == []
    assert respuestas[0]["texto"] == "He abierto Spotify."


def test_abre_algo_inexistente_no_pone_musica(jarvis):
    jarvis.procesar("abre inexistente")
    assert getattr(jarvis.herramientas, "musica", []) == []


def test_musica_desactivada(jarvis):
    jarvis.ajustes.establecer("herramientas.musica", False)
    respuestas = jarvis.procesar("¿qué canción suena?")
    assert "desactivada" in respuestas[0]["texto"]
    jarvis.procesar("pon inexistente")
    assert getattr(jarvis.herramientas, "musica", []) == []


# ==========================================
# FUNCIONES QUE PIDE LA IA (para no inventarse nada)
# ==========================================
def test_el_modelo_recibe_las_funciones_de_las_herramientas_activas(jarvis):
    jarvis.procesar("explícame una cosa")
    funciones = jarvis.cerebro.llamadas[0]["funciones"]
    assert {"buscar_en_internet", "que_esta_sonando", "reproducir_musica", "buscar_en_memoria"} <= set(funciones)
    jarvis.ajustes.establecer("herramientas.busqueda", False)
    jarvis.procesar("explícame otra cosa")
    funciones = jarvis.cerebro.llamadas[1]["funciones"]
    assert "buscar_en_internet" not in funciones and "leer_pagina" not in funciones
    assert "que_esta_sonando" in funciones


def test_el_modelo_busca_en_internet_y_se_ve_lo_que_ha_encontrado(jarvis):
    jarvis.cerebro.pedir = [("buscar_en_internet", {"consulta": "ganador mundial 2026"})]
    respuestas = jarvis.procesar("háblame del último mundial")
    assert jarvis.herramientas.busquedas == [("ganador mundial 2026", "web")]
    assert "Título uno" in jarvis.cerebro.resultados_funciones[0]          # el modelo recibe los datos reales
    assert respuestas[0]["tipo"] == "busqueda"
    assert respuestas[0]["datos"]["resultados"][0]["url"] == "https://ejemplo.es/1"
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Buscaste: ganador mundial 2026"


def test_busqueda_de_la_ia_sin_conexion(jarvis):
    jarvis.herramientas.fallo_busqueda = True
    jarvis.cerebro.pedir = [("buscar_en_internet", {"consulta": "algo"})]
    jarvis.procesar("explícame una cosa")
    assert "no has podido buscarlo" in jarvis.cerebro.resultados_funciones[0]


def test_el_modelo_mira_que_suena_en_vez_de_inventarlo(jarvis):
    jarvis.cerebro.pedir = [("que_esta_sonando", {})]
    respuestas = jarvis.procesar("explícame una cosa")
    assert "MONEY MACHINE" in jarvis.cerebro.resultados_funciones[0]
    assert respuestas[0]["tipo"] == "musica" and respuestas[0]["datos"]["sesion"]["titulo"] == "MONEY MACHINE"


def test_el_modelo_puede_poner_y_controlar_musica(jarvis):
    jarvis.cerebro.pedir = [("reproducir_musica", {"consulta": "Queen"}), ("controlar_musica", {"accion": "pausar"})]
    respuestas = jarvis.procesar("explícame una cosa")
    assert jarvis.herramientas.musica == [("poner", "Queen", "youtube"), ("pausar", "")]
    assert [a["descripcion"] for a in jarvis.seguridad.ultimas_acciones(2)] == ["Pausaste la música", "Pusiste: Queen"]
    assert respuestas[0]["tipo"] == "musica"


def test_el_modelo_consulta_el_tiempo(jarvis):
    jarvis.ajustes.establecer("internet.ciudad", "Valencia")
    jarvis.cerebro.pedir = [("consultar_tiempo", {"dia": "mañana"})]
    respuestas = jarvis.procesar("explícame una cosa")
    assert respuestas[0]["tipo"] == "tiempo"
    assert (respuestas[0]["datos"]["ciudad"], respuestas[0]["datos"]["dia"]) == ("Valencia", "manana")


def test_el_modelo_busca_en_la_memoria(jarvis):
    jarvis.memoria.guardar_recuerdo("mi color favorito es el morado")
    jarvis.cerebro.pedir = [("buscar_en_memoria", {"consulta": "color favorito"}),
                            ("buscar_en_memoria", {"consulta": "agujeros negros"})]
    jarvis.procesar("explícame una cosa")
    assert "morado" in jarvis.cerebro.resultados_funciones[0]
    assert jarvis.cerebro.resultados_funciones[1] == "No hay recuerdos sobre eso."


def test_el_modelo_no_puede_abrir_nada_delicado(jarvis):
    jarvis.cerebro.pedir = [("abrir_aplicacion", {"nombre": "PowerShell"}), ("abrir_aplicacion", {"nombre": "Spotify"})]
    jarvis.procesar("explícame una cosa")
    assert jarvis.cerebro.resultados_funciones[0].startswith("Rechazado")
    assert acciones_pc(jarvis) == [("open_app", "Spotify")]                 # solo lo que no es delicado
    assert jarvis.pendiente is None


def test_funciones_desconocidas_desactivadas_o_mal_formadas(jarvis):
    jarvis.ajustes.establecer("herramientas.musica", False)
    jarvis.cerebro.pedir = [("borrar_todo", {}), ("que_esta_sonando", {}), ("buscar_en_internet", "{no es json"),
                            ("controlar_musica", {"accion": "bailar"})]
    jarvis.procesar("explícame una cosa")
    assert jarvis.cerebro.resultados_funciones == [
        "Error: esa función no existe.", "Error: esa herramienta está desactivada.",
        "Error: la consulta está vacía.", "Error: esa herramienta está desactivada."]
    assert getattr(jarvis.herramientas, "musica", []) == []


def test_argumentos_en_texto_json(jarvis):
    jarvis.cerebro.pedir = [("reproducir_musica", '{"consulta": "Queen"}')]
    jarvis.procesar("explícame una cosa")
    assert jarvis.herramientas.musica == [("poner", "Queen", "youtube")]


# ==========================================
# APRENDER DE LO QUE SE HABLA
# ==========================================
@pytest.fixture
def aprende(jarvis):
    jarvis.ajustes.establecer("memoria.aprender_automaticamente", True)
    return jarvis


def test_aprende_sola_lo_que_cuenta_de_si_misma(aprende):
    aprende.cerebro.datos_extraidos = ["me gusta el rock", "tengo un gato que se llama Mishi"]
    respuestas = aprende.procesar("me encanta el rock y tengo un gato que se llama Mishi")
    assert respuestas[0]["texto"] == aprende.cerebro.respuesta
    assert "sugerir_recuerdo" not in respuestas[0]["datos"] and aprende.pendiente is None   # no pregunta
    assert aprende.cerebro.extracciones == ["me encanta el rock y tengo un gato que se llama Mishi"]
    recuerdos = aprende.memoria.listar_recuerdos()
    assert sorted(r["texto"] for r in recuerdos) == ["me gusta el rock", "tengo un gato que se llama Mishi"]
    assert {r["origen"] for r in recuerdos} == {"aprendido"}
    aviso = [e["datos"] for e in aprende.eventos if e["tipo"] == "aprendido"]
    assert aviso == [{"textos": ["me gusta el rock", "tengo un gato que se llama Mishi"]}]   # se avisa en pantalla
    assert aprende.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Aprendí algo sobre ti"


def test_solo_intenta_aprender_si_habla_de_si_misma(aprende):
    aprende.cerebro.datos_extraidos = ["me gusta el rock"]
    aprende.procesar("cuéntame un chiste")
    aprende.procesar("¿cuál es la capital de Francia?")
    assert aprende.cerebro.extracciones == []
    assert aprende.memoria.contar() == 0


def test_no_aprende_datos_sensibles(aprende):
    aprende.cerebro.datos_extraidos = ["mi contraseña es gato123"]
    aprende.procesar("me encanta pasear por la playa")
    assert aprende.memoria.contar() == 0
    aprende.procesar("mi contraseña es gato123 y me gusta el rock")       # ni se le pasa al modelo
    assert aprende.cerebro.extracciones == ["me encanta pasear por la playa"]


def test_no_repite_lo_que_ya_sabe(aprende):
    aprende.memoria.guardar_recuerdo("me gusta el rock")
    aprende.cerebro.datos_extraidos = ["Me gusta el rock", "me gusta el rock."]
    aprende.procesar("ya sabes que me gusta el rock")
    assert aprende.memoria.contar() == 1
    assert [e for e in aprende.eventos if e["tipo"] == "aprendido"] == []


def test_como_mucho_tres_datos_por_mensaje(aprende):
    aprende.cerebro.datos_extraidos = ["me gusta el rock", "vivo en Valencia", "tengo un perro", "estudio DAM"]
    aprende.procesar("me gusta el rock, vivo en Valencia, tengo un perro y estudio DAM")
    assert aprende.memoria.contar() == 3


def test_con_el_aprendizaje_desactivado_pregunta_antes_de_guardar(jarvis):
    jarvis.cerebro.datos_extraidos = ["me gusta el anime"]
    respuestas = jarvis.procesar("Me gusta el anime")
    assert respuestas[0]["datos"]["sugerir_recuerdo"] == "Me gusta el anime"
    assert jarvis.cerebro.extracciones == [] and jarvis.memoria.contar() == 0


def test_sin_la_herramienta_de_memoria_no_aprende(aprende):
    aprende.ajustes.establecer("herramientas.memoria", False)
    aprende.cerebro.datos_extraidos = ["me gusta el rock"]
    aprende.procesar("me gusta mucho el rock")
    assert aprende.cerebro.extracciones == [] and aprende.memoria.contar() == 0


def test_si_falla_la_extraccion_la_conversacion_sigue(aprende):
    def fallar(texto, nombre="la usuaria"):
        raise RuntimeError("Ollama caído")
    aprende.cerebro.extraer_datos = fallar
    respuestas = aprende.procesar("me gusta mucho el rock")
    assert respuestas[0]["texto"] == aprende.cerebro.respuesta
    assert aprende.memoria.contar() == 0


def test_aprende_en_segundo_plano_sin_retrasar_la_respuesta(aprende):
    import threading
    aprende.aprender_en_segundo_plano = True
    aprende.cerebro.datos_extraidos = ["me gusta el rock"]
    aprende.procesar("me gusta mucho el rock")
    for hilo in threading.enumerate():
        if hilo.name == "jarvis-aprender":
            hilo.join(5)
    assert [r["texto"] for r in aprende.memoria.listar_recuerdos()] == ["me gusta el rock"]


# ==========================================
# PREGUNTAS POR ALGUIEN CON NOMBRE PROPIO (se busca antes de preguntar al modelo)
# ==========================================
@pytest.mark.parametrize("frase,nombre", [
    ("estoy escuchando a Nicki Nicole, ¿la conoces?", "Nicki Nicole"), ("¿Quién es Nicki Nicole?", "Nicki Nicole"),
    ("Jarvis, ¿quién canta Money Machine?", "Money Machine"), ("¿De dónde es Rosalía?", "Rosalía"),
    ("¿Te suena Bizarrap?", "Bizarrap"), ("Cuéntame un chiste", ""), ("¿Quién es Ruth?", ""),
    ("quien es nicki nicole", ""), ("¿Qué hora es en Madrid?", ""), ("Jarvis", "")])
def test_nombre_por_el_que_pregunta(frase, nombre):
    from core.funciones import nombre_por_el_que_pregunta
    assert nombre_por_el_que_pregunta(frase, excluir=("Ruth",)) == nombre


def test_pregunta_por_alguien_busca_antes_de_preguntar_al_modelo(jarvis):
    # Con un modelo pequeño, si cree saberlo responde de memoria (decía que Nicki Nicole es española).
    respuestas = jarvis.procesar("estoy escuchando a Nicki Nicole, ¿la conoces?")
    assert jarvis.herramientas.busquedas == [("Nicki Nicole", "web")]
    assert jarvis.cerebro.llamadas[0]["previas"] == [("buscar_en_internet", {"consulta": "Nicki Nicole"})]
    assert respuestas[0]["tipo"] == "busqueda"                            # se ven las fuentes
    assert respuestas[0]["sugerencias"] == []                            # ya ha buscado: sin «Buscar en Internet»
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Buscaste: Nicki Nicole"


def test_no_busca_a_las_personas_de_su_vida(jarvis):
    jarvis.memoria.guardar_recuerdo("mi profesora de programación se llama Marta")
    jarvis.procesar("¿Conoces a Marta?")
    assert jarvis.herramientas.busquedas == [] and jarvis.cerebro.llamadas[0]["previas"] == []


def test_sin_la_herramienta_de_busqueda_no_busca_antes(jarvis):
    jarvis.ajustes.establecer("herramientas.busqueda", False)
    jarvis.procesar("¿Quién es Nicki Nicole?")
    assert jarvis.herramientas.busquedas == []


def test_una_charla_normal_no_busca(jarvis):
    jarvis.procesar("explícame una cosa")
    assert jarvis.herramientas.busquedas == [] and jarvis.cerebro.llamadas[0]["previas"] == []


# ==========================================
# RECORDATORIOS, TEMPORIZADORES, ALARMAS Y AGENDA
# ==========================================
def test_temporizador(jarvis):
    respuestas = jarvis.procesar("pon un temporizador de 10 minutos")
    assert respuestas[0]["tipo"] == "recordatorio" and respuestas[0]["texto"] == "Temporizador de 10 minutos en marcha."
    pendiente = jarvis.recordatorios.pendientes()[0]
    assert pendiente["tipo"] == "temporizador" and pendiente["texto"] == "10 minutos"
    assert jarvis.cerebro.llamadas == []                     # no pasa por el modelo
    assert jarvis.procesar("¿cuánto le queda al temporizador?")[0]["texto"].startswith("Quedan ")


def test_recordatorio_con_fecha_y_texto(jarvis):
    respuestas = jarvis.procesar("recuérdame mañana a las 9 llamar al médico")
    assert respuestas[0]["texto"] == "Te lo recordaré mañana a las 9:00: «llamar al médico»."
    assert respuestas[0]["datos"]["recordatorios"][0]["cuando"] == "mañana a las 9:00"
    assert jarvis.seguridad.ultimas_acciones(1)[0]["herramienta"] == "recordatorios"
    lista = jarvis.procesar("¿qué tengo mañana?")[0]
    assert "llamar al médico (mañana a las 9:00)" in lista["texto"]
    assert jarvis.procesar("borra el recordatorio de llamar al médico")[0]["texto"].startswith("Cancelado: «llamar")
    assert jarvis.recordatorios.pendientes() == []


def test_si_falta_algo_lo_pregunta(jarvis):
    assert "¿Para cuándo?" in jarvis.procesar("recuérdame llamar a mamá")[0]["texto"]
    assert "¿Qué quieres que te recuerde?" in jarvis.procesar("recuérdame mañana a las 9")[0]["texto"]
    assert "¿A qué hora" in jarvis.procesar("pon una alarma")[0]["texto"]
    assert jarvis.recordatorios.pendientes() == []
    # «avísame en 10 minutos», sin más, es un temporizador
    assert jarvis.procesar("avísame en 10 minutos")[0]["texto"] == "Temporizador de 10 minutos en marcha."


def test_el_aviso_suena_aunque_este_en_modo_texto_y_se_puede_posponer(jarvis):
    import datetime as dt
    alarma = jarvis.recordatorios.crear("alarma", "", dt.datetime.now())
    jarvis.avisar([alarma])
    assert jarvis.habla.dicho and "Tu alarma" in jarvis.habla.dicho[-1]       # en modo texto también
    assert [e for e in jarvis.eventos if e["tipo"] == "aviso"]
    mensaje = [e["datos"] for e in jarvis.eventos if e["tipo"] == "mensaje"][-1]
    assert mensaje["tipo"] == "recordatorio" and mensaje["datos"]["aviso"] is True
    assert jarvis.procesar("dame 5 minutos más")[0]["texto"] == "Vale, te aviso otra vez en 5 minutos."
    assert jarvis.recordatorios.pendientes()[0]["tipo"] == "alarma"
    assert "No hay ninguna alarma" in jarvis.procesar("pospón la alarma")[0]["texto"]   # ya se pospuso


def test_avisos_perdidos_en_un_solo_mensaje(jarvis):
    import datetime as dt
    ayer = dt.datetime.now() - dt.timedelta(days=1)
    perdidos = [jarvis.recordatorios.crear("recordatorio", texto, ayer) for texto in ("regar", "llamar")]
    jarvis.avisar(perdidos, perdidos=True)
    assert jarvis.habla.dicho[-1].startswith("Mientras no estabas se pasaron 2 avisos: regar")


def test_la_ia_puede_crear_recordatorios(jarvis):
    jarvis.cerebro.pedir = [("crear_recordatorio", {"cuando": "en 2 horas", "que": "regar las plantas"})]
    respuestas = jarvis.procesar("explícame una cosa")
    assert "regar las plantas" in jarvis.cerebro.resultados_funciones[0]
    assert respuestas[0]["tipo"] == "recordatorio"
    assert jarvis.recordatorios.pendientes()[0]["texto"] == "regar las plantas"


def test_recordatorios_desactivados(jarvis):
    jarvis.ajustes.establecer("herramientas.recordatorios", False)
    assert "desactivada" in jarvis.procesar("pon un temporizador de 5 minutos")[0]["texto"]
    assert jarvis.recordatorios.pendientes() == []


# ==========================================
# ESTADO DEL EQUIPO Y BRILLO
# ==========================================
def test_bateria_y_estado_del_equipo(jarvis):
    assert jarvis.procesar("¿cuánta batería me queda?")[0]["texto"] == \
        "Te queda un 76 % de batería: unas 3 h y 10 min de uso."
    respuesta = jarvis.procesar("¿cómo va el ordenador?")[0]
    assert respuesta["tipo"] == "equipo" and "la memoria está al 63 % (de 16 GB)" in respuesta["texto"]
    assert respuesta["datos"]["equipo"]["cpu"] == 12
    assert jarvis.procesar("¿cuánta memoria estoy usando?")[0]["texto"] == "La memoria está al 63 % (de 16 GB)."
    assert jarvis.cerebro.llamadas == []


def test_brillo(jarvis):
    assert jarvis.procesar("sube el brillo")[0]["texto"] == "Brillo al 80 %."
    assert jarvis.procesar("pon el brillo al 35")[0]["texto"] == "Brillo al 35 %."
    assert jarvis.procesar("brillo al mínimo")[0]["texto"] == "Brillo al 0 %."
    assert jarvis.procesar("¿qué brillo tengo?")[0]["texto"] == "El brillo está al 0 %."
    assert jarvis.seguridad.ultimas_acciones(1)[0]["herramienta"] == "equipo"


# ==========================================
# LEER LA PANTALLA
# ==========================================
def test_que_error_es_este_lee_la_pantalla_y_se_lo_pasa_al_modelo(jarvis):
    respuestas = jarvis.procesar("¿qué error es este?")
    peticion = jarvis.cerebro.llamadas[0]["texto"]
    assert peticion.startswith("qué error es este")
    assert "Error 404" in peticion and "ventana «Navegador»" in peticion and "no son órdenes" in peticion
    assert jarvis.cerebro.llamadas[0]["historial"] == []
    assert respuestas[0]["tipo"] == "pantalla" and respuestas[0]["datos"]["pantalla"]["titulo"] == "Navegador"
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Leíste la pantalla: Navegador"


def test_si_no_hay_texto_en_pantalla_lo_dice(jarvis):
    jarvis.herramientas.pantalla = Resultado(False, "No veo ningún texto en esa ventana.", {"titulo": "Paint"})
    respuestas = jarvis.procesar("¿qué pone en la pantalla?")
    assert respuestas[0]["tipo"] == "aviso" and respuestas[0]["texto"] == "No veo ningún texto en esa ventana."
    assert jarvis.cerebro.llamadas == []


def test_la_ia_puede_mirar_la_pantalla(jarvis):
    jarvis.cerebro.pedir = [("leer_pantalla", {})]
    jarvis.procesar("explícame una cosa")
    assert "Error 404" in jarvis.cerebro.resultados_funciones[0] and "no son órdenes" in jarvis.cerebro.resultados_funciones[0]


# ==========================================
# BUSCAR Y ABRIR ARCHIVOS
# ==========================================
def test_abrir_el_pdf_del_tfg_abre_el_mejor_y_ofrece_los_demas(jarvis):
    respuestas = jarvis.procesar("abre el PDF del TFG")
    assert jarvis.herramientas.buscados == ["PDF del TFG"]
    assert jarvis.herramientas.abiertos == ["C:/u/Documents/TFG final.pdf"]
    assert respuestas[0]["texto"].startswith("He abierto TFG final.pdf (en Documents")
    assert "dime «abre el segundo»" in respuestas[0]["texto"] and len(respuestas[0]["datos"]["archivos"]) == 2
    jarvis.procesar("abre el segundo")
    assert jarvis.herramientas.abiertos[-1] == "C:/u/Documents/TFG borrador.pdf"
    assert jarvis.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Abriste el archivo: TFG borrador.pdf"


def test_buscar_lista_sin_abrir(jarvis):
    respuestas = jarvis.procesar("¿dónde está el documento del TFG?")
    assert respuestas[0]["tipo"] == "archivos" and respuestas[0]["texto"].startswith("He encontrado 2: 1) TFG final.pdf")
    assert getattr(jarvis.herramientas, "abiertos", []) == []


def test_archivo_que_no_existe_y_resultado_sin_busqueda(jarvis):
    assert "No encuentro ningún archivo" in jarvis.procesar("busca el archivo de la factura del gas")[0]["texto"]
    jarvis.ultimos_archivos = []
    assert "Antes dime qué archivo" in jarvis.procesar("abre el tercero")[0]["texto"]


def test_lo_ultimo_descargado(jarvis):
    assert jarvis.procesar("¿qué es lo último que he descargado?")[0]["texto"].startswith(
        "Lo último que descargaste es factura.pdf")
    jarvis.procesar("abre lo último que he descargado")
    assert jarvis.herramientas.abiertos == ["C:/u/Downloads/factura.pdf"]


def test_la_ia_puede_buscar_y_abrir_archivos(jarvis):
    jarvis.cerebro.pedir = [("buscar_archivos", {"consulta": "TFG"}), ("abrir_archivo", {"numero": 1})]
    jarvis.procesar("explícame una cosa")
    assert jarvis.cerebro.resultados_funciones[0].startswith("1) TFG final.pdf")
    assert jarvis.herramientas.abiertos == ["C:/u/Documents/TFG final.pdf"]


# ==========================================
# RESUMEN DE BUENOS DÍAS
# ==========================================
def test_buenos_dias_da_el_resumen_una_vez_al_dia(jarvis):
    import datetime as dt
    jarvis.ajustes.establecer("internet.ciudad", "Valencia")
    fin_de_hoy = dt.datetime.combine(dt.date.today(), dt.time(23, 59, 59))
    jarvis.recordatorios.crear("cita", "dentista", min(dt.datetime.now() + dt.timedelta(minutes=30), fin_de_hoy))
    respuesta = jarvis.procesar("Buenos días, Jarvis")[0]
    texto = respuesta["texto"]
    assert texto.startswith("Buenos días") and "Hoy es " in texto
    assert "Ahora mismo en Valencia hay 22 grados" in texto                 # el tiempo
    assert "dentista" in texto                                               # la agenda de hoy
    assert texto.endswith("Titulares: Título uno.")                          # las noticias
    assert jarvis.herramientas.busquedas == [("noticias de hoy en España", "noticias")]
    assert respuesta["tipo"] == "resumen" and respuesta["datos"]["recordatorios"][0]["texto"] == "dentista"
    assert jarvis.cerebro.llamadas == []                                     # sin el modelo: nada inventado
    jarvis.procesar("buenos días")                                           # la segunda vez es un saludo
    assert len(jarvis.cerebro.llamadas) == 1


def test_resumen_a_peticion_sin_ciudad_ni_titulares(jarvis):
    jarvis.ajustes.establecer("resumen.noticias", False)
    texto = jarvis.procesar("dame el resumen del día")[0]["texto"]
    assert "No tienes nada apuntado para hoy." in texto and "Titulares" not in texto and "grados" not in texto
    assert jarvis.herramientas.busquedas == []


def test_resumen_automatico_al_arrancar(jarvis):
    jarvis.resumen_automatico()
    assert jarvis.habla.dicho and "Hoy es " in jarvis.habla.dicho[-1]        # se oye aunque sea modo texto
    dicho = len(jarvis.habla.dicho)
    jarvis.resumen_automatico()
    assert len(jarvis.habla.dicho) == dicho                                  # una vez al día


# ==========================================
# HERRAMIENTAS MCP
# ==========================================
@pytest.fixture
def con_mcp(jarvis, tmp_path):
    import json
    import sys
    from pathlib import Path
    from core.mcp import GestorMCP
    ruta = tmp_path / "mcp.json"
    servidor = str(Path(__file__).with_name("servidor_mcp_falso.py"))
    ruta.write_text(json.dumps({"servidores": {"notas": {"comando": sys.executable, "argumentos": [servidor]}}}),
                    encoding="utf-8")
    gestor = GestorMCP(ruta)
    gestor.arrancar()
    jarvis.mcp = gestor
    yield jarvis
    gestor.cerrar()


def test_el_modelo_recibe_y_usa_herramientas_mcp(con_mcp):
    con_mcp.cerebro.pedir = [("mcp_notas_hora", {}), ("mcp_notas_borrar_todo", {})]
    con_mcp.procesar("explícame una cosa")
    funciones = con_mcp.cerebro.llamadas[0]["funciones"]
    assert "mcp_notas_hora" in funciones and "mcp_notas_borrar_todo" not in funciones
    assert con_mcp.cerebro.resultados_funciones[0] == "Son las 10:30."
    assert con_mcp.cerebro.resultados_funciones[1].startswith("Rechazado")
    assert con_mcp.seguridad.ultimas_acciones(1)[0]["descripcion"] == "Herramienta MCP: notas_hora"


def test_mcp_desactivado(con_mcp):
    con_mcp.ajustes.establecer("herramientas.mcp", False)
    con_mcp.cerebro.pedir = [("mcp_notas_hora", {})]
    con_mcp.procesar("explícame una cosa")
    assert not any(f.startswith("mcp_") for f in con_mcp.cerebro.llamadas[0]["funciones"])
    assert con_mcp.cerebro.resultados_funciones[0] == "Error: esa herramienta está desactivada."
