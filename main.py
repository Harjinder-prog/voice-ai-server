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
The call opens with a greeting: "Sat Sri Akal {patient_name} ji! Main Nihal Hospital ton gal kar rahi haan. Tusi davaian di home delivery lai order ditta si. Ki tusi eh order place krna chaunde ho?"
Do not repeat the greeting or the opening sentence after it plays. Stay quiet and wait for the patient's answer to the question.

Never skip the question "Ki tusi eh order place krna chaunde ho?". If the patient says only okay, haan, accha, or a vague acknowledgment, ask it again in one short sentence and wait. Do not reply until the patient has clearly answered.

After the patient has clearly answered, close politely with: "Nihal Hospital vallo tuhaada dhannvaad ji. Tuhaada din shubh rahe."

Handling Patient Responses:
Treat only a clear yes or no as an answer to the opening question. If the patient says only okay, haan, accha, or any vague acknowledgment, ask again: "Ki tusi eh order place krna chaunde ho?" and wait.
If the patient clearly says yes or haanji, say: "Tuhada order place krr ditta gea hai. Dhanwaad." and close politely.
If the patient says no, say: "Asi tuhada order agge nhi bhej rhe. Dhanwaad." and close politely.
If the patient says "Thank you" or speaks in English, reply in Punjabi: "Ki tusi eh order place krna chaunde ho?"

Speaking Style:
One idea per turn; one question at a time; wait for the response.

Guardrails:
You must respond ONLY in Punjabi (Gurmukhi script). NEVER output an empty response. Keep your answers very short.
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
        
        # SAFETY NET: Ensure ai_reply is a string and not empty
        raw_reply = completion.choices[0].message.content
        ai_reply = str(raw_reply).strip() if raw_reply else ""
        
        if not ai_reply:
            # If AI gives an empty response, use this fallback question
            ai_reply = "Ki tusi eh order place krna chaunde ho?"
            
        print(f"AI replied: {ai_reply}")

        # 3. MOUTH
        communicate = edge_tts.Communicate(ai_reply, "pa-IN-VaaniNeural")
        audio_stream = io.BytesIO()
        
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
