"""
Base de datos local de JARVIS (SQLite)
=======================================
Un único fichero, data/jarvis.db, con las entidades del plan técnico
(sección 8, «Diseño de la memoria»):

    recuerdos        lo que JARVIS recuerda de forma permanente
    conversaciones   cada charla, con su título y su resumen
    mensajes         los mensajes de cada conversación
    consentimientos  qué ha autorizado la usuaria y cuándo

Las acciones de herramientas se auditan aparte, en data/logs/acciones.jsonl
(ver tools/security.py).

Cada operación abre y cierra su propia conexión: así se puede usar sin
problemas desde varios hilos (voz, interfaz, orquestador).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

VERSION_ESQUEMA = 1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS recuerdos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    clave               TEXT,
    valor               TEXT,
    texto               TEXT    NOT NULL,
    categoria           TEXT    NOT NULL DEFAULT 'información',
    confianza           REAL    NOT NULL DEFAULT 1.0,
    origen              TEXT    NOT NULL DEFAULT 'usuario',
    fecha_creacion      REAL    NOT NULL,
    fecha_actualizacion REAL    NOT NULL,
    activo              INTEGER NOT NULL DEFAULT 1,
    embedding           BLOB,
    modelo_embedding    TEXT
);
CREATE INDEX IF NOT EXISTS idx_recuerdos_activo ON recuerdos(activo, categoria);

CREATE TABLE IF NOT EXISTS conversaciones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo           TEXT,
    fecha_inicio     REAL NOT NULL,
    fecha_fin        REAL NOT NULL,
    resumen          TEXT,
    etiquetas        TEXT,
    embedding        BLOB,
    modelo_embedding TEXT
);

CREATE TABLE IF NOT EXISTS mensajes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL REFERENCES conversaciones(id) ON DELETE CASCADE,
    rol             TEXT    NOT NULL,
    contenido       TEXT    NOT NULL,
    fecha           REAL    NOT NULL,
    extra           TEXT
);
CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion ON mensajes(conversacion_id, id);

CREATE TABLE IF NOT EXISTS consentimientos (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo   TEXT    NOT NULL,
    estado INTEGER NOT NULL,
    fecha  REAL    NOT NULL
);
"""


class BaseDatos:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.conexion() as con:
            con.executescript(ESQUEMA)
            con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")

    @contextmanager
    def conexion(self):
        con = sqlite3.connect(str(self.ruta), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # Atajos -----------------------------------------------------------
    def consultar(self, sql: str, parametros: tuple = ()) -> list:
        with self.conexion() as con:
            return con.execute(sql, parametros).fetchall()

    def uno(self, sql: str, parametros: tuple = ()):
        with self.conexion() as con:
            return con.execute(sql, parametros).fetchone()

    def ejecutar(self, sql: str, parametros: tuple = ()) -> int:
        """Ejecuta una escritura. Devuelve el id insertado o el nº de filas afectadas."""
        with self.conexion() as con:
            cursor = con.execute(sql, parametros)
            return cursor.lastrowid if sql.lstrip().upper().startswith("INSERT") else cursor.rowcount
