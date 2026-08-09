"""
Herramienta de consulta de farmacias de turno (API MINSAL).

Implementa el pipeline de cinco pasos exigido por el enunciado:
  1. recibir     — timeout, validación HTTP y de esquema
  2. normalizar  — texto, comunas sin tilde, teléfonos
  3. filtrar     — por fecha vigente y por comuna
  4. interpretar — turnos que cruzan medianoche
  5. responder   — dato acotado, sin inferir stock ni precio

Decisiones de diseño documentadas en el informe:
- El endpoint devuelve registros obsoletos junto a los vigentes (en la
  inspección del 2026-08-09: 69 de 143 registros con fecha 2026-07-01).
  Filtrar por fecha es un control de calidad, no una optimización.
- El 49% de los registros son turnos nocturnos (apertura 09:00, cierre
  08:59). Una comparación ingenua hora_actual < hora_cierre los descarta.
- El fallback usa un snapshot local y SIEMPRE se rotula como tal, con la
  fecha de captura visible. Nunca se presenta como dato en vivo.
"""

import json
import re
import time
import unicodedata
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import httpx

URL_TURNOS = "https://midas.minsal.cl/farmacia_v2/WS/getLocalesTurnos.php"
TIMEOUT_S = 8.0
CACHE_TTL_S = 300  # 5 minutos: los turnos no cambian en el intradía
SNAPSHOT_PATH = Path("data/snapshot_turnos.json")

# Caché en memoria del proceso. Suficiente para el alcance del proyecto;
# en producción correspondería un caché compartido entre instancias.
_cache: dict = {"datos": None, "ts": 0.0}


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------


def normalizar_texto(valor: str | None) -> str:
    """Mayúsculas, sin tildes, espacios colapsados.

    El endpoint entrega comunas como 'MAIPU' o 'VIÑA DEL MAR': mayúsculas,
    a veces sin tilde. Si el usuario escribe 'Maipú', el match directo
    falla. Se normalizan ambos lados con la misma función.
    La Ñ se preserva: es una letra distinta, no una N con diacrítico.
    """
    if not valor:
        return ""
    texto = str(valor).strip().upper()
    texto = texto.replace("Ñ", "\x00")  # protege la Ñ del stripping
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.replace("\x00", "Ñ")
    return re.sub(r"\s+", " ", texto)


def normalizar_telefono(valor: str | None) -> str | None:
    """Deja solo dígitos y el + inicial. Devuelve None si queda vacío."""
    if not valor:
        return None
    limpio = re.sub(r"[^\d+]", "", str(valor))
    return limpio or None


# ---------------------------------------------------------------------------
# Paso 1 — Recibir
# ---------------------------------------------------------------------------

CAMPOS_MINIMOS = {
    "fecha",
    "local_nombre",
    "comuna_nombre",
    "local_direccion",
    "funcionamiento_hora_apertura",
    "funcionamiento_hora_cierre",
}


def _descargar() -> list[dict]:
    """Descarga con timeout y valida esquema mínimo. Lanza en caso de fallo."""
    r = httpx.get(URL_TURNOS, timeout=TIMEOUT_S)
    r.raise_for_status()

    datos = r.json()
    if not isinstance(datos, list) or not datos:
        raise ValueError("Respuesta sin registros o con formato inesperado")

    faltantes = CAMPOS_MINIMOS - set(datos[0].keys())
    if faltantes:
        raise ValueError(f"Faltan campos esperados: {sorted(faltantes)}")

    return datos


def _cargar_snapshot() -> list[dict] | None:
    """Lee el respaldo local. Devuelve None si no existe."""
    if not SNAPSHOT_PATH.exists():
        return None
    with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def obtener_datos() -> tuple[list[dict], str]:
    """
    Devuelve (registros, origen) donde origen es 'vivo', 'cache' o 'snapshot'.

    El origen se propaga hasta la respuesta final: el usuario debe poder
    distinguir un dato en vivo de un respaldo, siempre.
    """
    ahora = time.time()
    if _cache["datos"] is not None and (ahora - _cache["ts"]) < CACHE_TTL_S:
        return _cache["datos"], "cache"

    try:
        datos = _descargar()
        _cache["datos"] = datos
        _cache["ts"] = ahora
        return datos, "vivo"
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
        print(f"[minsal] Fallo la consulta en vivo ({type(e).__name__}: {e})")
        snapshot = _cargar_snapshot()
        if snapshot is None:
            raise RuntimeError(
                "Sin datos en vivo y sin snapshot de respaldo disponible"
            ) from e
        return snapshot, "snapshot"


# ---------------------------------------------------------------------------
# Paso 2 — Normalizar
# ---------------------------------------------------------------------------


def normalizar_registro(reg: dict) -> dict:
    return {
        "fecha": (reg.get("fecha") or "").strip(),
        "nombre": (reg.get("local_nombre") or "").strip(),
        "comuna": (reg.get("comuna_nombre") or "").strip(),
        "comuna_norm": normalizar_texto(reg.get("comuna_nombre")),
        "direccion": (reg.get("local_direccion") or "").strip(),
        "telefono": normalizar_telefono(reg.get("local_telefono")),
        "apertura": (reg.get("funcionamiento_hora_apertura") or "").strip(),
        "cierre": (reg.get("funcionamiento_hora_cierre") or "").strip(),
        "dia": (reg.get("funcionamiento_dia") or "").strip(),
        "lat": reg.get("local_lat"),
        "lng": reg.get("local_lng"),
    }


# ---------------------------------------------------------------------------
# Paso 4 — Interpretar horarios (se usa dentro del filtrado)
# ---------------------------------------------------------------------------


def _parse_hora(valor: str) -> dtime | None:
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(valor, fmt).time()
        except ValueError:
            continue
    return None


def cruza_medianoche(apertura: str, cierre: str) -> bool:
    """True si el turno termina al día siguiente (ej. 09:00 → 08:59)."""
    a, c = _parse_hora(apertura), _parse_hora(cierre)
    if a is None or c is None:
        return False
    return c < a


def esta_abierta(reg: dict, momento: datetime) -> bool | None:
    """
    ¿Está abierta en este momento? None si el horario no es interpretable.

    Un turno que cruza medianoche está abierto si la hora actual es
    posterior a la apertura O anterior al cierre. Compararlo como un
    intervalo simple lo daría por cerrado durante toda la noche, que es
    justo cuando el usuario lo necesita.
    """
    a, c = _parse_hora(reg["apertura"]), _parse_hora(reg["cierre"])
    if a is None or c is None:
        return None
    hora = momento.time()
    if c < a:  # cruza medianoche
        return hora >= a or hora <= c
    return a <= hora <= c


# ---------------------------------------------------------------------------
# Paso 3 — Filtrar
# ---------------------------------------------------------------------------


def filtrar_vigentes(registros: list[dict], hoy: date) -> list[dict]:
    """
    Descarta registros con fecha fuera de la ventana vigente.

    Se acepta hoy y mañana: el turno nocturno iniciado ayer sigue vigente
    en la madrugada, y el endpoint publica el turno del día siguiente con
    anticipación. Los registros de fechas anteriores son datos obsoletos
    que el endpoint arrastra (verificado en la inspección de la fuente).
    """
    ventana = {
        (hoy - timedelta(days=1)).isoformat(),
        hoy.isoformat(),
        (hoy + timedelta(days=1)).isoformat(),
    }
    return [r for r in registros if r["fecha"] in ventana]


def filtrar_comuna(registros: list[dict], comuna: str) -> list[dict]:
    objetivo = normalizar_texto(comuna)
    if not objetivo:
        return []
    return [r for r in registros if r["comuna_norm"] == objetivo]


# ---------------------------------------------------------------------------
# Paso 5 — Responder
# ---------------------------------------------------------------------------

def _centroide_comuna(registros: list[dict], comuna: str) -> tuple[float, float] | None:
    """
    Ubicación aproximada de una comuna, calculada desde cualquier registro
    que la mencione, aunque su turno esté vencido. Los datos obsoletos no
    sirven para informar turnos, pero sí para ubicar la comuna en el mapa.
    """
    objetivo = normalizar_texto(comuna)
    puntos = []
    for r in registros:
        if r["comuna_norm"] == objetivo and r["lat"] and r["lng"]:
            try:
                puntos.append((float(r["lat"]), float(r["lng"])))
            except (TypeError, ValueError):
                continue
    if not puntos:
        return None
    return (
        sum(p[0] for p in puntos) / len(puntos),
        sum(p[1] for p in puntos) / len(puntos),
    )


def _comunas_cercanas(
    vigentes: list[dict], ref: tuple[float, float], n: int = 5
) -> list[str]:
    """
    Comunas con turno vigente más próximas a un punto de referencia.

    Se usa distancia euclidiana sobre lat/lng, no la fórmula de Haversine:
    para ordenar comunas dentro de una misma zona la diferencia es
    irrelevante, y evita una dependencia adicional.
    """
    distancias: dict[str, float] = {}
    for r in vigentes:
        if not (r["lat"] and r["lng"]):
            continue
        try:
            lat, lng = float(r["lat"]), float(r["lng"])
        except (TypeError, ValueError):
            continue
        d = (lat - ref[0]) ** 2 + (lng - ref[1]) ** 2
        if r["comuna"] not in distancias or d < distancias[r["comuna"]]:
            distancias[r["comuna"]] = d
    return [c for c, _ in sorted(distancias.items(), key=lambda x: x[1])[:n]]

def buscar_turnos(comuna: str, momento: datetime | None = None) -> dict:
    """
    Punto de entrada de la herramienta.

    Devuelve un dict con:
      - origen: 'vivo' | 'cache' | 'snapshot'
      - advertencia: texto a mostrar si el origen no es en vivo, o None
      - farmacias: lista de locales, cada uno con su estado de apertura
      - comuna_encontrada: False si la comuna no aparece en la fuente
      - comunas_sugeridas: alternativas cuando no hubo coincidencia
    """
    momento = momento or datetime.now()

    crudos, origen = obtener_datos()
    registros = [normalizar_registro(r) for r in crudos]
    vigentes = filtrar_vigentes(registros, momento.date())
    en_comuna = filtrar_comuna(vigentes, comuna)

    advertencia = None
    if origen == "snapshot":
        fechas = sorted({r["fecha"] for r in registros if r["fecha"]})
        captura = fechas[-1] if fechas else "desconocida"
        advertencia = (
            f"Dato de respaldo capturado el {captura}, no consultado en vivo. "
            "Confirma el turno antes de trasladarte."
        )

    if not en_comuna:
        # Sugerencias por cercanía geográfica, no alfabéticas: si el usuario
        # pregunta por una comuna sin turno vigente, las alternativas útiles
        # son las que puede alcanzar, no las primeras del abecedario.
        ref = _centroide_comuna(registros, comuna)
        if ref:
            cercanas = _comunas_cercanas(vigentes, ref, n=5)
        else:
            cercanas = sorted({r["comuna"] for r in vigentes})[:5]
        return {
            "origen": origen,
            "advertencia": advertencia,
            "farmacias": [],
            "comuna_encontrada": False,
            "comunas_sugeridas": cercanas,
        }

    farmacias = []
    for r in en_comuna:
        abierta = esta_abierta(r, momento)
        farmacias.append(
            {
                "nombre": r["nombre"],
                "direccion": r["direccion"],
                "comuna": r["comuna"],
                "telefono": r["telefono"],
                "horario": f"{r['apertura'][:5]} a {r['cierre'][:5]}",
                "nocturno": cruza_medianoche(r["apertura"], r["cierre"]),
                "abierta_ahora": abierta,
                "fecha_turno": r["fecha"],
            }
        )

    return {
        "origen": origen,
        "advertencia": advertencia,
        "farmacias": farmacias,
        "comuna_encontrada": True,
        "comunas_sugeridas": [],
    }


def formatear_contexto(resultado: dict) -> str:
    """
    Convierte el resultado en texto para el modelo generador.

    No se entrega el JSON crudo: el enunciado lo prohíbe explícitamente.
    Cada dato entregado es uno que la fuente respalda; no se infiere stock,
    precio ni disponibilidad de medicamentos.
    """
    if not resultado["comuna_encontrada"]:
        sugeridas = ", ".join(resultado["comunas_sugeridas"])
        return (
            "No hay farmacias de turno registradas para esa comuna en la "
            f"fuente. Comunas con turno vigente, entre otras: {sugeridas}."
        )

    lineas = []
    if resultado["advertencia"]:
        lineas.append(f"AVISO: {resultado['advertencia']}")

    for f in resultado["farmacias"]:
        estado = {True: "abierta ahora", False: "cerrada ahora"}.get(
            f["abierta_ahora"], "horario no verificable"
        )
        nocturno = " (turno nocturno, cierra al día siguiente)" if f["nocturno"] else ""
        tel = f" · Teléfono: {f['telefono']}" if f["telefono"] else ""
        lineas.append(
            f"- {f['nombre']} · {f['direccion']}, {f['comuna']} · "
            f"Horario {f['horario']}{nocturno} · {estado}{tel}"
        )

    lineas.append(
        "[Nota interna: esta fuente informa locales y turnos únicamente. "
        "Si el usuario pregunta por stock, precio o disponibilidad de un "
        "medicamento, aclara que no puedes confirmarlo. No menciones esta "
        "limitación si no viene al caso.]"
    )
    return "\n".join(lineas)


if __name__ == "__main__":
    for comuna in ["Recoleta", "Quilicura", "Estación Central", "Providencia"]:
        print(f"\n{'=' * 70}\nCOMUNA: {comuna}")
        res = buscar_turnos(comuna)
        print(f"origen={res['origen']} · encontrada={res['comuna_encontrada']}")
        print(formatear_contexto(res))