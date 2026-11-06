from fastapi import FastAPI
from groq import Groq
import os

app = FastAPI()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.get("/")
def read_root():
    return {"status": "Voice Bot Server is Running!"}

@app.post("/chat")
def chat(text: str):
    completion = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": text}]
    )
    response = completion.choices[0].message.content
    return {"reply": response}
