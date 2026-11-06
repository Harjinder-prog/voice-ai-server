import os
import io
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
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
client = Groq(api_key=groq_key)

SYSTEM_PROMPT = """You are a warm, gentle, professional female assistant calling on behalf of Nihal Hospital.
Channel: voice telephony, outbound. You call patients who placed a home-delivery order for medicines from Nihal Hospital.
Keep the call short, ideally 20 to 40 seconds.

Objective:
Confirm that the call is regarding their medicine home-delivery order.
Record whether they confirm the order.
Politely thank them and end the call.

Script:
The call opens with a greeting: "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {patient_name} ਜੀ! ਮੈਂ ਨਿਹਾਲ ਹਸਪਤਾਲ ਤੋਂ ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। ਤੁਸੀਂ ਦਵਾਈਆਂ ਦੀ ਹੋਮ ਡਿਲੀਵਰੀ ਲਈ ਆਰਡਰ ਦਿੱਤਾ ਸੀ। ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
Do not repeat the greeting. Stay quiet and wait for the patient's answer.

If the patient says only okay, haan, accha, or a vague acknowledgment, ask again: "ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

Handling Patient Responses:
If the patient clearly says yes or haanji, say: "ਤੁਹਾਡਾ ਆਰਡਰ ਪਲੇਸ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ।" and close politely.
If the patient says no, say: "ਅਸੀਂ ਤੁਹਾਡਾ ਆਰਡਰ ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।" and close politely.
If the patient says "Thank you" or speaks in English, reply: "ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

CRITICAL GUARDRAIL: You must respond ONLY in Punjabi using the Gurmukhi script (e.g., ਕੀ ਤੁਸੀਂ...). NEVER use English letters or Romanized Punjabi.
"""

@app.get("/")
def read_root():
    return {"status": "Hospital Voice Agent is Running!"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/voice-chat")
async def voice_chat(
    file: UploadFile = File(...), 
    patient_name: str = Form("Patient")
):
    try:
        # 1. EARS
        audio_data = await file.read()
        transcription = client.audio.transcriptions.create(
            file=(file.filename, audio_data),
            model="whisper-large-v3",
            response_format="text"
        )
        user_text = transcription
        print(f"Patient said: {user_text}")

        # 2. BRAIN
        final_prompt = SYSTEM_PROMPT.replace("{patient_name}", patient_name)
        
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b", 
            messages=[
                {"role": "system", "content": final_prompt},
                {"role": "user", "content": user_text}
            ],
            max_tokens=150
        )
        
        raw_reply = completion.choices[0].message.content
        ai_reply = str(raw_reply).strip() if raw_reply else ""
        
        # Clean up any accidental markdown
        ai_reply = ai_reply.replace("*", "").replace("#", "").strip()
        
        if not ai_reply:
            ai_reply = "ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
            
        print(f"AI replied: {ai_reply}")

        # 3. MOUTH (With Bulletproof Fallback)
        audio_stream = io.BytesIO()
        try:
            communicate = edge_tts.Communicate(ai_reply, "pa-IN-VaaniNeural")
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_stream.write(chunk["data"])
        except Exception as tts_error:
            print(f"TTS Error with text '{ai_reply}': {tts_error}")
            # If TTS fails, use a perfect Gurmukhi fallback question
            fallback_text = "ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
            communicate = edge_tts.Communicate(fallback_text, "pa-IN-VaaniNeural")
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_stream.write(chunk["data"])

        audio_stream.seek(0)
        return StreamingResponse(audio_stream, media_type="audio/mpeg")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
