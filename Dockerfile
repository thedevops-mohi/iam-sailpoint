# -----------------------------
# Stage 1: Build Environment
# -----------------------------
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies only for this stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency list first for caching
COPY requirements.txt .

# Install dependencies in a clean environment
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# -----------------------------
# Stage 2: Final Runtime Image
# -----------------------------
FROM python:3.13-slim AS runtime

WORKDIR /app

# Install only what is needed at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python dependencies from builder
COPY --from=builder /install /usr/local

# Copy your source code (nothing else)
COPY sailpoint_03_11_2025_works_100.py .

# Drop root privileges (security best practice)
RUN useradd -m appuser
USER appuser

ENTRYPOINT ["python", "sailpoint_03_11_2025_works_100.py"]
