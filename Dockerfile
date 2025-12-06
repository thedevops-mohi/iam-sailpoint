# -----------------------------
# Stage 1: Build Stage
# -----------------------------
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies into /install
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# -----------------------------
# Stage 2: Runtime Stage
# -----------------------------
FROM python:3.13-slim AS runtime

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy script(s)
COPY sailpoint_03_11_2025_works_100.py .

# Create base directories and set permissions for non-root user
RUN useradd -m appuser && \
    mkdir -p /app/export_data && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Entrypoint
ENTRYPOINT ["python", "sailpoint_03_11_2025_works_100.py"]
