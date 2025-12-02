FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVENTLET_NO_GREENDNS=yes

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pré-créer les dossiers d'upload attendus par l'app
RUN mkdir -p frontend/static/uploads/logos \
    frontend/static/uploads/profiles \
    frontend/static/uploads/products \
    frontend/static/uploads/categories

ENV PORT=8080 \
    FLASK_ENV=production

# Commande de démarrage (Gunicorn + Eventlet, écoute sur PORT)
CMD ["sh", "-c", "gunicorn -k eventlet -w 1 --bind 0.0.0.0:${PORT:-8080} wsgi:app"]
