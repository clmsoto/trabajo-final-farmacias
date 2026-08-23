"""
Ingesta del vademécum chileno a Qdrant.

Corre una sola vez, desde una máquina con el JSON completo. El servicio
desplegado nunca abre este archivo: consulta Qdrant, que ya tiene el
texto y los metadatos en el payload.

Tres decisiones incrustadas acá:

1. La unidad de indexación es el PRINCIPIO ACTIVO, no el producto.
   El texto farmacológico depende del principio activo: Paracetamol
   aparece en 139 fichas con el mismo contenido. Sin agrupar, una
   consulta gastaría el top-k en copias del mismo documento.

2. Las secciones con posología quedan fuera por completo, y del resto
   se descartan las frases que contengan una dosis. El modelo no puede
   repetir lo que nunca vio.

3. Los nombres comerciales NO se embeben: el 66% lleva la dosis adentro
   ("IBUPROFENO COMPRIMIDOS 500 mg"). Quedan como metadato.
"""

import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict

import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"].rstrip("/")
HEADERS = {"api-key": os.environ["QDRANT_API_KEY"]}
COLECCION = "vademecum_cl"
EMBED_MODEL = "text-embedding-3-small"
DIMS = 1536
RUTA_JSON = "datos/vademecum.json"

openai_client = OpenAI()

# ---------------------------------------------------------------------------
# Qué entra al texto embebido
# ---------------------------------------------------------------------------

# Secciones informativas: describen el fármaco sin decir cuánto tomar.
SECCIONES_SEGURAS = [
    "Mecanismo de acción",
    "Indicaciones terapéuticas",
    "Contraindicaciones",
    "Advertencias y precauciones",
    "Interacciones",
    "Reacciones adversas",
    "Embarazo",
    "Lactancia",
    "Efectos sobre la capacidad de conducir",
]

# Secciones que se excluyen enteras: entre el 11% y el 40% de sus bloques
# contienen posología explícita, y su valor informativo sin la dosis es bajo.
SECCIONES_EXCLUIDAS = [
    "Modo de administración",
    "Insuficiencia renal",
    "Insuficiencia hepática",
    "Sobredosificación",
    "Posología",
]

# Principios activos que no son fármacos y solo agregan ruido.
ATC_EXCLUIDOS = {"Cosméticos"}

# Mismo criterio que el guardrail de salida: se busca la forma de una
# dosis, no palabras sueltas.
PATRON_DOSIS = re.compile(
    r"\b\d+[\.,]?\d*\s*(mg|mcg|µg|ug|g|ml|cc|UI)\b"
    r"|\bcada\s+\d+\s*(h|hora|horas|día|días)\b"
    r"|\b\d+\s*(vez|veces)\s+(al|por)\s+(día|dia)\b",
    re.IGNORECASE,
)

MAX_CHARS = 7000  # tope por documento, para no exceder el límite de tokens


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------


def limpiar_html(texto: str) -> str:
    """
    Quita etiquetas HTML del contenido.

    La fuente trae 14.658 bloques con etiquetas, algunas malformadas:
    '<sub>1<\\sub>' usa barra invertida en el cierre. La expresión acepta
    ambas formas.
    """
    texto = re.sub(r"<[/\\]?[a-zA-Z][^>]*>", "", texto)
    texto = unicodedata.normalize("NFKC", texto)
    return re.sub(r"[ \t]+", " ", texto).strip()


def normalizar_seccion(nombre: str) -> str:
    """
    Corrige los nombres de sección concatenados de la fuente.

    Aparecen casos como 'Mecanismo de acciónÓxido nítrico': el nombre de
    la sección quedó pegado al del principio activo.
    """
    nombre = (nombre or "").strip()
    for base in SECCIONES_SEGURAS + SECCIONES_EXCLUIDAS:
        if nombre.startswith(base):
            return base
    return nombre


def filtrar_dosis(contenido: str) -> str:
    """
    Descarta las frases que contengan una dosis, conservando el resto.

    Se filtra por frase y no por bloque completo porque las secciones
    informativas son valiosas: 'Contraindicado en insuficiencia hepática
    grave' debe conservarse aunque otra frase del mismo bloque mencione
    un ajuste de dosis.
    """
    frases = re.split(r"(?<=[.;])\s+", contenido)
    limpias = [f for f in frases if not PATRON_DOSIS.search(f)]
    return " ".join(limpias).strip()


def construir_texto(registro: dict) -> tuple[str, list[dict]]:
    """Devuelve (texto embebible, secciones conservadas)."""
    secciones = []
    for bloque in registro.get("informacion", []):
        seccion = normalizar_seccion(bloque.get("seccion"))
        if seccion not in SECCIONES_SEGURAS:
            continue
        contenido = filtrar_dosis(limpiar_html(bloque.get("contenido", "")))
        if len(contenido) < 10:
            continue
        secciones.append({"seccion": seccion, "contenido": contenido})

    texto = "\n".join(f"{s['seccion']}: {s['contenido']}" for s in secciones)
    return texto[:MAX_CHARS], secciones


# ---------------------------------------------------------------------------
# Qdrant vía REST
# ---------------------------------------------------------------------------


def crear_coleccion() -> None:
    r = httpx.put(
        f"{QDRANT_URL}/collections/{COLECCION}",
        headers=HEADERS,
        json={"vectors": {"size": DIMS, "distance": "Cosine"}},
        timeout=60,
    )
    r.raise_for_status()
    print(f"Colección '{COLECCION}' lista.")


def embeber(textos: list[str]) -> list[list[float]]:
    resp = openai_client.embeddings.create(model=EMBED_MODEL, input=textos)
    return [d.embedding for d in resp.data]


def subir(puntos: list[dict]) -> None:
    r = httpx.put(
        f"{QDRANT_URL}/collections/{COLECCION}/points?wait=true",
        headers=HEADERS,
        json={"points": puntos},
        timeout=180,
    )
    r.raise_for_status()


# ---------------------------------------------------------------------------


def main() -> None:
    with open(RUTA_JSON, encoding="utf-8") as fh:
        datos = json.load(fh)
    print(f"Fichas leídas: {len(datos)}")

    # Agrupar por principio activo. Se conserva el texto de la ficha más
    # completa del grupo (la que aporta más secciones seguras) y se acumulan
    # todos los productos comerciales como metadato.
    grupos: dict[str, dict] = defaultdict(
        lambda: {"texto": "", "secciones": [], "productos": []}
    )

    descartados = 0
    for reg in datos:
        atc = (reg.get("atc") or "").strip()
        if not atc or atc in ATC_EXCLUIDOS:
            descartados += 1
            continue

        texto, secciones = construir_texto(reg)
        if not texto:
            descartados += 1
            continue

        g = grupos[atc]
        if len(secciones) > len(g["secciones"]):
            g["texto"] = texto
            g["secciones"] = secciones

        g["productos"].append(
            {
                "nombre": (reg.get("nombre") or "").strip(),
                "laboratorio": (reg.get("laboratorio") or "").strip(),
                "via": (reg.get("via") or "").strip(),
                "forma": (reg.get("forma") or "").strip(),
            }
        )

    print(f"Descartadas: {descartados}")
    print(f"Principios activos únicos: {len(grupos)}")

    crear_coleccion()

    claves = list(grupos.keys())
    LOTE = 96
    total = 0
    for i in range(0, len(claves), LOTE):
        sub = claves[i : i + LOTE]
        vectores = embeber([grupos[k]["texto"] for k in sub])

        puntos = []
        for atc, vector in zip(sub, vectores):
            g = grupos[atc]
            puntos.append(
                {
                    # id determinista: reingestar no duplica puntos
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"vademecum:{atc}")),
                    "vector": vector,
                    "payload": {
                        "atc": atc,
                        "texto": g["texto"],
                        "secciones": g["secciones"],
                        "productos": g["productos"][:40],
                        "n_productos": len(g["productos"]),
                    },
                }
            )

        subir(puntos)
        total += len(puntos)
        print(f"  subidos {total}/{len(claves)}")

    r = httpx.get(f"{QDRANT_URL}/collections/{COLECCION}", headers=HEADERS, timeout=30)
    print(f"Puntos indexados: {r.json()['result']['points_count']}")


if __name__ == "__main__":
    main()