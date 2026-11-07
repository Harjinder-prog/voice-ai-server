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

GREETING_TEXT = "ਸਾ ਸ੍ਰੀ ਅਕਾਲ ਜੀ! ਮੈਂ ਨਿਹਾਲ ਹਸਪਤਾਲ ਤੋਂ ਗੱਲ ਕਰ ਰਹੀ ਹਾਂ। ਕੀ ਤੁਸੀਂ ਆਪਣਾ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request):
    # MAKE SURE THIS IS YOUR CURRENT NGROK LINK
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
            wss://eldercare-flatterer-skier.ngrok-free.dev/ws
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    ignore_audio_until = time.time() + 2.0 
    print(">>> WEBSOCKET CONNECTED BY VOBIZ! <<<")
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data['event'] == 'start':
                print(">>> CALL STARTED. SENDING GREETING... <<<")
                ulaw_greeting = await text_to_mulaw(GREETING_TEXT)
                if ulaw_greeting:
                    base64_audio = base64.b64encode(ulaw_greeting).decode('utf-8')
                    ignore_audio_until = time.time() + (len(ulaw_greeting) / 8000.0) + 1.5
                    await websocket.send_json({
                        "event": "playAudio",
                        "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64_audio}
                    })
                
            elif data['event'] == 'media':
                if time.time() < ignore_audio_until: 
                    continue

                payload = base64.b64decode(data['media']['payload'])
                audio_buffer.extend(payload)
                
                # Increased buffer to 2 seconds (16000 bytes) for better Whisper accuracy
                if len(audio_buffer) > 16000: 
                    audio_chunk = bytes(audio_buffer)
                    audio_buffer.clear()
                    
                    pcm_8k = audioop.ulaw2lin(audio_chunk, 2)
                    
                    # Volume check (ignore background noise)
                    volume = audioop.rms(pcm_8k, 2)
                    if volume < 500: 
                        print(f"--- DEBUG: IGNORING SILENCE (Volume: {volume}) ---")
                        continue
                        
                    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
                    wav_io = io.BytesIO()
                    with wave.open(wav_io, 'wb') as wf:
                        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                        wf.writeframes(pcm_16k)
                    wav_io.seek(0)
                    
                    try:
                        transcription = client.audio.transcriptions.create(
                            file=("audio.wav", wav_io.read()), 
                            model="whisper-large-v3", 
                            language="pa", 
                            response_format="text"
                        )
                        
                        user_text = transcription.strip()
                        print(f"--- DEBUG: WHISPER HEARD: '{user_text}' ---")
                        
                        # Ignore clear hallucinations
                        if len(user_text) < 1 or any(x in user_text for x in ["ਕੋਲੋ", "ਮੀਡਿ", "ਗੀਤ", "thank you"]): 
                            continue
                            
                        user_text_lower = user_text.lower()
                        
                        # MASSIVE YES & NO LISTS (Phonetic & Direct)
                        yes_words = ["haan", "han", "yes", "theek", "ok", "ji", "krdo", "kar do", "kar", "kardo", 
                                     "ਹਾਂ", "ਜੀ", "ਠੀਕ", "ਹਾਂਜੀ", "ਹਾਂ ਜੀ", "ਹਾਜੀ", "ਕਰ ਦੋ", "ਕਰ", "ਕਰਦੀ", "ਕਰਾਂ", 
                                     "ਘਾਂਜੀ", "ਘਾਂ", "ਦੱ", "ਹਾਂ ਜੀ"]
                        no_words = ["nahi", "no", "nhi", "nhii", "glti naal", "galti se", "ਨਹੀਂ", "ਨਾ", "ਗਲਤੀ ਨਾਲ"]
                        
                        hang_up = False
                        intent_matched = False
                        
                        # Step 1: Try Python matching first (Fastest)
                        if any(word in user_text_lower for word in yes_words):
                            print("--- DEBUG: PYTHON MATCHED YES! ---")
                            intent_matched = True
                            ai_reply = "ਤੁਹਾਡਾ Order ਪਲੇਸ ਹੋ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ, ਤੁਹਾਡਾ ਦਿਨ ਸ਼ੁਭ ਹੋਵੇ।"
                            hang_up = True
                        elif any(word in user_text_lower for word in no_words):
                            print("--- DEBUG: PYTHON MATCHED NO! ---")
                            intent_matched = True
                            ai_reply = "ਅਸੀਂ ਤੁਹਾਡਾ Order ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
                            hang_up = True
                            
                        # Step 2: If Python fails, ask the LLM
                        if not intent_matched:
                            print("--- DEBUG: ASKING LLM CLASSIFIER ---")
                            classify_prompt = f"User was asked 'Do you want to place the order?' in Punjabi. User replied: '{user_text}'. Does this mean YES, NO, or UNCLEAR? Reply with exactly one word."
                            
                            classification = client.chat.completions.create(
                                model="llama-3.1-8b-instant",
                                messages=[{"role": "user", "content": classify_prompt}],
                                max_tokens=5,
                                temperature=0.0
                            )
                            
                            intent = classification.choices[0].message.content.strip().upper()
                            print(f"--- DEBUG: LLM INTENT: '{intent}' ---")
                            
                            if "YES" in intent:
                                ai_reply = "ਤੁਹਾਡਾ Order ਪਲੇਸ ਹੋ ਗਿਆ ਹੈ। ਧੰਨਵਾਦ, ਤੁਹਾਡਾ ਦਿਨ ਸ਼ੁਭ ਹੋਵੇ।"
                                hang_up = True
                            elif "NO" in intent:
                                ai_reply = "ਅਸੀਂ ਤੁਹਾਡਾ Order ਅੱਗੇ ਨਹੀਂ ਭੇਜ ਰਹੇ। ਧੰਨਵਾਦ।"
                                hang_up = True
                            else:
                                ai_reply = "ਕੀ ਤੁਸੀਂ ਆਪਣਾ Order ਪਲੇਸ ਕਰਨਾ ਚਾਹੁੰਦੇ ਹੋ?"
                        else:
                            pass
                            
                        print(f"--- DEBUG: AI REPLYING WITH: '{ai_reply}' ---")
                        ulaw_reply = await text_to_mulaw(ai_reply)
                        
                        if ulaw_reply:
                            base64_reply = base64.b64encode(ulaw_reply).decode('utf-8')
                            duration = len(ulaw_reply) / 8000.0
                            ignore_audio_until = time.time() + duration + 1.5
                            
                            await websocket.send_json({
                                "event": "playAudio",
                                "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64_reply}
                            })
                            
                            if hang_up:
                                print("--- DEBUG: HANGING UP CALL ---")
                                await asyncio.sleep(duration + 1.0)
                                await websocket.close()
                                break
                                
                        audio_buffer.clear()
                    except Exception as e: 
                        print(f"!!! PIPELINE ERROR: {e} !!!")
            elif data['event'] == 'stop': 
                print(">>> CALL ENDED <<<")
                break
    except Exception as e: 
        print(f"!!! WEBSOCKET ERROR: {e} !!!")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
