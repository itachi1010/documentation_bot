FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
ENV OLLAMA_MODEL=deepseek-coder:6.7b
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
