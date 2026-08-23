"""
Alimenta el servicio con el volcado fresco de farmacias de turno.

Corre desde una conexión chilena, donde la fuente responde. La petición
sale desde donde la fuente la acepta: no hay nada que enmascarar.

Uso:
    poetry run python sincronizar_turnos.py                    # local
    poetry run python sincronizar_turnos.py --destino <url>    # producción
"""

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

URL_FUENTE = "https://midas.minsal.cl/farmacia_v2/WS/getLocalesTurnos.php"
TIMEOUT = 20.0

CAMPOS_MINIMOS = {
    "fecha",
    "local_nombre",
    "comuna_nombre",
    "local_direccion",
    "funcionamiento_hora_apertura",
    "funcionamiento_hora_cierre",
}


def descargar() -> list[dict]:
    """Descarga el volcado y valida el esquema antes de enviarlo."""
    r = httpx.get(URL_FUENTE, timeout=TIMEOUT)
    r.raise_for_status()

    datos = r.json()
    if not isinstance(datos, list) or len(datos) < 10:
        raise ValueError(f"Volcado insuficiente: {len(datos) if isinstance(datos, list) else 'no es lista'}")

    faltantes = CAMPOS_MINIMOS - set(datos[0].keys())
    if faltantes:
        raise ValueError(f"Faltan campos esperados: {sorted(faltantes)}")

    return datos


def enviar(destino: str, registros: list[dict]) -> dict:
    token = os.environ.get("TOKEN_SYNC")
    if not token:
        raise RuntimeError("Falta TOKEN_SYNC en el entorno")

    r = httpx.post(
        f"{destino.rstrip('/')}/turnos/sync",
        headers={"x-token": token},
        json={"registros": registros},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destino", default="http://localhost:8100")
    args = parser.parse_args()

    try:
        registros = descargar()
    except Exception as e:
        # No se envía nada si la descarga falla: dejar el servicio con el
        # volcado anterior es mejor que dejarlo sin datos.
        print(f"ERROR al descargar de la fuente ({type(e).__name__}): {e}")
        return 1

    print(f"Descargados {len(registros)} registros de la fuente.")

    try:
        resp = enviar(args.destino, registros)
    except Exception as e:
        print(f"ERROR al enviar a {args.destino} ({type(e).__name__}): {e}")
        return 1

    print(
        f"Enviados a {args.destino}: {resp['recibidos']} recibidos, "
        f"{resp['vigentes']} vigentes tras el filtro de fecha."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())