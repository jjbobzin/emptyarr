FROM python:3.12-slim

# Install runtime dependencies
# su-exec: lightweight privilege dropping (like gosu but smaller)
# util-linux: provides the mountpoint binary used by health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    util-linux \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /app/data && touch /app/data/config.yml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

EXPOSE 8222

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "app.py"]