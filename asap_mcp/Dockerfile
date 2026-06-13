FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Points the adapter at the ASAP API service. Override per environment.
ENV API_BASE_URL=http://asap-api:8021

EXPOSE 8011

CMD ["python", "server.py"]
