# Azure Discord Bot - Multi-stage Docker Build
# Optimized for production deployment with LLM support

# =============================================================================
# Stage 1: Base Python environment
# =============================================================================
FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app user (non-root for security)
RUN useradd -m -u 1000 -s /bin/bash azurebot

WORKDIR /app

# =============================================================================
# Stage 2: Python dependencies
# =============================================================================
FROM base as dependencies

# Copy requirements files
COPY requirements.txt requirements-web.txt ./

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    pip install -r requirements-web.txt

# =============================================================================
# Stage 3: Final production image
# =============================================================================
FROM base as production

# Copy installed packages from dependencies stage
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=azurebot:azurebot . /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs /app/models && \
    chown -R azurebot:azurebot /app

# Switch to non-root user
USER azurebot

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

# Expose ports
EXPOSE 8088 8080

# Default command
CMD ["python", "run_bot.py"]

# Development image: same validated runtime, with source mounts supplied by
# docker-compose.dev.yml for rapid iteration.
FROM production as development
USER azurebot
CMD ["python", "-u", "run_bot.py"]
