FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint (PDF) braucht Pango/HarfBuzz; DejaVu-Schriften werden ins PDF/A eingebettet
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./
RUN chmod 755 entrypoint.sh \
    && useradd --system --uid 10001 --no-create-home selbstlite

# ── Test-Stufe: docker build --target test -t selbstlite-backend-test backend ──
FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
ENV APP_ENV=test
CMD ["python", "-m", "pytest", "-p", "no:warnings"]

# ── Laufzeit ──
FROM base AS runtime
USER 10001
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
