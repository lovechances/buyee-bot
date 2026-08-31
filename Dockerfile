FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUYEYE_DATA_DIR=/data \
    BUYEYE_DB_PATH=/data/buyeeye.db \
    BUYEYE_SESSION_DIR=/data/browser \
    BUYEYE_CONFIG_DIR=/app/config

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends xauth \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/container-entrypoint.sh /usr/local/bin/buyeeye-container

RUN mkdir -p /data/browser /data/logs

RUN chmod 0755 /usr/local/bin/buyeeye-container

ENTRYPOINT ["/usr/local/bin/buyeeye-container"]
CMD ["python", "-m", "buyeeye", "run"]
