import os
import io
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from groq import Groq
import edge_tts

app = FastAPI()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.get("/")
def read_root():
    return {"status": "Voice Bot Server is Running!"}

@app.post("/voice-chat")
async def voice_chat(file: UploadFile = File(...)):
    try:
        # 1. EARS: Transcribe the uploaded audio using Groq Whisper
        audio_data = await file.read()
        transcription = client.audio.transcriptions.create(
            file=(file.filename, audio_data),
            model="whisper-large-v3",
            response_format="text"
        )
        user_text = transcription
        print(f"User said: {user_text}")

        # 2. BRAIN: Generate a response using Groq LLM
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a helpful, friendly voice assistant. Keep your answers short and conversational."},
                {"role": "user", "content": user_text}
            ]
        )
        ai_reply = completion.choices[0].message.content
        print(f"AI replied: {ai_reply}")

        # 3. MOUTH: Convert the AI text into speech using Edge TTS
        # Using a natural Indian English female voice
        communicate = edge_tts.Communicate(ai_reply, "en-IN-NeerjaNeural")
        
        # Stream the audio into memory
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        
        audio_stream.seek(0)

        # Return the MP3 audio file directly
        return StreamingResponse(audio_stream, media_type="audio/mpeg")

    except Exception as e:
        return {"error": str(e)}
