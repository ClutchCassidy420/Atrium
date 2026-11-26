FROM python:3.12-alpine
RUN adduser -D -u 10001 app
WORKDIR /srv/app
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY app/ /srv/app/
COPY scripts/ /srv/scripts/
# Defaults so `docker compose up` works with no setup: a starter instance folder, and writable data folders that a named volume inherits.
COPY instance.example/ /srv/instance-default/
RUN mkdir -p /data /opt/data && chown 10001:10001 /data /opt/data
ENV DATA_DIR=/data INSTANCE_DIR=/srv/instance-default
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
