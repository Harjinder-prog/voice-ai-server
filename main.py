import os
import io
import uvicorn
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

groq_key = os.environ.get("GROQ_API_KEY")
if not groq_key:
    print("WARNING: GROQ_API_KEY is missing!")
client = Groq(api_key=groq_key)

@app.get("/")
def read_root():
    return {"status": "Voice Bot Server is Running!"}

@app.post("/voice-chat")
async def voice_chat(file: UploadFile = File(...)):
    try:
        audio_data = await file.read()
        transcription = client.audio.transcriptions.create(
            file=(file.filename, audio_data),
            model="whisper-large-v3",
            response_format="text"
        )
        user_text = transcription
        print(f"User said: {user_text}")

        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b", 
            messages=[
                {"role": "system", "content": "You are a helpful, friendly voice assistant. Keep your answers short, natural, and conversational. Do not use markdown or emojis, as your reply will be spoken aloud."},
                {"role": "user", "content": user_text}
            ],
            max_tokens=1024
        )
        ai_reply = completion.choices[0].message.content
        print(f"AI replied: {ai_reply}")

        communicate = edge_tts.Communicate(ai_reply, "en-IN-NeerjaNeural")
        audio_stream = io.BytesIO()
        async with communicate.stream() as stream:
            async for chunk in stream:
                if chunk["type"] == "audio":
                    audio_stream.write(chunk["data"])
        
        audio_stream.seek(0)
        
        if audio_stream.getbuffer().nbytes == 0:
            raise Exception("TTS generated empty audio")

        return StreamingResponse(audio_stream, media_type="audio/mpeg")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Read the port from the environment variable, defaulting to 7860 if not set.
    port = int(os.environ.get("PORT", 7860))
    # Run the server on 0.0.0.0 to be accessible from outside the container.
    uvicorn.run(app, host="0.0.0.0", port=port)
