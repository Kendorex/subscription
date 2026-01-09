FROM python:3.12-slim

WORKDIR /app

# системные зависимости (psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# по умолчанию ничего не запускаем — команда в docker-compose
