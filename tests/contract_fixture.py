"""CI-only model replies for a real HTTP attach to the v1 mock portal."""
import json
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()
RECIPE = Path(__file__).resolve().parents[1] / "testset/fixtures/mock/recipe_v1.json"


@app.post("/v1/responses")
def respond(body: dict):
    evidence = json.loads(body["input"].removeprefix("Answer in JSON.\n"))
    if "entry" in evidence:
        answer = {"list_url": evidence["entry"]["url"], "detail_url": "/tender/MOCK-001"}
    else:
        answer = json.loads(RECIPE.read_text())
        answer["list"]["url"] = evidence["list_page"]["url"]
    return {
        "id": "resp_fixture", "object": "response", "status": "completed",
        "model": body["model"], "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(answer), "annotations": []}]}],
    }
