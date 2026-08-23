"""
Servicio de datos: vademécum chileno y farmacias de turno.

Es el dueño de los datos. El asistente no abre archivos ni consulta
Qdrant: llama a esta API por HTTP, igual que llamaría a cualquier
servicio externo.

Los turnos no se consultan a la fuente desde acá: el cortafuegos que
tiene delante bloquea el tráfico desde rangos de datacenter. El volcado
llega por POST desde un proceso que corre en una conexión chilena, donde
la fuente sí responde.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query

import busqueda
import turnos

load_dotenv()

TOKEN_SYNC = os.environ.get("TOKEN_SYNC")


def autorizar(x_token: str | None = Header(default=None)) -> None:
    """Protege el endpoint de sincronización con un token compartido."""
    if not TOKEN_SYNC or x_token != TOKEN_SYNC:
        raise HTTPException(status_code=401, detail="No autorizado")


@asynccontextmanager
async def lifespan(app: FastAPI):
    faltantes = [
        v for v in ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY", "TOKEN_SYNC")
        if not os.environ.get(v)
    ]
    if faltantes:
        raise RuntimeError(f"Faltan variables de entorno: {faltantes}")
    turnos.cargar_inicial()
    yield


api = FastAPI(
    title="Servicio de datos · farmacias y vademécum",
    description=(
        "Expone el vademécum chileno con búsqueda semántica y las farmacias "
        "de turno vigentes. La dosificación no forma parte del índice."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@api.get("/health")
def health() -> dict:
    estado = turnos.estado()
    return {
        "status": "ok",
        "turnos_capturados_en": estado["capturado_en"],
        "turnos_vigentes": estado["vigentes"],
    }


# --- Vademécum -------------------------------------------------------------


@api.get("/vademecum/buscar")
def vademecum_buscar(
    q: str = Query(min_length=2, max_length=300),
    k: int = Query(default=3, ge=1, le=10),
) -> dict:
    return {"consulta": q, "resultados": busqueda.buscar(q, k=k)}


@api.get("/vademecum/{atc}")
def vademecum_ficha(atc: str) -> dict:
    ficha = busqueda.obtener(atc)
    if ficha is None:
        raise HTTPException(status_code=404, detail="Principio activo no encontrado")
    return ficha


# --- Turnos ----------------------------------------------------------------


@api.get("/turnos")
def turnos_consultar(
    comuna: str | None = None,
    region: str | None = None,
) -> dict:
    if not comuna and not region:
        raise HTTPException(status_code=400, detail="Indica comuna o region")
    if region:
        return turnos.por_region(region)
    return turnos.por_comuna(comuna)


@api.post("/turnos/sync", dependencies=[Depends(autorizar)])
def turnos_sync(payload: dict) -> dict:
    """
    Recibe el volcado crudo de la fuente desde una conexión chilena.

    Se valida el esquema antes de reemplazar los datos vigentes: un
    volcado incompleto dejaría al servicio sin turnos y sin aviso.
    """
    registros = payload.get("registros")
    if not isinstance(registros, list) or len(registros) < 10:
        raise HTTPException(status_code=422, detail="Volcado vacío o insuficiente")

    n = turnos.reemplazar(registros)
    return {
        "recibidos": len(registros),
        "vigentes": n,
        "capturado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }