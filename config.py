# config.py - Configuration Flask

import os
from dotenv import load_dotenv

load_dotenv()

# Base directory (chemin absolu du projet) pour créer des chemins par défaut
BASEDIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Configuration de base"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    # Par défaut la DB est créée dans le dossier du projet (manga/database.db)
    DEFAULT_SQLITE_PATH = os.path.join(BASEDIR, 'database.db')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', f'sqlite:///{DEFAULT_SQLITE_PATH}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = 'frontend/static/uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # Configuration Email
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_USERNAME')
    # Par défaut, autoriser l'envoi d'emails. Pour les environnements de dev,
    # vous pouvez mettre dans votre .env: MAIL_SUPPRESS_SEND=True
    MAIL_SUPPRESS_SEND = os.getenv('MAIL_SUPPRESS_SEND', 'False').lower() == 'true'
    
    # Configuration Boutique
    SHOP_NAME = os.getenv('SHOP_NAME', 'Manga Store')
    SHOP_EMAIL = os.getenv('SHOP_EMAIL', 'contact@mangastore.com')
    SHOP_PHONE = os.getenv('SHOP_PHONE', '+243000000000')
    BASE_CURRENCY = os.getenv('BASE_CURRENCY', 'USD')

    # WebRTC / TURN-STUN (remplir dans .env pour des appels fiables)
    ICE_STUN_URL = os.getenv('ICE_STUN_URL', 'stun:stun.l.google.com:19302')
    ICE_TURN_URL = os.getenv('ICE_TURN_URL')  # ex: turn:turn.example.com:3478
    ICE_TURN_USER = os.getenv('ICE_TURN_USER')
    ICE_TURN_PASS = os.getenv('ICE_TURN_PASS')

class DevelopmentConfig(Config):
    """Configuration développement"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Configuration production"""
    DEBUG = False
    TESTING = False

# Configuration par défaut
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
