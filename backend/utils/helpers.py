import json
import os
from flask import url_for, current_app


def get_first_image_filename(product):
    """Returne le nom de fichier de la première image du produit.
    Supporte deux formats possibles:
    - JSON list stored as string: '["a.png","b.png"]'
    - pipe-delimited string: 'a.png|b.png'
    Retourne None si aucune image disponible.
    """
    if not product:
        return None

    images = product.images
    if not images:
        return None

    # Tentative JSON
    try:
        parsed = json.loads(images)
        if isinstance(parsed, list) and parsed:
            return parsed[0]
    except Exception:
        pass

    # Fallback pipe-delimited
    try:
        if '|' in images:
            return images.split('|')[0]
        return images
    except Exception:
        return None


def get_first_image_url(product):
    """Retourne l'URL complète vers la première image produit si disponible, sinon None."""
    filename = get_first_image_filename(product)
    if not filename:
        return None
    # URL absolue fournie
    if filename.startswith('http://') or filename.startswith('https://'):
        return filename

    # Nettoyer les préfixes superflus
    cleaned = filename.lstrip('/')
    if cleaned.startswith('static/'):
        cleaned = cleaned[len('static/'):]

    # Si le chemin inclut déjà uploads/products, ne pas le dupliquer
    if cleaned.startswith('uploads/'):
        static_path = cleaned
    elif cleaned.startswith('products/'):
        static_path = os.path.join('uploads', cleaned)
    else:
        static_path = os.path.join('uploads', 'products', cleaned)

    try:
        return url_for('static', filename=static_path)
    except RuntimeError:
        # Pas de contexte d'application: retourner chemin relatif
        return os.path.join('/static', static_path)
