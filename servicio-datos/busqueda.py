"""
Búsqueda semántica sobre el vademécum indexado.

Vive en el servicio de datos, no en el asistente: el servicio es dueño
del dominio de datos completo y el asistente solo consume su API.

No hay traducción de la consulta. El corpus anterior estaba en inglés y
había que traducir para igualar el idioma; este está en español, así que
la consulta se embebe tal como llega.
"""

import os

import httpx
from dotenv import load_dotenv
from openai import OpenAI

# Se carga acá y no solo en api_datos: este módulo lee las variables al
# importarse, y el import puede ocurrir antes de que el servicio las cargue.
load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")

HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLECCION = "vademecum_cl"
EMBED_MODEL = "text-embedding-3-small"

# Umbral mínimo de similitud. Por debajo se considera que el corpus no
# tiene información pertinente y se devuelve vacío, en vez de entregar
# la ficha menos mala disponible.
UMBRAL = 0.30

# Si el primer resultado domina por este margen, se devuelve solo ese:
# citar fichas que no respaldan la respuesta debilita la trazabilidad.
MARGEN_DOMINANCIA = 0.10

_openai = OpenAI()


def _embeber(texto: str) -> list[float]:
    return _openai.embeddings.create(model=EMBED_MODEL, input=[texto]).data[0].embedding


def buscar(consulta: str, k: int = 3) -> list[dict]:
    """Devuelve los principios activos más pertinentes, con su puntaje."""
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLECCION}/points/search",
        headers=HEADERS,
        json={"vector": _embeber(consulta), "limit": k, "with_payload": True},
        timeout=30,
    )
    r.raise_for_status()
    crudos = r.json()["result"]

    pertinentes = [x for x in crudos if x["score"] >= UMBRAL]
    if len(pertinentes) > 1:
        if pertinentes[0]["score"] - pertinentes[1]["score"] > MARGEN_DOMINANCIA:
            pertinentes = pertinentes[:1]

    return [
        {
            "atc": x["payload"]["atc"],
            "score": round(x["score"], 4),
            "secciones": x["payload"]["secciones"],
            "productos": x["payload"]["productos"][:8],
            "n_productos": x["payload"]["n_productos"],
        }
        for x in pertinentes
    ]


def obtener(atc: str) -> dict | None:
    """Recupera un principio activo por su nombre exacto."""
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLECCION}/points/scroll",
        headers=HEADERS,
        json={
            "filter": {"must": [{"key": "atc", "match": {"value": atc}}]},
            "limit": 1,
            "with_payload": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    puntos = r.json()["result"]["points"]
    if not puntos:
        return None

    p = puntos[0]["payload"]
    return {
        "atc": p["atc"],
        "secciones": p["secciones"],
        "productos": p["productos"],
        "n_productos": p["n_productos"],
    }


if __name__ == "__main__":
    for consulta in [
        "contraindicaciones del ibuprofeno",
        "medicamento para la presión alta",
        "¿se puede tomar paracetamol en el embarazo?",
        "cuál es la capital de Francia",
    ]:
        print(f"\n{'=' * 70}\n{consulta}")
        for r in buscar(consulta):
            secs = ", ".join(s["seccion"] for s in r["secciones"][:3])
            print(f"  {r['score']}  {r['atc']:32} [{secs}…]")
        else:
            pass