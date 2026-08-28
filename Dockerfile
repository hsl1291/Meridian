# Single stage: the layer fetcher is Python now (scripts/fetch_layers.py), so
# the old Node build stage that existed only to run fetch-layers.js is gone.
# Layers are fetched at build time, so the build needs network access.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY run.py .
# Map layers plus the two datasets the rent and population overlays read.
RUN python scripts/fetch_layers.py \
 && python scripts/fetch_zori.py \
 && python scripts/fetch_zcta_population.py
EXPOSE 8012
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8012"]
