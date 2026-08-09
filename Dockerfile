FROM python:3.12-slim

WORKDIR /app

# Poetry instala las dependencias del proyecto en el entorno del sistema:
# dentro de un contenedor no hace falta un virtualenv adicional.
RUN pip install --no-cache-dir poetry==1.8.3 \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

COPY . .

EXPOSE 8080
CMD ["uvicorn", "api:api", "--host", "0.0.0.0", "--port", "8080"]