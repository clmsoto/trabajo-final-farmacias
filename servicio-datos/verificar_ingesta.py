"""
Verificación posterior a la ingesta.

Dos preguntas: qué fichas quedaron sin texto y por qué, y si alguna
sección indexada conserva una dosis pese al filtro.
"""

import collections
import json
import os

import httpx
from dotenv import load_dotenv

from ingesta_vademecum_cl import (
    ATC_EXCLUIDOS,
    PATRON_DOSIS,
    RUTA_JSON,
    construir_texto,
)

load_dotenv()
QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLECCION = "vademecum_cl"

# --- 1. Fichas que quedaron sin texto embebible -----------------------------

with open(RUTA_JSON, encoding="utf-8") as fh:
    datos = json.load(fh)

vacios = [
    x for x in datos
    if (x.get("atc") or "").strip()
    and x.get("atc") not in ATC_EXCLUIDOS
    and not construir_texto(x)[0]
]
print(f"Fichas sin texto embebible: {len(vacios)}")
for atc, n in collections.Counter(x["atc"] for x in vacios).most_common(10):
    print(f"  {n:4}  {atc}")

# --- 2. ¿Se coló alguna dosis en lo indexado? -------------------------------

r = httpx.post(
    f"{QDRANT_URL}/collections/{COLECCION}/points/scroll",
    headers=HEADERS,
    json={"limit": 2000, "with_payload": True},
    timeout=120,
)
r.raise_for_status()
puntos = r.json()["result"]["points"]
print(f"\nPuntos revisados: {len(puntos)}")

con_dosis = []
for p in puntos:
    for s in p["payload"].get("secciones", []):
        m = PATRON_DOSIS.search(s["contenido"])
        if m:
            con_dosis.append((p["payload"]["atc"], s["seccion"], m.group(0)))

print(f"Secciones con patrón de dosis: {len(con_dosis)}")
for atc, sec, hallazgo in con_dosis[:15]:
    print(f"  {atc[:28]:30} {sec[:26]:28} → {hallazgo!r}")

# --- 3. Muestra de una ficha conocida ---------------------------------------

muestra = next((p for p in puntos if p["payload"]["atc"] == "Paracetamol"), None)
if muestra:
    print("\n--- Paracetamol: secciones indexadas ---")
    for s in muestra["payload"]["secciones"]:
        print(f"\n[{s['seccion']}]")
        print(s["contenido"][:260])
    print(f"\nProductos comerciales asociados: {muestra['payload']['n_productos']}")