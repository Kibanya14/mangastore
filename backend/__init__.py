# backend/__init__.py
from .apps import create_app, socketio
from .models import db

__all__ = ['create_app', 'db', 'socketio']
