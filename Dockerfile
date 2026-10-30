FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home mediahook

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 改變 TOOLS_REFRESH 就只會重跑這一層，用來更新 yt-dlp / gallery-dl
ARG TOOLS_REFRESH=0
RUN echo "tools refresh: ${TOOLS_REFRESH}" \
    && pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    MEDIAHOOK_DATA_DIR=/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
