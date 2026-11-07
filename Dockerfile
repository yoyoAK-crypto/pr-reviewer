# Same Python version as local development.
FROM python:3.14-slim

# No .pyc files, and logs appear immediately instead of being buffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, code later: Docker caches each step, so editing your code
# doesn't reinstall every package on the next build.
COPY requirements.txt requirements-rag.txt ./

# Hybrid and rerank retrieval need embedding models (~45 MB of packages, plus models
# downloaded at runtime). Keyword retrieval doesn't, so they're opt-in:
#   docker build --build-arg WITH_RAG=true .
ARG WITH_RAG=false
RUN pip install -r requirements.txt \
 && if [ "$WITH_RAG" = "true" ]; then pip install -r requirements-rag.txt; fi

# Run as a normal user, not root: if the app were ever exploited, the attacker
# couldn't modify the system inside the container.
RUN useradd --create-home --uid 1000 reviewer \
 && mkdir -p /app/.rag_cache \
 && chown reviewer:reviewer /app/.rag_cache

# Only what the service needs to run. Evals, tests and secrets stay out (see .dockerignore).
COPY --chown=reviewer:reviewer app ./app
COPY --chown=reviewer:reviewer scripts ./scripts

USER reviewer
EXPOSE 8000

# Docker calls /health every 30s and marks the container unhealthy if it stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# 0.0.0.0 = listen on every network interface. The default, 127.0.0.1, would only
# accept connections from inside the container itself.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
