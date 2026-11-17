"""
Importa a la memoria nueva (SQLite) los recuerdos de la versión antigua de JARVIS,
que los guardaba en ChromaDB.

Uso:
    python scripts/importar_recuerdos_chroma.py "..\\Proyecto_JARVIS\\jarvis_db"
    python scripts/importar_recuerdos_chroma.py "..\\Proyecto_JARVIS\\jarvis_db" --ver   (solo muestra, no importa)

No modifica la base de datos antigua. Los recuerdos repetidos y los que parezcan
datos sensibles (contraseñas, claves...) se omiten.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    analizador = argparse.ArgumentParser(description="Importa recuerdos de la versión con ChromaDB.")
    analizador.add_argument("carpeta", help="carpeta jarvis_db de la versión antigua")
    analizador.add_argument("--ver", action="store_true", help="solo mostrar lo que se importaría")
    opciones = analizador.parse_args()

    if not os.path.isdir(opciones.carpeta):
        print(f"No existe la carpeta: {opciones.carpeta}")
        return 1
    try:
        import chromadb
    except ImportError:
        print("Hace falta el paquete chromadb para leer la base antigua (pip install chromadb).")
        return 1

    cliente = chromadb.PersistentClient(path=opciones.carpeta)
    try:
        coleccion = cliente.get_collection("memoria_core")
    except Exception:
        print("La base antigua no contiene la colección «memoria_core».")
        return 1
    datos = coleccion.get(include=["documents", "metadatas"])
    antiguos = []
    for texto, meta in zip(datos.get("documents") or [], datos.get("metadatas") or []):
        if texto and (meta or {}).get("activo", True):
            antiguos.append((texto, (meta or {}).get("categoria")))
    print(f"Recuerdos activos en la versión antigua: {len(antiguos)}")
    if opciones.ver:
        for texto, categoria in antiguos:
            print(f"  [{categoria or 'sin categoría'}] {texto}")
        return 0

    from tools.memory import MemoriaLargoPlazo
    memoria = MemoriaLargoPlazo()
    importados = 0
    for texto, categoria in antiguos:
        resultado = memoria.guardar(texto, categoria)
        if resultado["ok"]:
            importados += 1
        else:
            print(f"  omitido ({resultado['motivo']}): {texto}")
    print(f"Importados: {importados}. Total en la memoria nueva: {memoria.contar()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
