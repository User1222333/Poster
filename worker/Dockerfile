FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/worker
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/app ./app
COPY worker/dev ./dev
COPY worker/fixtures ./fixtures
COPY supabase/migrations /srv/migrations
ENV MIGRATIONS_DIR=/srv/migrations
RUN useradd --system mdb && mkdir -p /data && chown mdb /data
USER mdb
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
