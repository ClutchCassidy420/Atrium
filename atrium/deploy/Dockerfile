FROM python:3.12-alpine
RUN adduser -D -u 10001 app
WORKDIR /srv/app
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY app/ /srv/app/
COPY scripts/ /srv/scripts/
USER 10001
EXPOSE 8000
CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
