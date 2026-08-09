"""
Grafo LangGraph del asistente de farmacias.
Trabajo Final · Módulo 04 · Diplomado IA Generativa FEN.

Cubre los criterios 2 (LangGraph + historial) y 3 (RAG con cita).
Pendiente: tool_minsal_node sigue siendo un stub.
"""

from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from tool_rag import recuperar_contexto

load_dotenv()

INTENTS = Literal["turno", "medicamento", "rechazo", "fuera_de_dominio"]


# ---------------------------------------------------------------------------
# 1. Estado del grafo
# ---------------------------------------------------------------------------
# add_messages es un "reducer": en vez de sobrescribir la lista de mensajes
# en cada paso, LangGraph la va concatenando. Eso habilita el historial
# multi-turno sin escribir lógica de acumulación a mano.


class AssistantState(TypedDict):
    user_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    intent: INTENTS | None
    comuna: str | None
    minsal_context: str | None
    rag_context: str | None
    rag_citas: list | None


# ---------------------------------------------------------------------------
# 2. Router: clasificación de intención con salida estructurada
# ---------------------------------------------------------------------------


class IntentClassification(BaseModel):
    intent: INTENTS = Field(
        description=(
            "turno: pregunta por farmacias abiertas o de turno en una comuna. "
            "medicamento: pide informacion general de un medicamento o ficha, "
            "sin pedir dosis ni tratamiento. "
            "rechazo: pide diagnostico, dosis, tratamiento o recomendacion "
            "clinica personalizada. "
            "fuera_de_dominio: no trata de farmacias ni de medicamentos."
        )
    )
    comuna: str | None = Field(
        default=None, description="Comuna mencionada por el usuario, si aplica."
    )


ROUTER_SYSTEM_PROMPT = """Eres el clasificador de intención de un asistente
informativo sobre farmacias de turno y medicamentos. Tu única tarea es
decidir la ruta: turno, medicamento, rechazo o fuera_de_dominio.

Clasifica como "rechazo" ante cualquier pedido de diagnóstico, dosis,
tratamiento o recomendación clínica, incluso si viene disfrazado de
pregunta hipotética, de tercero, o insistente. Ante la duda entre
"medicamento" y "rechazo", clasifica como "rechazo": el asistente informa,
no trata.

Si la consulta no trata de farmacias ni de medicamentos, clasifícala como
"fuera_de_dominio". La preferencia por el rechazo aplica solo a consultas
del dominio sanitario, no a temas ajenos.

Usas el historial de la conversación para resolver referencias implícitas:
si el usuario dice "¿y ahí?" o no repite la comuna, dedúcela de los
mensajes anteriores.
"""

router_llm = ChatOpenAI(model="gpt-5.6-luna").with_structured_output(
    IntentClassification
)


# ---------------------------------------------------------------------------
# 3. Nodos
# ---------------------------------------------------------------------------


def reset_node(state: AssistantState) -> dict:
    """
    Limpia el contexto recuperado del turno anterior.
    Sin esto, el estado persiste entre invocaciones del mismo thread y una
    respuesta puede citar fuentes que no se usaron en el turno actual.
    El historial (messages) NO se limpia: esa persistencia sí la queremos.
    """
    return {
        "rag_context": None,
        "rag_citas": [],
        "minsal_context": None,
        "intent": None,
    }


def router_node(state: AssistantState) -> dict:
    # Le pasamos TODO el historial, no solo el último mensaje: así el router
    # puede resolver referencias como "¿y ahí?" o "¿a qué hora cierra?".
    classification = router_llm.invoke(
        [("system", ROUTER_SYSTEM_PROMPT), *state["messages"]]
    )
    print(f"[router] intent={classification.intent}, comuna={classification.comuna}")

    # La comuna solo se arrastra dentro de una conversación sobre turnos.
    # Si el usuario cambió de tema, se descarta: conservarla haría que una
    # consulta de turnos posterior use una comuna que ya no está en contexto.
    if classification.intent == "turno":
        comuna = classification.comuna or state.get("comuna")
    else:
        comuna = None

    return {"intent": classification.intent, "comuna": comuna}


def route_from_intent(state: AssistantState) -> str:
    destinos = {
        "turno": "tool_minsal",
        "medicamento": "tool_rag",
        "rechazo": "guardrail_reject",
        "fuera_de_dominio": "fuera_de_dominio",
    }
    # Si el clasificador devolviera algo inesperado, se cae al rechazo:
    # es el destino más conservador de los cuatro.
    return destinos.get(state["intent"], "guardrail_reject")


def tool_minsal_node(state: AssistantState) -> dict:
    # TODO: llamar getLocalesTurnos.php, filtrar por comuna, timeout + cache.
    return {"minsal_context": f"PENDIENTE: turnos para comuna={state.get('comuna')}"}


def tool_rag_node(state: AssistantState) -> dict:
    # TODO: la consulta usa solo el último mensaje. Una pregunta de
    # seguimiento como "¿y sus efectos secundarios?" pierde el referente.
    # Se resuelve reformulando la consulta con el historial antes de embeber.
    consulta = state["messages"][-1].content
    resultado = recuperar_contexto(consulta, k=3)

    if not resultado["hay_resultados"]:
        return {"rag_context": None, "rag_citas": []}

    return {
        "rag_context": resultado["contexto"],
        "rag_citas": resultado["citas"],
    }


def guardrail_reject_node(state: AssistantState) -> dict:
    # Rechaza y deriva, sin negarse a secas (ver criterio 5 de la rúbrica).
    rechazo = (
        "No puedo recomendarte un medicamento ni una dosis; eso requiere "
        "evaluación profesional. Sí puedo ayudarte a encontrar una farmacia "
        "de turno o explicar una ficha que ya te hayan indicado."
    )
    return {"messages": [AIMessage(content=rechazo)]}


def fuera_de_dominio_node(state: AssistantState) -> dict:
    # Distinto del rechazo clínico: acá no hay riesgo sanitario, solo una
    # consulta ajena al alcance del asistente.
    mensaje = (
        "Solo puedo ayudarte con farmacias de turno en Chile y con "
        "información referencial de fichas de medicamentos."
    )
    return {"messages": [AIMessage(content=mensaje)]}


RESPUESTA_SYSTEM_PROMPT = """Eres un asistente informativo sobre farmacias
de turno y medicamentos en Chile.

Reglas que no puedes romper:
- Nunca indiques dosis, cantidades, frecuencia de administración ni pautas
  de tratamiento, aunque el contexto las contenga.
- Nunca recomiendes un medicamento para un síntoma ni sugieras cuál tomar.
- No inventes información que no esté en el contexto entregado.
- Si el contexto no responde la pregunta, dilo claramente.

Responde en español de Chile, de forma breve y clara. La información del
vademécum es referencial y educativa, no reemplaza indicación profesional.
"""

respuesta_llm = ChatOpenAI(model="gpt-5.6-luna")


def response_node(state: AssistantState) -> dict:
    contexto = state.get("rag_context") or state.get("minsal_context")

    if not contexto:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "No encontré información sobre eso en las fuentes que "
                        "tengo disponibles. Puedo ayudarte con farmacias de "
                        "turno o con fichas de medicamentos del vademécum."
                    )
                )
            ]
        }

    r = respuesta_llm.invoke(
        [
            ("system", RESPUESTA_SYSTEM_PROMPT),
            (
                "human",
                f"Contexto:\n{contexto}\n\n"
                f"Pregunta: {state['messages'][-1].content}",
            ),
        ]
    )

    texto = r.content
    citas = state.get("rag_citas") or []
    if citas:
        fuentes = ", ".join(c["ficha"] for c in citas)
        texto += (
            f"\n\nFuente: fichas de {fuentes} "
            "(Comprehensive Drug Information Dataset)."
        )

    return {"messages": [AIMessage(content=texto)]}


# ---------------------------------------------------------------------------
# 4. Construcción del grafo
# ---------------------------------------------------------------------------

graph = StateGraph(AssistantState)
graph.add_node("reset", reset_node)
graph.add_node("router", router_node)
graph.add_node("tool_minsal", tool_minsal_node)
graph.add_node("tool_rag", tool_rag_node)
graph.add_node("guardrail_reject", guardrail_reject_node)
graph.add_node("fuera_de_dominio", fuera_de_dominio_node)
graph.add_node("responder", response_node)

graph.add_edge(START, "reset")
graph.add_edge("reset", "router")

graph.add_conditional_edges(
    "router",
    route_from_intent,
    {
        "tool_minsal": "tool_minsal",
        "tool_rag": "tool_rag",
        "guardrail_reject": "guardrail_reject",
        "fuera_de_dominio": "fuera_de_dominio",
    },
)
graph.add_edge("tool_minsal", "responder")
graph.add_edge("tool_rag", "responder")
graph.add_edge("guardrail_reject", END)
graph.add_edge("fuera_de_dominio", END)
graph.add_edge("responder", END)

checkpointer = MemorySaver()
app = graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 5. Pruebas de invocación
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = {"configurable": {"thread_id": "user_123"}}

    pruebas = [
        # 1 y 2: historial. El segundo turno no menciona la comuna.
        "¿Hay una farmacia de turno en Providencia?",
        "¿Y cuál queda más cerca del metro?",
        # 3: ruta RAG con cita.
        "¿Qué contraindicaciones tiene el ibuprofeno?",
        # 4: guardrail clínico.
        "¿Qué dosis de paracetamol le doy a mi hijo?",
        # 5: fuera de dominio (no debe dar el mensaje de rechazo clínico).
        "¿Cuál es la capital de Francia?",
    ]

    for i, pregunta in enumerate(pruebas, 1):
        print(f"\n{'=' * 70}\n[{i}] {pregunta}")
        r = app.invoke(
            {"user_id": "user_123", "messages": [HumanMessage(pregunta)]},
            config,
        )
        print(r["messages"][-1].content)