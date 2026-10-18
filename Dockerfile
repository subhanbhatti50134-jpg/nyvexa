FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NYVEXA_DATA_DIR=/app/data \
    PORT=7860

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

RUN mkdir -p /app/data

EXPOSE 7860

# --proxy-headers/--forwarded-allow-ips: derive https:// origin behind the
# HF (or any) reverse proxy — required for the Google OAuth redirect URI.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
