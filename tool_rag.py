"""
Herramienta de recuperación semántica sobre el vademécum.

Flujo: consulta en español → traducción al inglés → embedding →
búsqueda en Qdrant → contexto formateado con cita.

La traducción previa se justifica en la medición documentada:
la coincidencia monolingüe mejora la similitud en todas las
consultas de prueba.
"""

import os

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLLECTION = "vademecum_farmacias"
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-5.6-luna"

# Umbral mínimo de similitud. Por debajo, se considera que el corpus
# no tiene información pertinente y se responde que no se encontró,
# en vez de entregar la ficha menos mala disponible.
UMBRAL = 0.35

client = OpenAI()


def traducir_consulta(texto: str) -> str:
    """Traduce la consulta al inglés para igualar el idioma del corpus."""
    r = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Traduce la consulta del usuario al inglés, conservando "
                    "términos farmacológicos. Responde solo con la traducción."
                ),
            },
            {"role": "user", "content": texto},
        ],
    )
    return r.choices[0].message.content.strip()


def embeber(texto: str) -> list[float]:
    return (
        client.embeddings.create(model=EMBED_MODEL, input=[texto])
        .data[0]
        .embedding
    )


def buscar(vector: list[float], k: int = 3) -> list[dict]:
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
        headers=HEADERS,
        json={"vector": vector, "limit": k, "with_payload": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]


def _nombre_ficha(texto: str) -> str:
    """Extrae el nombre del medicamento desde la primera línea del chunk."""
    primera = texto.split("\n")[0]
    return primera.replace("Drug Name:", "").strip()


def recuperar_contexto(consulta_es: str, k: int = 3) -> dict:
    """
    Devuelve el contexto para el modelo generador y las citas asociadas.

    Retorna un dict con:
      - contexto: texto concatenado de las fichas (sin dosis, por diseño
        de la ingesta: Strength y Dosage Form no están en el embebido)
      - citas: lista de nombres de ficha con su score, para mostrar al usuario
      - hay_resultados: False si nada superó el umbral
    """
    consulta_en = traducir_consulta(consulta_es)
    resultados = buscar(embeber(consulta_en), k=k)

    pertinentes = [r for r in resultados if r["score"] >= UMBRAL]

    # Si el primer resultado domina claramente, se cita solo ese: incluir
    # fichas que el modelo no llegó a usar debilita la trazabilidad de la
    # cita, que es justamente lo que la cita debería garantizar.
    if len(pertinentes) > 1:
        top, segundo = pertinentes[0]["score"], pertinentes[1]["score"]
        if top - segundo > 0.10:
            pertinentes = pertinentes[:1]

    if not pertinentes:
        return {
            "contexto": "",
            "citas": [],
            "hay_resultados": False,
            "consulta_traducida": consulta_en,
        }

    bloques, citas = [], []
    for r in pertinentes:
        texto = r["payload"]["texto"]
        nombre = _nombre_ficha(texto)
        bloques.append(f"--- Ficha: {nombre} ---\n{texto}")
        citas.append({"ficha": nombre, "score": round(r["score"], 4)})

    return {
        "contexto": "\n\n".join(bloques),
        "citas": citas,
        "hay_resultados": True,
        "consulta_traducida": consulta_en,
    }

if __name__ == "__main__":
    for consulta in [
        "¿Qué contraindicaciones tiene el ibuprofeno?",
        "¿Para qué sirve el omeprazol?",
        "¿Cómo se llama la capital de Francia?",  # debe quedar bajo el umbral
    ]:
        print(f"\n{'=' * 70}\nCONSULTA: {consulta}")
        res = recuperar_contexto(consulta)
        print(f"Traducida: {res['consulta_traducida']}")
        if res["hay_resultados"]:
            for c in res["citas"]:
                print(f"  {c['score']}  {c['ficha']}")
        else:
            print("  Sin resultados sobre el umbral.")