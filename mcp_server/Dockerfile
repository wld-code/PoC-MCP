FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Points the adapter at the API service. Override per environment.
ENV API_BASE_URL=http://api:8000

EXPOSE 8001

CMD ["python", "server.py"]
