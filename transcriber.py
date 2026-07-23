import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def transcribe_audio(file_path: str) -> str:
    """Transcribes an audio file using Groq Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            language="uk",
            response_format="text",
        )
    return transcript
