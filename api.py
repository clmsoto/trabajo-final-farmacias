"""
API del asistente de farmacias.

Expone el grafo LangGraph como endpoint HTTP. El contrato es mínimo por
diseño: el cliente envía user_id y mensaje, y recibe la respuesta. Todo
el estado conversacional vive en el checkpointer del grafo, no en el
cliente: así el front no necesita reenviar el historial.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from grafo_farmacias import app as grafo

load_dotenv()


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    mensaje: str = Field(min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    respuesta: str
    intent: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verificación temprana: si faltan credenciales, es mejor fallar al
    # arrancar que devolver error 500 en la primera consulta del usuario.
    faltantes = [
        v for v in ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY")
        if not os.environ.get(v)
    ]
    if faltantes:
        raise RuntimeError(f"Faltan variables de entorno: {faltantes}")
    yield


api = FastAPI(
    title="Asistente de farmacias de turno",
    description=(
        "Asistente informativo sobre farmacias de turno (MINSAL) y fichas "
        "referenciales de medicamentos. No entrega dosis ni recomendaciones "
        "clínicas."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS abierto: el front se sirve desde el mismo origen, pero se deja
# permisivo para permitir pruebas desde local durante el desarrollo.
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@api.get("/health")
def health() -> dict:
    """Chequeo de vida para el orquestador de despliegue."""
    return {"status": "ok"}


@api.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # El thread_id se deriva del user_id: cada usuario tiene su propio
    # hilo de conversación y no ve el historial de otros.
    config = {"configurable": {"thread_id": req.user_id}}

    try:
        resultado = grafo.invoke(
            {"user_id": req.user_id, "messages": [HumanMessage(req.mensaje)]},
            config,
        )
    except Exception as e:
        # No se propaga el detalle del error al cliente: puede contener
        # rutas, credenciales o estructura interna.
        print(f"[api] Error al procesar: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=503,
            detail="El asistente no está disponible en este momento.",
        ) from e

    return ChatResponse(
        respuesta=resultado["messages"][-1].content,
        intent=resultado.get("intent"),
    )


# El front estático se monta al final para no capturar las rutas de la API.
if os.path.isdir("static"):
    api.mount("/", StaticFiles(directory="static", html=True), name="static")