"""
Grafo LangGraph del asistente de farmacias de turno.
Trabajo Final · Módulo 04 · Diplomado IA Generativa FEN.

Orquesta cuatro rutas según la intención detectada:
  turno            → consulta MINSAL en vivo (por comuna o por región)
  medicamento      → RAG semántico sobre el vademécum, con cita
  rechazo          → guardrail clínico de entrada
  fuera_de_dominio → consulta ajena al alcance del asistente

Cubre los criterios 2 (LangGraph + historial), 3 (RAG semántico),
4 (MINSAL en vivo) y 5 (seguridad, en dos capas).
"""

from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from guardrail_salida import revisar
from tool_minsal import (
    buscar_turnos,
    buscar_turnos_region,
    formatear_contexto,
    formatear_contexto_region,
    normalizar_texto,
)
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
    region: str | None
    minsal_context: str | None
    minsal_sugerencias: list | None
    # Estos dos NO se limpian entre turnos: son la memoria que permite
    # responder un seguimiento sin volver a consultar la API.
    minsal_comuna_consultada: str | None
    minsal_resultado: dict | None
    rag_context: str | None
    rag_citas: list | None


# ---------------------------------------------------------------------------
# 2. Router: clasificación de intención con salida estructurada
# ---------------------------------------------------------------------------


class IntentClassification(BaseModel):
    intent: INTENTS = Field(
        description=(
            "turno: pregunta por farmacias abiertas o de turno en una comuna "
            "o región. "
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
    region: str | None = Field(
        default=None,
        description=(
            "Región mencionada por el usuario, si la consulta es regional "
            "y no de una comuna específica."
        ),
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

Si el usuario pregunta por una región completa ("¿hay farmacias de turno
en la Región Metropolitana?"), pon el nombre en "region" y deja "comuna"
vacía. Si menciona una comuna específica, usa "comuna".

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
    Tampoco se limpian minsal_resultado ni minsal_comuna_consultada: son
    la caché que evita reconsultar la API en un turno de seguimiento.
    """
    return {
        "intent": None,
        "region": None,
        "minsal_context": None,
        "minsal_sugerencias": [],
        "rag_context": None,
        "rag_citas": [],
    }


def router_node(state: AssistantState) -> dict:
    # Le pasamos TODO el historial, no solo el último mensaje: así el router
    # puede resolver referencias como "¿y ahí?" o "¿a qué hora cierra?".
    classification = router_llm.invoke(
        [("system", ROUTER_SYSTEM_PROMPT), *state["messages"]]
    )
    print(
        f"[router] intent={classification.intent}, "
        f"comuna={classification.comuna}, region={classification.region}"
    )

    if classification.intent == "turno":
        # La comuna se arrastra dentro de una conversación sobre turnos,
        # para resolver seguimientos que no la repiten.
        comuna = classification.comuna or state.get("comuna")
        region = classification.region
        # Precedencia: si el usuario nombra una comuna en este turno, la
        # consulta dejó de ser regional. Sin esto, la región extraída del
        # historial seguiría mandando y la comuna nueva se ignoraría.
        if classification.comuna:
            region = None
    else:
        # Al cambiar de tema, ambas se descartan: conservarlas haría que
        # una consulta posterior use una ubicación fuera de contexto.
        comuna, region = None, None

    return {
        "intent": classification.intent,
        "comuna": comuna,
        "region": region,
    }


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
    # Consulta regional: el usuario pidió el panorama amplio.
    region = state.get("region")
    if region:
        resultado = buscar_turnos_region(region)
        return {
            "minsal_context": formatear_contexto_region(resultado),
            "minsal_sugerencias": [],
        }

    comuna = state.get("comuna")
    if not comuna:
        return {
            "minsal_context": (
                "El usuario no indicó comuna. Pídele que la especifique "
                "para poder buscar farmacias de turno."
            ),
            "minsal_sugerencias": [],
        }

    # Si es la misma comuna del turno anterior, se reutiliza el resultado.
    # Un seguimiento como "¿cuál es su dirección?" no necesita reconsultar:
    # los datos no cambiaron, y reconsultar arriesga que el resultado
    # difiera del que el usuario ya vio en pantalla.
    previo = state.get("minsal_resultado")
    misma_comuna = state.get("minsal_comuna_consultada") == normalizar_texto(comuna)
    if previo and misma_comuna:
        print(f"[minsal] Reutilizando resultado de {comuna} (turno previo)")
        return {
            "minsal_context": formatear_contexto(previo),
            "minsal_sugerencias": previo.get("comunas_sugeridas") or [],
            "minsal_comuna_consultada": normalizar_texto(comuna),
            "minsal_resultado": previo,
        }

    try:
        resultado = buscar_turnos(comuna)
    except RuntimeError as e:
        # Ni datos en vivo ni snapshot: se informa la falla, no se inventa.
        print(f"[minsal] {e}")
        return {
            "minsal_context": (
                "El servicio de farmacias de turno no está disponible en "
                "este momento y no hay datos de respaldo. Informa al usuario "
                "que intente más tarde."
            ),
            "minsal_sugerencias": [],
        }

    return {
        "minsal_context": formatear_contexto(resultado),
        "minsal_sugerencias": resultado.get("comunas_sugeridas") or [],
        "minsal_comuna_consultada": normalizar_texto(comuna),
        "minsal_resultado": resultado,
    }


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
    # Primera capa del criterio 5. Rechaza y deriva, sin negarse a secas.
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

Habla como una persona, no como un sistema. Nunca menciones "el contexto",
"la ficha entregada", "la fuente consultada" ni "mi base de datos": son
detalles internos que al usuario no le sirven. Si un dato no lo tienes,
dilo directo: "No tengo el teléfono de esa farmacia" en vez de "el
contexto no informa el teléfono".

No uses formato markdown: responde en texto plano.
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

    # Las citas se adjuntan por código, no se dejan a criterio del modelo:
    # son la trazabilidad de la evidencia, no una redacción.
    citas = state.get("rag_citas") or []
    if citas:
        fuentes = ", ".join(c["ficha"] for c in citas)
        texto += (
            f"\n\nFuente: fichas de {fuentes} "
            "(Comprehensive Drug Information Dataset)."
        )

    # Ídem para las comunas alternativas. Por eso formatear_contexto no las
    # incluye en el texto que ve el modelo: si llegaran por ambas vías, la
    # respuesta las mostraría dos veces.
    sugerencias = state.get("minsal_sugerencias") or []
    if sugerencias:
        texto += (
            "\n\nComunas cercanas con turno vigente: "
            + ", ".join(sugerencias)
            + "."
        )

    # Segunda capa del criterio 5: inspección determinista de la respuesta
    # ya generada. Se aplica al final, sobre el texto completo con citas y
    # sugerencias incluidas, porque cualquiera de esas piezas podría
    # arrastrar una dosis desde el contexto.
    texto, hallazgos = revisar(texto)
    if hallazgos:
        print(f"[guardrail-salida] BLOQUEADO · hallazgos={hallazgos}")

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

# MemorySaver vive en el proceso: al reiniciar se pierden los hilos.
# Para persistencia real en la nube correspondería SqliteSaver sobre un
# volumen montado.
checkpointer = MemorySaver()
app = graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 5. Pruebas de invocación
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = {"configurable": {"thread_id": "user_123"}}

    pruebas = [
        # 1 y 2: historial. El segundo turno no menciona la comuna.
        "¿Hay una farmacia de turno en Recoleta?",
        "¿Cuál es su dirección?",
        # 3: consulta regional.
        "¿Y en la Región Metropolitana?",
        # 4: comuna sin turno vigente, tras una regional (precedencia).
        "Estoy en Peñalolén",
        # 5: ruta RAG con cita.
        "¿Qué contraindicaciones tiene el ibuprofeno?",
        # 6: guardrail clínico.
        "¿Qué dosis de paracetamol le doy a mi hijo?",
        # 7: fuera de dominio.
        "¿Cuál es la capital de Francia?",
    ]

    for i, pregunta in enumerate(pruebas, 1):
        print(f"\n{'=' * 70}\n[{i}] {pregunta}")
        r = app.invoke(
            {"user_id": "user_123", "messages": [HumanMessage(pregunta)]},
            config,
        )
        print(r["messages"][-1].content)