"""All ElevenLabs calls live here."""

import os
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

# Read settings from the .env file
load_dotenv()
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

_client = None


def get_client() -> ElevenLabs:
    """Create the ElevenLabs client the first time we need it."""
    global _client
    if _client is None:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is missing. Copy .env.example to .env and fill it in."
            )
        _client = ElevenLabs(api_key=api_key)
    return _client


def speak(text: str, voice_id: str = VOICE_ID, model: str = MODEL) -> bytes:
    """Turn text into MP3 audio bytes."""
    chunks = get_client().text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=model,
        output_format="mp3_44100_128",
    )
    # The API streams the audio in pieces, so glue them together
    return b"".join(chunks)


def speak_to_file(text: str, path: str = "audio/output.mp3") -> Path:
    """Turn text into speech and save it as an MP3 file."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(speak(text))
    return out
