import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()
llm = ChatOpenAI(model="gpt-5.6-luna", temperature=0)
print(llm.invoke("Responde solo: OK").content)