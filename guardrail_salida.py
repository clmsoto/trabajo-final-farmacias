"""
Guardrail de salida: inspección determinista de la respuesta generada.

Segunda capa de la defensa del criterio 5. La primera capa (clasificación
de intención en el router) bloqueó 27 de 27 casos adversarios en la
batería de pruebas, por lo que esta capa hoy no tiene fallos que atrapar.

Se implementa igual porque la primera capa depende de un clasificador LLM,
que es probabilístico: un fraseo no contemplado en las pruebas podría
pasar. Esta capa no depende del criterio del modelo, es determinista y
barata. Es una red de seguridad, no la respuesta a un fallo observado.
"""

import re

# Patrones de posología. Buscan la forma de una dosis, no palabras sueltas:
# "500 mg", "10 ml cada 8 horas", "2 comprimidos al día".
PATRONES_DOSIS = [
    # Cantidad + unidad de masa/volumen farmacéutica
    r"\b\d+[\.,]?\d*\s*(mg|mcg|µg|ug|g|ml|cc|UI)\b",
    # Frecuencia de administración
    r"\bcada\s+\d+\s*(hora|horas|h|día|días)\b",
    r"\b\d+\s*(vez|veces)\s+(al|por)\s+(día|dia|jornada)\b",
    # Unidades de forma farmacéutica con cantidad
    r"\b\d+\s*(comprimido|comprimidos|tableta|tabletas|cápsula|cápsulas|"
    r"gota|gotas|cucharada|cucharadas|sobre|sobres)\b",
]

MENSAJE_BLOQUEO = (
    "No puedo entregarte información de dosis ni pautas de administración; "
    "eso requiere evaluación de un profesional de salud. Sí puedo ayudarte "
    "a encontrar una farmacia de turno o explicarte una ficha referencial."
)


def detectar_dosis(texto: str) -> list[str]:
    """Devuelve las coincidencias de posología encontradas en el texto."""
    hallazgos = []
    for patron in PATRONES_DOSIS:
        hallazgos.extend(m.group(0) for m in re.finditer(patron, texto, re.IGNORECASE))
    return hallazgos


def revisar(texto: str) -> tuple[str, list[str]]:
    """
    Inspecciona la respuesta antes de devolverla al usuario.

    Devuelve (texto_final, hallazgos). Si hay hallazgos, el texto original
    se descarta completo: no se intenta redactar ni enmascarar la dosis,
    porque una respuesta parcialmente censurada puede seguir siendo
    interpretable y da falsa sensación de control.
    """
    hallazgos = detectar_dosis(texto)
    if hallazgos:
        return MENSAJE_BLOQUEO, hallazgos
    return texto, []


if __name__ == "__main__":
    casos = [
        ("Debe tomar 500 mg cada 8 horas.", True),
        ("Se administran 2 comprimidos al día.", True),
        ("Tome 10 ml de jarabe.", True),
        ("El ibuprofeno está contraindicado en sangrado gastrointestinal.", False),
        ("La farmacia atiende de 08:00 a 07:59 del día siguiente.", False),
        ("Avenida Recoleta 836, Recoleta. Teléfono 26031339.", False),
    ]
    for texto, espera_bloqueo in casos:
        _, hallazgos = revisar(texto)
        ok = bool(hallazgos) == espera_bloqueo
        print(f"{'OK ' if ok else 'FALLA'} · {texto}")
        if hallazgos:
            print(f"        hallazgos: {hallazgos}")