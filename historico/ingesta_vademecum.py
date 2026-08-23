"""
Ingesta del vademécum a Qdrant.
Trabajo Final · Módulo 04 · Diplomado IA Generativa FEN.

Estrategia de chunking: una ficha = un chunk. El dataset tiene campos
cortos y estructurados (220 filas), por lo que NO se usa un text splitter:
partir una ficha rompería su coherencia semántica sin ganancia alguna.

Decisión de seguridad (criterio 5 de la rúbrica): los campos de dosis y
datos comerciales quedan SOLO en el payload, fuera del texto embebido.
Así el modelo generador nunca los recibe como contexto y no puede
repetirlos. El guardrail de salida es la segunda capa de esta defensa.
"""

import os
import uuid

import httpx
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLLECTION = "vademecum_farmacias"
EMBED_MODEL = "text-embedding-3-small"

openai_client = OpenAI()

# Campos que SÍ alimentan el retrieval y el contexto del modelo.
CAMPOS_TEXTO = [
    "Drug Name",
    "Generic Name",
    "Drug Class",
    "Indications",
    "Route of Administration",
    "Mechanism of Action",
    "Side Effects",
    "Contraindications",
    "Interactions",
    "Warnings and Precautions",
    "Pregnancy Category",
]

# Campos que quedan solo como metadatos: dosis y datos comerciales.
CAMPOS_PAYLOAD = [
    "Drug ID",
    "Strength",
    "Dosage Form",
    "Price",
    "Manufacturer",
    "Availability",
    "NDC",
    "Approval Date",
    "Storage Conditions",
]


def construir_texto(fila: pd.Series) -> str:
    """Concatena los campos permitidos en un texto legible para embeber."""
    partes = []
    for campo in CAMPOS_TEXTO:
        valor = fila.get(campo)
        if pd.notna(valor) and str(valor).strip():
            partes.append(f"{campo}: {str(valor).strip()}")
    return "\n".join(partes)


def embeber(textos: list[str]) -> list[list[float]]:
    """Genera embeddings en lote (más barato y rápido que uno por uno)."""
    resp = openai_client.embeddings.create(model=EMBED_MODEL, input=textos)
    return [d.embedding for d in resp.data]


def subir_puntos(puntos: list[dict]) -> None:
    r = httpx.put(
        f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true",
        headers=HEADERS,
        json={"points": puntos},
        timeout=120,
    )
    r.raise_for_status()


def main() -> None:
    df = pd.read_csv("data/DrugData.csv")
    print(f"Filas leídas: {len(df)}")

    # Validación: descartar filas sin nombre de medicamento.
    antes = len(df)
    df = df[df["Drug Name"].notna() & (df["Drug Name"].str.strip() != "")]
    if len(df) < antes:
        print(f"Descartadas {antes - len(df)} filas sin 'Drug Name'.")

    # Deduplicación por texto embebido.
    # Los 11 campos farmacológicos son idénticos entre presentaciones del
    # mismo medicamento; solo varían Strength, Price, NDC y Approval Date.
    # Sin deduplicar, una consulta gastaría el top-k en vectores idénticos.
    #
    # Los campos que varían se guardan como lista de OBJETOS (un objeto por
    # registro original), no como listas paralelas: así se conserva qué
    # precio y qué NDC corresponden a cada concentración. Con listas
    # paralelas esa correspondencia se pierde al deduplicar cada campo
    # por separado.
    CAMPOS_REGISTRO = ["Strength", "Dosage Form", "Price", "NDC",
                       "Manufacturer", "Approval Date"]
    CAMPOS_FIJOS = ["Availability", "Storage Conditions"]

    def limpiar(valor):
        return None if pd.isna(valor) else str(valor).strip()

    fichas: dict[str, dict] = {}
    for _, fila in df.iterrows():
        texto = construir_texto(fila)
        if texto not in fichas:
            payload = {"texto": texto, "presentaciones": []}
            for campo in CAMPOS_FIJOS:
                payload[campo] = limpiar(fila.get(campo))
            fichas[texto] = payload

        # Un objeto por registro original del CSV.
        registro = {campo: limpiar(fila.get(campo)) for campo in CAMPOS_REGISTRO}
        registro["drug_id"] = limpiar(fila.get("Drug ID"))
        fichas[texto]["presentaciones"].append(registro)

    # n_registros queda derivado, no duplicado como campo aparte.
    for ficha in fichas.values():
        ficha["n_registros"] = len(ficha["presentaciones"])

    print(f"Fichas únicas tras deduplicar: {len(fichas)} (desde {len(df)} filas)")
    textos = list(fichas.keys())

    # Procesamos en lotes para no exceder límites de la API de embeddings.
    LOTE = 50
    total = 0
    for i in range(0, len(textos), LOTE):
        sub_textos = textos[i : i + LOTE]
        vectores = embeber(sub_textos)

        puntos = [
            {"id": str(uuid.uuid4()), "vector": vector, "payload": fichas[texto]}
            for texto, vector in zip(sub_textos, vectores)
        ]

        subir_puntos(puntos)
        total += len(puntos)
        print(f"Subidos {total}/{len(textos)} puntos.")

    # Verificación final contra el servidor.
    r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    print(f"Puntos indexados en Qdrant: {r.json()['result']['points_count']}")


if __name__ == "__main__":
    main()