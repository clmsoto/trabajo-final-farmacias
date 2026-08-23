"""
Crea la colección de Qdrant para el vademécum.
Se ejecuta una sola vez; es idempotente (no falla si ya existe).
"""

import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

load_dotenv()

COLLECTION_NAME = "vademecum_farmacias"

client = QdrantClient(
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
)

if not client.collection_exists(COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        # 1536 = dimensión de text-embedding-3-small.
        # Si más adelante cambian de modelo de embeddings, hay que
        # recrear la colección con la dimensión nueva.
        vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
    )
    print(f"Colección '{COLLECTION_NAME}' creada.")
else:
    print(f"Colección '{COLLECTION_NAME}' ya existe.")

# Verificación: muestra la configuración quedada en el servidor.
info = client.get_collection(COLLECTION_NAME)
print(f"Puntos indexados: {info.points_count}")