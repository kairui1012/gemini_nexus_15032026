FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source.
COPY . .

EXPOSE 8080

CMD ["sh", "-c", "uvicorn agents.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
