"""
Historial de conversaciones
============================
Guarda cada conversación y sus mensajes en SQLite (entidades «conversaciones»
y «mensajes» del plan técnico) para poder consultarlas, buscarlas y borrarlas.

Cada conversación terminada recibe un resumen corto. Ese resumen permite
«recordar conversaciones relevantes»: cuando una charla nueva trata de algo
ya hablado, se recupera el resumen y se le da al modelo como contexto.

Solo se escribe aquí si la usuaria mantiene activado «Guardar historial».
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np

from core.database import BaseDatos
from core.embeddings import Embeddings, a_bytes, desde_bytes
from core.text import normalizar, similitud_lexica

registro = logging.getLogger("jarvis.historial")

LONGITUD_TITULO = 48
UMBRAL_RESUMEN = 0.55
COSENO_MINIMO, COSENO_MAXIMO = 0.55, 0.80


def _titulo_desde(texto: str) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= LONGITUD_TITULO else texto[:LONGITUD_TITULO - 1].rstrip() + "…"


class Historial:
    def __init__(self, base_datos: BaseDatos, embeddings: Embeddings | None = None):
        self.bd = base_datos
        self.embeddings = embeddings if embeddings is not None else Embeddings()

    # ------------------------------------------------------------------
    # ESCRITURA
    # ------------------------------------------------------------------
    def crear(self, titulo: str | None = None) -> int:
        ahora = time.time()
        return self.bd.ejecutar("INSERT INTO conversaciones (titulo, fecha_inicio, fecha_fin) VALUES (?,?,?)",
                                (titulo, ahora, ahora))

    def anadir(self, conversacion_id: int, rol: str, contenido: str, extra: dict | None = None) -> int:
        ahora = time.time()
        with self.bd.conexion() as con:
            cursor = con.execute(
                "INSERT INTO mensajes (conversacion_id, rol, contenido, fecha, extra) VALUES (?,?,?,?,?)",
                (conversacion_id, rol, contenido, ahora, json.dumps(extra, ensure_ascii=False) if extra else None))
            con.execute("UPDATE conversaciones SET fecha_fin = ? WHERE id = ?", (ahora, conversacion_id))
            if rol == "usuario":
                con.execute("UPDATE conversaciones SET titulo = ? WHERE id = ? AND titulo IS NULL",
                            (_titulo_desde(contenido), conversacion_id))
            return cursor.lastrowid

    def renombrar(self, conversacion_id: int, titulo: str) -> bool:
        return self.bd.ejecutar("UPDATE conversaciones SET titulo = ? WHERE id = ?",
                                (_titulo_desde(titulo), conversacion_id)) > 0

    def guardar_resumen(self, conversacion_id: int, resumen: str) -> None:
        vector = self.embeddings.vector(resumen, "documento") if resumen else None
        self.bd.ejecutar(
            "UPDATE conversaciones SET resumen = ?, embedding = ?, modelo_embedding = ? WHERE id = ?",
            (resumen, a_bytes(vector), self.embeddings.modelo if vector is not None else None, conversacion_id))

    def borrar(self, conversacion_id: int) -> bool:
        return self.bd.ejecutar("DELETE FROM conversaciones WHERE id = ?", (conversacion_id,)) > 0

    def borrar_todo(self) -> int:
        return self.bd.ejecutar("DELETE FROM conversaciones")

    # ------------------------------------------------------------------
    # LECTURA
    # ------------------------------------------------------------------
    def obtener(self, conversacion_id: int) -> dict | None:
        fila = self.bd.uno(
            "SELECT c.*, (SELECT COUNT(*) FROM mensajes m WHERE m.conversacion_id = c.id) AS n_mensajes "
            "FROM conversaciones c WHERE c.id = ?", (conversacion_id,))
        return self._conversacion(fila) if fila else None

    def mensajes(self, conversacion_id: int) -> list:
        filas = self.bd.consultar("SELECT * FROM mensajes WHERE conversacion_id = ? ORDER BY id",
                                  (conversacion_id,))
        resultado = []
        for fila in filas:
            extra = {}
            if fila["extra"]:
                try:
                    extra = json.loads(fila["extra"])
                except ValueError:
                    pass
            resultado.append({"id": fila["id"], "rol": fila["rol"], "texto": fila["contenido"],
                              "fecha": fila["fecha"], "tipo": extra.get("tipo", "chat"),
                              "datos": extra.get("datos", {}), "origen": extra.get("origen", "texto")})
        return resultado

    def listar(self, buscar: str | None = None, limite: int = 100) -> list:
        """Conversaciones con mensajes, de la más reciente a la más antigua."""
        filas = self.bd.consultar(
            "SELECT c.*, (SELECT COUNT(*) FROM mensajes m WHERE m.conversacion_id = c.id) AS n_mensajes "
            "FROM conversaciones c ORDER BY c.fecha_fin DESC LIMIT ?", (max(limite, 1) * 3,))
        conversaciones = [self._conversacion(fila) for fila in filas if fila["n_mensajes"] > 0]
        if buscar and buscar.strip():
            aguja = normalizar(buscar)
            con_mensaje = {fila["conversacion_id"] for fila in self.bd.consultar(
                "SELECT DISTINCT conversacion_id FROM mensajes WHERE contenido LIKE ?", (f"%{buscar.strip()}%",))}
            conversaciones = [c for c in conversaciones
                              if c["id"] in con_mensaje
                              or aguja in normalizar(f"{c['titulo'] or ''} {c['resumen'] or ''}")]
        return conversaciones[:limite]

    def sin_resumen(self, minimo_mensajes: int = 4) -> list:
        """Ids de conversaciones con suficiente contenido que aún no tienen resumen."""
        filas = self.bd.consultar(
            "SELECT c.id FROM conversaciones c WHERE (c.resumen IS NULL OR c.resumen = '') "
            "AND (SELECT COUNT(*) FROM mensajes m WHERE m.conversacion_id = c.id) >= ? "
            "ORDER BY c.fecha_fin DESC LIMIT 5", (minimo_mensajes,))
        return [fila["id"] for fila in filas]

    def buscar_resumen(self, consulta: str, excluir_id: int | None = None) -> str:
        """Resumen de la conversación anterior más relacionada con `consulta` ("" si ninguna)."""
        filas = self.bd.consultar(
            "SELECT id, resumen, embedding, modelo_embedding FROM conversaciones "
            "WHERE resumen IS NOT NULL AND resumen != '' ORDER BY fecha_fin DESC LIMIT 200")
        filas = [fila for fila in filas if fila["id"] != excluir_id]
        if not filas:
            return ""
        vector_consulta = None
        if any(fila["embedding"] for fila in filas):
            vector_consulta = self.embeddings.vector(consulta, "consulta")
        mejor, mejor_puntuacion = "", 0.0
        for fila in filas:
            puntuacion = similitud_lexica(consulta, fila["resumen"])
            vector = desde_bytes(fila["embedding"])
            if (vector_consulta is not None and vector is not None
                    and fila["modelo_embedding"] == self.embeddings.modelo
                    and vector.shape == vector_consulta.shape):
                coseno = float(np.dot(vector_consulta, vector))
                puntuacion = max(puntuacion, (coseno - COSENO_MINIMO) / (COSENO_MAXIMO - COSENO_MINIMO))
            if puntuacion > mejor_puntuacion:
                mejor, mejor_puntuacion = fila["resumen"], puntuacion
        return mejor if mejor_puntuacion >= UMBRAL_RESUMEN else ""

    # ------------------------------------------------------------------
    # CONSENTIMIENTOS (queda constancia de cuándo se autoriza o se retira cada permiso)
    # ------------------------------------------------------------------
    def registrar_consentimiento(self, tipo: str, estado: bool) -> None:
        self.bd.ejecutar("INSERT INTO consentimientos (tipo, estado, fecha) VALUES (?,?,?)",
                         (tipo, 1 if estado else 0, time.time()))

    def consentimientos(self) -> list:
        return [dict(fila) for fila in self.bd.consultar("SELECT * FROM consentimientos ORDER BY id DESC LIMIT 50")]

    # ------------------------------------------------------------------
    @staticmethod
    def _conversacion(fila) -> dict:
        return {"id": fila["id"], "titulo": fila["titulo"] or "Conversación sin título",
                "fecha_inicio": fila["fecha_inicio"], "fecha_fin": fila["fecha_fin"],
                "resumen": fila["resumen"] or "", "n_mensajes": fila["n_mensajes"]}
