"""Quick check that the ElevenLabs key works. Run: python test_voice.py"""

from src.voice import speak_to_file

path = speak_to_file("Hello from team TokenBurners!", "audio/test_voice.mp3")
print(f"OK - ElevenLabs works. Audio saved to {path.resolve()}")
