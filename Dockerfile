# Exact Python runtime requested for reproducibility. Security patch refresh requires approval.
FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMEPILOT_HOME=/data \
    TIMEPILOT_LOCAL_MODE=1
WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 timepilot \
    && mkdir -p /data \
    && chown -R timepilot:timepilot /data
USER timepilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=2)" || exit 1
CMD ["python","-m","uvicorn","timepilot.asgi:app","--host","0.0.0.0","--port","8000"]
