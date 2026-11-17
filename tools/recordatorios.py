"""
Recordatorios, temporizadores, alarmas y agenda
================================================
Todo local, en la base de datos de JARVIS (tabla «recordatorios»):

    temporizador   «pon un temporizador de 10 minutos»
    alarma         «pon una alarma a las 7», «despiértame mañana a las 7 y media»
    recordatorio   «recuérdame mañana a las 9 llamar al médico», «avísame en 20 minutos de sacar la pizza»
    cita           «apunta que el jueves a las 5 tengo dentista»; «¿qué tengo hoy?»

Las fechas se entienden en español: «en 10 minutos», «dentro de una hora y media», «a las 9»,
«mañana a las 7 y media», «el jueves a las 17:30», «el 24 de diciembre», «esta tarde a las 6»,
«a las 8 de la tarde», «pasado mañana»...

La agenda también puede leer un calendario en formato iCal (.ics), por ejemplo la «dirección
secreta en formato iCal» de Google Calendar: solo lectura, sin cuentas ni contraseñas.

Si el ordenador estaba apagado a la hora de un aviso, JARVIS lo dice al arrancar.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from dataclasses import dataclass, field

from core.text import fecha_larga, hora_corta, plano

registro = logging.getLogger("jarvis.recordatorios")

TIPOS = ("temporizador", "alarma", "recordatorio", "cita")
HORA_POR_DEFECTO = 9                  # «mañana llamar al médico»: a las 9
HORA_DEL_PERIODO = {"manana": 9, "mediodia": 13, "tarde": 17, "noche": 21, "madrugada": 2}
DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
MAXIMO_FUTURO = dt.timedelta(days=3 * 366)

_NUMEROS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticinco": 25, "treinta": 30, "cuarenta": 40,
    "cuarenta y cinco": 45, "cincuenta": 50, "sesenta": 60, "noventa": 90,
}
_NUM = r"(?:\d+(?:[.,]\d+)?|" + "|".join(sorted(map(re.escape, _NUMEROS), key=len, reverse=True)) + r")"
_SEGUNDOS = {"segundo": 1, "seg": 1, "minuto": 60, "min": 60, "hora": 3600, "dia": 86400, "semana": 604800}

_TROZO_DURACION = (rf"(?:{_NUM}\s+(?:segundos?|seg|minutos?|min|horas?|dias?|semanas?)(?:\s+y\s+(?:media|cuarto))?"
                   r"|media\s+hora|(?:un\s+)?cuarto\s+de\s+hora|hora\s+y\s+media)")
DURACION = re.compile(rf"{_TROZO_DURACION}(?:\s*(?:,|y)\s*{_TROZO_DURACION})*")
_RELATIVO = re.compile(rf"\b(?:en|dentro\s+de)\s+(?:unos?\s+|unas?\s+)?(?P<duracion>{DURACION.pattern})")

_PERIODO = re.compile(r"\b(?:de|por)\s+la\s+(?P<p>manana|tarde|noche|madrugada)\b|\b(?:al|a)\s+(?P<m>mediodia|medianoche)\b")
_ESTE_PERIODO = re.compile(r"\b(?:esta|esa)\s+(?P<p>manana|tarde|noche|madrugada)\b")
_PASADO_MANANA = re.compile(r"\bpasado\s+manana\b")
_MANANA = re.compile(r"\bmanana\b")
_HOY = re.compile(r"\b(?:hoy|esta\s+noche)\b")
_SEMANA = re.compile(r"\b(?:el\s+|este\s+|el\s+proximo\s+|el\s+que\s+viene\s+)?(?P<d>lunes|martes|miercoles|jueves|"
                     r"viernes|sabado|domingo)(?:\s+que\s+viene|\s+proximo)?\b")
_FECHA = re.compile(r"\b(?:el\s+)?(?:dia\s+)?(?P<dia>\d{1,2})(?:\s+de\s+(?P<mes>" + "|".join(MESES) + r")"
                    r"|/(?P<mes_n>\d{1,2})(?:/(?P<anio>\d{2,4}))?)\b")
_HORA = re.compile(
    r"\ba\s+(?:la|las)\s+(?P<h>\d{1,2}|" + "|".join(sorted(_NUMEROS, key=len, reverse=True)) + r")"
    r"(?:\s*[:.h]\s*(?P<mm>\d{2})|\s+y\s+(?P<y>media|cuarto|\d{1,2}|" + "|".join(sorted(_NUMEROS, key=len, reverse=True))
    + r")|\s+menos\s+(?P<menos>cuarto|\d{1,2}|" + "|".join(sorted(_NUMEROS, key=len, reverse=True)) + r"))?"
    r"(?:\s+en\s+punto)?(?:\s*(?P<ampm>am|pm|a\.\s*m\.|p\.\s*m\.))?\b")

# Lo que queda alrededor del texto del recordatorio («de», «que», «para»...)
_RELLENO_INICIO = re.compile(r"^(?:[,.:;]\s*)*(?:(?:que|de|para|a|y|el|la|lo|sobre)\s+)*", re.IGNORECASE)
_RELLENO_FINAL = re.compile(r"(?:\s+(?:de|para|a|y|el|la))+\s*$|[\s,.;:¡!¿?]+$", re.IGNORECASE)


def _numero(texto: str) -> float:
    texto = texto.strip()
    if texto in _NUMEROS:
        return float(_NUMEROS[texto])
    return float(texto.replace(",", "."))


_UNIDAD = {"segundo": 1, "segundos": 1, "seg": 1, "minuto": 60, "minutos": 60, "min": 60, "hora": 3600,
           "horas": 3600, "dia": 86400, "dias": 86400, "semana": 604800, "semanas": 604800}


def segundos_de(texto: str) -> int:
    """«1 hora y 20 minutos» -> 4800. 0 si no hay ninguna duración."""
    total = 0.0
    for trozo in re.finditer(_TROZO_DURACION, plano(texto or "")):
        t = trozo.group(0)
        if re.fullmatch(r"media\s+hora", t):
            total += 1800
        elif re.fullmatch(r"(?:un\s+)?cuarto\s+de\s+hora", t):
            total += 900
        elif re.fullmatch(r"hora\s+y\s+media", t):
            total += 5400
        else:
            partes = re.match(rf"(?P<n>{_NUM})\s+(?P<u>[a-z]+)(?:\s+y\s+(?P<f>media|cuarto))?", t)
            unidad = _UNIDAD.get(partes.group("u"), 60)
            total += _numero(partes.group("n")) * unidad
            if partes.group("f"):
                total += unidad * (0.5 if partes.group("f") == "media" else 0.25)
    return int(round(total))


def describir_duracion(segundos: float) -> str:
    segundos = int(segundos)
    partes = []
    for nombre, valor in (("día", 86400), ("hora", 3600), ("minuto", 60), ("segundo", 1)):
        if segundos >= valor:
            cantidad, segundos = divmod(segundos, valor)
            partes.append(f"{cantidad} {nombre}{'s' if cantidad != 1 else ''}")
    if not partes:
        return "un momento"
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


@dataclass
class Momento:
    cuando: dt.datetime
    trozos: list = field(default_factory=list)         # (inicio, fin) de lo que expresaba la fecha
    relativo: bool = False


def _hora_y_minutos(c) -> tuple:
    h = int(_numero(c.group("h")))
    m = 0
    if c.group("mm"):
        m = int(c.group("mm"))
    elif c.group("y"):
        y = c.group("y")
        m = 30 if y == "media" else 15 if y == "cuarto" else int(_numero(y))
    elif c.group("menos"):
        menos = c.group("menos")
        h, m = (h - 1) % 24, 60 - (15 if menos == "cuarto" else int(_numero(menos)))
    return h, m


def _buscar_fuera(patron, texto: str, trozos: list):
    """La primera coincidencia que no pisa algo ya reconocido («mañana» dentro de «por la mañana»)."""
    for encontrado in patron.finditer(texto):
        if not any(a < encontrado.end() and encontrado.start() < b for a, b in trozos):
            return encontrado
    return None


def interpretar_momento(texto: str, ahora: dt.datetime | None = None) -> Momento | None:
    """La fecha y hora que expresa una frase («mañana a las 9», «en 20 minutos»...), o None."""
    ahora = (ahora or dt.datetime.now()).replace(microsecond=0)
    p = plano(texto or "")

    relativo = _RELATIVO.search(p)
    if relativo:
        segundos = segundos_de(relativo.group("duracion"))
        if segundos > 0:
            return Momento(ahora + dt.timedelta(seconds=segundos), [relativo.span()], relativo=True)

    trozos, periodo, dia, mismo_dia_semana = [], None, None, False
    encontrado = _PERIODO.search(p)
    if encontrado:
        periodo = encontrado.group("p") or encontrado.group("m")
        trozos.append(encontrado.span())
    encontrado = _buscar_fuera(_ESTE_PERIODO, p, trozos)
    if encontrado:
        periodo = periodo or encontrado.group("p")
        dia = ahora.date()
        trozos.append(encontrado.span())
    if dia is None:
        for patron, desfase in ((_PASADO_MANANA, 2), (_MANANA, 1), (_HOY, 0)):
            encontrado = _buscar_fuera(patron, p, trozos)
            if encontrado:
                dia = ahora.date() + dt.timedelta(days=desfase)
                trozos.append(encontrado.span())
                if "noche" in encontrado.group(0):
                    periodo = periodo or "noche"
                break
    if dia is None:
        encontrado = _buscar_fuera(_SEMANA, p, trozos)
        if encontrado:
            dias = (DIAS_SEMANA.index(encontrado.group("d")) - ahora.weekday()) % 7
            dia = ahora.date() + dt.timedelta(days=dias)
            mismo_dia_semana = dias == 0
            trozos.append(encontrado.span())

    hora = _buscar_fuera(_HORA, p, trozos)
    if dia is None:
        encontrado = _buscar_fuera(_FECHA, p, trozos + ([hora.span()] if hora else []))
        if encontrado:
            numero_mes = (MESES.index(encontrado.group("mes")) + 1 if encontrado.group("mes")
                          else int(encontrado.group("mes_n") or 0))
            anio = int(encontrado.group("anio")) if encontrado.group("anio") else ahora.year
            anio = anio + 2000 if anio < 100 else anio
            try:
                candidato = dt.date(anio, numero_mes, int(encontrado.group("dia")))
            except ValueError:
                candidato = None
            if candidato:
                if candidato < ahora.date() and not encontrado.group("anio"):
                    candidato = candidato.replace(year=candidato.year + 1)
                dia = candidato
                trozos.append(encontrado.span())

    if hora:
        h, m = _hora_y_minutos(hora)
        if not (0 <= h <= 24 and 0 <= m <= 59):
            return None
        trozos.append(hora.span())
        ampm = re.sub(r"[\s.]", "", hora.group("ampm") or "")
        dia_siguiente = False
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        elif not ampm and periodo in ("tarde", "noche") and h < 12:
            h += 12
        elif not ampm and periodo in ("noche", "madrugada", "medianoche") and h in (12, 24):
            h, dia_siguiente = 0, periodo != "madrugada"
        elif not ampm and periodo is None and h <= 12:
            if dia is None:
                # Sin día ni «de la tarde»: la próxima vez que sea esa hora (las 9 o las 21).
                opciones = [dt.datetime.combine(ahora.date(), dt.time(hh, m)) for hh in {h % 12, h % 12 + 12}]
                futuras = [o for o in opciones if o > ahora]
                if futuras:
                    return Momento(min(futuras), trozos)
                return Momento(dt.datetime.combine(ahora.date() + dt.timedelta(days=1), dt.time(h % 24, m)), trozos)
            if 1 <= h <= 7:
                h += 12                              # «el jueves a las 5»: por la tarde
        cuando = dt.datetime.combine(dia or ahora.date(), dt.time(h % 24, m))
        if dia_siguiente:
            cuando += dt.timedelta(days=1)
        if cuando <= ahora:
            if dia is None:
                cuando += dt.timedelta(days=1)
            elif mismo_dia_semana:
                cuando += dt.timedelta(days=7)
        return Momento(cuando, trozos)

    if dia is None and periodo is None:
        return None
    if periodo == "medianoche":
        cuando = dt.datetime.combine((dia or ahora.date()) + dt.timedelta(days=1), dt.time(0, 0))
    else:
        cuando = dt.datetime.combine(dia or ahora.date(),
                                     dt.time(HORA_DEL_PERIODO.get(periodo or "", HORA_POR_DEFECTO), 0))
    if cuando <= ahora:
        if mismo_dia_semana:
            cuando += dt.timedelta(days=7)
        else:
            cuando = ahora + dt.timedelta(hours=1)  # «hoy llamar a…» cuando ya pasó la hora por defecto
    return Momento(cuando, trozos)


def separar(texto: str, momento: Momento | None) -> str:
    """El texto del recordatorio, sin la parte que decía cuándo («llamar al médico»)."""
    texto = texto or ""
    if momento:
        for inicio, fin in sorted(momento.trozos, reverse=True):
            texto = texto[:inicio] + " " + texto[fin:]
    texto = " ".join(texto.split())
    for _ in range(3):
        texto = _RELLENO_INICIO.sub("", texto)
        texto = _RELLENO_FINAL.sub("", texto)
    return texto.strip()


def tiene_momento(texto: str) -> bool:
    return interpretar_momento(texto) is not None


def describir_momento(cuando: dt.datetime, ahora: dt.datetime | None = None) -> str:
    """«hoy a las 17:30», «mañana a las 9:00», «el jueves 20 a las 17:00», «el 24 de diciembre a las 10:00»."""
    ahora = ahora or dt.datetime.now()
    dias = (cuando.date() - ahora.date()).days
    hora = f"a las {hora_corta(cuando)}"
    if dias == 0:
        return f"hoy {hora}"
    if dias == 1:
        return f"mañana {hora}"
    if dias == 2:
        return f"pasado mañana {hora}"
    nombre_dia = fecha_larga(cuando).split(",")[0]
    if 0 < dias < 7:
        return f"el {nombre_dia} {cuando.day} {hora}"
    fecha = f"el {cuando.day} de {MESES[cuando.month - 1]}"
    if cuando.year != ahora.year:
        fecha += f" de {cuando.year}"
    return f"{fecha} {hora}"


# ==========================================
# ALMACÉN
# ==========================================
ESQUEMA = """
CREATE TABLE IF NOT EXISTS recordatorios (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo     TEXT NOT NULL,
    texto    TEXT NOT NULL DEFAULT '',
    momento  REAL NOT NULL,
    creado   REAL NOT NULL,
    estado   TEXT NOT NULL DEFAULT 'pendiente',
    origen   TEXT NOT NULL DEFAULT 'usuaria',
    uid      TEXT
);
CREATE INDEX IF NOT EXISTS idx_recordatorios_estado ON recordatorios(estado, momento);
"""


class Recordatorios:
    """Recordatorios guardados en la base de datos de JARVIS."""

    def __init__(self, base_datos):
        self.bd = base_datos
        with self.bd.conexion() as con:
            con.executescript(ESQUEMA)
        self._cerrojo = threading.Lock()

    @staticmethod
    def _dict(fila) -> dict:
        datos = dict(fila)
        datos["cuando"] = dt.datetime.fromtimestamp(datos["momento"])
        return datos

    def crear(self, tipo: str, texto: str, cuando: dt.datetime, origen: str = "usuaria", uid: str | None = None) -> dict:
        if tipo not in TIPOS:
            raise ValueError(f"Tipo de recordatorio desconocido: {tipo}")
        texto = " ".join((texto or "").split())[:300]
        nuevo = self.bd.ejecutar(
            "INSERT INTO recordatorios (tipo, texto, momento, creado, estado, origen, uid) VALUES (?,?,?,?,?,?,?)",
            (tipo, texto, cuando.timestamp(), time.time(), "pendiente", origen, uid))
        return self.obtener(nuevo)

    def obtener(self, identificador: int) -> dict | None:
        fila = self.bd.uno("SELECT * FROM recordatorios WHERE id = ?", (identificador,))
        return self._dict(fila) if fila else None

    def pendientes(self, tipo: str | None = None) -> list:
        sql, parametros = "SELECT * FROM recordatorios WHERE estado = 'pendiente'", []
        if tipo:
            sql += " AND tipo = ?"
            parametros.append(tipo)
        return [self._dict(f) for f in self.bd.consultar(sql + " ORDER BY momento", tuple(parametros))]

    def entre(self, desde: dt.datetime, hasta: dt.datetime) -> list:
        filas = self.bd.consultar(
            "SELECT * FROM recordatorios WHERE estado = 'pendiente' AND momento >= ? AND momento < ? ORDER BY momento",
            (desde.timestamp(), hasta.timestamp()))
        return [self._dict(f) for f in filas]

    def del_dia(self, dia: dt.date) -> list:
        inicio = dt.datetime.combine(dia, dt.time())
        return self.entre(inicio, inicio + dt.timedelta(days=1))

    def vencidos(self, ahora: float | None = None) -> list:
        filas = self.bd.consultar(
            "SELECT * FROM recordatorios WHERE estado = 'pendiente' AND momento <= ? ORDER BY momento",
            (ahora or time.time(),))
        return [self._dict(f) for f in filas]

    def marcar(self, identificador: int, estado: str) -> None:
        self.bd.ejecutar("UPDATE recordatorios SET estado = ? WHERE id = ?", (estado, identificador))

    def cancelar(self, tipo: str | None = None, texto: str = "", todos: bool = False) -> list:
        """Cancela lo que encaje (el próximo, o todos con `todos`). Devuelve los cancelados."""
        candidatos = self.pendientes(tipo)
        if texto:
            clave = plano(texto)
            candidatos = [r for r in candidatos if clave in plano(r["texto"])]
        elegidos = candidatos if todos else candidatos[:1]
        for recordatorio in elegidos:
            self.marcar(recordatorio["id"], "cancelado")
        return elegidos

    def sincronizar_calendario(self, eventos: list, antelacion_min: int = 0) -> int:
        """Pone en la agenda los eventos de un calendario (.ics). Devuelve cuántos son nuevos.
        Los que ya estaban (mismo uid) se actualizan; los que han desaparecido se quitan."""
        ahora = dt.datetime.now()
        vistos, nuevos = set(), 0
        with self._cerrojo:
            existentes = {f["uid"]: dict(f) for f in self.bd.consultar(
                "SELECT * FROM recordatorios WHERE origen = 'calendario' AND estado = 'pendiente'")}
            for evento in eventos:
                if evento["inicio"] <= ahora or evento["inicio"] - ahora > MAXIMO_FUTURO:
                    continue
                uid = f'{evento["uid"]}@{evento["inicio"].isoformat()}'
                vistos.add(uid)
                aviso = evento["inicio"] - dt.timedelta(minutes=antelacion_min)
                if uid in existentes:
                    self.bd.ejecutar("UPDATE recordatorios SET texto = ?, momento = ? WHERE id = ?",
                                     (evento["titulo"], aviso.timestamp(), existentes[uid]["id"]))
                else:
                    self.crear("cita", evento["titulo"], aviso, origen="calendario", uid=uid)
                    nuevos += 1
            for uid, fila in existentes.items():
                if uid not in vistos:
                    self.marcar(fila["id"], "cancelado")
        return nuevos


# ==========================================
# CALENDARIO iCal (.ics), solo lectura
# ==========================================
def _fecha_ics(valor: str, parametros: str) -> dt.datetime | None:
    valor = valor.strip()
    try:
        if "VALUE=DATE" in parametros or re.fullmatch(r"\d{8}", valor):
            return dt.datetime.strptime(valor[:8], "%Y%m%d").replace(hour=HORA_POR_DEFECTO)
        if valor.endswith("Z"):
            utc = dt.datetime.strptime(valor, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            return utc.astimezone().replace(tzinfo=None)
        return dt.datetime.strptime(valor[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def leer_ics(contenido: str) -> list:
    """Eventos de un calendario iCal: [{"uid", "titulo", "inicio"}]. Los que se repiten (RRULE)
    solo cuentan en su primera fecha: el formato de repetición completo no está implementado."""
    lineas = []
    for linea in (contenido or "").replace("\r\n", "\n").split("\n"):
        if linea.startswith((" ", "\t")) and lineas:
            lineas[-1] += linea[1:]                     # líneas partidas
        else:
            lineas.append(linea)
    eventos, actual = [], None
    for linea in lineas:
        if linea == "BEGIN:VEVENT":
            actual = {}
        elif linea == "END:VEVENT" and actual is not None:
            if actual.get("inicio") and actual.get("estado") != "CANCELLED":
                eventos.append({"uid": actual.get("uid") or actual["inicio"].isoformat(),
                                "titulo": actual.get("titulo") or "Evento", "inicio": actual["inicio"]})
            actual = None
        elif actual is not None and ":" in linea:
            clave, valor = linea.split(":", 1)
            nombre, _, parametros = clave.partition(";")
            if nombre == "DTSTART":
                actual["inicio"] = _fecha_ics(valor, parametros)
            elif nombre == "SUMMARY":
                actual["titulo"] = valor.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
            elif nombre == "UID":
                actual["uid"] = valor.strip()
            elif nombre == "STATUS":
                actual["estado"] = valor.strip().upper()
    return eventos


def descargar_ics(direccion: str, tiempo_maximo: int = 15) -> str:
    """Lee un calendario .ics de una dirección (http/https/webcal) o de un fichero local."""
    direccion = (direccion or "").strip()
    if direccion.startswith("webcal://"):
        direccion = "https://" + direccion[len("webcal://"):]
    if direccion.startswith(("http://", "https://")):
        import requests
        respuesta = requests.get(direccion, timeout=tiempo_maximo)
        respuesta.raise_for_status()
        return respuesta.text
    with open(direccion, encoding="utf-8", errors="replace") as fichero:
        return fichero.read()


# ==========================================
# AVISADOR (hilo que comprueba cada segundo)
# ==========================================
MARGEN_PERDIDO = 120          # segundos: un aviso con más retraso es que el ordenador estaba apagado


class Avisador(threading.Thread):
    """Avisa de lo que vence. Los avisos que se pasaron con el ordenador apagado se agrupan en uno.

    al_avisar(recordatorios, perdidos) recibe una lista: normalmente de uno, a la hora en punto;
    al arrancar, todos los que se pasaron mientras JARVIS no estaba abierto.
    """

    def __init__(self, recordatorios: Recordatorios, al_avisar, intervalo: float = 1.0):
        super().__init__(name="jarvis-avisos", daemon=True)
        self.recordatorios = recordatorios
        self.al_avisar = al_avisar
        self.intervalo = intervalo
        self._parar = threading.Event()

    def comprobar(self, ahora: float | None = None) -> list:
        ahora = ahora or time.time()
        vencidos = self.recordatorios.vencidos(ahora)
        for recordatorio in vencidos:
            self.recordatorios.marcar(recordatorio["id"], "avisado")      # antes de avisar: nunca dos veces
        perdidos = [r for r in vencidos if ahora - r["momento"] > MARGEN_PERDIDO]
        a_tiempo = [r for r in vencidos if r not in perdidos]
        for lista, son_perdidos in ((perdidos, True), *(([r], False) for r in a_tiempo)):
            if lista:
                try:
                    self.al_avisar(lista, son_perdidos)
                except Exception:
                    registro.exception("Fallo al avisar de un recordatorio")
        return vencidos

    def run(self) -> None:
        while not self._parar.wait(self.intervalo):
            try:
                self.comprobar()
            except Exception:
                registro.exception("Fallo al revisar los recordatorios")

    def parar(self) -> None:
        self._parar.set()


def rango_de(texto: str, ahora: dt.datetime | None = None) -> tuple:
    """(desde, hasta, cómo decirlo) para «hoy», «mañana», «el jueves», «esta semana»... o todo."""
    ahora = ahora or dt.datetime.now()
    p = plano(texto or "").strip()
    hoy = dt.datetime.combine(ahora.date(), dt.time())
    if not p:
        return ahora - dt.timedelta(days=1), ahora + MAXIMO_FUTURO, ""
    if re.search(r"\bsemana\s+que\s+viene\b|\bproxima\s+semana\b", p):
        lunes = hoy + dt.timedelta(days=7 - ahora.weekday())
        return lunes, lunes + dt.timedelta(days=7), "la semana que viene"
    if re.search(r"\besta\s+semana\b", p):
        return ahora, hoy + dt.timedelta(days=7 - ahora.weekday()), "esta semana"
    momento = interpretar_momento(p, ahora)
    if momento is None or momento.relativo:
        return ahora - dt.timedelta(days=1), ahora + MAXIMO_FUTURO, ""
    dia = dt.datetime.combine(momento.cuando.date(), dt.time())
    if dia.date() == ahora.date():
        return ahora, dia + dt.timedelta(days=1), "hoy"
    nombre = describir_momento(dia, ahora).rsplit(" a las ", 1)[0]
    return dia, dia + dt.timedelta(days=1), nombre


def publico(recordatorio: dict, ahora: dt.datetime | None = None) -> dict:
    """Lo que se enseña en la interfaz de un recordatorio."""
    return {"id": recordatorio["id"], "tipo": recordatorio["tipo"], "texto": recordatorio["texto"],
            "momento": recordatorio["momento"], "cuando": describir_momento(recordatorio["cuando"], ahora),
            "origen": recordatorio.get("origen", "usuaria")}


# ==========================================
# FRASES
# ==========================================
def frase_creado(recordatorio: dict, ahora: dt.datetime | None = None) -> str:
    ahora = ahora or dt.datetime.now()
    tipo, texto, cuando = recordatorio["tipo"], recordatorio["texto"], recordatorio["cuando"]
    if tipo == "temporizador":
        return f"Temporizador de {describir_duracion((cuando - ahora).total_seconds() + 0.5)} en marcha."
    if tipo == "alarma":
        return f"Alarma puesta para {describir_momento(cuando, ahora)}."
    if tipo == "cita":
        return f"Apuntado en tu agenda: «{texto}», {describir_momento(cuando, ahora)}."
    return f"Te lo recordaré {describir_momento(cuando, ahora)}: «{texto}»."


def frase_aviso(recordatorio: dict, nombre: str = "") -> str:
    tipo, texto = recordatorio["tipo"], recordatorio["texto"]
    saludo = f"{nombre}, " if nombre else ""
    if tipo == "temporizador":
        return f"{saludo}¡tiempo! Se ha terminado el temporizador" + (f" de {texto}." if texto else ".")
    if tipo == "alarma":
        return f"{saludo}son las {hora_corta(recordatorio['cuando'])}. Tu alarma" + (f": {texto}." if texto else ".")
    if tipo == "cita":
        return f"{saludo}en tu agenda: {texto}, a las {hora_corta(recordatorio['cuando'])}."
    return f"{saludo}te recuerdo: {texto}."


def frase_lista(recordatorios: list, que: str = "", ahora: dt.datetime | None = None) -> str:
    ahora = ahora or dt.datetime.now()
    if not recordatorios:
        return f"No tienes nada {que}.".replace("  ", " ") if que else "No tienes recordatorios, alarmas ni temporizadores."
    partes = []
    for r in recordatorios[:8]:
        if r["tipo"] == "temporizador":
            partes.append(f"un temporizador que acaba en {describir_duracion((r['cuando'] - ahora).total_seconds())}")
        elif r["tipo"] == "alarma":
            partes.append(f"una alarma {describir_momento(r['cuando'], ahora)}")
        else:
            partes.append(f"{r['texto']} ({describir_momento(r['cuando'], ahora)})")
    inicio = f"Tienes {que}: " if que else "Tienes: "
    resto = f" y {len(recordatorios) - 8} más" if len(recordatorios) > 8 else ""
    return inicio + "; ".join(partes) + resto + "."
