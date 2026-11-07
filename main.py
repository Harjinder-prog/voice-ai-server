import os
import io
import base64
import json
import wave
import audioop
import uvicorn
import subprocess
import tempfile
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from groq import Groq
import edge_tts

app = FastAPI()
groq_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=groq_key)

SYSTEM_PROMPT = """You are a warm, gentle, professional female assistant calling on behalf of Nihal Hospital.
Keep the call short. Confirm their medicine home-delivery order. 
If they say yes, say: "ਤੁਹਾਡਾ ਆਰਡਰ ਪਲੇਸ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ।"
If they say no, say: "ਅਸੀਂ ਤੁਹਾਡਾ ਆਰਡਰ ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
You must respond ONLY in Punjabi (Gurmukhi script). Keep replies to one short sentence."""

GREETING_TEXT = "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ ਜੀ! ਮੈਂ ਨਿਹਾਲ ਹਸਪਤਾਲ ਤੋਂ ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। ਕੀ ਤੁਸੀਂ ਇਹ ਆਰਡਰ ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
            wss://asylum-powerseller-networks-effects.trycloudflare.com/ws
        </Stream>
    </Response>"""
    return PlainTextResponse(content=xml, media_type="application/xml")

async def text_to_mulaw(text):
    communicate = edge_tts.Communicate(text, "pa-IN-VaaniNeural")
    mp3_data = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_data.write(chunk["data"])
    mp3_data.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
        f_in.write(mp3_data.read())
        in_path = f_in.name
    with tempfile.NamedTemporaryFile(suffix=".ulaw", delete=False) as f_out:
        out_path = f_out.name
    subprocess.run(["ffmpeg", "-y", "-i", in_path, "-ar", "8000", "-ac", "1", "-f", "mulaw", out_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(out_path, "rb") as f:
        ulaw_data = f.read()
    os.remove(in_path)
    os.remove(out_path)
    return ulaw_data

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    stream_sid = None
    print(">>> WEBSOCKET CONNECTED BY VOBIZ! <<<")
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                stream_sid = data['start']['streamSid']
                print("Call Connected. Sending Greeting...")
                ulaw_greeting = await text_to_mulaw(GREETING_TEXT)
                base64_audio = base64.b64encode(ulaw_greeting).decode('utf-8')
                await websocket.send_json({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": base64_audio}
                })
                
            elif data['event'] == 'media':
                payload = base64.b64decode(data['media']['payload'])
                audio_buffer.extend(payload)
                
                if len(audio_buffer) > 16000: 
                    audio_chunk = bytes(audio_buffer)
                    audio_buffer.clear()
                    pcm = audioop.ulaw2lin(audio_chunk, 2)
                    wav_io = io.BytesIO()
                    with wave.open(wav_io, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(8000)
                        wf.writeframes(pcm)
                    wav_io.seek(0)
                    
                    try:
                        transcription = client.audio.transcriptions.create(
                            file=("audio.wav", wav_io.read()),
                            model="whisper-large-v3",
                            response_format="text"
                        )
                        user_text = transcription
                        print(f"Patient said: {user_text}")
                        
                        completion = client.chat.completions.create(
                            model="openai/gpt-oss-120b", 
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_text}
                            ],
                            max_tokens=150
                        )
                        ai_reply = completion.choices[0].message.content
                        print(f"AI replied: {ai_reply}")
                        
                        ulaw_reply = await text_to_mulaw(ai_reply)
                        base64_reply = base64.b64encode(ulaw_reply).decode('utf-8')
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64_reply}
                        })
                    except Exception as e:
                        print(f"Pipeline error: {e}")
            elif data['event'] == 'stop':
                break
    except Exception as e:
        print(f"WebSocket error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
