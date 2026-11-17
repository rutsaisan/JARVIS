"""
Pruebas de los recordatorios, temporizadores, alarmas y la agenda (tools/recordatorios.py):
fechas en español, almacén, avisador, calendario iCal y las órdenes que los crean.

Ejecutar con:  pytest tests/test_recordatorios.py -v
"""
import datetime as dt
import os
import tempfile

import pytest

from core.database import BaseDatos
from tools import recordatorios as r
from tools.intent_engine import interpretar

AHORA = dt.datetime(2026, 11, 16, 15, 30)          # lunes, 15:30


def momento(texto):
    m = r.interpretar_momento(texto, AHORA)
    return (m.cuando.strftime("%d/%m %H:%M"), r.separar(texto, m)) if m else None


# ==========================================
# FECHAS EN ESPAÑOL
# ==========================================
@pytest.mark.parametrize("texto,esperado", [
    ("mañana a las 9 llamar al médico", ("17/11 09:00", "llamar al médico")),
    ("en 20 minutos de sacar la pizza", ("16/11 15:50", "sacar la pizza")),
    ("dentro de una hora y media mirar el horno", ("16/11 17:00", "mirar el horno")),
    ("que el jueves tengo dentista a las 5", ("19/11 17:00", "tengo dentista")),          # «a las 5»: tarde
    ("a las 7 y media de la tarde ir al gimnasio", ("16/11 19:30", "ir al gimnasio")),
    ("esta noche ver la peli", ("16/11 21:00", "ver la peli")),
    ("el 24 de diciembre a las 10 comprar turrón", ("24/12 10:00", "comprar turrón")),
    ("pasado mañana por la mañana entregar el TFG", ("18/11 09:00", "entregar el TFG")),
    ("mañana por la tarde", ("17/11 17:00", "")),
    ("a las 9", ("16/11 21:00", "")),                    # son las 15:30: la próxima vez que sean las 9
    ("a las 14:45 reunión", ("17/11 14:45", "reunión")),  # ya ha pasado hoy: mañana
    ("a las 12 de la noche", ("17/11 00:00", "")),
    ("el lunes a las 8 clase", ("23/11 08:00", "clase")),  # hoy es lunes y ya pasó: el que viene
    ("a las 9 menos cuarto", ("16/11 20:45", "")),
    ("a las nueve y cuarto de la mañana", ("17/11 09:15", "")),
    ("el 3/12 a las 18:00", ("03/12 18:00", "")),
    ("a medianoche", ("17/11 00:00", "")),
])
def test_entiende_fechas(texto, esperado):
    assert momento(texto) == esperado


@pytest.mark.parametrize("texto", ["llamar a mamá", "comprar pan", "", "la reunión de las cosas"])
def test_sin_fecha(texto):
    assert r.interpretar_momento(texto, AHORA) is None


def test_duraciones():
    assert r.segundos_de("1 hora y 20 minutos") == 4800
    assert r.segundos_de("media hora") == 1800 and r.segundos_de("un cuarto de hora") == 900
    assert r.segundos_de("2 minutos y 30 segundos") == 150 and r.segundos_de("nada") == 0
    assert r.describir_duracion(4800) == "1 hora y 20 minutos"
    assert r.describir_duracion(61) == "1 minuto y 1 segundo"


def test_como_se_dice_cada_momento():
    assert r.describir_momento(dt.datetime(2026, 11, 16, 17, 0), AHORA) == "hoy a las 17:00"
    assert r.describir_momento(dt.datetime(2026, 11, 17, 9, 0), AHORA) == "mañana a las 9:00"
    assert r.describir_momento(dt.datetime(2026, 11, 19, 17, 0), AHORA) == "el jueves 19 a las 17:00"
    assert r.describir_momento(dt.datetime(2027, 1, 6, 10, 0), AHORA) == "el 6 de enero de 2027 a las 10:00"


def test_rangos_de_la_agenda():
    assert r.rango_de("hoy", AHORA)[2] == "hoy" and r.rango_de("mañana", AHORA)[0] == dt.datetime(2026, 11, 17)
    assert r.rango_de("el jueves", AHORA)[2] == "el jueves 19"
    desde, hasta, nombre = r.rango_de("la semana que viene", AHORA)
    assert (desde, hasta, nombre) == (dt.datetime(2026, 11, 23), dt.datetime(2026, 11, 30), "la semana que viene")


# ==========================================
# ALMACÉN Y AVISADOR
# ==========================================
@pytest.fixture
def almacen():
    carpeta = tempfile.mkdtemp(prefix="jarvis_test_recordatorios_")
    yield r.Recordatorios(BaseDatos(os.path.join(carpeta, "jarvis.db")))


def test_crear_listar_y_cancelar(almacen):
    medico = almacen.crear("recordatorio", "llamar al médico", AHORA + dt.timedelta(hours=1))
    almacen.crear("alarma", "", AHORA + dt.timedelta(days=1))
    almacen.crear("alarma", "", AHORA + dt.timedelta(days=2))
    assert medico["cuando"] == AHORA + dt.timedelta(hours=1)
    assert [x["tipo"] for x in almacen.pendientes()] == ["recordatorio", "alarma", "alarma"]
    assert [x["texto"] for x in almacen.del_dia(AHORA.date())] == ["llamar al médico"]
    assert [x["id"] for x in almacen.cancelar(texto="medico")] == [medico["id"]]      # sin tildes también
    assert len(almacen.cancelar("alarma", todos=True)) == 2 and almacen.pendientes() == []
    with pytest.raises(ValueError):
        almacen.crear("cumpleaños", "", AHORA)


def test_el_avisador_avisa_una_vez_y_agrupa_los_perdidos(almacen):
    ahora = dt.datetime.now()
    almacen.crear("recordatorio", "ayer 1", ahora - dt.timedelta(days=1))
    almacen.crear("recordatorio", "ayer 2", ahora - dt.timedelta(hours=20))
    almacen.crear("temporizador", "10 minutos", ahora - dt.timedelta(seconds=1))
    almacen.crear("alarma", "", ahora + dt.timedelta(hours=1))
    avisos = []
    avisador = r.Avisador(almacen, lambda lista, perdidos: avisos.append(([x["texto"] for x in lista], perdidos)))
    avisador.comprobar()
    assert avisos == [(["ayer 1", "ayer 2"], True), (["10 minutos"], False)]
    avisador.comprobar()
    assert len(avisos) == 2                                   # nunca dos veces
    assert [x["tipo"] for x in almacen.pendientes()] == ["alarma"]


# ==========================================
# CALENDARIO iCal
# ==========================================
ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:abc@google.com\r
DTSTART:20261119T160000Z\r
SUMMARY:Dentista\\, revisión\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:xyz@google.com\r
DTSTART;VALUE=DATE:20261124\r
SUMMARY:Entrega del T\r
 FG\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cancelado@google.com\r
DTSTART:20261120T100000\r
SUMMARY:Ya no\r
STATUS:CANCELLED\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_leer_un_calendario_ics():
    eventos = r.leer_ics(ICS)
    assert [e["titulo"] for e in eventos] == ["Dentista, revisión", "Entrega del TFG"]
    assert eventos[1]["inicio"] == dt.datetime(2026, 11, 24, 9, 0)              # día entero: a las 9
    utc = dt.datetime(2026, 11, 19, 16, 0, tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    assert eventos[0]["inicio"] == utc                                          # a la hora local


def test_sincronizar_el_calendario(almacen):
    futuro = dt.datetime.now() + dt.timedelta(days=3)
    eventos = [{"uid": "a", "titulo": "Dentista", "inicio": futuro}, {"uid": "b", "titulo": "Clase", "inicio": futuro}]
    assert almacen.sincronizar_calendario(eventos, antelacion_min=15) == 2
    cita = almacen.pendientes()[0]
    assert cita["tipo"] == "cita" and cita["origen"] == "calendario"
    assert cita["cuando"] == (futuro - dt.timedelta(minutes=15)).replace(microsecond=cita["cuando"].microsecond)
    # Al volver a leerlo: lo que ya estaba no se repite y lo que ha desaparecido se quita.
    assert almacen.sincronizar_calendario(eventos[:1]) == 0
    assert [x["texto"] for x in almacen.pendientes()] == ["Dentista"]


# ==========================================
# ÓRDENES
# ==========================================
@pytest.mark.parametrize("frase,accion,tipo,objetivo", [
    ("recuérdame mañana a las 9 llamar al médico", "create", "recordatorio", "mañana a las 9 llamar al médico"),
    ("¿puedes recordarme el jueves que tengo dentista?", "create", "recordatorio", "el jueves que tengo dentista"),
    ("quiero que me recuerdes en una hora sacar la ropa", "create", "recordatorio", "en una hora sacar la ropa"),
    ("avísame en 10 minutos", "create", "recordatorio", "en 10 minutos"),
    ("despiértame a las 7", "create", "alarma", "a las 7"),
    ("ponme una alarma para mañana a las 7 y media", "create", "alarma", "mañana a las 7 y media"),
    ("pon un temporizador de 10 minutos", "create", "temporizador", "10 minutos"),
    ("apunta en mi agenda que el jueves a las 5 tengo dentista", "create", "cita", "el jueves a las 5 tengo dentista"),
    ("¿qué tengo hoy?", "list", "", "hoy"), ("¿qué tengo el jueves?", "list", "", "el jueves"),
    ("¿qué alarmas tengo?", "list", "alarma", ""), ("¿qué hay en mi agenda?", "list", "", ""),
    ("cancela la alarma", "cancel", "alarma", ""),
    ("borra el recordatorio de llamar al médico", "cancel", "recordatorio", "llamar al médico"),
    ("¿cuánto le queda al temporizador?", "status", "", ""),
    ("pospón la alarma", "snooze", "", ""), ("dame 5 minutos más", "snooze", "", "5 minutos"),
])
def test_ordenes_de_recordatorios(frase, accion, tipo, objetivo):
    i = interpretar(frase)
    assert (i["intent"], i["action"], i.get("tipo", ""), i["target"]) == ("reminder", accion, tipo, objetivo)


@pytest.mark.parametrize("frase", ["recuerda que mi cumpleaños es el 3 de mayo", "¿cuánto falta para Navidad?",
                                   "¿qué tengo que hacer para aprobar?", "dame un momento"])
def test_lo_que_no_es_un_recordatorio(frase):
    assert interpretar(frase)["intent"] != "reminder"
