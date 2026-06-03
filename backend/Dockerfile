FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git: versionado local por app (trazabilidad/rollback de los repos en /data/apps).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Análisis de planillas (capa aparte: no invalida la capa grande de deps al cambiar).
RUN pip install --retries 5 --timeout 120 pandas==2.2.3 openpyxl==3.1.5

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
