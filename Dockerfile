FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py seed-state.json ./
COPY public/ ./public/
RUN groupadd --gid 10001 ariel && useradd --uid 10001 --gid ariel --no-create-home ariel && mkdir -p /data && chown ariel:ariel /data
ENV PORT=4567 DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ENABLE_TELEGRAM=0 TZ=America/Argentina/Cordoba
USER ariel
EXPOSE 4567
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4567/health/ready', timeout=3)"
CMD ["python", "server.py"]
