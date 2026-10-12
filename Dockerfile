FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 subsynq && useradd --uid 1000 --gid subsynq --no-create-home subsynq \
    && mkdir -p /config /movies /tv /extra /models \
    && chown subsynq:subsynq /config /models
COPY app/ /app/
USER subsynq
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"
CMD ["python", "/app/server.py"]
