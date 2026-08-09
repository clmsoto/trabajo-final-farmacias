"""Captura una muestra del endpoint como respaldo para el fallback."""

import json
from pathlib import Path

import httpx

r = httpx.get("https://midas.minsal.cl/farmacia_v2/WS/getLocalesTurnos.php", timeout=20)
r.raise_for_status()
datos = r.json()

Path("data").mkdir(exist_ok=True)
with open("data/snapshot_turnos.json", "w", encoding="utf-8") as fh:
    json.dump(datos, fh, ensure_ascii=False, indent=2)

print(f"Snapshot guardado: {len(datos)} registros.")