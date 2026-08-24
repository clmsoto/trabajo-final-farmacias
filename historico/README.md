# Histórico

Código de la arquitectura anterior: un solo servicio, corpus en inglés de
Kaggle (220 fichas), y una consulta directa a MINSAL sin intermediario.
Se conserva como referencia y para que la evolución del proyecto quede
trazable — nada de esta carpeta se despliega ni se importa desde el
código activo.

Dos eventos volvieron obsoleto todo lo que sigue: el profesor entregó el
vademécum chileno real (12.411 fichas) para reemplazar el corpus sintético,
y el cortafuegos de MINSAL empezó a bloquear el tráfico desde el
proveedor de despliegue, lo que obligó a partir el sistema en dos
servicios. Ningún archivo de acá se retiró por estar mal escrito.

## Qué era cada archivo

**`crear_coleccion.py`** — Crea la colección de Qdrant usando el cliente
oficial (`qdrant-client`, sobre gRPC). Se retiró en dos pasos: primero
porque esa dependencia gRPC quedó bloqueada por la política de Control de
aplicaciones del equipo (de ahí nació `qdrant_rest.py`, más abajo), y
después porque la creación de la colección se integró directamente al
pipeline de ingesta. Hoy `servicio-datos/ingesta_vademecum_cl.py` la crea
por REST como parte de un solo flujo, sin script aparte.

**`explorar_minsal.py`** — Script exploratorio de una sola vez, para ver
la forma real del endpoint de turnos antes de escribir la tool. No fue
reemplazado por nada: cumplió su función y sus hallazgos quedaron
codificados directamente en la lógica de `servicio-datos/turnos.py`.

**`generar_snapshot.py`** — Capturaba una foto estática del endpoint de
MINSAL como respaldo local para cuando la fuente fallara. Tenía sentido
en la arquitectura de un solo servicio, donde el respaldo era la única
defensa contra el bloqueo. Con la arquitectura de dos servicios ya no
hace falta: `servicio-datos/sincronizar_turnos.py` trae un volcado
*fresco* desde una conexión chilena en vez de depender de una captura
vieja.

**`ingesta_vademecum.py`** — Indexaba el corpus original de Kaggle: 220
fichas en inglés, estrategia una-ficha-un-chunk (razonable para ese
tamaño). Se retiró junto con el corpus completo. Su reemplazo,
`servicio-datos/ingesta_vademecum_cl.py`, no es solo una actualización de
fuente: cambia la estrategia de indexación a agrupar por principio
activo, porque el vademécum chileno repite el mismo texto farmacológico
en decenas de presentaciones comerciales.

**`qdrant_rest.py`** — El cliente REST mínimo que reemplazó al oficial
tras el bloqueo de gRPC, usado con el corpus y la arquitectura originales.
No se retiró por defectuoso — al contrario, la decisión de fondo (REST en
vez del cliente oficial) se mantiene intacta — sino porque las llamadas
REST se integraron directamente donde se usan, en
`servicio-datos/busqueda.py` e `ingesta_vademecum_cl.py`, en vez de vivir
en un módulo cliente compartido.

**`test_idioma.py`** — Medía la similitud de recuperación con la consulta
en español directo contra la consulta traducida al inglés antes de
embeber. La traducción ganaba por un margen medido (entre 0,045 y 0,088),
y esa decisión quedó documentada con esa evidencia. Se volvió innecesaria
cuando el corpus cambió de inglés a español: no es que la medición
estuviera mal, es que la pregunta que respondía dejó de aplicar.

**`test_key.py`** — Verificación mínima de que la clave de OpenAI estaba
bien configurada, usada al principio del desarrollo. Se volvió
innecesaria en cuanto ambos servicios empezaron a validar sus credenciales
al arrancar (ver la función `lifespan` en `api.py` y en
`servicio-datos/api_datos.py`), que es una verificación más completa y
que corre automáticamente en cada despliegue.

## Si algo de acá parece útil hoy

Antes de reutilizar algo de esta carpeta, dos preguntas: ¿el corpus al
que apunta (Kaggle, en inglés) sigue siendo el que usa el sistema?, ¿y la
arquitectura a la que asume que habla (un solo servicio, consulta directa
a MINSAL) sigue siendo la actual? Las dos respuestas son no, así que
cualquier fragmento de código de acá necesita revisión antes de volver a
usarse, no solo copiarse.
