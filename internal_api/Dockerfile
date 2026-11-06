FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QWEN_MODEL=qwen3.8:27b \
    QWEN_PROVIDER=litellm \
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    QWEN_TIMEOUT_SECONDS=60

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py mapping_config.py mapping_service.py model_request.py inference_storage.py review_service.py scan_history.py ./
COPY config ./config

RUN useradd --create-home appuser
USER appuser

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
