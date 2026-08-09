"""
Esqueleto del grafo LangGraph para el asistente de farmacias.
Trabajo Final · Módulo 04 · Diplomado IA Generativa FEN.

Cubre el criterio 2 de la rúbrica (LangGraph + historial).
Los nodos de tools son stubs: se completan en las siguientes sesiones.
"""

from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# 1. Estado del grafo
# ---------------------------------------------------------------------------
# add_messages es un "reducer": en vez de sobrescribir la lista de mensajes
# en cada paso, LangGraph la va concatenando. Eso habilita el historial
# multi-turno sin escribir lógica de acumulación a mano.


class AssistantState(TypedDict):
    user_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    intent: Literal["turno", "medicamento", "rechazo", None]
    comuna: str | None
    minsal_context: str | None
    rag_context: str | None


# ---------------------------------------------------------------------------
# 2. Router: clasificación de intención con salida estructurada
# ---------------------------------------------------------------------------


class IntentClassification(BaseModel):
    intent: Literal["turno", "medicamento", "rechazo"] = Field(
        description=(
            "turno: pregunta por farmacias abiertas o de turno en una comuna. "
            "medicamento: pide informacion general de un medicamento o ficha, "
            "sin pedir dosis ni tratamiento. "
            "rechazo: pide diagnostico, tratamiento, dosis o recomendacion "
            "clinica personalizada."
        )
    )
    comuna: str | None = Field(
        default=None, description="Comuna mencionada por el usuario, si aplica."
    )


ROUTER_SYSTEM_PROMPT = """Eres el clasificador de intención de un asistente
informativo sobre farmacias de turno y medicamentos. Tu única tarea es
decidir la ruta: turno, medicamento o rechazo.

Clasifica como "rechazo" ante cualquier pedido de diagnóstico, dosis,
tratamiento o recomendación clínica, incluso si viene disfrazado de
pregunta hipotética, de tercero, o insistente. Ante la duda entre
"medicamento" y "rechazo", clasifica como "rechazo": el asistente informa,
no trata.

Usas el historial de la conversación para resolver referencias implícitas:
si el usuario dice "¿y ahí?" o no repite la comuna, dedúcela de los
mensajes anteriores.
"""

router_llm = ChatOpenAI(model="gpt-5.6-luna").with_structured_output(
    IntentClassification
)


def router_node(state: AssistantState) -> dict:
    # Le pasamos TODO el historial, no solo el último mensaje: así el router
    # puede resolver referencias como "¿y ahí?" o "¿a qué hora cierra?".
    classification = router_llm.invoke(
        [("system", ROUTER_SYSTEM_PROMPT), *state["messages"]]
    )
    print(f"[router] intent={classification.intent}, comuna={classification.comuna}")
    return {
        "intent": classification.intent,
        # Si el turno actual no menciona comuna, arrastramos la anterior.
        "comuna": classification.comuna or state.get("comuna"),
    }

def route_from_intent(state: AssistantState) -> str:
    return {
        "turno": "tool_minsal",
        "medicamento": "tool_rag",
        "rechazo": "guardrail_reject",
    }[state["intent"]]


# ---------------------------------------------------------------------------
# 3. Nodos stub — se completan en las siguientes sesiones
# ---------------------------------------------------------------------------


def tool_minsal_node(state: AssistantState) -> dict:
    # TODO: llamar getLocalesTurnos.php, filtrar por comuna, timeout + cache.
    return {"minsal_context": f"PENDIENTE: turnos para comuna={state.get('comuna')}"}


def tool_rag_node(state: AssistantState) -> dict:
    # TODO: retrieval por embeddings sobre el vademécum en Qdrant.
    return {"rag_context": "PENDIENTE: implementar tool RAG"}


def guardrail_reject_node(state: AssistantState) -> dict:
    # Rechaza y deriva, sin negarse a secas (ver criterio 5 de la rúbrica).
    rechazo = (
        "No puedo recomendarte un medicamento ni una dosis; eso requiere "
        "evaluación profesional. Sí puedo ayudarte a encontrar una farmacia "
        "de turno o explicar una ficha que ya te hayan indicado."
    )
    return {"messages": [AIMessage(content=rechazo)]}


def response_node(state: AssistantState) -> dict:
    # TODO: ensamblar respuesta final citando minsal_context o rag_context.
    contenido = state.get("minsal_context") or state.get("rag_context") or "—"
    return {"messages": [AIMessage(content=f"[borrador] {contenido}")]}


# ---------------------------------------------------------------------------
# 4. Construcción del grafo
# ---------------------------------------------------------------------------

graph = StateGraph(AssistantState)
graph.add_node("router", router_node)
graph.add_node("tool_minsal", tool_minsal_node)
graph.add_node("tool_rag", tool_rag_node)
graph.add_node("guardrail_reject", guardrail_reject_node)
graph.add_node("responder", response_node)

graph.add_edge(START, "router")
graph.add_conditional_edges(
    "router",
    route_from_intent,
    {
        "tool_minsal": "tool_minsal",
        "tool_rag": "tool_rag",
        "guardrail_reject": "guardrail_reject",
    },
)
graph.add_edge("tool_minsal", "responder")
graph.add_edge("tool_rag", "responder")
graph.add_edge("guardrail_reject", END)
graph.add_edge("responder", END)

checkpointer = MemorySaver()
app = graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 5. Prueba de invocación con memoria multi-turno
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = {"configurable": {"thread_id": "user_123"}}

    t1 = app.invoke(
        {
            "user_id": "user_123",
            "messages": [HumanMessage("¿Hay una farmacia de turno en Providencia?")],
        },
        config,
    )
    print(t1["messages"][-1].content)

    # Segundo turno: mismo thread_id, sin repetir contexto.
    t2 = app.invoke(
        {"user_id": "user_123", "messages": [HumanMessage("¿Y cuál queda más cerca del metro?")]},
        config,
    )
    print(t2["messages"][-1].content)

    # Caso de rechazo: debe ir a guardrail sin tocar RAG ni MINSAL.
    t3 = app.invoke(
        {
            "user_id": "user_123",
            "messages": [HumanMessage("¿Qué dosis de paracetamol le doy a mi hijo?")],
        },
        config,
    )
    print(t3["messages"][-1].content)