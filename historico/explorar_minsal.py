"""
Exploración del endpoint de turnos de MINSAL.
Solo para inspeccionar la estructura real antes de escribir la tool.
"""

import json
from collections import Counter

import httpx

URL = "https://midas.minsal.cl/farmacia_v2/WS/getLocalesTurnos.php"

r = httpx.get(URL, timeout=20)
print(f"HTTP {r.status_code} · {len(r.content)} bytes")

datos = r.json()
print(f"Registros: {len(datos)}")
print(f"\nCampos del primer registro:")
print(json.dumps(datos[0], indent=2, ensure_ascii=False))

print(f"\nComunas presentes ({len(set(d.get('comuna_nombre') for d in datos))}):")
for comuna, n in Counter(d.get("comuna_nombre") for d in datos).most_common(15):
    print(f"  {n:3}  {comuna}")

print("\nRango de horarios observados:")
aperturas = Counter(d.get("funcionamiento_hora_apertura") for d in datos)
cierres = Counter(d.get("funcionamiento_hora_cierre") for d in datos)
print(f"  Aperturas: {dict(aperturas.most_common(5))}")
print(f"  Cierres:   {dict(cierres.most_common(5))}")

print("\nFechas presentes:")
print(f"  {dict(Counter(d.get('fecha') for d in datos).most_common(5))}")