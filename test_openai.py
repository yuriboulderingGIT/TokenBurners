"""Quick check that the OpenAI key works. Run: python test_openai.py"""

from src.llm import ask, ask_json

# 1. Plain text answer
print("Text reply:", ask("Say hello to the TokenBurners team in one short sentence."))

# 2. JSON answer
print("JSON reply:", ask_json('Give me a JSON object with keys "team" and "mood".'))

print("OK - OpenAI works.")
