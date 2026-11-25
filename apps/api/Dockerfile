FROM python:3.12-slim-bookworm AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY chessmind chessmind
COPY alembic.ini .
COPY alembic alembic
RUN useradd --create-home chessmind
USER chessmind
CMD ["uvicorn", "chessmind.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM api AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends stockfish && rm -rf /var/lib/apt/lists/*
ENV STOCKFISH_PATH=/usr/games/stockfish
USER chessmind
CMD ["celery", "-A", "chessmind.worker:celery", "worker", "--loglevel=INFO"]
