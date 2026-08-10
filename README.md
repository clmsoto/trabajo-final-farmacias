# Farmacias de turno · Asistente conversacional

Asistente que responde dos tipos de pregunta: dónde hay una farmacia de turno
en una comuna o región de Chile, y qué es un medicamento determinado.
No indica dosis, no recomienda qué tomar y no diagnostica.

**Desplegado en:** https://trabajo-final-farmacias.fly.dev

Trabajo final · Módulo 04 · Diploma en IA Generativa para Organizaciones
FEN UEjecutivos, Universidad de Chile
Mariano Soto · Enrique Guerra · Diego Herrera

---

## Qué hace

Combina dos fuentes de naturaleza distinta:

- **Turnos en vivo** — endpoint público de MINSAL, con normalización de
  comunas, filtro de vigencia, interpretación de horarios que cruzan
  medianoche y sugerencias por cercanía geográfica.
- **Fichas de medicamentos** — recuperación semántica sobre un corpus
  indexado en base vectorial, con cita a la ficha recuperada.

La orquestación es un grafo LangGraph con clasificación de intención en
cuatro rutas y memoria conversacional por usuario.

## Arquitectura

```
Navegador → FastAPI → LangGraph
                        ├── reset          limpia el contexto del turno previo
                        ├── router         clasifica la intención (salida tipada)
                        ├── tool_minsal    API en vivo · caché · respaldo
                        ├── tool_rag       traduce · embebe · busca
                        ├── guardrail      rechazo clínico
                        ├── fuera_dominio  consulta fuera de alcance
                        └── responder      redacta · cita · inspecciona
```

El diagrama generado desde el grafo compilado está en `grafo_langgraph.png`,
y su versión en texto Mermaid en `grafo_mermaid.txt`.

## Stack

| Componente | Elección |
|---|---|
| Orquestación | LangGraph |
| Modelo | OpenAI (chat + embeddings) |
| Base vectorial | Qdrant Cloud, vía API REST |
| API | FastAPI + Uvicorn |
| Frontend | HTML + CSS + JS, sin framework |
| Dependencias | Poetry |
| Despliegue | Docker sobre Fly.io |

## Seguridad

Tres capas, en orden de aparición:

1. **Datos** — los campos de dosificación quedan fuera del texto indexado.
   El modelo no puede repetir una dosis porque nunca la ve.
2. **Entrada** — el clasificador enruta a rechazo antes de consultar
   cualquier fuente. Ante ambigüedad, prefiere rechazar.
3. **Salida** — inspección determinista por patrones sobre la respuesta ya
   generada, antes de devolverla.

Batería de pruebas adversarias: 27 casos en 11 técnicas de evasión, más 4
controles negativos que el sistema debe responder. Ejecutable con
`pruebas_adversarias.py`.

## Estructura

```
grafo_farmacias.py       estado, router, nodos y construcción del grafo
tool_minsal.py           pipeline de cinco pasos sobre la API de turnos
tool_rag.py              traducción, embedding, búsqueda y citas
guardrail_salida.py      inspección determinista de la respuesta
comunas_regiones.py      mapa comuna → región y alias regionales
api.py                   endpoint HTTP y servido del frontend
ingesta_vademecum.py     carga del corpus a la base vectorial
qdrant_rest.py           cliente mínimo sobre la API REST
pruebas_adversarias.py   batería de casos adversarios
static/index.html        interfaz de chat con banda de 24 horas
```

Cada herramienta tiene su propio bloque de pruebas al final y se puede
ejecutar por separado.

## Correr localmente

Requiere Python 3.11 o superior y Poetry.

```bash
poetry install
```

Crear un archivo `.env` en la raíz:

```
OPENAI_API_KEY=...
QDRANT_URL=https://tu-cluster.cloud.qdrant.io
QDRANT_API_KEY=...
```

Preparar el corpus (una sola vez):

```bash
poetry run python qdrant_rest.py          # crea la colección
poetry run python ingesta_vademecum.py    # indexa las fichas
poetry run python generar_snapshot.py     # captura el respaldo de turnos
```

Levantar el servidor:

```bash
poetry run uvicorn api:api --reload --port 8000
```

## Decisiones documentadas

**Cliente REST en vez de la librería oficial de Qdrant.** Su dependencia
gRPC quedó bloqueada por la política de control de aplicaciones del equipo
de desarrollo. En lugar de desactivar el control, se implementó un cliente
sobre la API REST.

**Consulta traducida al inglés antes de embeber.** El corpus está en inglés.
Traducir la consulta mejora la similitud en todas las pruebas realizadas,
y mantiene la cita apuntando al texto original en vez de a una traducción.

**Filtro de vigencia sobre los turnos.** El endpoint entrega registros
vencidos junto a los vigentes: en la inspección del 9 de agosto de 2026,
69 de 143 registros tenían fecha del 1 de julio. Se filtran por una ventana
de tres días.

**Interpretación del cruce de medianoche.** 70 de 143 registros cierran al
día siguiente. Una comparación de intervalo simple los daría por cerrados
durante toda la madrugada, justo cuando se necesitan.

**Agrupamiento del corpus.** De 220 filas resultan 105 fichas: el resto son
presentaciones del mismo medicamento que solo difieren en concentración,
precio y código. Los campos que varían se conservan como lista de objetos,
preservando la correspondencia entre ellos.

## Limitaciones conocidas

- El endpoint de MINSAL responde 403 a las peticiones desde el proveedor de
  despliegue, por una regla del WAF que tiene delante del servicio. La
  consulta en vivo funciona desde una conexión en Chile; en producción se
  activa el respaldo, rotulado con su fecha de captura.
- El historial vive en memoria del proceso y se pierde al reiniciar.
- La recuperación semántica usa solo el último mensaje, a diferencia del
  clasificador, que lee el historial completo.
- No hay límite de peticiones por usuario ni tope de gasto configurado.
- El corpus de medicamentos es educativo, no una fuente clínica
  autoritativa: contiene inconsistencias internas verificadas.
