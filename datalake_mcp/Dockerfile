FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Points the adapter at the Datalake API service. Override per environment.
ENV API_BASE_URL=http://datalake-api:8024

EXPOSE 8014

CMD ["python", "server.py"]
