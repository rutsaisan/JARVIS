"""Utilidades de texto compartidas: normalización, palabras clave y similitud léxica."""
from __future__ import annotations

import re
import unicodedata


def _plano_caracter(caracter: str) -> str:
    minuscula = caracter.lower()
    if len(minuscula) != 1:
        minuscula = minuscula[0]
    descompuesto = unicodedata.normalize("NFD", minuscula)
    return descompuesto[0] if descompuesto else minuscula


def plano(texto: str) -> str:
    """Minúsculas y sin acentos (ñ -> n), conservando la longitud del texto.

    Al mantener cada carácter en su posición, se puede buscar un patrón en la
    versión plana y recortar el resultado del texto original, con sus
    mayúsculas y acentos intactos.
    """
    return "".join(_plano_caracter(c) for c in texto)


def normalizar(texto: str) -> str:
    """Versión para comparar: plana, sin signos de puntuación y con espacios simples."""
    sin_signos = re.sub(r"[^a-z0-9\s]", " ", plano(texto or ""))
    return re.sub(r"\s+", " ", sin_signos).strip()


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def fecha_larga(momento) -> str:
    """"sábado, 24 de mayo de 2025" (sin depender del idioma configurado en Windows)."""
    return f"{DIAS[momento.weekday()]}, {momento.day} de {MESES[momento.month - 1]} de {momento.year}"


def hora_corta(momento) -> str:
    return f"{momento.hour}:{momento.minute:02d}"


PALABRAS_VACIAS = frozenset("""
a al algo algun alguna algunas alguno algunos ante antes aquel aquella aquellas aquello aquellos aqui asi aun
aunque cada como con cual cuales cuando cuanta cuantas cuanto cuantos de del desde donde dos el ella ellas ellos
en entre era eran eres es esa esas ese eso esos esta estaba estado estan estar estas este esto estos estoy fue
fueron ha habia han has hasta hay he la las le les lo los mas me mi mis mucho muy nada ni no nos nosotros o os
otra otras otro otros para pero poco por porque que quien quienes se sea segun ser si sido sin sobre son soy su
sus tal tambien tan te tengo ti tiene tienen todo todos tu tus un una unas uno unos y ya yo
jarvis dime sabes puedes recuerdas recuerda acerca cosa cosas
""".split())


def raiz(palabra: str) -> str:
    """Reducción muy simple de una palabra (plurales y género) para comparar."""
    if len(palabra) > 5 and palabra.endswith("es"):
        palabra = palabra[:-2]
    elif len(palabra) > 4 and palabra.endswith("s"):
        palabra = palabra[:-1]
    if len(palabra) > 4 and palabra[-1] in "oa":
        palabra = palabra[:-1]
    return palabra


def palabras_clave(texto: str) -> list:
    """Palabras con significado de un texto, ya reducidas a su raíz."""
    return [raiz(p) for p in normalizar(texto).split() if p not in PALABRAS_VACIAS and len(p) > 1]


def similitud_lexica(consulta: str, documento: str) -> float:
    """Parecido entre 0 y 1 según las palabras clave que comparten los dos textos.

    Pesa sobre todo cuánta parte de la consulta aparece en el documento, y un
    poco cuánto se solapan en conjunto. Las consultas de una sola palabra se
    penalizan: una coincidencia suelta es una pista débil.
    """
    q = set(palabras_clave(consulta))
    d = set(palabras_clave(documento))
    if not q or not d:
        return 0.0
    comunes = q & d
    if not comunes:
        return 0.0
    cobertura = len(comunes) / len(q)
    solapamiento = len(comunes) / len(q | d)
    puntuacion = 0.7 * cobertura + 0.3 * solapamiento
    if len(q) == 1:
        # Una sola palabra en común ("tiempo") no basta para dar un recuerdo por
        # relevante en una conversación; sí para señalarlo al editar o borrar.
        puntuacion *= 0.45
    return round(puntuacion, 4)
