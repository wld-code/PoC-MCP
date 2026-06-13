FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Points the adapter at the CVC API service. Override per environment.
ENV API_BASE_URL=http://cvc-api:8022

EXPOSE 8012

CMD ["python", "server.py"]
