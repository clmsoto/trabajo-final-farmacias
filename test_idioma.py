"""
Compara retrieval con consulta en español directo vs. traducida al inglés.
Sirve para justificar empíricamente la decisión de diseño en el informe.
"""

import os

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLLECTION = "vademecum_farmacias"

client = OpenAI()


def embeber(texto: str) -> list[float]:
    return client.embeddings.create(
        model="text-embedding-3-small", input=[texto]
    ).data[0].embedding


def traducir(texto: str) -> str:
    r = client.chat.completions.create(
        model="gpt-5.6-luna",
        messages=[
            {
                "role": "system",
                "content": "Traduce al inglés. Responde solo con la traducción.",
            },
            {"role": "user", "content": texto},
        ],
    )
    return r.choices[0].message.content.strip()


def buscar(vector: list[float], k: int = 3) -> list[tuple[str, float]]:
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
        headers=HEADERS,
        json={"vector": vector, "limit": k, "with_payload": True},
        timeout=30,
    )
    r.raise_for_status()
    return [
        (p["payload"]["texto"].split("\n")[0], round(p["score"], 4))
        for p in r.json()["result"]
    ]


CONSULTAS = [
    "¿Para qué sirve la aspirina?",
    "¿Qué efectos secundarios tiene un antibiótico?",
    "medicamento para la presión alta",
    "¿Qué contraindicaciones tiene el ibuprofeno?",
]

for consulta in CONSULTAS:
    print(f"\n{'=' * 70}\nCONSULTA: {consulta}")

    print("\n  [ES directo]")
    for nombre, score in buscar(embeber(consulta)):
        print(f"    {score}  {nombre}")

    en = traducir(consulta)
    print(f"\n  [EN traducido] -> {en}")
    for nombre, score in buscar(embeber(en)):
        print(f"    {score}  {nombre}")