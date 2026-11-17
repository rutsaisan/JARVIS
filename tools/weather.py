"""
Tiempo meteorológico (Open-Meteo)
==================================
Consulta el tiempo actual y la previsión con la API pública de Open-Meteo,
que no necesita clave ni registro. A la API solo se envía el nombre de la
ciudad consultada (y luego sus coordenadas).
"""
from __future__ import annotations

import logging

registro = logging.getLogger("jarvis.tiempo")

URL_GEOCODIFICACION = "https://geocoding-api.open-meteo.com/v1/search"
URL_PREVISION = "https://api.open-meteo.com/v1/forecast"
ESPERA = 8

# Códigos WMO -> (descripción para decir «con ...», icono de la interfaz)
CODIGOS = {
    0: ("cielos despejados", "sol"), 1: ("cielos casi despejados", "sol"),
    2: ("cielo parcialmente nublado", "nubes_sol"), 3: ("cielo nublado", "nubes"),
    45: ("niebla", "niebla"), 48: ("niebla con escarcha", "niebla"),
    51: ("llovizna ligera", "lluvia"), 53: ("llovizna", "lluvia"), 55: ("llovizna intensa", "lluvia"),
    56: ("llovizna helada", "lluvia"), 57: ("llovizna helada intensa", "lluvia"),
    61: ("lluvia ligera", "lluvia"), 63: ("lluvia", "lluvia"), 65: ("lluvia fuerte", "lluvia"),
    66: ("lluvia helada", "lluvia"), 67: ("lluvia helada fuerte", "lluvia"),
    71: ("nevada ligera", "nieve"), 73: ("nieve", "nieve"), 75: ("nevada intensa", "nieve"),
    77: ("granos de nieve", "nieve"),
    80: ("chubascos ligeros", "lluvia"), 81: ("chubascos", "lluvia"), 82: ("chubascos fuertes", "lluvia"),
    85: ("chubascos de nieve", "nieve"), 86: ("chubascos fuertes de nieve", "nieve"),
    95: ("tormenta", "tormenta"), 96: ("tormenta con granizo", "tormenta"),
    99: ("tormenta fuerte con granizo", "tormenta"),
}

_coordenadas = {}   # caché: nombre de ciudad -> datos de geocodificación


class ErrorTiempo(Exception):
    """No se pudo obtener el tiempo (ciudad desconocida, sin conexión...)."""


def _json(url: str, parametros: dict) -> dict:
    import requests
    respuesta = requests.get(url, params=parametros, timeout=ESPERA)
    respuesta.raise_for_status()
    return respuesta.json()


def localizar(ciudad: str) -> dict:
    clave = ciudad.strip().lower()
    if clave in _coordenadas:
        return _coordenadas[clave]
    try:
        datos = _json(URL_GEOCODIFICACION, {"name": ciudad, "count": 1, "language": "es", "format": "json"})
    except Exception as error:
        raise ErrorTiempo("sin_conexion") from error
    resultados = datos.get("results") or []
    if not resultados:
        raise ErrorTiempo("ciudad_desconocida")
    lugar = resultados[0]
    _coordenadas[clave] = {
        "nombre": lugar.get("name", ciudad), "pais": lugar.get("country", ""),
        "latitud": lugar["latitude"], "longitud": lugar["longitude"],
    }
    return _coordenadas[clave]


def obtener_tiempo(ciudad: str, dia: str = "hoy") -> dict:
    """Tiempo de `ciudad` para "hoy" o "manana". Lanza ErrorTiempo si no se puede."""
    if not (ciudad or "").strip():
        raise ErrorTiempo("sin_ciudad")
    lugar = localizar(ciudad)
    try:
        datos = _json(URL_PREVISION, {
            "latitude": lugar["latitud"], "longitude": lugar["longitud"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 3,
        })
    except Exception as error:
        raise ErrorTiempo("sin_conexion") from error

    diario, actual = datos.get("daily", {}), datos.get("current", {})
    indice = 1 if dia == "manana" else 0

    def del_dia(clave, defecto=None):
        valores = diario.get(clave) or []
        return valores[indice] if len(valores) > indice and valores[indice] is not None else defecto

    codigo = del_dia("weather_code", 0) if dia == "manana" else actual.get("weather_code", 0)
    descripcion, icono = CODIGOS.get(codigo, ("tiempo variable", "nubes"))
    es_de_dia = bool(actual.get("is_day", 1)) if dia != "manana" else True
    if icono == "sol" and not es_de_dia:
        icono = "luna"
    return {
        "ciudad": lugar["nombre"], "pais": lugar["pais"], "dia": dia,
        "temperatura": round(actual["temperature_2m"]) if dia != "manana" and "temperature_2m" in actual else None,
        "sensacion": round(actual["apparent_temperature"]) if dia != "manana" and "apparent_temperature" in actual else None,
        "humedad": actual.get("relative_humidity_2m") if dia != "manana" else None,
        "viento": round(actual["wind_speed_10m"]) if dia != "manana" and "wind_speed_10m" in actual else None,
        "maxima": round(del_dia("temperature_2m_max", 0)),
        "minima": round(del_dia("temperature_2m_min", 0)),
        "prob_lluvia": del_dia("precipitation_probability_max"),
        "descripcion": descripcion, "icono": icono,
    }


def frase_tiempo(datos: dict) -> str:
    """Frase para decir en voz alta a partir de los datos de obtener_tiempo()."""
    if datos["dia"] == "manana":
        frase = (f"Mañana en {datos['ciudad']} se esperan entre {datos['minima']} y {datos['maxima']} grados, "
                 f"con {datos['descripcion']}.")
    else:
        frase = (f"Ahora mismo en {datos['ciudad']} hay {datos['temperatura']} grados, con {datos['descripcion']}. "
                 f"Máxima de {datos['maxima']} y mínima de {datos['minima']}.")
    if datos.get("prob_lluvia") is not None and (datos["dia"] == "manana" or datos["prob_lluvia"] >= 30):
        frase += f" Probabilidad de lluvia: {datos['prob_lluvia']} por ciento."
    return frase
