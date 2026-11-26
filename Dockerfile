# FlightQL API image.
#   docker build -t flightql .
#   docker run -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data:ro" flightql
# The 1.5 GB database is mounted at run time, not baked into the image, and the
# API key comes from --env-file, so neither ends up inside the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLIGHTQL_DB=/app/data/flights.duckdb

WORKDIR /app

# Dependencies first, so code edits don't force a full reinstall.
COPY pyproject.toml .
COPY flightql/ flightql/
# Editable install keeps the package at /app, which is where config.py looks
# for data/ and logs/.
RUN pip install -e .

# Run as a non-root user that can only write its own log directory.
RUN useradd --create-home flightql && mkdir -p /app/logs && chown flightql /app/logs
USER flightql

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "flightql.api:app", "--host", "0.0.0.0", "--port", "8000"]
