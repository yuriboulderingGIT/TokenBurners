# TokenBurners - MAIN Summit Hackathon 2026

**One sentence:** A [user] does X and sees Y.

**What it does:** TBD

**How it works:** input → OpenAI → ElevenLabs → output

## Run it

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
copy .env.example .env      # then open .env and paste your keys
pip install -r requirements.txt
python test_openai.py
python test_voice.py
streamlit run app.py
```

### Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env        # then open .env and paste your keys
pip install -r requirements.txt
python test_openai.py
python test_voice.py
streamlit run app.py
```

## Roles

| Role           | Owns                              |
| -------------- | --------------------------------- |
| Lead/PM        | Scope + pitch                     |
| AI/backend     | `src/`                            |
| Frontend       | `app.py`                          |
| Research/pitch | Slides + video + submission       |

## Git loop

```bash
git pull
git add .
git commit -m "What I changed"
git push
```

**Never commit `.env`.** It holds your API keys.
