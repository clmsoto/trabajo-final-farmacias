"""
Cliente mínimo de Qdrant vía API REST.
Evita qdrant-client porque su dependencia gRPC está bloqueada
por la política de Control de aplicaciones de Windows.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}


def crear_coleccion(nombre: str, size: int = 1536, distance: str = "Cosine") -> None:
    r = httpx.put(
        f"{QDRANT_URL}/collections/{nombre}",
        headers=HEADERS,
        json={"vectors": {"size": size, "distance": distance}},
        timeout=30,
    )
    r.raise_for_status()
    print(f"Colección '{nombre}' creada o ya existente.")


def info_coleccion(nombre: str) -> dict:
    r = httpx.get(f"{QDRANT_URL}/collections/{nombre}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


if __name__ == "__main__":
    crear_coleccion("vademecum_farmacias")
    info = info_coleccion("vademecum_farmacias")
    print(f"Puntos indexados: {info['points_count']}")