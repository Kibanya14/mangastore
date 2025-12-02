# Déploiement sur Fly.io (Flask + Gunicorn/Eventlet)

## Build & déploiement
1. Installez/initialisez Fly: `flyctl auth login` puis `flyctl apps create mangastorerdc` (ou utilisez `fly launch --no-deploy` si l'app n'existe pas).
2. Vérifiez/ajustez `fly.toml` : `internal_port = 8080`, région `jnb` (modifiez si nécessaire).
3. Build & déployez: `fly deploy`.

## Secrets indispensables
Configurez vos variables via Fly secrets avant de déployer (ou juste après, puis `fly deploy` / `fly apps restart`):

### SMTP Gmail (mot de passe d'application)
```
fly secrets set \
  SECRET_KEY=change-me \
  DATABASE_URL=postgresql://... \
  MAIL_SERVER=smtp.gmail.com \
  MAIL_PORT=587 \
  MAIL_USE_TLS=True \
  MAIL_USE_SSL=False \
  MAIL_USERNAME=votre@gmail.com \
  MAIL_PASSWORD=mot_de_passe_application \
  MAIL_DEFAULT_SENDER=votre@gmail.com \
  MAIL_SUPPRESS_SEND=False
```

### SMTP Brevo (option)
```
fly secrets set \
  SECRET_KEY=change-me \
  DATABASE_URL=postgresql://... \
  MAIL_SERVER=smtp-relay.brevo.com \
  MAIL_PORT=587 \
  MAIL_USE_TLS=True \
  MAIL_USE_SSL=False \
  MAIL_USERNAME=9cf48b001@smtp-brevo.com \
  MAIL_PASSWORD=cle_smtp_brevo \
  MAIL_DEFAULT_SENDER=noreply@votre-domaine-valide.com \
  MAIL_SUPPRESS_SEND=False
```

## Détails runtime
- `Dockerfile` lance `gunicorn -k eventlet -w 1 -b 0.0.0.0:${PORT:-8080} wsgi:app`.
- `fly.toml` mappe 80/443 vers `internal_port=8080`; gardez la même valeur que le port d'écoute de Gunicorn.
- Les dossiers d'upload statiques sont créés dans l'image (`frontend/static/uploads/...`).

## Vérifications rapides
- Après déploiement : `fly logs` pour vérifier le démarrage, puis tester un envoi d'email (Gmail ou Brevo).
- Si email bloqué : vérifier les secrets (`MAIL_*`), les creds SMTP, et côté Brevo/Gmail (quota, anti-spam). 

## Variables à pousser en secrets (selon ton .env actuel)
Ne mets **aucun secret** dans ce fichier ni dans le dépôt. Prends les valeurs de ton `.env` et pousse-les via `fly secrets set` :
- `SECRET_KEY`
- `DATABASE_URL`
- SMTP : `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`, `MAIL_SUPPRESS_SEND`
- Supabase : `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_BUCKET`
- Optionnel temps réel : `ICE_STUN_URL`, `ICE_TURN_URL`, `ICE_TURN_USER`, `ICE_TURN_PASS`
- Boutique : `SHOP_NAME`, `SHOP_EMAIL`, `SHOP_PHONE`

Exemple (Gmail, mot de passe d'application) :
```
fly secrets set \
  SECRET_KEY=... \
  DATABASE_URL=... \
  MAIL_SERVER=smtp.gmail.com \
  MAIL_PORT=587 \
  MAIL_USE_TLS=True \
  MAIL_USE_SSL=False \
  MAIL_USERNAME=... \
  MAIL_PASSWORD=... \
  MAIL_DEFAULT_SENDER=... \
  MAIL_SUPPRESS_SEND=False \
  SUPABASE_URL=... \
  SUPABASE_KEY=... \
  SUPABASE_BUCKET=...
```
