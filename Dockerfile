# Release gate: requires a checked-in, complete npm package-lock.json.
# The source-only ZIP intentionally lacks that file; a clean Node 24 build
# must first generate/review it on a connected machine. No CDN at runtime.
FROM node:24-bookworm-slim AS perspective-build
WORKDIR /build
COPY frontend/native-bi ./frontend/native-bi
RUN test -f frontend/native-bi/package-lock.json || (echo 'BLOCKED: commit the verified npm lock before building Docker' >&2; exit 2)
RUN cd frontend/native-bi && npm ci --no-audit --no-fund --include=optional
ENV TIMEPILOT_NATIVE_BI_OUTDIR=/native-bi-bundle
RUN cd frontend/native-bi && npm run build

FROM python:3.12.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMEPILOT_HOME=/data \
    PYTHONPATH=/app/src \
    TIMEPILOT_LOCAL_MODE=1
WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
COPY frontend/native-bi ./frontend/native-bi
COPY scripts/verify_native_bi_release.py ./scripts/verify_native_bi_release.py
COPY --from=perspective-build /native-bi-bundle ./src/timepilot/static/native-bi
RUN python -m pip install --no-cache-dir -r requirements.txt
# Generate a SHA256 manifest against the very source and lock shipped here;
# verify the public ESM exports and the actual DuckDB/Perspective adapters.
RUN python -c "import sys;sys.path.insert(0,'src');from pathlib import Path;from timepilot.native_bi_bundle import generate_manifest,frontend_source_hash;generate_manifest(Path('src/timepilot/static/native-bi'),frontend_sha256=frontend_source_hash(Path('frontend/native-bi')))" \
    && python scripts/verify_native_bi_release.py --require-python31212 --require-duckdb --require-build --require-source-current --require-virtual-server \
    && useradd --create-home --uid 10001 timepilot \
    && mkdir -p /data \
    && chown -R timepilot:timepilot /data
USER timepilot
EXPOSE 8000
# Liveness stays separate from /api/v1/ready; orchestrators must also probe ready.
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=2)" || exit 1
CMD ["python","-m","uvicorn","timepilot.asgi:app","--host","0.0.0.0","--port","8000"]
