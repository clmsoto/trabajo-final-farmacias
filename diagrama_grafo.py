"""
Genera el diagrama del grafo desde el objeto compilado.

El diagrama sale del grafo real, no de un dibujo hecho aparte: si la
estructura del código cambia, el diagrama cambia con ella.
"""

from grafo_farmacias import app

# Mermaid en texto: útil para pegar en documentación o en el README.
mermaid = app.get_graph().draw_mermaid()
with open("grafo_mermaid.txt", "w", encoding="utf-8") as fh:
    fh.write(mermaid)
print("Mermaid guardado en grafo_mermaid.txt\n")
print(mermaid)

# PNG: requiere conexión a internet (usa el servicio de render de mermaid).
try:
    png = app.get_graph().draw_mermaid_png()
    with open("grafo_langgraph.png", "wb") as fh:
        fh.write(png)
    print("\nPNG guardado en grafo_langgraph.png")
except Exception as e:
    print(f"\nNo se pudo generar el PNG ({type(e).__name__}). "
          "El texto Mermaid de arriba se puede pegar en mermaid.live")