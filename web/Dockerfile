FROM python:3.11-slim

WORKDIR /app

# Installer dépendances système et client Postgres
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc git python3-dev libxml2-dev libxslt1-dev libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copier les règles de migration et l'API
COPY scripts    /app/scripts
COPY web/upload.py /app/upload.py
COPY web/index.html /app/index.html

# Installer pip et dépendances OpenUpgrade
RUN pip install --upgrade pip setuptools wheel

# Exposer port HTTP
EXPOSE 8080

# Lancer l'API Flask
CMD ["python3", "upload.py"]