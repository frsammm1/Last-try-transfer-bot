FROM python:3.11-slim

WORKDIR /app

# System dependencies install karo
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies install karo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application files copy karo
COPY main.py .
COPY generate_session.py .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Port expose karo
EXPOSE 8080

# Bot start karo
CMD ["python", "main.py"]
