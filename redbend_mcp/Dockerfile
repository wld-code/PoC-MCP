FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Points the adapter at the Redbend API service. Override per environment.
ENV API_BASE_URL=http://redbend-api:8023

EXPOSE 8013

CMD ["python", "server.py"]
