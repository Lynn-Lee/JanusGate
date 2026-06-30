FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/home/appuser/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libldap-2.5-0 \
        libpq5 \
        libsasl2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 1000 appuser \
    && adduser --system --uid 1000 --ingroup appuser --home /home/appuser appuser

COPY --chown=appuser:appuser pyproject.toml ./
COPY --chown=appuser:appuser app ./app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
    && python -m pip install --upgrade pip \
    && python -m pip install . \
    && apt-get purge -y --auto-remove build-essential libldap2-dev libpq-dev libsasl2-dev \
    && rm -rf /var/lib/apt/lists/* /root/.cache

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
