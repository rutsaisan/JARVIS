"""
Memoria a largo plazo de JARVIS (v3)
=====================================
Guarda los recuerdos en SQLite (data/jarvis.db) con el esquema de la entidad
«recuerdos» del plan técnico: id, clave, valor, categoría, confianza,
fecha_creación, fecha_actualización y activo (borrado lógico: «olvidar» no
destruye el dato de forma irreversible).

La búsqueda combina dos señales:
  - léxica: palabras clave compartidas (siempre disponible, sin dependencias);
  - semántica: vectores de `nomic-embed-text` vía Ollama, si está en marcha.

Principios de privacidad (sección 9 del plan):
  - Solo se guarda lo que la usuaria pide o autoriza de forma explícita.
  - No se guardan contraseñas, claves ni datos bancarios.
  - Todo recuerdo se puede consultar, modificar y borrar.

Operaciones disponibles (misma API que la v2 + añadidos):
    guardar_recuerdo(texto)            -> crear
    buscar_contexto(consulta)          -> leer (lo usa el núcleo conversacional)
    listar_recuerdos(categoria=None)   -> leer (todos / por categoría)
    editar_recuerdo(consulta, nuevo)   -> actualizar
    borrar_recuerdo(consulta)          -> borrar (borrado lógico)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import numpy as np

from core.config import dir_datos
from core.database import BaseDatos
from core.embeddings import Embeddings, a_bytes, desde_bytes
from core.text import normalizar, plano, similitud_lexica

registro = logging.getLogger("jarvis.memoria")

# Relevancia mínima (0..1) para inyectar un recuerdo en la conversación…
UMBRAL_CONTEXTO = 0.5
# …y para considerar que un recuerdo es el que la usuaria quiere editar o borrar.
UMBRAL_OBJETIVO = 0.35

# Rango en el que el parecido semántico (coseno) pasa de «nada que ver» a «lo mismo».
# Calibrado con nomic-embed-text y frases en español.
COSENO_MINIMO, COSENO_MAXIMO = 0.55, 0.80

CATEGORIAS_PALABRAS_CLAVE = {
    "preferencias": ["me gusta", "me gustan", "prefiero", "odio", "no me gusta", "favorito", "favorita",
                     "detesto", "me encanta", "me encantan", "no soporto"],
    "personales": ["cumpleanos", "vivo en", "trabajo en", "trabajo de", "mi nombre es", "me llamo", "soy de",
                   "mi direccion", "mi telefono", "mi email", "mi correo", "naci en", "se llama", "estudio",
                   "soy alergic", "mi familia", "mi pareja"],
    "tareas": ["tengo que", "no olvidar", "pendiente", "reunion", "cita", "entrega", "examen", "recordatorio"],
}
CATEGORIA_POR_DEFECTO = "información"
CATEGORIAS = ["personales", "preferencias", "tareas", CATEGORIA_POR_DEFECTO]

PREFIJOS_GUARDAR = sorted((
    "recuerda que", "recuerda", "memoriza que", "memoriza", "guarda que", "guarda", "apunta que", "apunta",
    "anota que", "anota", "no olvides que", "ten en cuenta que",
), key=len, reverse=True)

_DATO_SENSIBLE = re.compile(
    r"contrasena|password|passwd|\bpin\b|\btoken\b|api ?key|clave secreta|mi clave es|"
    r"clave (?:de|del|de la) (?:acceso|wifi|internet|correo|banco|cuenta|ordenador|movil|telefono|tarjeta)|"
    r"\bcvv\b|\biban\b|numero de (?:la )?(?:cuenta|tarjeta)|tarjeta de (?:credito|debito)|"
    r"\b(?:\d[ -]?){13,19}\b"
)

# "<de qué trata> <es|son|se llama…> <valor>"
_COPULA = re.compile(r"^(.*?)\s*\b(es|son|esta|estan|era|eran|sera|seran|se llama|se llaman)\s+(.+)$")
_FRASE_COMPLETA = re.compile(r"\b(?:es|son|esta|estan|se llama|gusta|gustan|prefiero|vivo|trabajo|tengo|soy)\b")
_PATRONES_CATEGORIA = {
    categoria: re.compile(r"\b(?:" + "|".join(re.escape(palabra) for palabra in palabras) + r")")
    for categoria, palabras in CATEGORIAS_PALABRAS_CLAVE.items()
}


def _detectar_categoria(texto: str) -> str:
    texto_plano = plano(texto)
    for categoria, patron in _PATRONES_CATEGORIA.items():
        if patron.search(texto_plano):
            return categoria
    return CATEGORIA_POR_DEFECTO


def es_dato_sensible(texto: str) -> bool:
    """True si el texto parece contener contraseñas, claves o datos bancarios."""
    return bool(_DATO_SENSIBLE.search(plano(texto)))


def _separar_clave_valor(texto: str) -> tuple:
    """"mi color favorito es el morado" -> ("mi color favorito", "el morado")."""
    coincidencia = _COPULA.match(plano(texto))
    if not coincidencia:
        return None, texto
    clave = texto[:coincidencia.end(1)].strip()
    return clave or None, texto[coincidencia.start(3):].strip()


def _recomponer(texto_antiguo: str, objetivo: str, nuevo_valor: str) -> str:
    """Construye el recuerdo actualizado sin perder de qué trataba.

    "mi color favorito es el morado" + "verde" -> "mi color favorito es verde"
    """
    nuevo_plano = normalizar(nuevo_valor)
    if len(nuevo_plano.split()) >= 4 and _FRASE_COMPLETA.search(nuevo_plano):
        return nuevo_valor                       # ya es una frase completa: se usa tal cual
    coincidencia = _COPULA.match(plano(texto_antiguo))
    if coincidencia and coincidencia.group(1):
        return f"{texto_antiguo[:coincidencia.end(2)]} {nuevo_valor}"
    if objetivo:
        return f"{objetivo}: {nuevo_valor}"
    return nuevo_valor


class MemoriaLargoPlazo:
    def __init__(self, db_path: str | Path | None = None, embeddings: Embeddings | None = None,
                 base_datos: BaseDatos | None = None, indexar_en_segundo_plano: bool = False):
        if base_datos is not None:
            self.bd = base_datos
        else:
            ruta = Path(db_path) if db_path else dir_datos() / "jarvis.db"
            if ruta.suffix != ".db":             # compatibilidad: la v2 recibía una carpeta
                ruta = ruta / "jarvis.db"
            self.bd = BaseDatos(ruta)
        self.embeddings = embeddings if embeddings is not None else Embeddings()
        # En la aplicación, guardar un recuerdo no espera a Ollama: el vector se
        # calcula después, en otro hilo. Así «recuerda que...» responde al instante
        # aunque el modelo esté ocupado o todavía cargándose.
        self.indexar_en_segundo_plano = indexar_en_segundo_plano
        self._cerrojo_indice = threading.Lock()

    def _vector_inmediato(self, texto: str):
        return None if self.indexar_en_segundo_plano else self.embeddings.vector(texto, "documento")

    def _indexar_luego(self) -> None:
        if self.indexar_en_segundo_plano:
            threading.Thread(target=self.reindexar, name="jarvis-indice", daemon=True).start()

    # ------------------------------------------------------------------
    # CREAR
    # ------------------------------------------------------------------
    def guardar(self, texto: str, categoria: str | None = None, origen: str = "usuario") -> dict:
        """Guarda un recuerdo. Devuelve {"ok", "mensaje", "id", "motivo"}."""
        texto_limpio = re.sub(r"\s+", " ", texto or "").strip().strip(".").strip()
        texto_plano = plano(texto_limpio)
        for prefijo in PREFIJOS_GUARDAR:
            if texto_plano == prefijo or texto_plano.startswith(prefijo + " "):
                texto_limpio = texto_limpio[len(prefijo):].strip()
                break

        if not texto_limpio:
            return {"ok": False, "id": None, "motivo": "vacio",
                    "mensaje": "No he detectado qué debo recordar exactamente."}
        if es_dato_sensible(texto_limpio):
            return {"ok": False, "id": None, "motivo": "sensible",
                    "mensaje": "Eso parece un dato sensible (contraseñas, claves o datos bancarios). "
                               "Por seguridad no lo guardo en mi memoria."}
        clave_comparacion = normalizar(texto_limpio)
        for existente in self._activos():
            if normalizar(existente["texto"]) == clave_comparacion:
                return {"ok": False, "id": existente["id"], "motivo": "duplicado",
                        "mensaje": "Eso ya lo tenía guardado en mi memoria."}

        categoria_final = categoria if categoria in CATEGORIAS else _detectar_categoria(texto_limpio)
        clave, valor = _separar_clave_valor(texto_limpio)
        ahora = time.time()
        vector = self._vector_inmediato(texto_limpio)
        try:
            nuevo_id = self.bd.ejecutar(
                "INSERT INTO recuerdos (clave, valor, texto, categoria, origen, fecha_creacion, "
                "fecha_actualizacion, activo, embedding, modelo_embedding) VALUES (?,?,?,?,?,?,?,1,?,?)",
                (clave, valor, texto_limpio, categoria_final, origen, ahora, ahora,
                 a_bytes(vector), self.embeddings.modelo if vector is not None else None),
            )
        except Exception as error:
            registro.error("Error al guardar un recuerdo: %s", error)
            return {"ok": False, "id": None, "motivo": "error",
                    "mensaje": "Ha habido un error en mis bancos de memoria."}
        registro.info("Recuerdo guardado (categoría %s).", categoria_final)
        self._indexar_luego()
        return {"ok": True, "id": nuevo_id, "motivo": None, "categoria": categoria_final, "texto": texto_limpio,
                "mensaje": f"Dato almacenado en mi memoria permanente. Categoría: {categoria_final}."}

    def guardar_recuerdo(self, texto: str, categoria: str | None = None) -> str:
        return self.guardar(texto, categoria)["mensaje"]

    # ------------------------------------------------------------------
    # LEER — búsqueda por relevancia
    # ------------------------------------------------------------------
    def buscar(self, consulta: str, n: int = 3, umbral: float = UMBRAL_CONTEXTO) -> list:
        """Recuerdos activos ordenados por relevancia (0..1), por encima del umbral."""
        activos = self._activos(con_vector=True)
        if not activos or not (consulta or "").strip():
            return []
        vector_consulta = None
        if any(r["_vector"] is not None for r in activos):
            vector_consulta = self.embeddings.vector(consulta, "consulta")

        puntuados = []
        for recuerdo in activos:
            lexica = similitud_lexica(consulta, recuerdo["texto"])
            semantica = 0.0
            vector = recuerdo.pop("_vector")
            if (vector_consulta is not None and vector is not None
                    and recuerdo.pop("_modelo", None) == self.embeddings.modelo
                    and vector.shape == vector_consulta.shape):
                coseno = float(np.dot(vector_consulta, vector))
                semantica = max(0.0, min(1.0, (coseno - COSENO_MINIMO) / (COSENO_MAXIMO - COSENO_MINIMO)))
            recuerdo.pop("_modelo", None)
            relevancia = max(lexica, semantica)
            registro.debug("Relevancia %.2f (léxica %.2f, semántica %.2f): %s",
                           relevancia, lexica, semantica, recuerdo["texto"])
            if relevancia >= umbral:
                recuerdo["relevancia"] = round(relevancia, 3)
                puntuados.append(recuerdo)
        puntuados.sort(key=lambda r: r["relevancia"], reverse=True)
        return puntuados[:max(n, 1)]

    def buscar_contexto(self, consulta: str, n_resultados: int = 1) -> str:
        """Texto con los recuerdos relevantes para una frase ("" si no hay ninguno)."""
        return "; ".join(r["texto"] for r in self.buscar(consulta, n_resultados, UMBRAL_CONTEXTO))

    def mejor_coincidencia(self, consulta: str) -> dict | None:
        """El recuerdo activo que mejor encaja con lo que describe la usuaria, o None."""
        resultados = self.buscar(consulta, 1, UMBRAL_OBJETIVO)
        return resultados[0] if resultados else None

    # ------------------------------------------------------------------
    # LEER — listado (panel de memoria / "qué recuerdas de mí")
    # ------------------------------------------------------------------
    def listar_recuerdos(self, categoria: str | None = None, buscar: str | None = None) -> list:
        activos = self._activos()
        if categoria:
            activos = [r for r in activos if r["categoria"] == categoria]
        if buscar and buscar.strip():
            aguja = normalizar(buscar)
            activos = [r for r in activos if aguja in normalizar(r["texto"])]
        activos.sort(key=lambda r: (r["fecha_creacion"], r["id"]), reverse=True)
        return activos

    def obtener(self, recuerdo_id: int) -> dict | None:
        fila = self.bd.uno("SELECT * FROM recuerdos WHERE id = ? AND activo = 1", (recuerdo_id,))
        return self._a_dict(fila) if fila else None

    def contar(self) -> int:
        return self.bd.uno("SELECT COUNT(*) AS n FROM recuerdos WHERE activo = 1")["n"]

    def conteo_por_categoria(self) -> dict:
        filas = self.bd.consultar("SELECT categoria, COUNT(*) AS n FROM recuerdos WHERE activo = 1 GROUP BY categoria")
        return {fila["categoria"]: fila["n"] for fila in filas}

    # ------------------------------------------------------------------
    # ACTUALIZAR
    # ------------------------------------------------------------------
    def actualizar(self, recuerdo_id: int, texto: str | None = None, categoria: str | None = None) -> bool:
        """Edición directa por id (la usa el panel de memoria)."""
        actual = self.obtener(recuerdo_id)
        if not actual:
            return False
        nuevo_texto = re.sub(r"\s+", " ", texto).strip() if texto is not None else actual["texto"]
        if not nuevo_texto or es_dato_sensible(nuevo_texto):
            return False
        if categoria in CATEGORIAS:
            nueva_categoria = categoria
        elif texto is not None and nuevo_texto != actual["texto"]:
            nueva_categoria = _detectar_categoria(nuevo_texto)
        else:
            nueva_categoria = actual["categoria"]
        clave, valor = _separar_clave_valor(nuevo_texto)
        vector = self._vector_inmediato(nuevo_texto)
        self.bd.ejecutar(
            "UPDATE recuerdos SET texto = ?, clave = ?, valor = ?, categoria = ?, fecha_actualizacion = ?, "
            "embedding = ?, modelo_embedding = ? WHERE id = ?",
            (nuevo_texto, clave, valor, nueva_categoria, time.time(), a_bytes(vector),
             self.embeddings.modelo if vector is not None else None, recuerdo_id),
        )
        self._indexar_luego()
        return True

    def editar_recuerdo(self, consulta: str, nuevo_valor: str) -> str:
        nuevo_valor = (nuevo_valor or "").strip()
        if not consulta or not consulta.strip():
            return "No he entendido a qué recuerdo te refieres."
        if not nuevo_valor:
            return "No he entendido el nuevo valor que debo guardar."

        candidato = self.mejor_coincidencia(consulta)
        if not candidato:
            return f"No encuentro ningún recuerdo parecido a «{consulta}» para modificarlo."

        nuevo_texto = _recomponer(candidato["texto"], consulta.strip(), nuevo_valor)
        if es_dato_sensible(nuevo_texto):
            return "Eso parece un dato sensible. Por seguridad no lo guardo en mi memoria."
        if not self.actualizar(candidato["id"], texto=nuevo_texto):
            return "No he podido modificar ese recuerdo."
        registro.info("Recuerdo editado: '%s' -> '%s'", candidato["texto"], nuevo_texto)
        return f"Actualizado. Antes recordaba «{candidato['texto']}»; ahora recuerdo «{nuevo_texto}»."

    # ------------------------------------------------------------------
    # BORRAR (borrado lógico: se marca inactivo, no se pierde el histórico)
    # ------------------------------------------------------------------
    def borrar_por_id(self, recuerdo_id: int) -> bool:
        filas = self.bd.ejecutar(
            "UPDATE recuerdos SET activo = 0, fecha_actualizacion = ? WHERE id = ? AND activo = 1",
            (time.time(), recuerdo_id),
        )
        return filas > 0

    def borrar_recuerdo(self, consulta: str) -> str:
        if not consulta or not consulta.strip():
            return "No he entendido qué recuerdo debo borrar."
        candidato = self.mejor_coincidencia(consulta)
        if not candidato:
            return f"No encuentro ningún recuerdo parecido a «{consulta}»."
        if not self.borrar_por_id(candidato["id"]):
            return "No he podido borrar ese recuerdo."
        registro.info("Recuerdo borrado (lógico): %s", candidato["texto"])
        return f"He olvidado esto: «{candidato['texto']}»."

    def borrar_todo(self, definitivo: bool = False) -> int:
        """Olvida todos los recuerdos. Con definitivo=True se eliminan también del fichero."""
        if definitivo:
            return self.bd.ejecutar("DELETE FROM recuerdos")
        return self.bd.ejecutar("UPDATE recuerdos SET activo = 0, fecha_actualizacion = ? WHERE activo = 1",
                                (time.time(),))

    # ------------------------------------------------------------------
    # MANTENIMIENTO
    # ------------------------------------------------------------------
    def reindexar(self) -> int:
        """Calcula los vectores que falten (recuerdos guardados sin Ollama o con otro modelo)."""
        if not self.embeddings.activo:
            return 0
        with self._cerrojo_indice:               # un solo indexado a la vez
            pendientes = self.bd.consultar(
                "SELECT id, texto FROM recuerdos WHERE activo = 1 "
                "AND (embedding IS NULL OR modelo_embedding IS NOT ?)", (self.embeddings.modelo,))
            if not pendientes:
                return 0
            vectores = self.embeddings.vectores([fila["texto"] for fila in pendientes], "documento")
            if not vectores:
                return 0
            for fila, vector in zip(pendientes, vectores):
                # Solo si el texto no ha cambiado mientras se calculaba su vector.
                self.bd.ejecutar("UPDATE recuerdos SET embedding = ?, modelo_embedding = ? WHERE id = ? AND texto = ?",
                                 (a_bytes(vector), self.embeddings.modelo, fila["id"], fila["texto"]))
            registro.info("Memoria reindexada: %d recuerdos.", len(pendientes))
            return len(pendientes)

    # ------------------------------------------------------------------
    # INTERNOS
    # ------------------------------------------------------------------
    @staticmethod
    def _a_dict(fila, con_vector: bool = False) -> dict:
        recuerdo = {
            "id": fila["id"],
            "documento": fila["texto"],          # nombre que usaba la v2
            "texto": fila["texto"],
            "clave": fila["clave"],
            "valor": fila["valor"],
            "categoria": fila["categoria"],
            "origen": fila["origen"],
            "fecha_creacion": fila["fecha_creacion"],
            "fecha_actualizacion": fila["fecha_actualizacion"],
            "metadata": {"categoria": fila["categoria"], "fecha_creacion": fila["fecha_creacion"],
                         "fecha_actualizacion": fila["fecha_actualizacion"], "activo": bool(fila["activo"])},
        }
        if con_vector:
            recuerdo["_vector"] = desde_bytes(fila["embedding"])
            recuerdo["_modelo"] = fila["modelo_embedding"]
        return recuerdo

    def _activos(self, con_vector: bool = False) -> list:
        try:
            filas = self.bd.consultar("SELECT * FROM recuerdos WHERE activo = 1")
        except Exception as error:
            registro.error("Error al leer la memoria: %s", error)
            return []
        return [self._a_dict(fila, con_vector) for fila in filas]
