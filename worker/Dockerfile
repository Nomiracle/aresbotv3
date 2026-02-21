FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY worker/ ./worker/
COPY config.yaml .
RUN chmod +x worker/entrypoint.sh

ARG BUILD_VERSION=""
RUN echo "${BUILD_VERSION}" > /app/VERSION

ENTRYPOINT ["worker/entrypoint.sh"]
