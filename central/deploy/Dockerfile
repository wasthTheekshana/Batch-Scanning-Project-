# DOK Scanner central service. Build from the REPOSITORY ROOT:
#   docker build -f central/deploy/Dockerfile -t dokscanner-central .
#
# Only what the service needs is copied: central/ plus the two core files it
# imports to validate project settings (core/profile_manager.py and
# core/project_schema.json). Nothing of the desktop application is included.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY central/requirements.txt central/requirements.txt
RUN pip install -r central/requirements.txt

COPY central/ central/
COPY core/__init__.py core/profile_manager.py core/project_schema.json core/

# an unprivileged user: the service never needs root
RUN useradd --system --no-create-home --uid 10001 dokcentral
USER dokcentral

EXPOSE 8000
# CENTRAL_WORKERS stays 1 for the very first start on an empty database (the
# migrations run then), and can be raised afterwards.
CMD ["sh", "-c", "exec uvicorn central.app:create_app_from_env --factory --host 0.0.0.0 --port 8000 --workers ${CENTRAL_WORKERS:-1} --proxy-headers --forwarded-allow-ips='*'"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
