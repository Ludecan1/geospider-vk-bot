FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bothost: персистентные подписчики и состояние станций — в /app/data (см. docs/database-storage)
CMD ["python", "bot.py"]
