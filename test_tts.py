import os
import wave
import struct
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

text = "안녕하세요! 생활꿀템연구소입니다. 오늘의 추천 상품을 소개합니다."

response = client.models.generate_content(
    model="gemini-2.5-flash-preview-tts",
    contents=text,
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Sadaltager")
            )
        ),
    ),
)

audio_data = response.candidates[0].content.parts[0].inline_data.data

# PCM to WAV
with wave.open("output.wav", "wb") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)  # 16-bit
    wav_file.setframerate(24000)
    wav_file.writeframes(audio_data)

print("output.wav 저장 완료")
