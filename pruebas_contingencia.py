"""
Pruebas de contingencia y casos de borde.

Complementa pruebas_adversarias.py, que verifica el comportamiento del
guardrail pero solo ejercita el camino feliz de la infraestructura: en
todas sus consultas la fuente responde bien. Los dos defectos del aviso
de respaldo detectados en producción estaban fuera de su alcance.

Acá los fallos se inyectan a propósito, reemplazando temporalmente las
funciones de red. El código de producción no se modifica.

Estas pruebas sí verifican automáticamente: cada caso declara qué debe
cumplirse y el script reporta pasa o falla.
"""

import json
from datetime import datetime, timedelta

import httpx
from langchain_core.messages import HumanMessage

import tool_minsal
from grafo_farmacias import app

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

RESULTADOS: list[tuple[str, bool, str]] = []


def verificar(nombre: str, condicion: bool, detalle: str = "") -> None:
    RESULTADOS.append((nombre, condicion, detalle))
    marca = "PASA " if condicion else "FALLA"
    print(f"  [{marca}] {nombre}")
    if not condicion and detalle:
        print(f"          {detalle}")


def preguntar(texto: str, hilo: str) -> str:
    """Invoca el grafo y devuelve la respuesta final."""
    r = app.invoke(
        {"user_id": hilo, "messages": [HumanMessage(texto)]},
        {"configurable": {"thread_id": hilo}},
    )
    return r["messages"][-1].content


def limpiar_cache() -> None:
    """El caché enmascara los fallos inyectados: hay que vaciarlo."""
    tool_minsal._cache["datos"] = None
    tool_minsal._cache["ts"] = 0.0


class InyectarFallo:
    """
    Reemplaza temporalmente _descargar por una función que falla.

    Se usa como context manager para garantizar que la función original
    se restaure aunque la prueba lance una excepción.
    """

    def __init__(self, excepcion: Exception):
        self.excepcion = excepcion
        self.original = None

    def __enter__(self):
        self.original = tool_minsal._descargar
        tool_minsal._descargar = self._fallar
        limpiar_cache()
        return self

    def __exit__(self, *args):
        tool_minsal._descargar = self.original
        limpiar_cache()

    def _fallar(self):
        raise self.excepcion


class SinRespaldo:
    """Simula que el archivo de respaldo no existe."""

    def __enter__(self):
        self.original = tool_minsal._cargar_snapshot
        tool_minsal._cargar_snapshot = lambda: None
        return self

    def __exit__(self, *args):
        tool_minsal._cargar_snapshot = self.original


# ---------------------------------------------------------------------------
# 1. Fallos de la fuente
# ---------------------------------------------------------------------------


def pruebas_fallos_fuente() -> None:
    print("\n1. FALLOS DE LA FUENTE")

    fallos = {
        "timeout de lectura": httpx.ReadTimeout("simulado"),
        "error de conexión": httpx.ConnectError("simulado"),
        "esquema inesperado": ValueError("Faltan campos esperados"),
        "respuesta no JSON": json.JSONDecodeError("simulado", "", 0),
    }

    for nombre, excepcion in fallos.items():
        with InyectarFallo(excepcion):
            resp = preguntar("¿Hay farmacia de turno en Recoleta?", f"cont_{nombre}")

        verificar(
            f"{nombre}: el sistema responde sin caerse",
            bool(resp and len(resp) > 20),
            f"respuesta: {resp[:80]!r}",
        )
        verificar(
            f"{nombre}: la respuesta advierte que el dato es de respaldo",
            "respaldo" in resp.lower() or "⚠" in resp,
            f"respuesta: {resp[:120]!r}",
        )


def prueba_sin_fuente_ni_respaldo() -> None:
    print("\n2. SIN FUENTE NI RESPALDO")

    with InyectarFallo(httpx.ConnectError("simulado")), SinRespaldo():
        resp = preguntar("¿Hay farmacia de turno en Recoleta?", "cont_sin_nada")

    verificar(
        "informa la indisponibilidad en vez de caerse",
        bool(resp and len(resp) > 20),
        f"respuesta: {resp[:80]!r}",
    )
    # Se buscan señales de un local concreto (dirección o estado de
    # apertura), no frases que puedan aparecer de forma legítima en un
    # mensaje de indisponibilidad. "de turno en" daba falso positivo:
    # el sistema respondía bien y la prueba lo marcaba como fallo.
    inventos = ["calle ", "avenida ", "abierta ahora", "cerrada ahora"]
    verificar(
        "no inventa una farmacia",
        not any(t in resp.lower() for t in inventos),
        f"respuesta: {resp[:150]!r}",
    )


# ---------------------------------------------------------------------------
# 3. Bordes de interpretación de horarios
# ---------------------------------------------------------------------------


def pruebas_horarios() -> None:
    print("\n3. BORDES DE HORARIO")

    momento = datetime(2026, 8, 10, 3, 0)  # 03:00, plena madrugada

    casos = [
        # (apertura, cierre, esperado, descripción)
        ("09:00:00", "08:59:00", True, "nocturno abierto a las 03:00"),
        ("00:00:00", "23:59:00", True, "turno diurno completo"),
        ("09:00:00", "18:00:00", False, "diurno cerrado a las 03:00"),
        ("00:00:00", "00:00:00", None, "apertura igual a cierre: no verificable"),
        ("", "", None, "horario vacío: no verificable"),
        ("no-es-hora", "tampoco", None, "horario ilegible: no verificable"),
    ]

    for apertura, cierre, esperado, desc in casos:
        reg = {"apertura": apertura, "cierre": cierre}
        obtenido = tool_minsal.esta_abierta(reg, momento)
        verificar(desc, obtenido is esperado, f"esperado={esperado} obtenido={obtenido}")


# ---------------------------------------------------------------------------
# 4. Bordes de normalización
# ---------------------------------------------------------------------------


def pruebas_normalizacion() -> None:
    print("\n4. NORMALIZACIÓN DE TEXTO")

    casos = [
        ("Maipú", "MAIPU", "quita la tilde"),
        ("Ñuñoa", "ÑUÑOA", "preserva la Ñ"),
        ("  Estación   Central  ", "ESTACION CENTRAL", "colapsa espacios"),
        ("viña del mar", "VIÑA DEL MAR", "mayúsculas y Ñ juntas"),
        (None, "", "None devuelve cadena vacía"),
        ("", "", "cadena vacía se mantiene"),
    ]

    for entrada, esperado, desc in casos:
        obtenido = tool_minsal.normalizar_texto(entrada)
        verificar(desc, obtenido == esperado, f"esperado={esperado!r} obtenido={obtenido!r}")


# ---------------------------------------------------------------------------
# 5. Bordes de filtrado por fecha
# ---------------------------------------------------------------------------


def pruebas_vigencia() -> None:
    print("\n5. VIGENCIA DE REGISTROS")

    hoy = datetime(2026, 8, 10).date()
    registros = [
        {"fecha": (hoy - timedelta(days=1)).isoformat()},
        {"fecha": hoy.isoformat()},
        {"fecha": (hoy + timedelta(days=1)).isoformat()},
        {"fecha": (hoy - timedelta(days=2)).isoformat()},
        {"fecha": (hoy - timedelta(days=40)).isoformat()},
        {"fecha": ""},
    ]

    vigentes = tool_minsal.filtrar_vigentes(registros, hoy)
    verificar(
        "conserva ayer, hoy y mañana; descarta el resto",
        len(vigentes) == 3,
        f"esperados 3, obtenidos {len(vigentes)}",
    )
    verificar(
        "descarta registros sin fecha",
        all(r["fecha"] for r in vigentes),
    )


# ---------------------------------------------------------------------------
# 6. Bordes de estado conversacional
# ---------------------------------------------------------------------------


def pruebas_estado() -> None:
    print("\n6. ESTADO CONVERSACIONAL")

    # Seguimiento sin turno previo: no debe inventar una farmacia.
    resp = preguntar("¿Cuál es su dirección?", "cont_sin_previo")
    verificar(
        "seguimiento sin contexto previo no inventa una dirección",
        not any(t in resp.lower() for t in ["calle", "avenida"]),
        f"respuesta: {resp[:120]!r}",
    )

    # Cambio de comuna a mitad de conversación.
    hilo = "cont_cambio"
    preguntar("¿Hay farmacia de turno en Recoleta?", hilo)
    resp2 = preguntar("¿Y en Quilicura?", hilo)
    verificar(
        "el cambio de comuna se refleja en la respuesta",
        "quilicura" in resp2.lower(),
        f"respuesta: {resp2[:120]!r}",
    )

    # Cambio de tema: la comuna no debe arrastrarse al RAG.
    hilo2 = "cont_tema"
    preguntar("¿Hay farmacia de turno en Recoleta?", hilo2)
    resp3 = preguntar("¿Para qué sirve el omeprazol?", hilo2)
    verificar(
        "al cambiar de tema no aparecen datos de farmacias",
        "recoleta" not in resp3.lower(),
        f"respuesta: {resp3[:120]!r}",
    )


# ---------------------------------------------------------------------------
# 7. Bordes de entrada
# ---------------------------------------------------------------------------


def pruebas_entrada() -> None:
    print("\n7. ENTRADAS ATÍPICAS")

    casos = [
        ("?", "solo signo de puntuación"),
        ("aaaaaaaaaa", "texto sin sentido"),
        ("farmacia " * 100, "mensaje muy largo"),
        ("🏥💊", "solo emojis"),
    ]

    for texto, desc in casos:
        try:
            resp = preguntar(texto, f"cont_ent_{desc[:8]}")
            verificar(f"{desc}: responde sin excepción", bool(resp))
        except Exception as e:
            verificar(f"{desc}: responde sin excepción", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pruebas_fallos_fuente()
    prueba_sin_fuente_ni_respaldo()
    pruebas_horarios()
    pruebas_normalizacion()
    pruebas_vigencia()
    pruebas_estado()
    pruebas_entrada()

    print(f"\n{'=' * 60}")
    total = len(RESULTADOS)
    pasaron = sum(1 for _, ok, _ in RESULTADOS if ok)
    print(f"RESULTADO: {pasaron}/{total} pruebas pasaron")

    fallidas = [n for n, ok, _ in RESULTADOS if not ok]
    if fallidas:
        print("\nFallaron:")
        for n in fallidas:
            print(f"  - {n}")