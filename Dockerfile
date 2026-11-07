FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GAMEVAULT_HOSTED=1 \
    GAMEVAULT_PUBLIC_HOST=1 \
    GAMEVAULT_DATA_DIR=/var/data/data \
    GAMEVAULT_LIBRARY_DIR=/var/data/library

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 gamevault \
    && mkdir -p /var/data \
    && chown gamevault:gamevault /var/data

COPY --chown=gamevault:gamevault . /app
COPY docker-entrypoint.sh /usr/local/bin/gamevault-entrypoint
RUN chmod 0755 /usr/local/bin/gamevault-entrypoint

EXPOSE 10000
ENTRYPOINT ["/usr/local/bin/gamevault-entrypoint"]
