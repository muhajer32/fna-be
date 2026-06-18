# Belgium FNA prototype — headless Linux image for cloud runs (e.g. Vultr).
#
# The model is fully headless: Excel I/O uses openpyxl, so no Microsoft Excel,
# xlwings, or COM automation is required. Only GAMS must be supplied separately
# because it is proprietary and cannot be redistributed in this image.
#
# Build:
#   docker build -t fna .
#
# Run (deterministic), mounting your data and your licensed GAMS install:
#   docker run --rm \
#     -v "$PWD/excel:/app/excel" \
#     -v "$PWD/data:/app/data" \
#     -v "/opt/gams:/opt/gams:ro" \
#     -e GAMS_EXE=/opt/gams/gams \
#     fna run-deterministic --target-year 2030
#
# audit / validate / make-report need no GAMS and run as-is.

FROM python:3.12-slim

# System libraries: matplotlib/pillow need libgomp + basic fonts; nothing Excel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libfreetype6 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Project source.
COPY python/ ./python/
COPY gams/ ./gams/
COPY excel/ ./excel/
COPY data/ ./data/

# Make GAMS discoverable when mounted at /opt/gams (override with -e GAMS_EXE=...).
ENV GAMS_EXE=/opt/gams/gams \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/python

# `fna_be` is the CLI package (python -m fna_be <command>).
ENTRYPOINT ["python", "-m", "fna_be"]
CMD ["--help"]
