FROM python:3.12-slim

WORKDIR /app

# Las dependencias se declaran explícitamente en vez de instalar el
# proyecto como paquete: este proyecto son módulos sueltos, no una
# librería, y no tiene un paquete importable que empaquetar.
RUN pip install --no-cache-dir \
    "langgraph>=1.2.10,<2.0.0" \
    "langchain-openai>=1.4.2,<2.0.0" \
    "langgraph-checkpoint-sqlite>=3.1.1,<4.0.0" \
    "fastapi>=0.141.1,<0.142.0" \
    "uvicorn>=0.52.1,<0.53.0" \
    "python-dotenv>=1.2.2,<2.0.0" \
    "pydantic>=2.13.4,<3.0.0" \
    "pandas>=3.0.5,<4.0.0" \
    "httpx>=0.28,<1.0"

COPY . .

EXPOSE 8080
CMD ["uvicorn", "api:api", "--host", "0.0.0.0", "--port", "8080"]