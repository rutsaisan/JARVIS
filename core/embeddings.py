"""
Embeddings locales (vectores de significado) vía Ollama
========================================================
Convierte textos en vectores para que la memoria pueda encontrar recuerdos
por significado y no solo por palabras exactas ("¿qué música me gusta?" ->
"me gusta el rock"). Usa el modelo `nomic-embed-text` de Ollama, en local.

Si Ollama no está disponible, devuelve None y la memoria sigue funcionando
con la comparación léxica (core/text.py): nunca es un punto único de fallo.
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np

registro = logging.getLogger("jarvis.embeddings")

PAUSA_TRAS_FALLO = 60  # segundos sin reintentar tras un error (para no bloquear cada consulta)
ESPERA = 6             # una consulta de memoria nunca debe dejar colgada la conversación
ESPERA_CARGA = 120     # la primera carga del modelo sí puede tardar


class Embeddings:
    def __init__(self, modelo: str = "nomic-embed-text", host: str | None = None):
        self.modelo = modelo
        self.host = host
        self._desactivado = os.environ.get("JARVIS_SIN_EMBEDDINGS") == "1"
        self._sin_reintentar_hasta = 0.0
        self._cliente = None

    @property
    def activo(self) -> bool:
        return not self._desactivado and time.time() >= self._sin_reintentar_hasta

    def reintentar(self) -> None:
        self._sin_reintentar_hasta = 0.0

    def calentar(self) -> bool:
        """Carga el modelo en Ollama con paciencia (se llama en segundo plano al arrancar)."""
        if self._desactivado:
            return False
        try:
            import ollama
            ollama.Client(host=self.host, timeout=ESPERA_CARGA).embed(model=self.modelo, input=["hola"])
            self._sin_reintentar_hasta = 0.0
            return True
        except Exception as error:
            registro.info("El modelo de memoria «%s» no está disponible (%s).", self.modelo, error)
            self._sin_reintentar_hasta = time.time() + PAUSA_TRAS_FALLO
            return False

    def cerrar(self) -> None:
        try:
            self._cliente._client.close()
        except Exception:
            pass
        self._cliente = None

    def _obtener_cliente(self):
        if self._cliente is None:
            import ollama
            self._cliente = ollama.Client(host=self.host, timeout=ESPERA)
        return self._cliente

    def vectores(self, textos: list, tipo: str = "documento") -> list | None:
        """Vectores normalizados (norma 1) de una lista de textos, o None si no hay servicio."""
        if not self.activo or not textos:
            return None
        prefijo = ""
        if "nomic" in self.modelo:      # este modelo distingue consultas de documentos
            prefijo = "search_query: " if tipo == "consulta" else "search_document: "
        try:
            respuesta = self._obtener_cliente().embed(model=self.modelo, input=[prefijo + t for t in textos])
            matriz = np.asarray(respuesta["embeddings"], dtype=np.float32)
            if matriz.ndim != 2 or matriz.shape[0] != len(textos):
                raise ValueError("respuesta de embeddings inesperada")
        except Exception as error:
            self._sin_reintentar_hasta = time.time() + PAUSA_TRAS_FALLO
            registro.debug("Embeddings no disponibles (%s); se usa la comparación léxica.", error)
            return None
        normas = np.linalg.norm(matriz, axis=1, keepdims=True)
        normas[normas == 0] = 1.0
        return list(matriz / normas)

    def vector(self, texto: str, tipo: str = "documento"):
        resultado = self.vectores([texto], tipo)
        return resultado[0] if resultado else None


def a_bytes(vector) -> bytes | None:
    return None if vector is None else np.asarray(vector, dtype=np.float32).tobytes()


def desde_bytes(datos: bytes | None):
    return None if not datos else np.frombuffer(datos, dtype=np.float32)
