# Official Playwright image: Python + Chromium already installed, so builds stay fast.
# The tag must match the playwright version in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    DATA_DIR=/data \
    LLM_CACHE=0 \
    PORT=8080

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY brain ./brain

VOLUME /data
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
