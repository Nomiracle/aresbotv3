FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY api/ ./api/
COPY config.yaml .

EXPOSE 8000

CMD ["python", "-m", "api.main"]
