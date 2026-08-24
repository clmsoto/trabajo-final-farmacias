# Farmacias de turno · Asistente conversacional

Asistente que responde dos tipos de pregunta: dónde hay una farmacia de turno
en una comuna o región de Chile, y qué es un medicamento determinado.
No indica dosis, no recomienda qué tomar y no diagnostica.

**Asistente:** https://trabajo-final-farmacias.fly.dev
**Servicio de datos:** https://farmacias-datos.fly.dev

Trabajo final · Módulo 04 · Diploma en IA Generativa para Organizaciones
FEN UEjecutivos, Universidad de Chile
Mariano Soto · Enrique Guerra · Diego Herrera

---

## Qué hace

Combina dos fuentes de naturaleza distinta:

- **Turnos en vivo** — endpoint público de MINSAL, con normalización de
  comunas, filtro de vigencia, interpretación de horarios que cruzan
  medianoche y sugerencias por cercanía geográfica.
- **Vademécum chileno** — 12.411 fichas reales agrupadas por principio
  activo en 1.599 vectores, con recuperación semántica y cita a la ficha
  usada. La posología queda fuera del índice por diseño.

La orquestación es un grafo LangGraph con clasificación de intención en
cuatro rutas y memoria conversacional por usuario.

## Arquitectura

El sistema son **dos servicios desplegados por separado**, no uno. La
división no fue una decisión de diseño previa: la impuso el cortafuegos
que MINSAL tiene delante de su API, que bloquea el tráfico desde rangos de
datacenter. El asistente nunca consulta la fuente directamente.

```
Navegador
   │  POST /chat · HTTPS
   ▼
Asistente (Fly.io · trabajo-final-farmacias)
   FastAPI + LangGraph — 7 nodos, 4 rutas condicionales
   │  HTTP interno
   ▼
Servicio de datos (Fly.io · farmacias-datos)
   FastAPI — dueño del dominio, sin LangChain
   ├──► Qdrant Cloud            vademécum · 1.599 vectores
   └──► Estado de turnos        en memoria del proceso
                 ▲
                 │  POST /turnos/sync (token compartido)
        sincronizar_turnos.py
        corre desde una conexión chilena, donde MINSAL sí responde
```

El grafo interno del asistente:

```
Navegador → FastAPI → LangGraph
                        ├── reset             limpia comuna y región del turno previo
                        ├── router            clasifica la intención (salida tipada, lee todo el historial)
                        ├── tool_minsal        HTTP → servicio de datos → turnos vigentes
                        ├── tool_rag           HTTP → servicio de datos → búsqueda semántica
                        ├── guardrail_reject   rechazo clínico, sin consultar fuentes
                        ├── fuera_de_dominio   consulta ajena al alcance del asistente
                        └── responder          redacta · cita · aplica el guardrail de salida
```

Solo `tool_minsal` y `tool_rag` llegan a `responder`; `guardrail_reject` y
`fuera_de_dominio` terminan la conversación de inmediato. El diagrama
generado desde el grafo compilado está en `grafo_langgraph.png`, y su
versión en texto Mermaid en `grafo_mermaid.txt` — ambos se regeneran con
`python diagrama_grafo.py`, así que si el código cambia, el diagrama
cambia con él.

## Stack

| Componente | Elección | Por qué |
|---|---|---|
| Orquestación | LangGraph | Enrutamiento explícito y estado persistente por hilo |
| Modelo de lenguaje | OpenAI (chat) | Clasificación con salida tipada y redacción |
| Embeddings | text-embedding-3-small | 1536 dimensiones, 1.599 vectores indexados |
| Base vectorial | Qdrant Cloud, vía REST | El cliente oficial quedó bloqueado por política del equipo |
| Observabilidad | LangSmith | Trazas del grafo: nodo, latencia, tokens y costo por paso |
| API | FastAPI + Uvicorn | Validación por esquema y documentación automática |
| Frontend | HTML + CSS + JS | Sin compilación ni framework |
| Dependencias | Poetry | Un `pyproject.toml` por servicio |
| Despliegue | Docker sobre Fly.io | Dos apps independientes, una por servicio |

## Seguridad

Tres capas, en orden de aparición:

1. **Datos** — cuatro secciones del vademécum quedan fuera del índice
   (Modo de administración, Insuficiencia renal, Insuficiencia hepática,
   Sobredosificación) y del resto se descartan las frases con posología.
   El modelo no puede repetir una dosis porque nunca la ve.
2. **Entrada** — el clasificador enruta a rechazo antes de consultar
   cualquier fuente. Ante ambigüedad entre "medicamento" y "rechazo",
   prefiere rechazar.
3. **Salida** — inspección determinista por patrones sobre la respuesta ya
   generada, antes de devolverla. Nunca se activó en las pruebas: es una
   red de seguridad, no la respuesta a un fallo observado.

Ochenta y siete pruebas en cuatro baterías:

| Batería | Casos | Qué mide |
|---|---|---|
| `pruebas_adversarias.py` | 31 | 27 ataques en 11 técnicas de evasión + 4 controles negativos |
| `pruebas_contingencia.py` | 18 | Reacción del asistente ante fallos del servicio de datos |
| `servicio-datos/pruebas_datos.py` | 32 | Horarios, normalización, vigencia y reconocimiento de regiones |
| `guardrail_salida.py` | 6 | Patrones de posología sin falsos positivos |

## Estructura

```
api.py                          endpoint del asistente y servido del frontend
grafo_farmacias.py               estado, router, nodos y construcción del grafo
tool_minsal.py                   cliente HTTP de turnos hacia el servicio de datos
tool_rag.py                      cliente HTTP de búsqueda semántica y armado de citas
guardrail_salida.py              inspección determinista de la respuesta
diagrama_grafo.py                regenera grafo_langgraph.png y grafo_mermaid.txt
pruebas_adversarias.py           31 casos: 27 ataques y 4 controles
pruebas_contingencia.py          18 casos: reacción ante fallos del servicio
static/index.html                interfaz de chat

servicio-datos/
  api_datos.py                   endpoints del servicio de datos
  turnos.py                      pipeline de cinco pasos sobre los turnos
  busqueda.py                    embedding, umbral y regla de dominancia
  comunas_regiones.py            mapa comuna → región y alias regionales
  sincronizar_turnos.py          alimenta el servicio desde una conexión chilena
  ingesta_vademecum_cl.py        indexación del vademécum, corre en local
  verificar_ingesta.py           chequeo posterior a la ingesta
  pruebas_datos.py               32 casos unitarios de la lógica de datos

historico/                       código de la arquitectura anterior (un solo
                                  servicio, corpus en inglés), conservado como
                                  referencia — no se despliega
```

Cada servicio tiene su propio `Dockerfile` y `fly.toml`, y se despliega
por separado. Solo el asistente usa Poetry; el servicio de datos instala
sus dependencias con `pip`, igual que en su contenedor.

## Correr localmente

Requiere Python 3.11 o superior. El asistente usa Poetry; el servicio de
datos no tiene `pyproject.toml` propio y se maneja con `pip` directo.

### Asistente (raíz del repo)

```bash
poetry install
```

Crear un archivo `.env` en la raíz:

```
OPENAI_API_KEY=...
QDRANT_URL=https://tu-cluster.cloud.qdrant.io
QDRANT_API_KEY=...
URL_DATOS=https://farmacias-datos.fly.dev
```

`URL_DATOS` apunta por defecto a `http://localhost:8100`. Se puede dejar
así y levantar `servicio-datos` en paralelo, o apuntar directo al servicio
ya desplegado para no duplicar el índice de Qdrant en local.

Opcional, para trazas del grafo en LangSmith:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=farmacias-turno
```

Levantar el servidor:

```bash
poetry run uvicorn api:api --reload --port 8000
```

### Servicio de datos

No es un proyecto Poetry aparte — no tiene `pyproject.toml` propio. Instala
las mismas dependencias que declara su `Dockerfile`, con un entorno virtual:

```bash
cd servicio-datos
python -m venv .venv && .venv\Scripts\activate   # PowerShell
pip install "fastapi>=0.141.1,<0.142.0" "uvicorn>=0.52.1,<0.53.0" \
    "httpx>=0.28,<1.0" "openai>=2.0,<3.0" \
    "python-dotenv>=1.2.2,<2.0.0" "pydantic>=2.13.4,<3.0.0"
```

No hace falta `pandas` ni el cliente de Qdrant: `ingesta_vademecum_cl.py`
indexa por REST con `httpx` directo, igual que el resto del servicio.

Crear un `.env` propio en `servicio-datos/`:

```
OPENAI_API_KEY=...
QDRANT_URL=https://tu-cluster.cloud.qdrant.io
QDRANT_API_KEY=...
TOKEN_SYNC=...
```

Preparar el corpus (una sola vez, corre en local — el vademécum de 68 MB
nunca se sube al contenedor):

```bash
python ingesta_vademecum_cl.py
python verificar_ingesta.py
```

Levantar el servidor:

```bash
uvicorn api_datos:api --reload --port 8100
```

Sincronizar turnos (requiere una conexión chilena; MINSAL rechaza el
tráfico desde datacenters):

```bash
python sincronizar_turnos.py --destino http://localhost:8100
```

## Decisiones documentadas

**Dos servicios, no uno.** El cortafuegos de MINSAL bloquea el tráfico
desde rangos de datacenter — la misma consulta responde 200 desde una
conexión chilena y 403 desde el despliegue. En vez de enmascarar el
origen, se invirtió el flujo: un proceso que corre en Chile descarga el
volcado y lo entrega por HTTP al servicio de datos, autenticado con un
token compartido.

**Cliente REST en vez de la librería oficial de Qdrant.** Su dependencia
gRPC quedó bloqueada por la política de control de aplicaciones del equipo
de desarrollo. En lugar de desactivar el control, se implementó un cliente
sobre la API REST. El mismo criterio se aplicó cuando esa política bloqueó
después el binario de despliegue de Fly: se pasó a desplegar desde la
integración con GitHub.

**El corpus se indexa por principio activo, no por ficha.** El vademécum
chileno trae 12.411 fichas, pero el texto farmacológico depende del
principio activo y no de la marca — Paracetamol aparece en 139 fichas con
contenido idéntico. Agrupar reduce el índice a 1.599 vectores y resuelve
de paso que el 66% de los nombres comerciales incluya la concentración
(citar por nombre habría activado el guardrail de salida).

**Filtro de vigencia sobre los turnos.** El endpoint entrega registros
vencidos junto a los vigentes: en la inspección de agosto de 2026, 69 de
143 registros tenían fecha del 1 de julio. Se filtran por una ventana de
tres días (ayer, hoy, mañana).

**Interpretación del cruce de medianoche.** 70 de 143 registros cierran al
día siguiente. Una comparación de intervalo simple los daría por cerrados
durante toda la madrugada, justo cuando se necesitan.

**LangSmith se agregó sin tocar el grafo.** El grafo ya usaba runnables
estándar de LangChain, así que la instrumentación fue tres variables de
entorno, no una línea de código. El costo del lado de privacidad se
documenta explícitamente: es la primera pieza del sistema que persiste
una conversación completa fuera de la memoria efímera del proceso, y está
declarado como riesgo propio (R-21) en la matriz de riesgos.

## Limitaciones conocidas

- El estado de turnos vive en memoria del servicio de datos y la
  sincronización es manual: un reinicio de la plataforma obliga a
  repetirla.
- El historial del asistente también vive en memoria y se pierde al
  reiniciar, pero desde que se activó LangSmith cada conversación queda
  igualmente trazada en un tercero, con su propia política de retención.
- La recuperación semántica usa solo el último mensaje, a diferencia del
  clasificador, que lee el historial completo.
- Dos consultas informativas legítimas se clasifican como rechazo, por la
  regla asimétrica del clasificador (ante la duda, rechaza).
- No hay límite de peticiones por usuario ni tope de gasto configurado.
- El vademécum es educativo, no una fuente clínica autoritativa: contiene
  inconsistencias internas verificadas y documentadas.
- La interfaz no advierte al usuario que su conversación puede quedar
  trazada en LangSmith.

## Documentos del proyecto

- `documento-tecnico-interno.docx` — arquitectura, código y problemas
  resueltos, con el detalle que no cabe acá.
- `informe-seguridad-privacidad-calidad.docx` — controles, datos que se
  guardan y método de evaluación.
- `matriz-riesgos-farmacias.docx` — 21 riesgos con mitigación verificable
  y dueño.
- `instructivo-demo.docx` — guion de las seis escenas de la defensa.
- `presentacion-defensa.pptx` — apoyo visual para la exposición.
