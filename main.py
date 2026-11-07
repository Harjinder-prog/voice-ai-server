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
import azure.cognitiveservices.speech as speechsdk

app = FastAPI()

groq_key = os.environ.get("GROQ_API_KEY")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_REGION = os.environ.get("AZURE_REGION", "centralindia")

client = Groq(api_key=groq_key)

GREETING_TEXT = "ਸਾ ਸ੍ਰੀ ਅਕਾਲ ਜੀ! ਮੈਂ ਨਿਹਾਲ ਹਸਪਤਾਲ ਤੋਂ ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। ਕੀ ਤੁਸੀਂ ਇਹ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

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

def generate_azure_audio(text):
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_REGION)
    speech_config.speech_synthesis_voice_name = "pa-IN-VaaniNeural"
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    
    result = synthesizer.speak_text_async(text).get()
    
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data
    else:
        print(f"Azure TTS Error: {result.reason}")
        return None

async def text_to_mulaw(text):
    audio_bytes = await asyncio.to_thread(generate_azure_audio, text)
    if not audio_bytes:
        return None

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
        f_in.write(audio_bytes)
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
    ignore_audio_until = time.time() + 2.0 
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                print("Call Connected. Sending Greeting...")
                ulaw_greeting = await text_to_mulaw(GREETING_TEXT)
                
                if ulaw_greeting:
                    base64_audio = base64.b64encode(ulaw_greeting).decode('utf-8')
                    duration = len(ulaw_greeting) / 8000.0
                    
                    # Mute mic exactly while greeting plays + 2 seconds for echo tail
                    ignore_audio_until = time.time() + duration + 2.0
                    
                    await websocket.send_json({
                        "event": "playAudio",
                        "media": {
                            "contentType": "audio/x-mulaw",
                            "sampleRate": 8000,
                            "payload": base64_audio
                        }
                    })
                
            elif data['event'] == 'media':
                if time.time() < ignore_audio_until:
                    continue

                payload = base64.b64decode(data['media']['payload'])
                audio_buffer.extend(payload)
                
                # Increased to 8000 bytes (1 full second) so Whisper has enough data
                if len(audio_buffer) > 8000: 
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
                            language="pa", 
                            response_format="text"
                        )
                        user_text = transcription.strip()
                        print(f"Patient said: {user_text}")
                        
                        if len(user_text) < 2 or "thank you" in user_text.lower() or "subtitles" in user_text.lower():
                            continue
                            
                        user_text_lower = user_text.lower()
                        if any(word in user_text_lower for word in ["haan", "han", "yes", "theek", "ok", "ਹਾਂ", "ਹਾਂਜੀ", "ਜੀ"]):
                            ai_reply = "ਅਪਣਾ Order ਪਲੇਸ ਹੋ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ।"
                        elif any(word in user_text_lower for word in ["nahi", "no", "ਨਹੀਂ", "ਨਾ"]):
                            ai_reply = "ਅਸੀਂ ਅਪਣਾ Order ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
                        else:
                            ai_reply = "ਕੀ ਤੁਸੀਂ ਇਹ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
                            
                        print(f"AI replied: {ai_reply}")
                        
                        ulaw_reply = await text_to_mulaw(ai_reply)
                        
                        if ulaw_reply:
                            base64_reply = base64.b64encode(ulaw_reply).decode('utf-8')
                            duration = len(ulaw_reply) / 8000.0
                            
                            # Mute mic exactly while reply plays + 2 seconds for echo tail
                            ignore_audio_until = time.time() + duration + 2.0
                            
                            await websocket.send_json({
                                "event": "playAudio",
                                "media": {
                                    "contentType": "audio/x-mulaw",
                                    "sampleRate": 8000,
                                    "payload": base64_reply
                                }
                            })
                        
                        audio_buffer.clear()
                        
                    except Exception as e:
                        print(f"Pipeline error: {e}")
            elif data['event'] == 'stop':
                break
    except Exception as e:
        print(f"WebSocket error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
