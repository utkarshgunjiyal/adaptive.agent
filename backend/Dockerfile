# Backend image (API + Celery worker share this image).
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install the primary deps + Postgres/Redis/Qdrant/MinIO/Celery deps used
# only by the docker-compose production stack. The preview build's
# requirements.txt is a strict subset and works with this superset.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir \
      asyncpg \
      SQLAlchemy \
      alembic \
      celery[redis] \
      qdrant-client \
      minio \
      psycopg2-binary \
 && pip install --no-cache-dir --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ emergentintegrations

COPY . /app

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
