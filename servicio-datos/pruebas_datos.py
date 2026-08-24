"""
Pruebas unitarias del servicio de datos.

Cubren la lógica que se mudó del asistente cuando el sistema se dividió
en dos servicios: interpretación de horarios, normalización de comunas y
filtro de vigencia. No requieren red ni credenciales.

Las pruebas de cómo reacciona el asistente cuando este servicio falla
están en pruebas_contingencia.py, en la raíz del proyecto.
"""

from datetime import date, datetime, timedelta

import turnos

RESULTADOS: list[tuple[str, bool, str]] = []


def verificar(nombre: str, condicion: bool, detalle: str = "") -> None:
    RESULTADOS.append((nombre, condicion, detalle))
    print(f"  [{'PASA ' if condicion else 'FALLA'}] {nombre}")
    if not condicion and detalle:
        print(f"          {detalle}")


# ---------------------------------------------------------------------------
# 1. Interpretación de horarios
# ---------------------------------------------------------------------------


def pruebas_horarios() -> None:
    print("\n1. INTERPRETACIÓN DE HORARIOS")

    madrugada = datetime(2026, 8, 24, 3, 0)
    tarde = datetime(2026, 8, 24, 15, 0)

    casos = [
        # (apertura, cierre, momento, esperado, descripción)
        ("09:00:00", "08:59:00", madrugada, True, "nocturno abierto a las 03:00"),
        ("09:00:00", "08:59:00", tarde, True, "nocturno abierto a las 15:00"),
        ("00:00:00", "23:59:00", madrugada, True, "turno continuo del día"),
        ("09:00:00", "18:00:00", madrugada, False, "diurno cerrado de madrugada"),
        ("09:00:00", "18:00:00", tarde, True, "diurno abierto de tarde"),
        ("00:00:00", "00:00:00", tarde, None, "apertura igual a cierre: no verificable"),
        ("", "", tarde, None, "horario vacío: no verificable"),
        ("no-es-hora", "tampoco", tarde, None, "horario ilegible: no verificable"),
    ]

    for apertura, cierre, momento, esperado, desc in casos:
        reg = {"apertura": apertura, "cierre": cierre}
        obtenido = turnos.esta_abierta(reg, momento)
        verificar(desc, obtenido is esperado, f"esperado={esperado} obtenido={obtenido}")

    # El rótulo de nocturno debe coincidir con la interpretación
    verificar(
        "09:00 a 08:59 se rotula como nocturno",
        turnos.cruza_medianoche("09:00:00", "08:59:00") is True,
    )
    verificar(
        "09:00 a 18:00 no se rotula como nocturno",
        turnos.cruza_medianoche("09:00:00", "18:00:00") is False,
    )


# ---------------------------------------------------------------------------
# 2. Normalización de texto
# ---------------------------------------------------------------------------


def pruebas_normalizacion() -> None:
    print("\n2. NORMALIZACIÓN DE COMUNAS")

    casos = [
        ("Maipú", "MAIPU", "quita la tilde"),
        ("Ñuñoa", "ÑUÑOA", "preserva la Ñ"),
        ("  Estación   Central  ", "ESTACION CENTRAL", "colapsa espacios"),
        ("viña del mar", "VIÑA DEL MAR", "mayúsculas y Ñ juntas"),
        ("Peñalolén", "PEÑALOLEN", "Ñ y tilde en la misma palabra"),
        (None, "", "None devuelve cadena vacía"),
        ("", "", "cadena vacía se mantiene"),
    ]

    for entrada, esperado, desc in casos:
        obtenido = turnos.normalizar_texto(entrada)
        verificar(desc, obtenido == esperado, f"esperado={esperado!r} obtenido={obtenido!r}")

    # Teléfonos
    verificar(
        "el teléfono conserva solo dígitos",
        turnos.normalizar_telefono("(2) 2492-146") == "22492146",
        f"obtenido={turnos.normalizar_telefono('(2) 2492-146')!r}",
    )
    verificar(
        "un teléfono vacío devuelve None",
        turnos.normalizar_telefono("") is None,
    )


# ---------------------------------------------------------------------------
# 3. Filtro de vigencia
# ---------------------------------------------------------------------------


def pruebas_vigencia() -> None:
    print("\n3. VIGENCIA DE REGISTROS")

    hoy = date(2026, 8, 24)
    registros = [
        {"fecha": (hoy - timedelta(days=1)).isoformat()},
        {"fecha": hoy.isoformat()},
        {"fecha": (hoy + timedelta(days=1)).isoformat()},
        {"fecha": (hoy - timedelta(days=2)).isoformat()},
        {"fecha": (hoy - timedelta(days=54)).isoformat()},
        {"fecha": ""},
    ]

    vigentes = turnos.filtrar_vigentes(registros, hoy)
    verificar(
        "conserva ayer, hoy y mañana",
        len(vigentes) == 3,
        f"esperados 3, obtenidos {len(vigentes)}",
    )
    verificar(
        "descarta registros vencidos y sin fecha",
        all(r["fecha"] for r in vigentes),
    )


# ---------------------------------------------------------------------------
# 4. Consulta con el estado vacío
# ---------------------------------------------------------------------------


def pruebas_estado_vacio() -> None:
    print("\n4. CONSULTA SIN TURNOS CARGADOS")

    original = turnos._estado.copy()
    turnos._estado["registros"] = []
    turnos._estado["capturado_en"] = None
    try:
        r = turnos.por_comuna("Recoleta")
        verificar("no encuentra farmacias", r["encontrada"] is False)
        verificar("no inventa sugerencias", r["comunas_sugeridas"] == [])
        verificar("capturado_en viaja como None", r["capturado_en"] is None)

        r = turnos.por_region("Metropolitana")
        verificar("la región se reconoce igual", r["encontrada"] is True)
        verificar("pero sin comunas", r["comunas"] == {})
    finally:
        turnos._estado.update(original)


# ---------------------------------------------------------------------------
# 5. Región no reconocida
# ---------------------------------------------------------------------------


def pruebas_region() -> None:
    print("\n5. RECONOCIMIENTO DE REGIONES")

    casos = [
        ("Región Metropolitana", True, "nombre completo"),
        ("metropolitana", True, "en minúsculas"),
        ("RM", True, "sigla"),
        ("Región del Biobío", True, "con tilde y artículo"),
        ("Provincia de Marga Marga", False, "no es una región"),
        ("Cundinamarca", False, "región de otro país"),
    ]

    for entrada, esperado, desc in casos:
        r = turnos.por_region(entrada)
        verificar(desc, r["encontrada"] is esperado, f"entrada={entrada!r}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pruebas_horarios()
    pruebas_normalizacion()
    pruebas_vigencia()
    pruebas_estado_vacio()
    pruebas_region()

    total = len(RESULTADOS)
    pasaron = sum(1 for _, ok, _ in RESULTADOS if ok)
    print(f"\n{'=' * 60}")
    print(f"RESULTADO: {pasaron}/{total} pruebas pasaron")

    fallidas = [n for n, ok, _ in RESULTADOS if not ok]
    if fallidas:
        print("\nFallaron:")
        for n in fallidas:
            print(f"  - {n}")