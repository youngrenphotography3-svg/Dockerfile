# Stage 1: build the frontend. Vite writes into ../backend/src/alpha_harness/web.
FROM node:26-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /app/backend/src/alpha_harness && npm run build

# Stage 2: Python backend serving the built UI (requires Python >= 3.14).
FROM python:3.14-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./backend/
COPY --from=frontend-builder /app/backend/src/alpha_harness/web ./backend/src/alpha_harness/web
RUN pip install ./backend

# State (credentials, sqlite, duckdb) lives in ~/.alpha-harness: mount the Railway volume at
# /root/.alpha-harness.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn alpha_harness.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
