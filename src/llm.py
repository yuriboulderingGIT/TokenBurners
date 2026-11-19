"""All OpenAI calls live here."""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

# Read settings from the .env file
load_dotenv()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

_client = None


def get_client() -> OpenAI:
    """Create the OpenAI client the first time we need it."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def ask(prompt: str, system: str = "You are a helpful assistant.", model: str = MODEL) -> str:
    """Send a prompt and get plain text back."""
    response = get_client().responses.create(
        model=model,
        instructions=system,
        input=prompt,
    )
    return response.output_text


def ask_json(prompt: str, system: str = "You are a helpful assistant.", model: str = MODEL) -> dict:
    """Send a prompt and get a Python dict back (the model answers in JSON)."""
    response = get_client().responses.create(
        model=model,
        instructions=system + " Always answer with valid JSON only.",
        input=prompt,
        text={"format": {"type": "json_object"}},
    )
    return json.loads(response.output_text)
