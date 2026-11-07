import os
import io
import base64
import json
import wave
import audioop
import uvicorn
import subprocess
import tempfile
import asyncio
import time
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from groq import Groq
from gtts import gTTS

app = FastAPI()
groq_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=groq_key)

SYSTEM_PROMPT = """You are a professional female assistant calling on behalf of Nihal Hospital.
Keep the call very short. Confirm their medicine home-delivery order. 
If they say yes, say ONLY: "ਤੁਹਾਡਾ ਆਰਡਰ ਪਲੇਸ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ।"
If they say no, say ONLY: "ਅਸੀਂ ਤੁਹਾਡਾ ਆਰਡਰ ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
You must respond ONLY in Punjabi (Gurmukhi script). Keep replies to one short sentence. DO NOT repeat yourself under any circumstances."""

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
    # Use Google TTS in a separate thread so it doesn't block the server
    def generate_mp3():
        tts = gTTS(text=text, lang='pa', slow=False)
        mp3_data = io.BytesIO()
        tts.write_to_fp(mp3_data)
        mp3_data.seek(0)
        return mp3_data

    mp3_data = await asyncio.to_thread(generate_mp3)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
        f_in.write(mp3_data.read())
        in_path = f_in.name
    with tempfile.NamedTemporaryFile(suffix=".ulaw", delete=False) as f_out:
        out_path = f_out.name
        
    # Convert MP3 to mu-law for Vobiz
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
    ignore_audio_until = time.time()
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                print("Call Connected. Sending Greeting...")
                ulaw_greeting = await text_to_mulaw(GREETING_TEXT)
                base64_audio = base64.b64encode(ulaw_greeting).decode('utf-8')
                await websocket.send_json({
                    "event": "playAudio",
                    "media": {
                        "contentType": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "payload": base64_audio
                    }
                })
                # Ignore audio for 4 seconds while greeting plays (to prevent echo)
                ignore_audio_until = time.time() + 4.0
                
            elif data['event'] == 'media':
                # If the AI is currently speaking or echoing, ignore the audio
                if time.time() < ignore_audio_until:
                    continue

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
                        user_text = transcription.strip()
                        if not user_text:
                            continue
                            
                        print(f"Patient said: {user_text}")
                        
                        completion = client.chat.completions.create(
                            model="openai/gpt-oss-120b", 
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_text}
                            ],
                            max_tokens=50
                        )
                        ai_reply = completion.choices[0].message.content
                        print(f"AI replied: {ai_reply}")
                        
                        ulaw_reply = await text_to_mulaw(ai_reply)
                        base64_reply = base64.b64encode(ulaw_reply).decode('utf-8')
                        await websocket.send_json({
                            "event": "playAudio",
                            "media": {
                                "contentType": "audio/x-mulaw",
                                "sampleRate": 8000,
                                "payload": base64_reply
                            }
                        })
                        
                        # Ignore incoming audio for 5 seconds while the AI speaks
                        # This stops it from hearing its own voice and repeating
                        ignore_audio_until = time.time() + 5.0
                        audio_buffer.clear()
                        
                    except Exception as e:
                        print(f"Pipeline error: {e}")
            elif data['event'] == 'stop':
                break
    except Exception as e:
        print(f"WebSocket error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
