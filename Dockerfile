# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# системные зависимости (если понадобятся)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# копируем всё
COPY . .

# создаём базу (инициализация)
RUN python - <<'PY'
import asyncio
from db import init_db
asyncio.run(init_db())
PY

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["./start.sh"]
