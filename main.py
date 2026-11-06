import os
import io
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
import edge_tts

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Check if key exists on startup
api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("WARNING: GROQ_API_KEY is not set!")
client = Groq(api_key=api_key)

@app.get("/")
def read_root():
    return {"status": "Voice Bot Server is Running!"}

@app.post("/voice-chat")
async def voice_chat(file: UploadFile = File(...)):
    try:
        # 1. EARS
        audio_data = await file.read()
        transcription = client.audio.transcriptions.create(
            file=(file.filename, audio_data),
            model="whisper-large-v3",
            response_format="text"
        )
        user_text = transcription
        print(f"User said: {user_text}")

        # 2. BRAIN
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a helpful, friendly voice assistant. Keep your answers short and conversational."},
                {"role": "user", "content": user_text}
            ]
        )
        ai_reply = completion.choices[0].message.content
        print(f"AI replied: {ai_reply}")

        # 3. MOUTH
        communicate = edge_tts.Communicate(ai_reply, "en-IN-NeerjaNeural")
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        
        audio_stream.seek(0)
        
        # Check if audio was actually generated
        if audio_stream.getbuffer().nbytes == 0:
            raise Exception("TTS generated empty audio")

        return StreamingResponse(audio_stream, media_type="audio/mpeg")

    except Exception as e:
        # Print the EXACT error to the SnapDeploy logs
        print(f"CRITICAL ERROR: {str(e)}")
        # Tell the frontend there was an error (500), instead of sending fake audio
        raise HTTPException(status_code=500, detail=str(e))
