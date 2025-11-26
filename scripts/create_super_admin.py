#!/usr/bin/env python3
"""
Script utilitaire pour créer un super-admin depuis le terminal.
Usage:
  - interactif: `./scripts/create_super_admin.py`
  - non-interactif: `./scripts/create_super_admin.py --email admin@example.com --first Jean --last Dupont --password secret123`

Ce script utilise la factory `create_app()` de `backend.apps` et crée la base (db.create_all()) si nécessaire.
Le fichier sqlite est créé là où pointe `SQLALCHEMY_DATABASE_URI` (par défaut `manga/database.db`).
"""
import os
import sys
import argparse
from getpass import getpass

# ajouter le dossier principal au PYTHONPATH
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.apps import create_app
from backend.models import db, User, ShopSettings


def parse_args():
    p = argparse.ArgumentParser(description='Créer un super-admin pour Manga Store')
    p.add_argument('--email', help='Email du super-admin')
    p.add_argument('--first', help='Prénom')
    p.add_argument('--last', help='Nom')
    p.add_argument('--password', help='Mot de passe (si absent, demandé en interactif)')
    return p.parse_args()


def interactive_prompts(args):
    first = args.first
    last = args.last
    email = args.email
    password = args.password

    if not first:
        first = input('Prénom du super admin: ').strip()
    if not last:
        last = input('Nom du super admin: ').strip()
    if not email:
        email = input('Email du super admin: ').strip()

    if not password:
        while True:
            pw = getpass('Mot de passe: ')
            pw2 = getpass('Confirmer le mot de passe: ')
            if pw != pw2:
                print('Les mots de passe ne correspondent pas, réessayez')
                continue
            if len(pw) < 6:
                print('Le mot de passe doit faire au moins 6 caractères')
                continue
            password = pw
            break

    return first, last, email, password


def main():
    args = parse_args()

    app = create_app()

    with app.app_context():
        # Création des tables si besoin
        db.create_all()

        # Vérifier s'il existe déjà un super admin
        existing = User.query.filter_by(is_super_admin=True).first()
        if existing:
            print(f"Un super-admin existe déjà: {existing.email}")
            return

        first, last, email, password = interactive_prompts(args)

        if User.query.filter_by(email=email).first():
            print('Erreur: un utilisateur avec cet email existe déjà')
            return

        user = User(
            email=email,
            first_name=first,
            last_name=last,
            is_admin=True,
            is_super_admin=True
        )
        user.set_password(password)
        db.session.add(user)

        # ajouter shopsettings si absent
        if not ShopSettings.query.first():
            db.session.add(ShopSettings())

        db.session.commit()
        print('\nSuper-admin créé avec succès:')
        print(f'  Email: {email}')
        print('  Mot de passe: (celui que vous avez saisi)')

        # afficher chemin réel de la DB
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
        if db_uri and db_uri.startswith('sqlite:'):
            path = db_uri.replace('sqlite:///', '')
        else:
            path = db_uri
        print(f"Base de données utilisée: {path}")


if __name__ == '__main__':
    main()
