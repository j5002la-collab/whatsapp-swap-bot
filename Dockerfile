FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] httpx aiosqlite python-dotenv websockets bolt11 bitcoinlib bip340

COPY swapbot/ ./swapbot/
COPY requirements.txt .

EXPOSE 2889

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:2889/health')" || exit 1

CMD ["python", "-m", "swapbot"]
