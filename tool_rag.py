"""
Herramienta de recuperación semántica sobre el vademécum.

Ya no embebe ni consulta la base vectorial: delega en el servicio de
datos, que es dueño del corpus. El umbral y la regla de dominancia
viven allá; acá solo se arma el contexto y las citas.

La cita es por principio activo y no por nombre comercial: el 66% de
los nombres del vademécum lleva la dosis adentro ("IBUPROFENO
COMPRIMIDOS 500 mg"), y citarla activaría el guardrail de salida.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

URL_DATOS = os.environ.get("URL_DATOS", "http://localhost:8100").rstrip("/")
TIMEOUT = 15.0


def recuperar_contexto(consulta: str, k: int = 3) -> dict:
    """
    Devuelve el contexto para el modelo generador y las citas asociadas.

    Retorna un dict con:
      - contexto: secciones farmacológicas de los principios activos
        pertinentes (sin posología: se excluyó en la ingesta)
      - citas: principios activos con su puntaje de similitud
      - hay_resultados: False si nada superó el umbral del servicio
    """
    try:
        r = httpx.get(
            f"{URL_DATOS}/vademecum/buscar",
            params={"q": consulta, "k": k},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        resultados = r.json()["resultados"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        print(f"[rag] Fallo la consulta al servicio de datos ({type(e).__name__}: {e})")
        return {"contexto": "", "citas": [], "hay_resultados": False}

    if not resultados:
        return {"contexto": "", "citas": [], "hay_resultados": False}

    bloques, citas = [], []
    for res in resultados:
        cuerpo = "\n".join(
            f"{s['seccion']}: {s['contenido']}" for s in res["secciones"]
        )
        bloques.append(f"--- Principio activo: {res['atc']} ---\n{cuerpo}")
        citas.append({"ficha": res["atc"], "score": res["score"]})

    return {
        "contexto": "\n\n".join(bloques),
        "citas": citas,
        "hay_resultados": True,
    }


if __name__ == "__main__":
    for consulta in [
        "¿Qué contraindicaciones tiene el ibuprofeno?",
        "medicamento para la presión alta",
        "¿Cómo se llama la capital de Francia?",
    ]:
        print(f"\n{'=' * 70}\nCONSULTA: {consulta}")
        res = recuperar_contexto(consulta)
        if res["hay_resultados"]:
            for c in res["citas"]:
                print(f"  {c['score']}  {c['ficha']}")
        else:
            print("  Sin resultados sobre el umbral.")