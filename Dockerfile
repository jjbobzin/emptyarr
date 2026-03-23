FROM python:3.12-slim

# gosu: privilege dropping (Debian equivalent of su-exec)
# util-linux: provides the mountpoint binary for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    util-linux \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for running the app
# The entrypoint.sh uses gosu to drop to PUID/PGID at runtime (default 99:100)
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app
RUN mkdir -p /app/data && touch /app/data/config.yml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/entrypoint.sh && \
    chown -R appuser:appgroup /app

EXPOSE 8222

# entrypoint.sh drops privileges to PUID/PGID via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8222", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]