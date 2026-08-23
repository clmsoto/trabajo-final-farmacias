"""
Farmacias de turno: normalización, vigencia e interpretación de horarios.

Este módulo es el pipeline de cinco pasos del enunciado, con una
diferencia respecto de la versión anterior: no consulta la fuente. El
cortafuegos que la fuente tiene delante bloquea el tráfico desde rangos
de datacenter, así que el volcado llega por POST desde un proceso que
corre en una conexión chilena.

  1. recibir     → POST /turnos/sync, validado en api_datos.py
  2. normalizar  → texto, comunas sin tilde, teléfonos
  3. filtrar     → por fecha vigente y por comuna o región
  4. interpretar → turnos que cruzan medianoche
  5. responder   → dato acotado, con su fecha de captura
"""

import json
import os
import re
import unicodedata
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from comunas_regiones import canonizar_region, region_de

RUTA_ESTADO = Path(os.environ.get("RUTA_ESTADO", "datos/turnos_vigentes.json"))

# Estado en memoria del proceso. Se persiste en disco para sobrevivir a
# un reinicio, pero la fuente de verdad es el último POST recibido.
_estado: dict = {"registros": [], "capturado_en": None}


# ---------------------------------------------------------------------------
# Paso 2 · Normalizar
# ---------------------------------------------------------------------------


def normalizar_texto(valor: str | None) -> str:
    """
    Mayúsculas, sin tildes, espacios colapsados.

    La Ñ se preserva: es una letra distinta, no una N con diacrítico.
    Ñuñoa no es Nunoa.
    """
    if not valor:
        return ""
    texto = str(valor).strip().upper()
    texto = texto.replace("Ñ", "\x00")
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.replace("\x00", "Ñ")
    return re.sub(r"\s+", " ", texto)


def normalizar_telefono(valor: str | None) -> str | None:
    if not valor:
        return None
    limpio = re.sub(r"[^\d+]", "", str(valor))
    return limpio or None


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
        "lat": reg.get("local_lat"),
        "lng": reg.get("local_lng"),
    }


# ---------------------------------------------------------------------------
# Paso 4 · Interpretar horarios
# ---------------------------------------------------------------------------


def _parse_hora(valor: str) -> dtime | None:
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(valor, fmt).time()
        except ValueError:
            continue
    return None


def cruza_medianoche(apertura: str, cierre: str) -> bool:
    a, c = _parse_hora(apertura), _parse_hora(cierre)
    return bool(a and c and c < a)


def esta_abierta(reg: dict, momento: datetime) -> bool | None:
    """
    ¿Está abierta ahora? None si el horario no es interpretable.

    Un turno que cruza medianoche está abierto si la hora actual es
    posterior a la apertura O anterior al cierre. Tratarlo como
    intervalo simple lo daría por cerrado toda la madrugada, que es
    justo cuando se necesita.
    """
    a, c = _parse_hora(reg["apertura"]), _parse_hora(reg["cierre"])
    if a is None or c is None:
        return None
    if a == c:
        # Dato ambiguo: ¿veinticuatro horas o registro incompleto? Se
        # rotula como no verificable en vez de afirmar que está cerrada.
        return None
    hora = momento.time()
    if c < a:
        return hora >= a or hora <= c
    return a <= hora <= c


# ---------------------------------------------------------------------------
# Paso 3 · Filtrar
# ---------------------------------------------------------------------------


def filtrar_vigentes(registros: list[dict], hoy: date) -> list[dict]:
    """
    Ventana de tres días: ayer, hoy y mañana.

    Ayer porque un turno nocturno iniciado el día anterior sigue vigente
    en la madrugada. Mañana porque la fuente publica con anticipación.
    Los registros anteriores son datos vencidos que la fuente arrastra.
    """
    ventana = {
        (hoy - timedelta(days=1)).isoformat(),
        hoy.isoformat(),
        (hoy + timedelta(days=1)).isoformat(),
    }
    return [r for r in registros if r["fecha"] in ventana]


# ---------------------------------------------------------------------------
# Sugerencias por cercanía
# ---------------------------------------------------------------------------


def _centroide(registros: list[dict], comuna: str) -> tuple[float, float] | None:
    """
    Ubicación aproximada de una comuna, calculada desde cualquier registro
    que la mencione, aunque su turno esté vencido. Un dato viejo no sirve
    para informar turnos, pero sí para ubicar la comuna en el mapa.
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


def _cercanas(vigentes: list[dict], ref: tuple[float, float], n: int = 5) -> list[str]:
    """
    Distancia euclidiana sobre lat/lng, no Haversine: para ordenar comunas
    de una misma zona la diferencia es irrelevante y evita una dependencia.
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


# ---------------------------------------------------------------------------
# Paso 5 · Responder
# ---------------------------------------------------------------------------


def _formatear(reg: dict, momento: datetime) -> dict:
    return {
        "nombre": reg["nombre"],
        "direccion": reg["direccion"],
        "comuna": reg["comuna"],
        "telefono": reg["telefono"],
        "horario": f"{reg['apertura'][:5]} a {reg['cierre'][:5]}",
        "nocturno": cruza_medianoche(reg["apertura"], reg["cierre"]),
        "abierta_ahora": esta_abierta(reg, momento),
        "fecha_turno": reg["fecha"],
    }


def por_comuna(comuna: str, momento: datetime | None = None) -> dict:
    momento = momento or datetime.now()
    registros = _estado["registros"]
    vigentes = filtrar_vigentes(registros, momento.date())
    objetivo = normalizar_texto(comuna)
    encontradas = [r for r in vigentes if r["comuna_norm"] == objetivo]

    if not encontradas:
        ref = _centroide(registros, comuna)
        sugeridas = (
            _cercanas(vigentes, ref, n=5)
            if ref
            else sorted({r["comuna"] for r in vigentes})[:5]
        )
        return {
            "comuna": comuna,
            "encontrada": False,
            "farmacias": [],
            "comunas_sugeridas": sugeridas,
            "capturado_en": _estado["capturado_en"],
        }

    return {
        "comuna": comuna,
        "encontrada": True,
        "farmacias": [_formatear(r, momento) for r in encontradas],
        "comunas_sugeridas": [],
        "capturado_en": _estado["capturado_en"],
    }


def por_region(region: str, momento: datetime | None = None) -> dict:
    """
    Agrupa por comuna dentro de una región.

    No se usa el campo de región de la fuente: es un identificador
    interno que no corresponde a la numeración oficial. La región se
    deriva del mapa comuna→región mantenido en el proyecto.
    """
    momento = momento or datetime.now()
    objetivo = canonizar_region(normalizar_texto(region))
    if not objetivo:
        return {
            "region": region,
            "encontrada": False,
            "comunas": {},
            "capturado_en": _estado["capturado_en"],
        }

    vigentes = filtrar_vigentes(_estado["registros"], momento.date())
    por_comuna_dict: dict[str, list[dict]] = {}
    for r in vigentes:
        if region_de(r["comuna_norm"]) != objetivo:
            continue
        por_comuna_dict.setdefault(r["comuna"], []).append(_formatear(r, momento))

    return {
        "region": objetivo,
        "encontrada": True,
        "comunas": dict(sorted(por_comuna_dict.items())),
        "total_locales": sum(len(v) for v in por_comuna_dict.values()),
        "capturado_en": _estado["capturado_en"],
    }


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


def reemplazar(registros_crudos: list[dict]) -> int:
    """Reemplaza los turnos vigentes con un volcado nuevo y lo persiste."""
    _estado["registros"] = [normalizar_registro(r) for r in registros_crudos]
    _estado["capturado_en"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    RUTA_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    with RUTA_ESTADO.open("w", encoding="utf-8") as fh:
        json.dump(_estado, fh, ensure_ascii=False)

    return len(filtrar_vigentes(_estado["registros"], date.today()))


def cargar_inicial() -> None:
    """Restaura el último volcado tras un reinicio del proceso."""
    if not RUTA_ESTADO.exists():
        print("[turnos] Sin volcado previo: el servicio arranca sin turnos.")
        return
    with RUTA_ESTADO.open(encoding="utf-8") as fh:
        datos = json.load(fh)
    _estado["registros"] = datos.get("registros", [])
    _estado["capturado_en"] = datos.get("capturado_en")
    print(f"[turnos] Volcado restaurado: {len(_estado['registros'])} registros.")


def estado() -> dict:
    return {
        "capturado_en": _estado["capturado_en"],
        "total": len(_estado["registros"]),
        "vigentes": len(filtrar_vigentes(_estado["registros"], date.today())),
    }