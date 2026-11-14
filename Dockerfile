# Stage 1: Build frontend
cad4730c704d8b6b2c937c68dae373197b551d79
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build
HEAD
# Normalize output dir: Vite -> dist, Create React App -> build
RUN mkdir -p /app/frontend-out && \
    if [ -d dist ]; then cp -r dist/. /app/frontend-out/; \
    elif [ -d build ]; then cp -r build/. /app/frontend-out/; \
    else echo "ERROR: no dist/ or build/ folder produced by npm run build" && exit 1; fi

# Stage 2: Python backend + final runtime
FROM python:3.10-slim


# Stage 2: Python backend + final runtime (backend/pyproject.toml requires Python >= 3.14)
FROM python:3.14-slim
cad4730c704d8b6b2c937c68dae373197b551d79
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

HEAD
COPY backend/requirements.txt ./backend/
RUN pip install -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend-builder /app/frontend-out ./backend/static

# Railway injects $PORT at runtime; the hardcoded 8000 is only a local fallback
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

COPY backend/ ./backend/
# The backend serves the built UI from alpha_harness/web inside the package.
COPY --from=frontend-builder /app/backend/src/alpha_harness/web ./backend/src/alpha_harness/web
RUN pip install ./backend

# Railway injects $PORT at runtime; the hardcoded 8000 is only a local fallback
EXPOSE 8000
CMD ["sh", "-c", "uvicorn alpha_harness.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
cad4730c704d8b6b2c937c68dae373197b551d79
