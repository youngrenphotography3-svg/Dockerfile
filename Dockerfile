# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + final runtime (backend/pyproject.toml requires Python >= 3.14)
FROM python:3.14-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./backend/
# The backend serves the built UI from alpha_harness/web inside the package.
COPY --from=frontend-builder /app/backend/src/alpha_harness/web ./backend/src/alpha_harness/web
RUN pip install ./backend

# Railway injects $PORT at runtime; the hardcoded 8000 is only a local fallback
EXPOSE 8000
CMD ["sh", "-c", "uvicorn alpha_harness.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
