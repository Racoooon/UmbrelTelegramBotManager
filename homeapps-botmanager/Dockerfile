# Lightweight runtime. Dependencies are installed at container start into
# /data/.local so the editable copy under /data/src can add packages later.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data \
    PATH=/data/.local/bin:/usr/local/bin:/usr/bin:/bin

# Fonts + build bits matplotlib needs for chart PNGs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libfreetype6 \
        libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/default-src

# Packaged source is also copied into the image so the app still starts
# if the bind-mount of ./src is missing (e.g. a plain `docker run` test).
COPY src/ /opt/default-src/

RUN chmod +x /opt/default-src/entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/opt/default-src/entrypoint.sh"]
