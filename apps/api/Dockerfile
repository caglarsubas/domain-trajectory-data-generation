FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/packages/trajectory_contract/src:/app/packages/sectors/src:/app/apps/api

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/api ./apps/api

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
