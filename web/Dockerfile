FROM python:3.11-slim

WORKDIR /app

# Installer dépendances système et client Postgres
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc git python3-dev libxml2-dev libxslt1-dev libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Récupérer OpenUpgrade pour les versions 13.0 à 18.0
RUN for v in 13.0 14.0 15.0 16.0 17.0 18.0; do \
        git clone --depth 1 -b "$v" https://github.com/OCA/OpenUpgrade.git \
        "/app/openupgrade/$v"; \
    done

# Copier les règles de migration et l'API
COPY scripts    /app/scripts
COPY web/upload.py /app/upload.py
COPY web/index.html /app/index.html

# Installer pip et dépendances OpenUpgrade
RUN pip install --upgrade pip setuptools wheel \
    && pip install flask openupgradelib

# Exposer port HTTP
EXPOSE 8080

# Lancer l'API Flask
CMD ["python3", "upload.py"]
