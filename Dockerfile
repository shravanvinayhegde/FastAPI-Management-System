FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render/Heroku-style platforms set $PORT
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:${PORT} app.main:app"]
