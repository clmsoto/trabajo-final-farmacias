"""
Herramienta de consulta de farmacias de turno.

Ya no consulta la fuente ni interpreta horarios: delega en el servicio
de datos. El pipeline de cinco pasos (recibir, normalizar, filtrar,
interpretar, responder) vive allá, que es donde corresponde.

Acá queda solo lo que es responsabilidad del asistente: convertir la
respuesta del servicio en texto para el modelo generador, sin entregarle
el JSON crudo.
"""

import os
import unicodedata
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

URL_DATOS = os.environ.get("URL_DATOS", "http://localhost:8100").rstrip("/")
TIMEOUT = 15.0


def normalizar_texto(valor: str | None) -> str:
    """
    Se conserva acá porque el grafo la usa para comparar la comuna del
    turno actual con la del anterior, antes de decidir si reconsultar.
    """
    if not valor:
        return ""
    texto = str(valor).strip().upper()
    texto = texto.replace("Ñ", "\x00")
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.replace("\x00", "Ñ")
    return re.sub(r"\s+", " ", texto)


def _consultar(params: dict) -> dict | None:
    try:
        r = httpx.get(f"{URL_DATOS}/turnos", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        print(f"[turnos] Fallo la consulta al servicio ({type(e).__name__}: {e})")
        return None


def buscar_turnos(comuna: str) -> dict:
    return _consultar({"comuna": comuna}) or {"error": True}


def buscar_turnos_region(region: str) -> dict:
    return _consultar({"region": region}) or {"error": True}


# ---------------------------------------------------------------------------
# Formateo para el modelo generador
# ---------------------------------------------------------------------------
# No se entrega el JSON crudo: el enunciado lo prohíbe. Cada dato que se
# pasa es uno que la fuente respalda; no se infiere stock ni precio.


def _linea(f: dict) -> str:
    estado = {True: "abierta ahora", False: "cerrada ahora"}.get(
        f["abierta_ahora"], "horario no verificable"
    )
    noct = " (turno nocturno, cierra al día siguiente)" if f["nocturno"] else ""
    tel = f" · Teléfono: {f['telefono']}" if f.get("telefono") else ""
    return (
        f"- {f['nombre']} · {f['direccion']}, {f['comuna']} · "
        f"Horario {f['horario']}{noct} · {estado}{tel}"
    )


def formatear_contexto(resultado: dict) -> str:
    if resultado.get("error"):
        return (
            "El servicio de farmacias de turno no está disponible en este "
            "momento. Informa al usuario que intente más tarde."
        )

    if not resultado.get("encontrada"):
        # Las comunas alternativas no van acá: se adjuntan por código en
        # response_node. Si llegaran por ambas vías, la respuesta las
        # mostraría dos veces.
        return (
            "No hay farmacias de turno registradas para esa comuna en la "
            "fuente. [Nota interna: no listes comunas alternativas ni "
            "advertencias sobre la vigencia del dato: se adjuntan aparte.]"
        )

    lineas = [_linea(f) for f in resultado["farmacias"]]
    lineas.append(
        "[Nota interna: esta fuente informa locales y turnos únicamente. "
        "Si el usuario pregunta por stock, precio o disponibilidad, aclara "
        "que no puedes confirmarlo. No menciones esta limitación si no "
        "viene al caso.]"
    )
    return "\n".join(lineas)


def formatear_contexto_region(resultado: dict) -> str:
    if resultado.get("error"):
        return (
            "El servicio de farmacias de turno no está disponible en este "
            "momento. Informa al usuario que intente más tarde."
        )

    if not resultado.get("encontrada"):
        return (
            f"No se reconoce '{resultado.get('region')}' como una región de "
            "Chile. Pide al usuario que indique una comuna o región válida."
        )

    comunas = resultado.get("comunas") or {}
    if not comunas:
        return (
            f"No hay farmacias de turno vigentes registradas en la región "
            f"{resultado['region']} en este momento."
        )

    lineas = [
    f"Región {resultado['region']}: {resultado['total_locales']} "
    f"farmacia(s) de turno en {len(comunas)} comuna(s)."
    ]
    lineas.append(
        "[Nota interna: NO listes las farmacias. El listado se adjunta "
        "aparte con los datos literales de la fuente. Presenta el total "
        "y ofrece ordenar por cercanía si el usuario indica su comuna.]"
    )
    return "\n".join(lineas)


if __name__ == "__main__":
    for comuna in ["Pudahuel", "Recoleta", "Providencia"]:
        print(f"\n{'=' * 70}\nCOMUNA: {comuna}")
        res = buscar_turnos(comuna)
        print(f"capturado_en={res.get('capturado_en')}")
        print(formatear_contexto(res))
