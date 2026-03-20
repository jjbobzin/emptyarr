FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8222

CMD ["python", "app.py"]
