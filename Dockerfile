ARG BUILD_FROM=docker.io/library/debian:bookworm-slim
ARG BUILD_VERSION=0.1.1
ARG BUILD_ARCH=amd64
FROM bluenviron/mediamtx:1.18.2 AS mediamtx
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.source="https://github.com/AllenJvN/Web2RTSP" \
      org.opencontainers.image.licenses="GPL-3.0-only"

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        ffmpeg \
        fonts-liberation \
        fonts-noto-color-emoji \
        python3 \
        python3-pip \
        python3-venv \
        tzdata \
        x11-utils \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mediamtx /mediamtx /usr/local/bin/mediamtx

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY web2rtsp /app/web2rtsp
COPY run.sh /run.sh
RUN chmod 0755 /run.sh /usr/local/bin/mediamtx \
    && mkdir -p /data /tmp/web2rtsp

EXPOSE 8099 8554 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=3)" || exit 1

ENTRYPOINT ["/run.sh"]
