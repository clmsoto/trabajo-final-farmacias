"""
Pruebas de contingencia del asistente.

Verifican cómo reacciona el asistente cuando el servicio de datos falla,
responde mal o responde vacío. Los fallos se inyectan reemplazando
temporalmente las funciones que hacen las llamadas HTTP; el código de
producción no se modifica.

Esta batería se reorganizó cuando el sistema se dividió en dos servicios.
Antes inyectaba fallos en la descarga desde la fuente de turnos, que
ocurría dentro del asistente. Hoy esa descarga vive en el servicio de
datos, y lo que corresponde probar acá es la reacción del cliente.

Las pruebas de la lógica que se mudó —horarios, normalización, vigencia—
están en servicio-datos/pruebas_datos.py.
"""

import httpx
from langchain_core.messages import HumanMessage

import tool_minsal
import tool_rag
from grafo_farmacias import app

import grafo_farmacias

RESULTADOS: list[tuple[str, bool, str]] = []


def verificar(nombre: str, condicion: bool, detalle: str = "") -> None:
    RESULTADOS.append((nombre, condicion, detalle))
    print(f"  [{'PASA ' if condicion else 'FALLA'}] {nombre}")
    if not condicion and detalle:
        print(f"          {detalle}")


def preguntar(texto: str, hilo: str) -> str:
    r = app.invoke(
        {"user_id": hilo, "messages": [HumanMessage(texto)]},
        {"configurable": {"thread_id": hilo}},
    )
    return r["messages"][-1].content


class Sustituir:
    """
    Reemplaza temporalmente un atributo de un módulo.

    Se usa como gestor de contexto para garantizar que el original se
    restaure aunque la prueba lance una excepción.
    """

    def __init__(self, modulo, atributo: str, reemplazo):
        self.modulo, self.atributo, self.reemplazo = modulo, atributo, reemplazo

    def __enter__(self):
        self.original = getattr(self.modulo, self.atributo)
        setattr(self.modulo, self.atributo, self.reemplazo)
        return self

    def __exit__(self, *args):
        setattr(self.modulo, self.atributo, self.original)


def limpiar_estado(hilo: str) -> None:
    """
    El grafo reutiliza el resultado del turno anterior si la comuna
    coincide. Sin hilos distintos, una prueba consumiría la caché de otra
    y el fallo inyectado nunca se ejercitaría.
    """
    pass  # se resuelve usando un hilo distinto por caso


# ---------------------------------------------------------------------------
# 1. El servicio de turnos no responde
# ---------------------------------------------------------------------------


def pruebas_servicio_turnos_caido() -> None:
    print("\n1. EL SERVICIO DE TURNOS NO RESPONDE")

    fallos = {
        "timeout de lectura": httpx.ReadTimeout("simulado"),
        "error de conexión": httpx.ConnectError("simulado"),
    }

    for i, (nombre, excepcion) in enumerate(fallos.items()):
        def falla(*args, **kwargs):
            raise excepcion

        with Sustituir(tool_minsal.httpx, "get", falla):
            resp = preguntar("¿Hay farmacia de turno en Recoleta?", f"cont_t{i}")

        verificar(
            f"{nombre}: responde sin caerse",
            bool(resp and len(resp) > 20),
            f"respuesta: {resp[:90]!r}",
        )
        verificar(
            f"{nombre}: informa la indisponibilidad",
            any(t in resp.lower() for t in ["no está disponible", "más tarde", "no disponible"]),
            f"respuesta: {resp[:150]!r}",
        )
        # Lo crítico: que no invente una farmacia concreta.
        verificar(
            f"{nombre}: no inventa una farmacia",
            not any(t in resp.lower() for t in ["calle ", "avenida ", "abierta ahora"]),
            f"respuesta: {resp[:150]!r}",
        )


# ---------------------------------------------------------------------------
# 2. El servicio de vademécum no responde
# ---------------------------------------------------------------------------


def pruebas_servicio_rag_caido() -> None:
    print("\n2. EL SERVICIO DE VADEMÉCUM NO RESPONDE")

    def falla(*args, **kwargs):
        raise httpx.ConnectError("simulado")

    with Sustituir(tool_rag.httpx, "get", falla):
        resp = preguntar("¿Qué contraindicaciones tiene el ibuprofeno?", "cont_r0")

    verificar(
        "responde sin caerse",
        bool(resp and len(resp) > 20),
        f"respuesta: {resp[:90]!r}",
    )
    verificar(
        "no inventa contenido farmacológico",
        not any(t in resp.lower() for t in ["contraindicado en", "hipersensibilidad", "úlcera"]),
        f"respuesta: {resp[:200]!r}",
    )
    verificar(
        "no adjunta cita de una fuente que no consultó",
        "vademécum chileno · principios activos" not in resp,
        f"respuesta: {resp[:200]!r}",
    )


# ---------------------------------------------------------------------------
# 3. El servicio responde, pero vacío o mal
# ---------------------------------------------------------------------------


def pruebas_respuesta_degradada() -> None:
    print("\n3. EL SERVICIO RESPONDE MAL")

    # Sin turnos cargados: el servicio contesta pero sin datos.
    def sin_turnos(comuna: str) -> dict:
        return {
            "comuna": comuna,
            "encontrada": False,
            "farmacias": [],
            "comunas_sugeridas": [],
            "capturado_en": None,
        }

    # grafo_farmacias importó estas funciones con "from ... import", así
    # que tiene su propia referencia: sustituir en el módulo de origen no
    # la afecta. Hay que sustituir donde efectivamente se usa.
    with Sustituir(grafo_farmacias, "buscar_turnos", sin_turnos):
        resp = preguntar("¿Hay farmacia de turno en Recoleta?", "cont_d0")

    verificar(
        "sin turnos cargados: advierte que el dato no está",
        any(t in resp.lower() for t in ["no tiene turnos", "no hay farmacias", "confirma"]),
        f"respuesta: {resp[:200]!r}",
    )

    # Búsqueda sin resultados sobre el umbral.
    def sin_resultados(consulta: str, k: int = 3) -> dict:
        return {"contexto": "", "citas": [], "hay_resultados": False}

    with Sustituir(grafo_farmacias, "recuperar_contexto", sin_resultados):
        resp = preguntar("¿Qué es la fenilbutazona?", "cont_d1")

    verificar(
        "sin resultados: lo dice en vez de inventar",
        any(t in resp.lower() for t in ["no encontré", "no tengo", "no dispongo"]),
        f"respuesta: {resp[:200]!r}",
    )


# ---------------------------------------------------------------------------
# 4. Bordes de estado conversacional
# ---------------------------------------------------------------------------


def pruebas_estado() -> None:
    print("\n4. ESTADO CONVERSACIONAL")

    resp = preguntar("¿Cuál es su dirección?", "cont_e0")
    verificar(
        "seguimiento sin contexto previo no inventa una dirección",
        not any(t in resp.lower() for t in ["calle ", "avenida "]),
        f"respuesta: {resp[:150]!r}",
    )

    hilo = "cont_e1"
    preguntar("¿Hay farmacia de turno en Recoleta?", hilo)
    resp = preguntar("¿Para qué sirve el omeprazol?", hilo)
    verificar(
        "al cambiar de tema no aparecen datos de farmacias",
        "recoleta" not in resp.lower(),
        f"respuesta: {resp[:150]!r}",
    )

    hilo = "cont_e2"
    preguntar("¿Qué farmacias de turno hay en la Región Metropolitana?", hilo)
    resp = preguntar("Estoy en Providencia", hilo)
    verificar(
        "una comuna explícita anula la región heredada",
        "providencia" in resp.lower(),
        f"respuesta: {resp[:150]!r}",
    )


# ---------------------------------------------------------------------------
# 5. Entradas atípicas
# ---------------------------------------------------------------------------


def pruebas_entrada() -> None:
    print("\n5. ENTRADAS ATÍPICAS")

    casos = [
        ("?", "solo signo de puntuación"),
        ("aaaaaaaaaa", "texto sin sentido"),
        ("farmacia " * 100, "mensaje muy largo"),
        ("🏥💊", "solo emojis"),
    ]

    for i, (texto, desc) in enumerate(casos):
        try:
            resp = preguntar(texto, f"cont_i{i}")
            verificar(f"{desc}: responde sin excepción", bool(resp))
        except Exception as e:
            verificar(f"{desc}: responde sin excepción", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pruebas_servicio_turnos_caido()
    pruebas_servicio_rag_caido()
    pruebas_respuesta_degradada()
    pruebas_estado()
    pruebas_entrada()

    total = len(RESULTADOS)
    pasaron = sum(1 for _, ok, _ in RESULTADOS if ok)
    print(f"\n{'=' * 60}")
    print(f"RESULTADO: {pasaron}/{total} pruebas pasaron")

    fallidas = [n for n, ok, _ in RESULTADOS if not ok]
    if fallidas:
        print("\nFallaron:")
        for n in fallidas:
            print(f"  - {n}")