FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
