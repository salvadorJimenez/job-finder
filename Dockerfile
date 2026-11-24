# job-finder as a background service. Build and run with: docker compose up -d --build
FROM python:3.13-slim

# tzdata: "today", the daily goal and the streak use local dates (TZ is set in compose.yaml).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jobfinder ./jobfinder
RUN pip install . \
    && useradd --create-home --uid 1000 jobfinder \
    && mkdir -p /app/data /app/exports \
    && chown -R jobfinder /app/data /app/exports

USER jobfinder
EXPOSE 8000

# Inside the container the app listens on every interface so Docker can forward to it;
# compose.yaml publishes it on 127.0.0.1 only, so it stays private to this machine.
# The config is not passed with --config: the app looks for /app/config.toml, then
# /app/config/config.toml (the folder compose.yaml mounts), and prints that hint when neither exists.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/manifest.webmanifest', timeout=4)"

CMD ["job-finder", "serve", "--host", "0.0.0.0", "--port", "8000"]
