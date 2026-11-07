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
import webrtcvad
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from groq import Groq
import azure.cognitiveservices.speech as speechsdk

app = FastAPI()
groq_key = os.environ.get("GROQ_API_KEY")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_REGION = os.environ.get("AZURE_REGION", "centralindia")
client = Groq(api_key=groq_key)

# Use webrtcvad (Stable and won't crash)
vad = webrtcvad.Vad(1) 

GREETING_TEXT = "ਸਾ ਸ੍ਰੀ ਅਕਾਲ ਜੀ! ਮੈਂ ਨਿਹਾਲ ਹਸਪਤਾਲ ਤੋਂ ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। ਕੀ ਤੁਸੀਂ ਆਪਣਾ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
YES_TEXT = "ਤੁਹਾਡਾ Order ਪਲੇਸ ਹੋ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ, ਤੁਹਾਡਾ ਦਿਨ ਸ਼ੁਭ ਹੋਵੇ।"
NO_TEXT = "ਅਸੀਂ ਤੁਹਾਡਾ Order ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
UNCLEAR_TEXT = "ਕੀ ਤੁਸੀਂ ਆਪਣਾ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

AUDIO_CACHE = {}

@app.get("/health")
def health():
    return {"status": "healthy"}

def generate_azure_audio(text):
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_REGION)
    speech_config.speech_synthesis_voice_name = "pa-IN-VaaniNeural"
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data
    return None

async def text_to_mulaw(text):
    audio_bytes = await asyncio.to_thread(generate_azure_audio, text)
    if not audio_bytes: return None
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

@app.on_event("startup")
async def startup_event():
    print(">>> PRE-GENERATING AUDIO CACHE... <<<")
    AUDIO_CACHE["greeting"] = await text_to_mulaw(GREETING_TEXT)
    AUDIO_CACHE["yes"] = await text_to_mulaw(YES_TEXT)
    AUDIO_CACHE["no"] = await text_to_mulaw(NO_TEXT)
    AUDIO_CACHE["unclear"] = await text_to_mulaw(UNCLEAR_TEXT)
    print(">>> CACHE READY! <<<")

@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
            wss://eldercare-flatterer-skier.ngrok-free.dev/ws
        </Stream>
    </Response>"""
    return PlainTextResponse(content=xml, media_type="application/xml")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    ignore_audio_until = time.time() + 2.0 
    voiced_frames = bytearray()
    silent_frames_count = 0
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                ulaw_greeting = AUDIO_CACHE["greeting"]
                if ulaw_greeting:
                    base64_audio = base64.b64encode(ulaw_greeting).decode('utf-8')
                    ignore_audio_until = time.time() + (len(ulaw_greeting) / 8000.0) + 0.5
                    await websocket.send_json({
                        "event": "playAudio",
                        "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64_audio}
                    })
                
            elif data['event'] == 'media':
                if time.time() < ignore_audio_until: continue
                payload = base64.b64decode(data['media']['payload'])
                audio_buffer.extend(payload)
                
                while len(audio_buffer) >= 240:
                    frame_mulaw = bytes(audio_buffer[:240])
                    del audio_buffer[:240]
                    frame_pcm = audioop.ulaw2lin(frame_mulaw, 2)
                    
                    # Check volume to ignore static/background noise
                    volume = audioop.rms(frame_pcm, 2)
                    
                    try:
                        is_speech = vad.is_speech(frame_pcm, 8000) and volume > 300
                    except Exception:
                        is_speech = False
                    
                    if is_speech:
                        voiced_frames.extend(frame_mulaw)
                        silent_frames_count = 0
                    else:
                        if len(voiced_frames) > 0:
                            silent_frames_count += 1
                            
                            # Wait for 0.18s of silence to trigger
                            if silent_frames_count > 6:
                                # Ignore tiny blips of noise
                                if len(voiced_frames) < 800: 
                                    voiced_frames.clear()
                                    silent_frames_count = 0
                                    continue

                                pcm_8k = audioop.ulaw2lin(voiced_frames, 2)
                                pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
                                wav_io = io.BytesIO()
                                with wave.open(wav_io, 'wb') as wf:
                                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                                    wf.writeframes(pcm_16k)
                                wav_io.seek(0)
                                voiced_frames.clear()
                                silent_frames_count = 0
                                
                                try:
                                    transcription = client.audio.transcriptions.create(
                                        file=("audio.wav", wav_io.read()), 
                                        model="whisper-large-v3-turbo", 
                                        language="pa", 
                                        response_format="text"
                                    )
                                    
                                    user_text = transcription.strip()
                                    user_text_lower = user_text.lower()
                                    
                                    # HALLUCINATION BLOCKER: If text is too long, ignore it.
                                    if len(user_text) > 15 or len(user_text) < 2:
                                        print(f"--- IGNORING NOISE: '{user_text}' ---")
                                        continue
                                        
                                    print(f"--- PATIENT SAID: '{user_text}' ---")
                                    
                                    hang_up = False
                                    audio_to_play = None
                                    
                                    if any(word in user_text_lower for word in ["ਹਾਂ", "ਜੀ", "ਠੀਕ", "ਹਾਂਜੀ", "ਹਾਜੀ", "ਕਰ ਦੋ", "ਹਾਂ ਜੀ"]):
                                        print("--- MATCHED YES! ---")
                                        audio_to_play = AUDIO_CACHE["yes"]
                                        hang_up = True
                                    elif any(word in user_text_lower for word in ["ਨਹੀਂ", "ਨਾ", "no", "nahi"]):
                                        print("--- MATCHED NO! ---")
                                        audio_to_play = AUDIO_CACHE["no"]
                                        hang_up = True
                                    else:
                                        print("--- UNCLEAR ---")
                                        audio_to_play = AUDIO_CACHE["unclear"]
                                        
                                    if audio_to_play:
                                        base64_reply = base64.b64encode(audio_to_play).decode('utf-8')
                                        duration = len(audio_to_play) / 8000.0
                                        ignore_audio_until = time.time() + duration + 0.5
                                        
                                        await websocket.send_json({
                                            "event": "playAudio",
                                            "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64_reply}
                                        })
                                        
                                        if hang_up:
                                            print("--- HANGING UP CALL ---")
                                            await asyncio.sleep(duration + 0.5)
                                            await websocket.close()
                                            return
                                except Exception as e: 
                                    print(f"Pipeline error: {e}")
                                    
            elif data['event'] == 'stop': 
                break
    except Exception as e: 
        print(f"WebSocket error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
