FROM python:3.12-slim

WORKDIR /app

# Solo lo que el servicio necesita en ejecución. No lleva pandas ni el
# cliente de Qdrant: la ingesta corre en local y las consultas van por REST.
RUN pip install --no-cache-dir \
    "fastapi>=0.141.1,<0.142.0" \
    "uvicorn>=0.52.1,<0.53.0" \
    "httpx>=0.28,<1.0" \
    "openai>=2.0,<3.0" \
    "python-dotenv>=1.2.2,<2.0.0" \
    "pydantic>=2.13.4,<3.0.0"

COPY . .

EXPOSE 8080
CMD ["uvicorn", "api_datos:api", "--host", "0.0.0.0", "--port", "8080"]