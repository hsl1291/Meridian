# Single stage: the layer fetcher is Python now (scripts/fetch_layers.py), so
# the old Node build stage that existed only to run fetch-layers.js is gone.
# Layers are fetched at build time, so the build needs network access.
#
# SCOPE: this image is the MAP. It does not build the condo screen.
#
# The condo pipeline needs a 59MB NAL tax roll and writes into a ~330MB shared
# store that lives outside the app folder and is shared with other tools --
# neither belongs baked into an image layer, and both change on their own
# schedule rather than on the image's. So prospect.db is absent here and the
# condo and metro routes answer 503 with the script that builds them, which is
# the intended behaviour rather than a missing step (see unbuilt_detail in
# backend/app.py).
#
# To run the full app, mount a prebuilt store and point the app at it:
#   docker run -p 8012:8012 \
#     -v /path/to/_shared:/shared -e APPS_SHARED=/shared \
#     -v /path/to/data:/app/data  meridian
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
# Map layers plus the two datasets the rent and population overlays read.
RUN python scripts/fetch_layers.py \
 && python scripts/fetch_zori.py \
 && python scripts/fetch_zcta_population.py
EXPOSE 8012
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8012"]
