import os

# Désactiver greendns avant d'importer eventlet (évite les timeouts DNS SMTP)
os.environ.setdefault("EVENTLET_NO_GREENDNS", "yes")

import eventlet
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, send_file, current_app, session, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from backend.models import db, User, Product, Category, Cart, CartItem, Order, OrderItem, ShopSettings, AccessRequest, Deliverer, DeliveryAssignment, ForumMessage, ActivityLog
from flask_migrate import Migrate
from backend.utils import generate_invoice_pdf, generate_products_pdf
from backend.utils.helpers import get_first_image_url
from backend.utils.storage import upload_media
import logging
from logging.handlers import RotatingFileHandler
from flask_wtf import CSRFProtect
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from datetime import datetime, timedelta
import json
import secrets
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
import requests
from threading import Timer
from flask_socketio import SocketIO, emit, join_room, leave_room
from urllib.parse import urljoin
from collections import defaultdict
from types import SimpleNamespace

# Patch standard eventlet après avoir configuré ENV
eventlet.monkey_patch()

socketio = SocketIO(cors_allowed_origins="*", async_mode="eventlet")

def create_app():
    # Configuration des chemins
    base_dir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.dirname(base_dir)

    # Static config (overridable via .env)
    static_folder = os.getenv('STATIC_FOLDER')
    if static_folder and not os.path.isabs(static_folder):
        static_folder = os.path.join(project_root, static_folder)
    if not static_folder:
        static_folder = os.path.join(project_root, 'frontend', 'static')
    static_url_path = os.getenv('STATIC_URL_PATH', '/static')

    app = Flask(__name__,
                template_folder=os.path.join(project_root, 'frontend', 'templates'),
                static_folder=static_folder,
                static_url_path=static_url_path)
    
    # Charger configuration depuis config.py (respecte .env et permet PostgreSQL via DATABASE_URL)
    try:
        from config import config as config_map
        env = os.getenv('FLASK_ENV', 'default')
        app.config.from_object(config_map.get(env, config_map['default']))
    except Exception:
        # Fallback minimal values
        app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
        app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(project_root, 'database.db')}"
        app.config['BASE_CURRENCY'] = 'USD'
        app.config['STATIC_FOLDER'] = static_folder
        app.config['STATIC_URL_PATH'] = static_url_path

    # Conserver les chemins statiques en config pour usage ultérieur
    app.config.setdefault('STATIC_FOLDER', static_folder)
    app.config.setdefault('STATIC_URL_PATH', static_url_path)

    # Ensure UPLOAD_FOLDER is absolute path
    upload_folder = app.config.get('UPLOAD_FOLDER', 'frontend/static/uploads')
    if not os.path.isabs(upload_folder):
        upload_folder = os.path.join(project_root, upload_folder)
    app.config['UPLOAD_FOLDER'] = upload_folder

    # Max upload size
    app.config['MAX_CONTENT_LENGTH'] = app.config.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)
    
    # Initialisation des extensions
    db.init_app(app)
    Migrate(app, db)
    socketio.init_app(app, manage_session=True)

    # Ensure DB tables exist (create missing tables at startup)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            app.logger.warning(f"Impossible de créer les tables DB automatiquement: {e}")

    # CSRF protection
    csrf = CSRFProtect()
    csrf.init_app(app)
    
    # Login Manager principal
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'client_login'
    login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
    login_manager.login_message_category = 'error'

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        """Redirige vers la page de login appropriée selon l'espace (admin/livreur/client)."""
        path = request.path or ''
        if path.startswith('/admin'):
            flash('Veuillez vous connecter en tant qu\'administrateur.', 'error')
            return redirect(url_for('admin_login_page'))
        if path.startswith('/livreur'):
            flash('Veuillez vous connecter en tant que livreur.', 'error')
            return redirect(url_for('deliverer_login_page'))
        flash('Veuillez vous connecter pour accéder à cette page.', 'error')
        return redirect(url_for('client_login'))
    
    mail = Mail(app)

    def _week_bounds(ref_dt=None):
        """Retourne le début et la fin (UTC) de la semaine courante (lundi -> lundi)."""
        ref_dt = ref_dt or datetime.utcnow()
        start = datetime(ref_dt.year, ref_dt.month, ref_dt.day) - timedelta(days=ref_dt.weekday())
        end = start + timedelta(days=7)
        return start, end

    def _commission_for_amount(total_amount: float) -> float:
        """Calcule la commission de base selon le montant de commande."""
        try:
            total = float(total_amount or 0)
        except Exception:
            total = 0.0
        if total <= 25:
            return 3.0
        if total < 80:
            return 4.0
        return 4.0 + (0.02 * total)

    def _apply_commission(assignment: DeliveryAssignment):
        """Crédite la commission de base (+ bonus dimanche) pour une livraison terminée."""
        if assignment.commission_recorded or not assignment.deliverer or not assignment.order:
            return 0.0

        now = datetime.utcnow()
        commission = _commission_for_amount(assignment.order.total_amount)
        completed_dt = assignment.completed_at or now
        # Bonus 5% si livraison finalisée un dimanche
        try:
            if completed_dt.weekday() == 6:  # 0=lundi ... 6=dimanche
                commission += 0.05 * float(assignment.order.total_amount or 0)
        except Exception:
            pass
        assignment.commission_recorded = True
        assignment.completed_at = assignment.completed_at or now
        assignment.payout_status = assignment.payout_status or 'pending'
        assignment.deliverer.commission_due = (assignment.deliverer.commission_due or 0) + commission
        return commission

    def _assignment_commission(a: DeliveryAssignment):
        """Renvoie la commission (base + dimanche) pour une affectation livrée."""
        if not a.order:
            return 0.0
        base = _commission_for_amount(a.order.total_amount)
        sunday_bonus = 0.0
        try:
            ref = a.completed_at or a.order.delivered_at or datetime.utcnow()
            if ref.weekday() == 6:
                sunday_bonus = 0.05 * float(a.order.total_amount or 0)
        except Exception:
            sunday_bonus = 0.0
        return base + sunday_bonus

    def _weekly_bonus_total(assignments):
        """Calcule le total des bonus hebdomadaires (5$ par bloc de 8 livraisons) pour une liste d'affectations livrées."""
        weekly_counts = defaultdict(int)
        for a in assignments:
            if not a.completed_at:
                continue
            week_start, _ = _week_bounds(a.completed_at)
            weekly_counts[week_start] += 1
        total = 0.0
        for count in weekly_counts.values():
            total += (count // 8) * 5.0
        return total

    def _weekly_bonus_state(deliverer: Deliverer):
        """Calcule le nombre de livraisons de la semaine et les bonus disponibles/non payés."""
        week_start, week_end = _week_bounds()
        delivered_this_week = (DeliveryAssignment.query
                               .filter(DeliveryAssignment.deliverer_id == deliverer.id)
                               .filter(DeliveryAssignment.status == 'delivered')
                               .filter(DeliveryAssignment.completed_at >= week_start)
                               .filter(DeliveryAssignment.completed_at < week_end)
                               .count())
        bonuses_earned = delivered_this_week // 8
        paid_count = deliverer.weekly_bonus_paid_count if deliverer.last_bonus_week_start == week_start.date() else 0
        outstanding = max(0, bonuses_earned - paid_count)
        return {
            'week_start': week_start.date(),
            'deliveries': delivered_this_week,
            'bonuses_earned': bonuses_earned,
            'paid_count': paid_count,
            'outstanding': outstanding,
            'current_block_count': delivered_this_week % 8,
        }

    # Logging: fichier rotatif
    logs_dir = os.path.join(project_root, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    file_handler = RotatingFileHandler(os.path.join(logs_dir, 'app.log'), maxBytes=1024*1024*5, backupCount=3)
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    file_handler.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            if isinstance(user_id, str) and user_id.startswith('d:'):
                return Deliverer.query.get(int(user_id.split(':', 1)[1]))
            return User.query.get(int(str(user_id).split(':')[-1]))
        except Exception:
            return None

    @app.context_processor
    def inject_media_url():
        def media_url(path):
            if not path:
                return None
            path_str = str(path)
            if path_str.startswith(('http://', 'https://')):
                return path_str
            cleaned = path_str.lstrip('/')
            # Handle accidental prefix like "uploads/logos/https://..."
            if cleaned.startswith('uploads/') and '://' in cleaned:
                parts = cleaned.split('/', 2)
                if len(parts) >= 3 and parts[2].startswith(('http://', 'https://')):
                    return parts[2]
            return url_for('static', filename=cleaned)
        return dict(media_url=media_url)
    
    # === UTILITAIRES ===
    _rate_cache = {'data': {}, 'timestamp': 0}
    CATEGORY_ICON_CHOICES = [
        ("fas fa-heartbeat", "Santé / Général"),
        ("fas fa-flask", "Complément / Détox"),
        ("fas fa-bolt", "Énergie / Performance"),
        ("fas fa-apple-alt", "Nutrition / Alimentaire"),
        ("fas fa-dumbbell", "Sport / Fitness"),
        ("fas fa-brain", "Cerveau / Neuro"),
        ("fas fa-procedures", "Sommeil / Détente"),
        ("fas fa-spa", "Beauté / Soin"),
        ("fas fa-baby", "Croissance / Bébé"),
        ("fas fa-user-md", "Hypertension / Cardiologie"),
        ("fas fa-lungs", "Respiratoire"),
        ("fas fa-leaf", "Plantes / Nature"),
        ("fas fa-tablets", "Patch / Traitement"),
        ("fas fa-eye", "Vue"),
        ("fas fa-male", "Prostate / Masculin"),
        ("fas fa-female", "Féminité / Fertilité"),
        ("fas fa-burn", "Brûleur / Métabolisme"),
        ("fas fa-shield-alt", "Immunité"),
        ("fas fa-tint", "Reins / Détox"),
        ("fas fa-concierge-bell", "Services"),
        ("fas fa-chalkboard-teacher", "Formations"),
        ("fas fa-blender", "Électroménager"),
        ("fas fa-mobile-alt", "Mobiles"),
        ("fas fa-laptop", "Ordinateurs"),
    ]

    PERMISSION_LABELS = {
        'view_products': 'Voir produits',
        'manage_products': 'Gérer produits',
        'view_orders': 'Voir commandes',
        'manage_orders': 'Gérer commandes',
        'view_categories': 'Voir catégories',
        'manage_categories': 'Gérer catégories',
        'manage_admins': 'Gérer admins',
        'manage_deliverers': 'Gérer livreurs',
        'manage_settings': 'Gérer paramètres',
        'manage_clients': 'Gérer clients'
    }

    def _parse_permissions_field(raw: str):
        """Retourne une liste de permissions à partir d'une chaîne séparée par | ou ,."""
        if not raw:
            return []
        perms = []
        for token in raw.replace('|', ',').split(','):
            t = (token or '').strip()
            if t:
                perms.append(t)
        seen = set()
        uniq = []
        for p in perms:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    PERMISSION_LABELS = {
        'view_products': 'Voir produits',
        'manage_products': 'Gérer produits',
        'view_orders': 'Voir commandes',
        'manage_orders': 'Gérer commandes',
        'view_categories': 'Voir catégories',
        'manage_categories': 'Gérer catégories',
        'manage_admins': 'Gérer admins',
        'manage_deliverers': 'Gérer livreurs',
        'manage_settings': 'Gérer paramètres'
    }

    def _parse_permissions_field(raw: str):
        """Retourne une liste de permissions à partir d'une chaîne séparée par | ou ,."""
        if not raw:
            return []
        perms = []
        for token in raw.replace('|', ',').split(','):
            t = (token or '').strip()
            if t:
                perms.append(t)
        seen = set()
        uniq = []
        for p in perms:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    def status_fr_helper(status: str, kind: str = None) -> str:
        if not status:
            return ''
        status = str(status)
        common = {
            'pending': 'En attente',
            'confirmed': 'Confirmée',
            'shipped': 'Expédiée',
            'delivered': 'Livrée',
            'cancelled': 'Annulée',
            'assigned': 'Assignée',
            'in_progress': 'En cours',
            'postponed': 'Reportée',
            'busy': 'Occupé',
            'available': 'Disponible',
            'offline': 'Hors ligne'
        }
        mapping = common
        if kind == 'order':
            mapping = {**common}
        elif kind == 'assignment':
            mapping = {k: v for k, v in common.items() if k in ['assigned', 'in_progress', 'delivered', 'postponed', 'cancelled']}
        elif kind == 'deliverer':
            mapping = {k: v for k, v in common.items() if k in ['available', 'busy', 'offline']}
        return mapping.get(status, status)

    def generate_order_number():
        return f"CMD-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
    
    def _html_wrapper(title: str, body_text: str) -> str:
        """Construit un gabarit HTML homogène pour les emails."""
        safe_body = (body_text or "").replace('\n', '<br>')
        return f"""
        <div style="font-family: Arial, sans-serif; background:#f7f7fb; padding:20px; color:#333;">
            <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 14px rgba(0,0,0,0.08);">
                <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:18px 24px;">
                    <h2 style="margin:0;font-size:20px;">{title}</h2>
                    <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">Manga Store rdc</p>
                </div>
                <div style="padding:20px;font-size:14px;line-height:1.6;">
                    {safe_body}
                </div>
                <div style="padding:16px 24px;background:#f3f4f6;font-size:12px;color:#6b7280;text-align:center;">
                    © {datetime.now().year} Manga Store — Propulsé par Esperdigi
                </div>
            </div>
        </div>
        """

    def _build_app_url(path: str = '/') -> str:
        """Construit une URL absolue basée sur APP_BASE_URL (ou l'hôte courant)."""
        if not path:
            path = '/'
        if str(path).startswith(('http://', 'https://')):
            return path
        base = app.config.get('APP_BASE_URL')
        try:
            if not base:
                base = request.url_root
        except RuntimeError:
            base = None
        if not base:
            return path
        base = base.rstrip('/') + '/'
        return urljoin(base, str(path).lstrip('/'))

    def send_email(to, subject, body, html_body=None):
        """Fonction améliorée pour l'envoi d'emails (gabarit unifié)."""
        try:
            # Vérification de la configuration SMTP
            if not all([app.config['MAIL_SERVER'], app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD']]):
                print("⚠️ Configuration SMTP incomplète")
                return False
                
            msg = Message(
                subject=subject,
                sender=app.config['MAIL_DEFAULT_SENDER'],
                recipients=[to]
            )
            msg.body = body
            msg.html = html_body or _html_wrapper(subject, body)
            
            mail.send(msg)
            print(f"✅ Email envoyé à: {to}")
            return True
        except Exception as e:
            print(f"❌ Erreur envoi email à {to}: {str(e)}")
            return False

    # Password reset token helpers
    def _get_serializer():
        secret = app.config.get('SECRET_KEY')
        return URLSafeTimedSerializer(secret)

    def generate_password_reset_token(email):
        s = _get_serializer()
        return s.dumps(email, salt='password-reset-salt')

    def verify_password_reset_token(token, max_age=3600):
        s = _get_serializer()
        try:
            email = s.loads(token, salt='password-reset-salt', max_age=max_age)
            return email
        except SignatureExpired:
            return None
        except BadSignature:
            return None

    def send_password_reset_email(user):
        token = generate_password_reset_token(user.email)
        reset_url = _build_app_url(url_for('reset_password', token=token))
        subject = 'Réinitialisation du mot de passe - Manga Store'
        body = f"Bonjour {user.first_name},\n\nPour réinitialiser votre mot de passe, cliquez sur le lien suivant:\n{reset_url}\n\nSi vous n'avez pas demandé cette réinitialisation, ignorez ce message.\n"
        send_email(user.email, subject, body)

    def geocode_address(address: str):
        """Géocode une adresse via Nominatim. Retourne (lat, lon, formatted) ou (None, None, None) en cas d'échec."""
        if not address:
            return None, None, None
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1},
                headers={"User-Agent": "MangaStore rdc"},
                timeout=5
            )
            data = resp.json()
            if isinstance(data, list) and data:
                item = data[0]
                return float(item.get('lat')), float(item.get('lon')), item.get('display_name')
        except Exception as e:
            app.logger.warning(f"Echec géocodage adresse '{address}': {e}")
        return None, None, None

    def _deduct_stock_if_due(order_id: int):
        """Déduit le stock d'une commande livrée depuis au moins 1h si ce n'est pas déjà fait."""
        try:
            order = (Order.query
                     .options(joinedload(Order.items))
                     .get(order_id))
        except Exception as e:
            app.logger.error(f"Erreur chargement commande pour déduction stock: {e}")
            return False

        if not order or order.stock_deducted:
            return False
        if order.status != 'delivered':
            return False
        if not order.delivered_at:
            return False
        if datetime.utcnow() < order.delivered_at + timedelta(hours=1):
            return False

        try:
            for item in order.items:
                product = Product.query.get(item.product_id)
                if not product:
                    continue
                current_qty = product.quantity or 0
                product.quantity = max(0, current_qty - (item.quantity or 0))
            order.stock_deducted = True
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur déduction stock pour commande {order_id}: {e}")
            return False

    def schedule_stock_deduction(order):
        """Planifie la déduction du stock 1h après la livraison effective."""
        if not order or order.stock_deducted or order.status != 'delivered':
            return
        if not order.delivered_at:
            order.delivered_at = datetime.utcnow()
            db.session.flush()

        target = order.delivered_at + timedelta(hours=1)
        delay = max(0, (target - datetime.utcnow()).total_seconds())

        def _run():
            with app.app_context():
                _deduct_stock_if_due(order.id)

        timer = Timer(delay, _run)
        timer.daemon = True
        timer.start()

    def process_due_stock_deductions():
        """Sécurise la déduction du stock (fallback si un timer a été perdu)."""
        cutoff = datetime.utcnow() - timedelta(hours=1)
        try:
            due_orders = (Order.query
                          .options(joinedload(Order.items))
                          .filter(
                              Order.status == 'delivered',
                              Order.stock_deducted.is_(False),
                              Order.delivered_at.isnot(None),
                              Order.delivered_at <= cutoff
                          ).all())
            if not due_orders:
                return
            for order in due_orders:
                for item in order.items:
                    product = Product.query.get(item.product_id)
                    if not product:
                        continue
                    current_qty = product.quantity or 0
                    product.quantity = max(0, current_qty - (item.quantity or 0))
                order.stock_deducted = True
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur batch déduction stock: {e}")

    def revenue_cutoff():
        """Instant à partir duquel un chiffre d'affaires est reconnu (1h après livraison)."""
        return datetime.utcnow() - timedelta(hours=1)

    def is_revenue_eligible(order, cutoff=None):
        """Vérifie si une commande livrée peut être comptabilisée dans le CA."""
        if not order or order.status != 'delivered':
            return False
        ref = order.delivered_at or order.status_changed_at or order.updated_at or order.created_at
        if not ref:
            return False
        threshold = cutoff or revenue_cutoff()
        return ref <= threshold

    def compute_recognized_revenue():
        """Calcule le CA reconnu (commandes livrées depuis au moins 1h)."""
        cutoff = revenue_cutoff()
        try:
            revenue = (
                db.session.query(db.func.coalesce(db.func.sum(Order.total_amount), 0))
                .filter(
                    Order.status == 'delivered',
                    Order.delivered_at.isnot(None),
                    Order.delivered_at <= cutoff
                )
                .scalar()
            )
            return revenue or 0
        except Exception as e:
            app.logger.error(f"Erreur calcul chiffre d'affaires: {e}")
            return 0

    def get_cart_for_user(user_id):
        """Récupère ou crée un panier pour l'utilisateur"""
        cart = Cart.query.filter_by(user_id=user_id).first()
        if not cart:
            cart = Cart(user_id=user_id)
            db.session.add(cart)
            db.session.commit()
        return cart

    def get_cart_items_count(user_id):
        """Retourne le nombre d'articles dans le panier (agrégat SQL pour éviter le stale state)."""
        cart = Cart.query.filter_by(user_id=user_id).first()
        if not cart:
            return 0
        try:
            from sqlalchemy import func
            total_q = (db.session.query(func.coalesce(func.sum(CartItem.quantity), 0))
                       .filter_by(cart_id=cart.id)
                       .scalar())
            return int(total_q or 0)
        except Exception:
            # Fallback
            return sum(item.quantity for item in cart.items)

    def get_guest_cart():
        """Panier pour les visiteurs stocké en session: [{product_id, quantity}]."""
        try:
            data = session.get('guest_cart', [])
            if not isinstance(data, list):
                return []
            normalized = []
            for item in data:
                try:
                    pid = int(item.get('product_id'))
                    qty = int(item.get('quantity', 1))
                except Exception:
                    continue
                if qty <= 0:
                    continue
                normalized.append({'product_id': pid, 'quantity': qty})
            return normalized
        except Exception:
            return []

    def set_guest_cart(items):
        """Enregistre le panier invité et met à jour le badge."""
        try:
            session['guest_cart'] = items
            session['cart_count'] = sum(int(i.get('quantity', 0)) for i in items)
            session.modified = True
        except Exception:
            session['guest_cart'] = []
            session['cart_count'] = 0

    def build_guest_cart_items():
        """Construit les items de panier pour l'affichage invité avec les objets produit."""
        items = []
        total = 0
        guest_cart = get_guest_cart()
        if not guest_cart:
            return items, total

        try:
            product_ids = [entry['product_id'] for entry in guest_cart]
            products = Product.query.filter(Product.id.in_(product_ids)).all()
            product_map = {p.id: p for p in products}
        except Exception:
            product_map = {}

        cleaned = []
        for entry in guest_cart:
            product = product_map.get(entry.get('product_id'))
            if not product or not product.is_active:
                continue
            qty = min(entry.get('quantity', 1), max(product.quantity or 0, 0)) or 0
            if qty <= 0:
                continue
            cleaned.append({'product_id': product.id, 'quantity': qty})
            items.append(SimpleNamespace(id=product.id, product=product, quantity=qty))
            try:
                total += float(product.price) * qty
            except Exception:
                pass

        # Nettoyer le panier si des articles ont été supprimés ou ajustés
        if cleaned != guest_cart:
            set_guest_cart(cleaned)

        return items, total

    def merge_guest_cart_into_user(user):
        """Fusionne le panier invité dans le panier client après connexion."""
        if not user:
            return
        guest_cart = get_guest_cart()
        if not guest_cart:
            return
        try:
            cart = get_cart_for_user(user.id)
            product_ids = [entry['product_id'] for entry in guest_cart]
            products = Product.query.filter(Product.id.in_(product_ids)).all()
            product_map = {p.id: p for p in products}

            for entry in guest_cart:
                product = product_map.get(entry.get('product_id'))
                if not product or not product.is_active:
                    continue
                qty = min(entry.get('quantity', 1), max(product.quantity or 0, 0))
                if qty <= 0:
                    continue
                cart_item = CartItem.query.filter_by(cart_id=cart.id, product_id=product.id).first()
                if cart_item:
                    cart_item.quantity = min(cart_item.quantity + qty, max(product.quantity or 0, 0) or qty)
                else:
                    db.session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=qty))
            db.session.commit()
            set_guest_cart([])
            sync_cart_count()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Fusion panier invité échouée: {e}")

    def sync_cart_count():
        """Met à jour le compteur panier dans la session pour l'utilisateur courant."""
        try:
            if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
                session['cart_count'] = get_cart_items_count(current_user.id)
            else:
                session['cart_count'] = sum(int(i.get('quantity', 0)) for i in get_guest_cart())
        except Exception:
            session['cart_count'] = session.get('cart_count', 0)

    def require_permission(permission=None):
        """Decorator to require a specific permission for admin routes.

        - Super-admins bypass all checks.
        - Admins must have `is_admin` True and the requested permission in their `permissions`.
        - If `permission` is None, only `is_admin` is required (or super-admin).
        """
        def decorator(f):
            @wraps(f)
            def wrapped(*args, **kwargs):
                if not current_user.is_authenticated:
                    flash('Veuillez vous connecter pour accéder à cette page.', 'error')
                    return redirect(url_for('admin_login_page'))
                # Super admin bypass
                if current_user.is_super_admin:
                    return f(*args, **kwargs)
                # Must be an admin
                if not getattr(current_user, 'is_admin', False):
                    flash('Accès réservé aux administrateurs', 'error')
                    return redirect(url_for('index'))
                # If a specific permission is requested, check it
                if permission and not current_user.has_permission(permission):
                    flash('Accès refusé — permission manquante', 'error')
                    return redirect(url_for('admin_dashboard'))
                return f(*args, **kwargs)
            return wrapped
        return decorator

    def deliverer_required(f):
        """Protection pour les routes livreur."""
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not getattr(current_user, 'is_deliverer', False):
                flash('Accès réservé aux livreurs', 'error')
                return redirect(url_for('deliverer_login_page'))
            return f(*args, **kwargs)
        return wrapped

    def record_activity(action: str, actor=None, extra: str | None = None):
        """Enregistre une action (tâche) avec l'acteur et un complément facultatif."""
        try:
            actor_id = getattr(actor, 'id', None)
            actor_email = getattr(actor, 'email', None)
            first = getattr(actor, 'first_name', '') or ''
            last = getattr(actor, 'last_name', '') or ''
            actor_name = f"{first} {last}".strip() or None
            actor_phone = getattr(actor, 'phone', None)
            log_entry = ActivityLog(
                action=action,
                actor_id=actor_id,
                actor_email=actor_email,
                actor_name=actor_name,
                actor_phone=actor_phone,
                extra=extra
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            try:
                app.logger.warning(f"Impossible d'enregistrer l'activité '{action}': {exc}")
            except Exception:
                pass

    # NOTE: PDF generation functions are provided by backend.utils (generate_invoice_pdf,
    # generate_products_pdf) to avoid duplication and to centralize file path handling.
    @app.before_request
    def refresh_cart_badge():
        """Synchronise le compteur panier en session avant chaque requête pour le badge nav."""
        try:
            if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
                session['cart_count'] = get_cart_items_count(current_user.id)
            else:
                session['cart_count'] = sum(int(i.get('quantity', 0)) for i in get_guest_cart())
        except Exception:
            try:
                session['cart_count'] = sum(int(i.get('quantity', 0)) for i in get_guest_cart())
            except Exception:
                session['cart_count'] = session.get('cart_count', 0)
    
    # === CONTEXTE GLOBAL POUR TOUS LES TEMPLATES ===
    @app.context_processor
    def inject_global_vars():
        shop_settings = ShopSettings.query.first()
        # Compteur panier: prioriser le calcul DB pour les clients connectés, sinon fallback session
        cart_items_count = session.get('cart_count', 0)
        try:
            if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
                cart_items_count = get_cart_items_count(current_user.id)
                session['cart_count'] = cart_items_count
            else:
                cart_items_count = sum(int(i.get('quantity', 0)) for i in get_guest_cart())
                session['cart_count'] = cart_items_count
        except Exception:
            cart_items_count = session.get('cart_count', cart_items_count)
        try:
            cart_items_count = int(cart_items_count or 0)
        except Exception:
            cart_items_count = 0

        access_request_count = 0
        try:
            if current_user.is_authenticated and current_user.is_super_admin:
                access_request_count = AccessRequest.query.filter_by(status='pending').count()
        except Exception:
            access_request_count = 0

        forum_unread_count = 0
        try:
            if current_user.is_authenticated:
                last_seen = getattr(current_user, 'last_forum_seen_at', None)
                q = ForumMessage.query
                if last_seen:
                    q = q.filter(ForumMessage.created_at > last_seen)
                if getattr(current_user, 'is_deliverer', False):
                    q = q.filter(or_(ForumMessage.deliverer_id.is_(None), ForumMessage.deliverer_id != getattr(current_user, 'id', None)))
                else:
                    q = q.filter(or_(ForumMessage.user_id.is_(None), ForumMessage.user_id != getattr(current_user, 'id', None)))
                forum_unread_count = int(q.count())
        except Exception:
            forum_unread_count = 0

        pending_orders_count = 0
        try:
            if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
                pending_orders_count = Order.query.filter_by(status='pending').count()
        except Exception:
            pending_orders_count = 0

        # Fournir l'année courante pour les footers et templates
        try:
            current_year = datetime.now().year
        except Exception:
            current_year = 2025

        # Liste de devises supportées (peut être étendue)
        available_currencies = app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])

        # Déterminer la devise courante pour affichage: priorité - user.selected_currency, session, shop default
        current_currency = None
        try:
            if current_user.is_authenticated and getattr(current_user, 'selected_currency', None):
                current_currency = current_user.selected_currency
        except Exception:
            current_currency = None

        # If a non-authenticated user selected a currency stored in session, use it
        try:
            if not current_currency and session.get('currency'):
                current_currency = session.get('currency')
        except Exception:
            pass

        if not current_currency:
            # fallback to shop default or base currency
            current_currency = shop_settings.currency if shop_settings and shop_settings.currency else app.config.get('BASE_CURRENCY', 'USD')

        # Helper pour obtenir le taux de conversion entre deux devises (utilise exchangerate.host)
        def get_rate(from_currency: str, to_currency: str) -> float:
            if not from_currency or not to_currency or from_currency == to_currency:
                return 1.0
            # conversion fixe : 1 USD = 2200 CDF
            rates = {
                ('USD', 'CDF'): 2200.0,
                ('CDF', 'USD'): 1/2200.0
            }
            key = (from_currency.upper(), to_currency.upper())
            return rates.get(key, 1.0)

        base_currency = app.config.get('BASE_CURRENCY', 'USD')
        ice_servers = []
        stun_url = app.config.get('ICE_STUN_URL')
        if stun_url:
            ice_servers.append({'urls': stun_url})
        turn_url = app.config.get('ICE_TURN_URL')
        turn_user = app.config.get('ICE_TURN_USER')
        turn_pass = app.config.get('ICE_TURN_PASS')
        if turn_url and turn_user and turn_pass:
            ice_servers.append({'urls': turn_url, 'username': turn_user, 'credential': turn_pass})

        def convert_amount(amount: float, from_currency: str = None, to_currency: str = None) -> float:
            """Retourne le montant converti (float) sans formatage."""
            src = from_currency or base_currency
            dest = to_currency or current_currency or base_currency
            rate = get_rate(src, dest)
            try:
                return round(float(amount) * rate, 2)
            except Exception:
                return float(amount)

        def convert_price(amount: float, from_currency: str = None, to_currency: str = None) -> str:
            try:
                src = from_currency or base_currency
                dest = to_currency or current_currency or base_currency
                rate = get_rate(src, dest)
                converted = round(amount * rate, 2)
                # Format simple: code + amount
                return f"{dest} {converted:,.2f}".replace(',', ' ').replace('.', ',')
            except Exception:
                return f"{from_currency or base_currency} {amount:.2f}"

        return {
            'shop_settings': shop_settings,
            'cart_items_count': cart_items_count,
            'access_request_count': access_request_count,
            'get_first_image_url': get_first_image_url,
            'current_year': current_year,
            'available_currencies': available_currencies,
            'current_currency': current_currency,
            'convert_price': convert_price,
            'convert_amount': convert_amount,
            'base_currency': base_currency,
            'status_fr': status_fr_helper,
            'permission_labels': PERMISSION_LABELS,
            'current_user_id': getattr(current_user, 'id', None),
            'current_user_role': ('deliverer' if getattr(current_user, 'is_deliverer', False) else ('admin' if getattr(current_user, 'is_admin', False) else 'client')) if current_user.is_authenticated else None,
            'current_user_name': f"{getattr(current_user, 'first_name', '')} {getattr(current_user, 'last_name', '')}".strip() if current_user.is_authenticated else None,
            'ice_servers': ice_servers,
            'forum_unread_count': forum_unread_count,
            'pending_orders_count': pending_orders_count,
        }

    @app.route('/set-currency', methods=['POST'])
    def set_currency():
        currency = request.form.get('currency')
        if not currency:
            flash('Devise non fournie', 'error')
            return redirect(request.referrer or url_for('index'))
        available = app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])
        if currency not in available:
            flash('Devise non supportée', 'error')
            return redirect(request.referrer or url_for('index'))

        try:
            if current_user.is_authenticated:
                if getattr(current_user, 'is_deliverer', False):
                    session['currency'] = currency
                    flash(f'Devise interface livreur: {currency}', 'success')
                else:
                    current_user.selected_currency = currency
                    db.session.commit()
                    flash(f'Devise d\'affichage mise à jour: {currency}', 'success')
            else:
                session['currency'] = currency
                flash(f'Devise sélectionnée: {currency}', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur enregistrement devise: {e}")
            flash('Erreur lors de la mise à jour de la devise', 'error')

        return redirect(request.referrer or url_for('index'))

    # === ROUTE D'INITIALISATION (création web du super-admin si nécessaire) ===
    @app.route('/setup-admin', methods=['GET', 'POST'])
    def setup_admin():
        # Si un super admin existe déjà, rediriger
        existing = User.query.filter_by(is_super_admin=True).first()
        if existing:
            flash('Un super administrateur existe déjà. Veuillez vous connecter.', 'info')
            return redirect(url_for('admin_login_page'))

        if request.method == 'POST':
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()

            if not all([first_name, last_name, email, password]) or len(password) < 6:
                flash('Veuillez remplir correctement le formulaire (mot de passe >= 6 caractères).', 'error')
                return render_template('setup_admin.html')

            if User.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé', 'error')
                return render_template('setup_admin.html')

            super_admin = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                is_admin=True,
                is_super_admin=True
            )
            super_admin.set_password(password)
            try:
                db.session.add(super_admin)
                # Créer settings par défaut si absent
                if not ShopSettings.query.first():
                    db.session.add(ShopSettings())
                db.session.commit()

                # Envoi d'email de bienvenue (tentative)
                try:
                    admin_login_url = _build_app_url('admin')
                    send_email(
                        to=email,
                        subject='🎉 Bienvenue sur Manga Store - Super Admin',
                        body=f"Bonjour {first_name},\n\nVotre compte Super Administrateur a été créé avec succès.\n\nConnectez-vous: {admin_login_url}",
                        html_body=None
                    )
                except Exception as e:
                    app.logger.warning(f"Email non envoyé: {e}")

                flash('Super administrateur créé avec succès. Veuillez vous connecter.', 'success')
                return redirect(url_for('admin_login_page'))
            except Exception as e:
                db.session.rollback()
                app.logger.error(f'Erreur création super admin: {e}')
                flash('Erreur lors de la création du super administrateur', 'error')
                return render_template('setup_admin.html')

        return render_template('setup_admin.html')
    
    # === ROUTES CLIENT ===
    @app.route('/about')
    def about():
        return render_template('client/about.html')

    @app.route('/conditions-generales')
    def terms():
        return render_template('client/terms.html')

    @app.route('/retours-remboursements')
    def returns_policy():
        return render_template('client/returns.html')

    @app.route('/confidentialite')
    def privacy_policy():
        return render_template('client/privacy.html')
    
    @app.route('/')
    def index():
        products = Product.query.filter_by(is_active=True).limit(8).all()
        categories = Category.query.filter_by(is_active=True).all()
        return render_template('client/index.html', 
                             products=products, 
                             categories=categories)

    @app.route('/categories')
    def client_categories():
        try:
            page = int(request.args.get('page', 1))
            if page < 1:
                page = 1
        except Exception:
            page = 1
        per_page = 12
        query = Category.query.filter_by(is_active=True).order_by(Category.created_at.desc())
        total = query.count()
        categories = query.offset((page - 1) * per_page).limit(per_page).all()
        total_pages = (total // per_page) + (1 if total % per_page else 0)
        return render_template(
            'client/categories.html',
            categories=categories,
            page=page,
            total_pages=total_pages,
            total=total,
        )
    
    @app.route('/products')
    def products():
        category_id = request.args.get('category_id')
        search_term = request.args.get('q', '').strip()
        query = Product.query.filter_by(is_active=True)
        
        if category_id:
            query = query.filter_by(category_id=category_id)

        if search_term:
            like_pattern = f"%{search_term}%"
            query = query.filter(or_(Product.name.ilike(like_pattern), Product.description.ilike(like_pattern)))
        
        products = query.order_by(Product.created_at.desc()).all()
        categories = Category.query.filter_by(is_active=True).all()
        return render_template('client/products.html', 
                             products=products, 
                             categories=categories,
                             search_term=search_term)
    
    @app.route('/product/<int:product_id>')
    def product_detail(product_id):
        product = Product.query.get_or_404(product_id)
        if not product.is_active:
            flash('Produit non disponible', 'error')
            return redirect(url_for('products'))
        return render_template('client/product_detail.html', product=product)
    
    @app.route('/add_to_cart/<int:product_id>', methods=['POST'])
    def add_to_cart(product_id):
        # Empêcher les admins/livreurs d'ajouter au panier
        if current_user.is_authenticated and (getattr(current_user, 'is_admin', False) or getattr(current_user, 'is_deliverer', False)):
            flash('Cette fonctionnalité est réservée aux clients', 'error')
            return redirect(url_for('admin_dashboard') if getattr(current_user, 'is_admin', False) else url_for('index'))

        product = Product.query.get_or_404(product_id)
        try:
            quantity = int(request.form.get('quantity', 1))
        except Exception:
            quantity = 1
        
        if quantity <= 0:
            flash('Quantité invalide', 'error')
            return redirect(request.referrer or url_for('products'))
            
        if product.quantity < quantity:
            flash('Stock insuffisant', 'error')
            return redirect(request.referrer or url_for('products'))

        if current_user.is_authenticated:
            cart = get_cart_for_user(current_user.id)
            # Vérifier si le produit est déjà dans le panier
            cart_item = CartItem.query.filter_by(cart_id=cart.id, product_id=product_id).first()
            if cart_item:
                new_quantity = cart_item.quantity + quantity
                if new_quantity > product.quantity:
                    flash('Quantité demandée dépasse le stock disponible', 'error')
                    return redirect(request.referrer or url_for('products'))
                cart_item.quantity = new_quantity
            else:
                cart_item = CartItem(cart_id=cart.id, product_id=product_id, quantity=quantity)
                db.session.add(cart_item)
            db.session.commit()
        else:
            guest_cart = get_guest_cart()
            existing = next((i for i in guest_cart if i.get('product_id') == product_id), None)
            if existing:
                new_quantity = existing.get('quantity', 1) + quantity
                if new_quantity > product.quantity:
                    flash('Quantité demandée dépasse le stock disponible', 'error')
                    return redirect(request.referrer or url_for('products'))
                existing['quantity'] = new_quantity
            else:
                guest_cart.append({'product_id': product_id, 'quantity': quantity})
            set_guest_cart(guest_cart)
        
        sync_cart_count()
        flash(f'{product.name} ajouté au panier ({quantity})', 'success')
        return redirect(request.referrer or url_for('products'))
    
    @app.route('/cart')
    def cart():
        # Allow anonymous users to view the cart page. Only logged-in non-admin users have a persisted cart.
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('admin_dashboard'))

        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('deliverer_login_page'))

        cart_items = []
        total = 0

        if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
            cart = Cart.query.filter_by(user_id=current_user.id).first()
            if cart:
                cart_items = CartItem.query.filter_by(cart_id=cart.id).all()
                for item in cart_items:
                    total += item.product.price * item.quantity
        else:
            cart_items, total = build_guest_cart_items()

        return render_template('client/cart.html', cart_items=cart_items, total=total)
    
    @app.route('/update_cart/<int:item_id>', methods=['POST'])
    def update_cart(item_id):
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('admin_dashboard'))

        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('deliverer_login_page'))

        try:
            quantity = int(request.form.get('quantity', 1))
        except Exception:
            quantity = 1

        if current_user.is_authenticated:
            cart_item = CartItem.query.get_or_404(item_id)
            
            # Vérifier que l'article appartient bien à l'utilisateur
            if cart_item.cart.user_id != current_user.id:
                flash('Action non autorisée', 'error')
                return redirect(url_for('cart'))
                
            if quantity <= 0:
                db.session.delete(cart_item)
                flash('Article retiré du panier', 'success')
            else:
                if quantity > cart_item.product.quantity:
                    flash('Stock insuffisant', 'error')
                    return redirect(url_for('cart'))
                cart_item.quantity = quantity
                flash('Quantité mise à jour', 'success')
            
            db.session.commit()
        else:
            guest_cart = get_guest_cart()
            entry = next((i for i in guest_cart if i.get('product_id') == item_id), None)
            if not entry:
                flash('Article introuvable dans votre panier', 'error')
                return redirect(url_for('cart'))

            product = Product.query.get(item_id)
            if not product:
                guest_cart = [i for i in guest_cart if i.get('product_id') != item_id]
                set_guest_cart(guest_cart)
                flash('Produit indisponible retiré du panier', 'info')
                return redirect(url_for('cart'))

            if quantity <= 0:
                guest_cart = [i for i in guest_cart if i.get('product_id') != item_id]
                set_guest_cart(guest_cart)
                flash('Article retiré du panier', 'success')
            else:
                if quantity > product.quantity:
                    flash('Stock insuffisant', 'error')
                    return redirect(url_for('cart'))
                entry['quantity'] = quantity
                set_guest_cart(guest_cart)
                flash('Quantité mise à jour', 'success')

        sync_cart_count()
        return redirect(url_for('cart'))
    
    @app.route('/remove_from_cart/<int:item_id>')
    def remove_from_cart(item_id):
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('admin_dashboard'))

        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('deliverer_login_page'))

        if current_user.is_authenticated:
            cart_item = CartItem.query.get_or_404(item_id)
            
            if cart_item.cart.user_id != current_user.id:
                flash('Action non autorisée', 'error')
                return redirect(url_for('cart'))
                
            product_name = cart_item.product.name
            db.session.delete(cart_item)
            db.session.commit()
            flash(f'{product_name} retiré du panier', 'success')
        else:
            guest_cart = get_guest_cart()
            entry = next((i for i in guest_cart if i.get('product_id') == item_id), None)
            if not entry:
                flash('Article introuvable dans votre panier', 'error')
                return redirect(url_for('cart'))
            product = Product.query.get(item_id)
            product_name = product.name if product else 'Article'
            guest_cart = [i for i in guest_cart if i.get('product_id') != item_id]
            set_guest_cart(guest_cart)
            flash(f'{product_name} retiré du panier', 'success')

        sync_cart_count()
        return redirect(url_for('cart'))
    
    @app.route('/clear_cart')
    def clear_cart():
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('admin_dashboard'))

        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('deliverer_login_page'))
            
        if current_user.is_authenticated:
            cart = Cart.query.filter_by(user_id=current_user.id).first()
            if cart:
                CartItem.query.filter_by(cart_id=cart.id).delete()
                db.session.commit()
                flash('Panier vidé', 'success')
        else:
            set_guest_cart([])
            flash('Panier vidé', 'success')

        sync_cart_count()
        return redirect(url_for('cart'))
    
    @app.route('/checkout', methods=['GET', 'POST'])
    def checkout():
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('admin_dashboard'))

        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('deliverer_login_page'))

        is_client = current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False)

        def _prefill():
            data = {
                'first_name': '',
                'last_name': '',
                'email': '',
                'shipping_address': '',
                'phone': ''
            }
            if is_client:
                data.update({
                    'first_name': current_user.first_name or '',
                    'last_name': current_user.last_name or '',
                    'email': current_user.email or '',
                    'shipping_address': current_user.address or '',
                    'phone': current_user.phone or ''
                })
            else:
                saved = session.get('guest_checkout', {})
                for key in data:
                    data[key] = saved.get(key, data[key])
            return data

        def _load_cart():
            if is_client:
                cart_obj = Cart.query.filter_by(user_id=current_user.id).first()
                items = []
                total_amt = 0
                if cart_obj:
                    items = CartItem.query.filter_by(cart_id=cart_obj.id).all()
                    for item in items:
                        if item.product:
                            total_amt += item.product.price * item.quantity
                return items, total_amt, cart_obj
            items, total_amt = build_guest_cart_items()
            return items, total_amt, None

        prefill = _prefill()

        if request.method == 'POST':
            cart_items, total, cart_obj = _load_cart()
            if not cart_items:
                flash('Votre panier est vide', 'error')
                return redirect(url_for('cart'))

            shipping_address = request.form.get('shipping_address', prefill['shipping_address']).strip()
            shipping_lat = request.form.get('shipping_latitude')
            shipping_lon = request.form.get('shipping_longitude')
            shipping_geocoded = request.form.get('shipping_geocoded')
            first_name = request.form.get('first_name', prefill['first_name']).strip()
            last_name = request.form.get('last_name', prefill['last_name']).strip()
            email = request.form.get('email', prefill['email']).strip()
            phone = request.form.get('phone', prefill['phone']).strip()
            order_notes = request.form.get('order_notes', '').strip()

            prefill.update({
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'shipping_address': shipping_address,
                'phone': phone
            })
            if not is_client:
                session['guest_checkout'] = prefill

            if not all([first_name, last_name, email, phone, shipping_address]):
                flash('Veuillez compléter vos informations pour finaliser la commande.', 'error')
                return render_template('client/checkout.html', cart_items=cart_items, total=total, prefill=prefill)

            # Vérifier le stock et recalculer le total
            total = 0
            for item in cart_items:
                product = item.product
                if not product or not product.is_active:
                    flash('Un article n’est plus disponible et a été retiré du panier.', 'warning')
                    if is_client and cart_obj:
                        try:
                            db.session.delete(item)
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                    else:
                        # Nettoyer le panier invité
                        cleaned = [i for i in get_guest_cart() if i.get('product_id') != getattr(item.product, 'id', item.id)]
                        set_guest_cart(cleaned)
                    return redirect(url_for('cart'))
                if item.quantity > product.quantity:
                    flash(f'Stock insuffisant pour {product.name}', 'error')
                    return redirect(url_for('cart'))
                total += product.price * item.quantity

            # Déterminer l'utilisateur associé à la commande
            order_user = current_user if is_client else None
            created_guest_user = False
            if not is_client:
                existing_user = User.query.filter_by(email=email).first()
                if existing_user and (existing_user.is_admin or existing_user.is_super_admin):
                    flash('Veuillez utiliser une adresse email dédiée aux clients.', 'error')
                    return render_template('client/checkout.html', cart_items=cart_items, total=total, prefill=prefill)
                if existing_user:
                    order_user = existing_user
                    order_user.first_name = order_user.first_name or first_name
                    order_user.last_name = order_user.last_name or last_name
                    order_user.phone = order_user.phone or phone
                    order_user.address = order_user.address or shipping_address
                else:
                    order_user = User(
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        phone=phone,
                        address=shipping_address,
                        is_admin=False,
                        is_super_admin=False
                    )
                    order_user.set_password(secrets.token_urlsafe(12))
                    db.session.add(order_user)
                    db.session.flush()
                    created_guest_user = True

            try:
                order_number = generate_order_number()
                order = Order(
                    order_number=order_number,
                    user_id=order_user.id,
                    total_amount=total,
                    shipping_address=shipping_address,
                    billing_address=order_user.address or shipping_address,
                    status='pending',
                    status_changed_at=datetime.utcnow(),
                    stock_deducted=False,
                    delivered_at=None,
                    notes=order_notes or None
                )

                db.session.add(order)
                db.session.flush()

                lat = lon = None
                formatted = None
                try:
                    if shipping_lat and shipping_lon:
                        lat = float(shipping_lat)
                        lon = float(shipping_lon)
                        formatted = shipping_geocoded or shipping_address
                except Exception:
                    lat = lon = None

                if lat is None or lon is None:
                    lat, lon, formatted = geocode_address(shipping_address)

                if lat and lon:
                    order.shipping_latitude = lat
                    order.shipping_longitude = lon
                if formatted:
                    order.shipping_geocoded = formatted

                for item in cart_items:
                    product = Product.query.get(getattr(item.product, 'id', None))
                    if not product or product.quantity < item.quantity:
                        raise ValueError(f"Stock insuffisant pour {getattr(item.product, 'name', 'Produit')}")

                    order_item = OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        quantity=item.quantity,
                        price=product.price
                    )
                    db.session.add(order_item)

                if is_client and cart_obj:
                    CartItem.query.filter_by(cart_id=cart_obj.id).delete()
                    db.session.delete(cart_obj)
                else:
                    set_guest_cart([])
                    session.pop('guest_checkout', None)

                db.session.commit()
                sync_cart_count()
                record_activity(
                    f"Nouvelle commande #{order.order_number}",
                    actor=order_user,
                    extra=f"Total: {total} | Statut: {order.status} | Téléphone: {order_user.phone or '-'}"
                )
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Erreur lors de la création de la commande: {e}")
                flash('Erreur lors du traitement de la commande. Veuillez réessayer.', 'error')
                return render_template('client/checkout.html', cart_items=cart_items, total=total, prefill=prefill)

            # Garder une trace pour l'accès invité à la confirmation
            try:
                recent_orders = session.get('recent_orders', [])
                recent_orders = [oid for oid in recent_orders if isinstance(oid, int)]
                recent_orders.append(order.id)
                session['recent_orders'] = recent_orders[-5:]
                session.modified = True
            except Exception:
                pass

            try:
                send_email(
                    to=order_user.email,
                    subject=f'🎉 Confirmation de commande #{order.order_number}',
                    body=f"""
                    Bonjour {order_user.first_name},
                    
                    Votre commande #{order.order_number} a été enregistrée avec succès!
                    
                    📦 DÉTAILS DE LA COMMANDE:
                    Montant total: {order.total_amount} €
                    Articles: {len(order.items)}
                    Statut: En traitement
                    
                    🏠 ADRESSE DE LIVRAISON:
                    {order.shipping_address}
                    
                    Merci pour votre confiance!
                    
                    L'équipe Manga Store
                    """
                )
            except Exception as e:
                print(f"⚠️ Email non envoyé: {e}")

            if created_guest_user:
                flash('Compte invité créé automatiquement pour suivre votre commande.', 'info')
            flash('Commande passée avec succès! Un email de confirmation vous a été envoyé.', 'success')
            return redirect(url_for('order_confirmation', order_id=order.id))

        cart_items, total, _ = _load_cart()
        if not cart_items:
            flash('Votre panier est vide', 'error')
            return redirect(url_for('cart'))

        return render_template('client/checkout.html', cart_items=cart_items, total=total, prefill=prefill)
    
    @app.route('/order_confirmation/<int:order_id>')
    def order_confirmation(order_id):
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('admin_dashboard'))
            
        order = Order.query.get_or_404(order_id)

        allowed = False
        if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
            allowed = order.user_id == current_user.id
        if not allowed:
            try:
                recent_orders = session.get('recent_orders', [])
                allowed = order.id in recent_orders
            except Exception:
                allowed = False

        if not allowed:
            flash('Commande non trouvée', 'error')
            return redirect(url_for('index'))
        
        return render_template('client/order_confirmation.html', order=order)
    
    @app.route('/orders')
    @login_required
    def client_orders():
        if current_user.is_admin:
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('admin_dashboard'))
            
        orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
        return render_template('client/orders.html', orders=orders)
    
    # === AUTHENTIFICATION CLIENT ===
    
    @app.route('/register', methods=['GET', 'POST'])
    def client_register():
        if current_user.is_authenticated:
            if current_user.is_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
            
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            first_name = request.form.get('first_name')
            last_name = request.form.get('last_name')
            phone = request.form.get('phone', '')
            address = request.form.get('address', '')
            
            if not all([email, password, first_name, last_name]):
                flash('Veuillez remplir tous les champs obligatoires', 'error')
                return redirect(url_for('client_register'))
            
            # Un email ne peut pas être utilisé par un autre compte (unicité globale)
            if User.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé', 'error')
                return redirect(url_for('client_register'))
            
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                address=address,
                is_admin=False
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            record_activity("Inscription client", actor=user, extra=f"Tél: {phone or '-'} | Adresse: {address or '-'}")
            
            # Email de bienvenue
            try:
                shop_url = _build_app_url('/')
                send_email(
                    to=email,
                    subject='👋 Bienvenue sur Manga Store!',
                    body=f"""
                    Bonjour {first_name},
                    
                    Bienvenue sur Manga Store! Votre compte client a été créé avec succès.
                    
                    Vous pouvez maintenant:
                    • Parcourir notre collection de produits ({shop_url})
                    • Ajouter des articles à votre panier
                    • Passer des commandes
                    • Suivre vos achats
                    
                    Nous sommes ravis de vous compter parmi nos clients!
                    
                    L'équipe Manga Store
                    """
                )
            except Exception as e:
                print(f"⚠️ Email non envoyé: {e}")
            
            flash('Inscription réussie! Vous pouvez maintenant vous connecter.', 'success')
            return redirect(url_for('client_login'))
        
        return render_template('client/register.html')

    # === Password reset routes ===
    @app.route('/reset_password', methods=['GET', 'POST'])
    def reset_request():
        if request.method == 'POST':
            email = request.form.get('email')
            user = User.query.filter_by(email=email).first()
            if user:
                try:
                    send_password_reset_email(user)
                    flash('Email de réinitialisation envoyé si l\'email existe.', 'info')
                except Exception as e:
                    app.logger.error(f"Erreur envoi email reset: {e}")
                    flash('Erreur lors de l\'envoi de l\'email', 'error')
            else:
                # Do not reveal whether the email exists
                flash('Email de réinitialisation envoyé si l\'email existe.', 'info')

            return redirect(url_for('client_login'))

        return render_template('client/reset_request.html')

    # === Admin password reset (identical logic but redirects to admin login) ===
    @app.route('/admin/reset_password', methods=['GET', 'POST'])
    def admin_reset_request():
        if request.method == 'POST':
            email = request.form.get('email')
            user = User.query.filter_by(email=email).first()
            if user:
                try:
                    send_password_reset_email(user)
                    flash('Email de réinitialisation envoyé si l\'email existe.', 'info')
                except Exception as e:
                    app.logger.error(f"Erreur envoi email reset (admin): {e}")
                    flash('Erreur lors de l\'envoi de l\'email', 'error')
            else:
                flash('Email de réinitialisation envoyé si l\'email existe.', 'info')

            return redirect(url_for('admin_login_page'))

        return render_template('admin/reset_request.html')

    @app.route('/reset_password/<token>', methods=['GET', 'POST'])
    def reset_password(token):
        email = verify_password_reset_token(token)
        if not email:
            flash('Lien de réinitialisation invalide ou expiré', 'error')
            return redirect(url_for('reset_request'))

        user = User.query.filter_by(email=email).first()
        if not user:
            flash('Utilisateur non trouvé', 'error')
            return redirect(url_for('reset_request'))

        if request.method == 'POST':
            new_password = request.form.get('password')
            if not new_password or len(new_password) < 6:
                flash('Le mot de passe doit contenir au moins 6 caractères', 'error')
                return render_template('client/reset_password.html', token=token)

            user.set_password(new_password)
            db.session.commit()
            flash('Mot de passe réinitialisé avec succès. Vous pouvez vous connecter.', 'success')
            return redirect(url_for('client_login'))

        return render_template('client/reset_password.html', token=token)

    @app.route('/admin/reset_password/<token>', methods=['GET', 'POST'])
    def admin_reset_password(token):
        email = verify_password_reset_token(token)
        if not email:
            flash('Lien de réinitialisation invalide ou expiré', 'error')
            return redirect(url_for('admin_reset_request'))

        user = User.query.filter_by(email=email).first()
        if not user:
            flash('Utilisateur non trouvé', 'error')
            return redirect(url_for('admin_reset_request'))

        if request.method == 'POST':
            new_password = request.form.get('password')
            if not new_password or len(new_password) < 6:
                flash('Le mot de passe doit contenir au moins 6 caractères', 'error')
                return render_template('admin/reset_password.html', token=token)

            user.set_password(new_password)
            db.session.commit()
            flash('Mot de passe réinitialisé avec succès. Vous pouvez vous connecter.', 'success')
            return redirect(url_for('admin_login_page'))

        return render_template('admin/reset_password.html', token=token)
    
    @app.route('/login', methods=['GET', 'POST'])
    def client_login():
        if current_user.is_authenticated:
            if current_user.is_admin or current_user.is_super_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
            
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')

            user = User.query.filter_by(email=email, is_admin=False, is_super_admin=False).first()

            if user and user.is_active and user.check_password(password):
                login_user(user, remember=True)
                merge_guest_cart_into_user(user)
                flash(f'Bienvenue {user.first_name}!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('index'))
            elif user and not user.is_active:
                flash('Compte bloqué. Contactez un administrateur.', 'error')
                return redirect(url_for('client_login'))

            flash('Email ou mot de passe incorrect', 'error')
            return redirect(url_for('client_login'))
        
        return render_template('client/login.html')
    
    @app.route('/logout')
    def client_logout():
        logout_user()
        flash('Vous avez été déconnecté', 'success')
        return redirect(url_for('index'))
    
    @app.route('/profile')
    @login_required
    def client_profile():
        if current_user.is_admin or current_user.is_super_admin:
            flash('Accès réservé aux clients', 'error')
            return redirect(url_for('admin_dashboard'))
        return render_template('client/profile.html')

    @app.route('/profile/update', methods=['POST'])
    @login_required
    def update_profile():
        if current_user.is_admin or current_user.is_super_admin:
            flash('Action réservée aux clients', 'error')
            return redirect(url_for('admin_dashboard'))
            
        current_user.first_name = request.form.get('first_name', current_user.first_name)
        current_user.last_name = request.form.get('last_name', current_user.last_name)
        current_user.phone = request.form.get('phone', current_user.phone)
        current_user.address = request.form.get('address', current_user.address)
        
        db.session.commit()
        flash('Profil mis à jour avec succès', 'success')
        return redirect(url_for('client_profile'))

    @app.route('/profile/update_picture', methods=['POST'])
    @login_required
    def update_profile_picture():
        # Allow both clients and admins to update their profile picture
        if 'profile_picture' not in request.files:
            flash('Aucun fichier sélectionné', 'error')
            return redirect(url_for('client_profile'))

        file = request.files['profile_picture']
        if file and file.filename:
            filename = secure_filename(file.filename)
            name, ext = os.path.splitext(filename)
            ext = ext.lower().lstrip('.')
            allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
            if ext not in allowed:
                flash('Type de fichier non autorisé', 'error')
                return redirect(url_for('client_profile'))

            # Photo de profil: priorité Supabase, sinon sauvegarde locale
            uploaded_url = upload_media(file, 'uploads/profiles', logger=app.logger)
            if uploaded_url:
                current_user.profile_picture = uploaded_url
                db.session.commit()
                flash('Photo de profil mise à jour', 'success')
            else:
                dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles')
                os.makedirs(dest_dir, exist_ok=True)
                new_filename = f"profile_{current_user.id}_{int(datetime.now().timestamp())}.{ext}"
                path = os.path.join(dest_dir, new_filename)
                try:
                    file.save(path)
                    current_user.profile_picture = new_filename
                    db.session.commit()
                    flash('Photo de profil mise à jour', 'success')
                except Exception as e:
                    db.session.rollback()
                    app.logger.error(f"Erreur enregistrement photo profil: {e}")
                    flash('Erreur lors de l\'upload de la photo', 'error')

        return redirect(url_for('client_profile') if not current_user.is_admin else url_for('admin_profile'))

    @app.route('/profile/change_password', methods=['POST'])
    @login_required
    def change_password():
        current_pwd = request.form.get('current_password', '')
        new_pwd = request.form.get('new_password', '')
        confirm_pwd = request.form.get('confirm_password', '')

        if not current_user.check_password(current_pwd):
            flash('Le mot de passe actuel est incorrect', 'error')
            return redirect(url_for('client_profile'))

        if not new_pwd or len(new_pwd) < 6:
            flash('Le nouveau mot de passe doit contenir au moins 6 caractères', 'error')
            return redirect(url_for('client_profile'))

        if new_pwd != confirm_pwd:
            flash('Les mots de passe ne correspondent pas', 'error')
            return redirect(url_for('client_profile'))

        try:
            current_user.set_password(new_pwd)
            db.session.commit()
            flash('Mot de passe mis à jour avec succès', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur changement mot de passe: {e}")
            flash('Erreur lors du changement de mot de passe', 'error')

        return redirect(url_for('client_profile'))

    @app.route('/profile/delete_account', methods=['POST'])
    @login_required
    def delete_own_account():
        if current_user.is_admin or current_user.is_super_admin:
            flash('Action réservée aux clients.', 'error')
            return redirect(url_for('admin_dashboard'))

        password = request.form.get('current_password', '')
        confirm_text = request.form.get('confirm_delete', '').strip().upper()

        if not current_user.check_password(password):
            flash('Mot de passe incorrect.', 'error')
            return redirect(url_for('client_profile'))

        if confirm_text != 'SUPPRIMER':
            flash('Merci de taper "SUPPRIMER" pour confirmer la suppression.', 'error')
            return redirect(url_for('client_profile'))

        try:
            _delete_customer_account(current_user)
            db.session.commit()
            logout_user()
            flash('Votre compte a été supprimé avec succès.', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur suppression compte client {current_user.id}: {e}")
            flash('Erreur lors de la suppression du compte.', 'error')
            return redirect(url_for('client_profile'))

    @app.route('/order/<int:order_id>')
    @login_required
    def view_order(order_id):
        order = Order.query.get_or_404(order_id)
        # Admins are redirected to admin detail view
        if current_user.is_authenticated and (current_user.is_admin or current_user.is_super_admin):
            return redirect(url_for('admin_order_detail', order_id=order.id))

        # Allow only the owner to view
        if order.user_id != current_user.id:
            flash('Commande non trouvée', 'error')
            return redirect(url_for('client_profile'))

        # Reuse order confirmation/detail template for clients
        return redirect(url_for('order_confirmation', order_id=order.id))
    
    # === ROUTES ADMIN ===
    
    @app.route('/admin')
    def admin_login_page():
        if current_user.is_authenticated and (current_user.is_admin or current_user.is_super_admin):
            return redirect(url_for('admin_dashboard'))
        return render_template('admin/loginadmin.html')
    
    @app.route('/admin/login', methods=['POST'])
    def admin_login():
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter(
            User.email == email,
            (User.is_admin.is_(True) | User.is_super_admin.is_(True))
        ).first()

        # Seuls les comptes admin ou super_admin peuvent se connecter ici
        if not user or not (user.is_admin or user.is_super_admin):
            flash('Accès administrateur non autorisé', 'error')
            return redirect(url_for('admin_login_page'))

        # Super admin : bypass blocage pour éviter de se verrouiller soi-même
        if user and user.is_super_admin and user.check_password(password):
            login_user(user, remember=True)
            flash('Connexion super administrateur réussie!', 'success')
            return redirect(url_for('admin_dashboard'))

        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            flash('Connexion administrateur réussie!', 'success')
            return redirect(url_for('admin_dashboard'))
        elif user and not user.is_active:
            flash('Compte administrateur bloqué. Contactez un super admin.', 'error')
            return redirect(url_for('admin_login_page'))

        flash('Email ou mot de passe administrateur incorrect', 'error')
        return redirect(url_for('admin_login_page'))

    @app.route('/admin/about')
    @login_required
    def admin_about():
        if not (current_user.is_admin or current_user.is_super_admin):
            flash('Accès réservé aux administrateurs', 'error')
            return redirect(url_for('index'))
        return render_template('admin/about.html')

    @app.route('/admin/logout')
    @login_required
    def admin_logout():
        if not (current_user.is_admin or current_user.is_super_admin):
            flash('Accès non autorisé', 'error')
            return redirect(url_for('index'))
        logout_user()
        flash('Déconnexion administrateur réussie', 'success')
        return redirect(url_for('admin_login_page'))
    
    @app.route('/admin/dashboard')
    @login_required
    def admin_dashboard():
        if not (current_user.is_admin or current_user.is_super_admin):
            flash('Accès réservé aux administrateurs', 'error')
            return redirect(url_for('index'))

        recognized_revenue = compute_recognized_revenue()

        stats = {
            'total_products': Product.query.count(),
            'total_orders': Order.query.count(),
            'total_users': User.query.filter_by(is_admin=False).count(),
            'pending_orders': Order.query.filter_by(status='pending').count(),
            'total_revenue': recognized_revenue
        }
        
        # Commandes récentes
        recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
        
        return render_template('admin/dashboard.html', stats=stats, recent_orders=recent_orders)
    
    @app.route('/admin/products')
    @login_required
    @require_permission('view_products')
    def admin_products():
        search_term = request.args.get('q', '').strip()
        query = Product.query
        if search_term:
            like_pattern = f"%{search_term}%"
            query = query.filter(or_(Product.name.ilike(like_pattern), Product.description.ilike(like_pattern)))

        products = query.order_by(Product.created_at.desc()).all()
        categories = Category.query.all()
        return render_template('admin/products.html', products=products, categories=categories, search_term=search_term)
    
    @app.route('/admin/products/add', methods=['POST'])
    @login_required
    @require_permission('manage_products')
    def admin_add_product():
        try:
            name = request.form.get('name')
            description = request.form.get('description')
            price = float(request.form.get('price', 0))
            quantity = int(request.form.get('quantity', 0))
            category_id = int(request.form.get('category_id'))
            is_active_raw = request.form.get('is_active', 'true')
            is_active = str(is_active_raw).lower() in ('on', 'true', '1', 'yes')
            
            if not all([name, price >= 0, quantity >= 0]):
                flash('Veuillez remplir tous les champs correctement', 'error')
                return redirect(url_for('admin_products'))

            # Unicité nom produit (case-insensitive)
            existing = Product.query.filter(db.func.lower(Product.name) == name.lower()).first()
            if existing:
                flash('Un produit avec ce nom existe déjà.', 'error')
                return redirect(url_for('admin_products'))
            
            product = Product(
                name=name,
                description=description,
                price=price,
                quantity=quantity,
                category_id=category_id,
                is_active=is_active
            )

            # Gérer les images: upload multiple et/ou URLs
            image_entries = []
            # URLs (champ image_urls fournit des liens séparés par newline)
            image_urls_raw = request.form.get('image_urls', '').strip()
            if image_urls_raw:
                for line in image_urls_raw.splitlines():
                    u = line.strip()
                    if u:
                        image_entries.append(u)

            # Upload fichiers: d'abord Supabase via upload_media, sinon fallback disque local
            if 'images' in request.files:
                files = request.files.getlist('images')
                dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'products')
                os.makedirs(dest_dir, exist_ok=True)
                for f in files:
                    if f and f.filename:
                        filename = secure_filename(f.filename)
                        namef, ext = os.path.splitext(filename)
                        ext = ext.lower().lstrip('.')
                        allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
                        if ext in allowed:
                            uploaded_url = upload_media(f, 'uploads/products', logger=app.logger)
                            if uploaded_url:
                                image_entries.append(uploaded_url)
                                continue
                            new_filename = f"prod_{int(datetime.now().timestamp())}_{secrets.token_hex(6)}.{ext}"
                            path = os.path.join(dest_dir, new_filename)
                            try:
                                f.save(path)
                                # store relative path for static serving
                                image_entries.append(f"uploads/products/{new_filename}")
                            except Exception as e:
                                app.logger.warning(f"Erreur sauvegarde image produit: {e}")

            if image_entries:
                # stocker en tant que chaîne séparée par |
                product.images = '|'.join(image_entries)

            db.session.add(product)
            db.session.commit()
            
            flash('Produit ajouté avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de l\'ajout du produit', 'error')
            print(f"Erreur ajout produit: {e}")
        
        return redirect(url_for('admin_products'))
    
    @app.route('/admin/products/edit/<int:product_id>', methods=['POST'])
    @login_required
    @require_permission('manage_products')
    def admin_edit_product(product_id):
        product = Product.query.get_or_404(product_id)
        
        try:
            new_name = request.form.get('name', product.name)
            if new_name and new_name.lower() != (product.name or '').lower():
                dup = Product.query.filter(db.func.lower(Product.name) == new_name.lower(),
                                           Product.id != product.id).first()
                if dup:
                    flash('Un autre produit porte déjà ce nom.', 'error')
                    return redirect(url_for('admin_products'))
                product.name = new_name
            product.description = request.form.get('description', product.description)
            product.price = float(request.form.get('price', product.price))
            product.quantity = int(request.form.get('quantity', product.quantity))
            product.category_id = int(request.form.get('category_id', product.category_id))
            is_active_raw = request.form.get('is_active', 'false')
            product.is_active = str(is_active_raw).lower() in ('on', 'true', '1', 'yes')
            replace_images = request.form.get('replace_images') == 'on'
            # Gérer images additionnelles (URLs ou upload)
            image_entries = []
            if not replace_images and product.images:
                try:
                    existing = [i for i in product.images.split('|') if i]
                    image_entries.extend(existing)
                except Exception:
                    pass

            image_urls_raw = request.form.get('image_urls', '').strip()
            if image_urls_raw:
                for line in image_urls_raw.splitlines():
                    u = line.strip()
                    if u:
                        image_entries.append(u)

            # Upload fichiers: d'abord Supabase via upload_media, sinon fallback disque local
            if 'images' in request.files:
                files = request.files.getlist('images')
                dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'products')
                os.makedirs(dest_dir, exist_ok=True)
                for f in files:
                    if f and f.filename:
                        filename = secure_filename(f.filename)
                        namef, ext = os.path.splitext(filename)
                        ext = ext.lower().lstrip('.')
                        allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
                        if ext in allowed:
                            uploaded_url = upload_media(f, 'uploads/products', logger=app.logger)
                            if uploaded_url:
                                image_entries.append(uploaded_url)
                                continue
                            new_filename = f"prod_{int(datetime.now().timestamp())}_{secrets.token_hex(6)}.{ext}"
                            path = os.path.join(dest_dir, new_filename)
                            try:
                                f.save(path)
                                image_entries.append(f"uploads/products/{new_filename}")
                            except Exception as e:
                                app.logger.warning(f"Erreur sauvegarde image produit: {e}")

            if replace_images or image_urls_raw or ('images' in request.files and request.files.get('images')):
                # Si remplacement demandé ou nouvelles images fournies, on met à jour
                product.images = '|'.join(image_entries) if image_entries else None

            db.session.commit()
            flash('Produit modifié avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de la modification du produit', 'error')
            print(f"Erreur modification produit: {e}")
        
        return redirect(url_for('admin_products'))
    
    @app.route('/admin/products/delete/<int:product_id>')
    @login_required
    @require_permission('manage_products')
    def admin_delete_product(product_id):
        product = Product.query.get_or_404(product_id)
        
        try:
            # Vérifier si le produit est dans des commandes
            order_items = OrderItem.query.filter_by(product_id=product_id).first()
            if order_items:
                flash('Impossible de supprimer ce produit car il est associé à des commandes', 'error')
                return redirect(url_for('admin_products'))
                
            db.session.delete(product)
            db.session.commit()
            flash('Produit supprimé avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de la suppression du produit', 'error')
            print(f"Erreur suppression produit: {e}")
        
        return redirect(url_for('admin_products'))
    
    @app.route('/admin/orders')
    @login_required
    @require_permission('view_orders')
    def admin_orders():
        process_due_stock_deductions()
        status = request.args.get('status')
        query = Order.query
        if status:
            query = query.filter_by(status=status)
        orders = query.order_by(Order.created_at.desc()).all()
        return render_template('admin/orders.html', orders=orders)

    @app.route('/admin/clients')
    @login_required
    @require_permission()
    def admin_clients():
        clients = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()
        client_summaries = []
        cutoff = revenue_cutoff()

        for client in clients:
            orders = (
                Order.query
                .options(joinedload(Order.items).joinedload(OrderItem.product))
                .filter_by(user_id=client.id)
                .order_by(Order.created_at.desc())
                .all()
            )

            recognized_orders = [order for order in orders if is_revenue_eligible(order, cutoff)]
            total_spent = sum((order.total_amount or 0) for order in recognized_orders)
            product_counter = {}

            for order in recognized_orders:
                for item in order.items:
                    if not item.product:
                        continue
                    product_data = product_counter.get(item.product.id, {'name': item.product.name, 'quantity': 0})
                    product_data['quantity'] += item.quantity or 0
                    product_counter[item.product.id] = product_data

            top_products = sorted(product_counter.values(), key=lambda p: p['quantity'], reverse=True)

            client_summaries.append({
                'client': client,
                'orders': orders,
                'total_spent': total_spent,
                'top_products': top_products[:3]
            })

        total_clients = len(client_summaries)
        total_orders = sum(len(entry['orders']) for entry in client_summaries)
        total_revenue = sum(entry['total_spent'] for entry in client_summaries)

        return render_template(
            'admin/clients.html',
            client_summaries=client_summaries,
            total_clients=total_clients,
            total_orders=total_orders,
            total_revenue=total_revenue
        )

    @app.route('/admin/clients/<int:user_id>/block', methods=['POST'])
    @login_required
    @require_permission('manage_clients')
    def admin_block_client(user_id):
        client = User.query.filter_by(id=user_id, is_admin=False, is_super_admin=False).first_or_404()
        client.is_active = False
        db.session.commit()
        flash('Client bloqué.', 'success')
        return redirect(request.referrer or url_for('admin_clients'))

    @app.route('/admin/clients/<int:user_id>/unblock', methods=['POST'])
    @login_required
    @require_permission('manage_clients')
    def admin_unblock_client(user_id):
        client = User.query.filter_by(id=user_id, is_admin=False, is_super_admin=False).first_or_404()
        client.is_active = True
        db.session.commit()
        flash('Client débloqué.', 'success')
        return redirect(request.referrer or url_for('admin_clients'))

    def _delete_customer_account(client: User):
        """Supprime toutes les données liées à un client avant suppression."""
        carts = Cart.query.filter_by(user_id=client.id).all()
        for cart in carts:
            db.session.delete(cart)  # cascade vers cart_items

        orders = Order.query.filter_by(user_id=client.id).all()
        for order in orders:
            db.session.delete(order)  # cascade vers order_items et delivery_assignments

        ForumMessage.query.filter_by(user_id=client.id).delete(synchronize_session=False)
        AccessRequest.query.filter(
            (AccessRequest.admin_id == client.id) | (AccessRequest.processed_by == client.id)
        ).delete(synchronize_session=False)

        db.session.delete(client)

    @app.route('/admin/clients/<int:user_id>/delete', methods=['POST'])
    @login_required
    @require_permission('manage_clients')
    def admin_delete_client(user_id):
        flash('La suppression définitive doit être effectuée par le client depuis son profil.', 'error')
        return redirect(request.referrer or url_for('admin_clients'))

    @app.route('/admin/order/<int:order_id>')
    @login_required
    @require_permission('view_orders')
    def admin_order_detail(order_id):
        process_due_stock_deductions()
        order = Order.query.get_or_404(order_id)
        deliverers = Deliverer.query.filter_by(is_active=True).order_by(Deliverer.first_name).all()
        assignments = (DeliveryAssignment.query
                       .options(joinedload(DeliveryAssignment.deliverer))
                       .filter_by(order_id=order.id)
                       .order_by(DeliveryAssignment.created_at.desc())
                       .all())
        map_data = None
        if order.shipping_latitude and order.shipping_longitude:
            map_data = {
                'lat': order.shipping_latitude,
                'lon': order.shipping_longitude,
                'label': order.shipping_geocoded or order.shipping_address,
                'link': f"https://www.google.com/maps?q={order.shipping_latitude},{order.shipping_longitude}"
            }
        return render_template('admin/order_detail.html', order=order, map_data=map_data, deliverers=deliverers, assignments=assignments)
    
    @app.route('/admin/order/<int:order_id>/assign', methods=['POST'])
    @login_required
    @require_permission('manage_orders')
    def admin_assign_deliverer(order_id):
        order = Order.query.get_or_404(order_id)
        if order.status == 'delivered':
            flash('Impossible d\'assigner un livreur à une commande déjà livrée.', 'error')
            return redirect(url_for('admin_order_detail', order_id=order.id))
        deliverer_id = request.form.get('deliverer_id')
        note = request.form.get('note')
        deliverer = Deliverer.query.get(deliverer_id)

        if not deliverer:
            flash('Livreur introuvable', 'error')
            return redirect(url_for('admin_order_detail', order_id=order.id))

        assignment = DeliveryAssignment(
            order_id=order.id,
            deliverer_id=deliverer.id,
            status='assigned',
            note=note
        )
        db.session.add(assignment)
        db.session.commit()
        flash('Commande assignée au livreur', 'success')
        return redirect(url_for('admin_order_detail', order_id=order.id))

    @app.route('/admin/order/<int:order_id>/update_status', methods=['POST'])
    @login_required
    @require_permission('manage_orders')
    def admin_update_order_status(order_id):
        order = Order.query.get_or_404(order_id)
        new_status = request.form.get('status')
        
        if new_status in ['pending', 'confirmed', 'shipped', 'delivered', 'cancelled']:
            old_status = order.status
            # Verrouillage après 1h pour les non super-admins
            limit_ref = order.status_changed_at or order.updated_at or order.created_at
            if not current_user.is_super_admin and limit_ref and datetime.utcnow() - limit_ref > timedelta(hours=1):
                flash('Statut verrouillé après 1h. Seul le super admin peut le modifier.', 'error')
                return redirect(url_for('admin_orders'))

            order.status = new_status
            if new_status == 'delivered' and old_status != 'delivered':
                order.delivered_at = datetime.utcnow()
            elif old_status == 'delivered' and new_status != 'delivered' and not order.stock_deducted:
                order.delivered_at = None
            order.status_changed_at = datetime.utcnow()
            db.session.commit()

            # Créditer le livreur si la commande est livrée et qu'une affectation livrée existe
            if new_status == 'delivered':
                assignments = DeliveryAssignment.query.filter_by(order_id=order.id).all()
                for assign in assignments:
                    if assign.status == 'delivered' and not assign.commission_recorded:
                        _apply_commission(assign)
                db.session.commit()
                schedule_stock_deduction(order)
                _deduct_stock_if_due(order.id)
            
            # Envoyer un email de mise à jour si le statut change
            if old_status != new_status:
                try:
                    status_fr = {
                        'pending': 'en attente',
                        'confirmed': 'confirmée', 
                        'shipped': 'expédiée',
                        'delivered': 'livrée',
                        'cancelled': 'annulée'
                    }
                    
                    send_email(
                        to=order.customer.email,
                        subject=f'📦 Mise à jour de votre commande #{order.order_number}',
                        body=f"""
                        Bonjour {order.customer.first_name},
                        
                        Le statut de votre commande #{order.order_number} a été mis à jour.
                        Nouveau statut: {status_fr.get(new_status, new_status)}
                        
                        Merci pour votre confiance,
                        Manga Store
                        """
                    )
                except Exception as e:
                    print(f"⚠️ Erreur envoi email statut: {e}")
            
            flash('Statut de commande mis à jour', 'success')
        else:
            flash('Statut invalide', 'error')
        
        return redirect(url_for('admin_orders'))
    
    @app.route('/admin/categories')
    @login_required
    @require_permission('view_categories')
    def admin_categories():
        categories = Category.query.all()
        return render_template('admin/categories.html', categories=categories, category_icons=CATEGORY_ICON_CHOICES)

    @app.route('/admin/tasks')
    @login_required
    @require_permission()
    def admin_tasks():
        logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(300).all()
        return render_template('admin/tasks.html', logs=logs)
    
    @app.route('/admin/categories/add', methods=['POST'])
    @login_required
    @require_permission('manage_categories')
    def admin_add_category():
        try:
            name = request.form.get('name')
            description = request.form.get('description', '')
            is_active = request.form.get('is_active', 'true') == 'true'
            icon = request.form.get('icon') or None
            
            if not name:
                flash('Le nom de la catégorie est obligatoire', 'error')
                return redirect(url_for('admin_categories'))

            # Unicité nom (case-insensitive)
            existing = Category.query.filter(db.func.lower(Category.name) == name.lower()).first()
            if existing:
                flash('Cette catégorie existe déjà.', 'error')
                return redirect(url_for('admin_categories'))
            
            category = Category(
                name=name,
                description=description,
                is_active=is_active,
                icon=icon
            )
            
            db.session.add(category)
            db.session.commit()
            record_activity(f"Ajout catégorie '{name}'", actor=current_user, extra=f"Statut: {'active' if is_active else 'inactive'}")
            flash('Catégorie ajoutée avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de l\'ajout de la catégorie', 'error')
            print(f"Erreur ajout catégorie: {e}")
        
        return redirect(url_for('admin_categories'))
    
    @app.route('/admin/categories/edit/<int:category_id>', methods=['POST'])
    @login_required
    @require_permission('manage_categories')
    def admin_edit_category(category_id):
        category = Category.query.get_or_404(category_id)
        
        try:
            new_name = request.form.get('name', category.name)
            # Vérifier unicité si le nom change
            if new_name and new_name.lower() != (category.name or '').lower():
                dup = Category.query.filter(db.func.lower(Category.name) == new_name.lower(),
                                            Category.id != category.id).first()
                if dup:
                    flash('Une autre catégorie porte déjà ce nom.', 'error')
                    return redirect(url_for('admin_categories'))
                category.name = new_name
            category.description = request.form.get('description', category.description)
            is_active_raw = request.form.get('is_active')
            category.is_active = str(is_active_raw).lower() in ('on', 'true', '1', 'yes')
            category.icon = request.form.get('icon') or category.icon
            
            db.session.commit()
            record_activity(f"Modification catégorie '{category.name}'", actor=current_user, extra=f"Statut: {'active' if category.is_active else 'inactive'}")
            flash('Catégorie modifiée avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de la modification de la catégorie', 'error')
            print(f"Erreur modification catégorie: {e}")
        
        return redirect(url_for('admin_categories'))
    
    @app.route('/admin/categories/delete/<int:category_id>', methods=['POST'])
    @login_required
    @require_permission('manage_categories')
    def admin_delete_category(category_id):
        category = Category.query.get_or_404(category_id)
        
        try:
            # Vérifier si la catégorie a des produits
            if category.products:
                flash('Impossible de supprimer cette catégorie car elle contient des produits', 'error')
                return redirect(url_for('admin_categories'))
                
            db.session.delete(category)
            db.session.commit()
            record_activity(f"Suppression catégorie '{category.name}'", actor=current_user)
            flash('Catégorie supprimée avec succès', 'success')
        except Exception as e:
            flash('Erreur lors de la suppression de la catégorie', 'error')
            print(f"Erreur suppression catégorie: {e}")
        
        return redirect(url_for('admin_categories'))
    
    @app.route('/admin/settings', methods=['GET', 'POST'])
    @login_required
    def admin_settings():
        if not (current_user.is_super_admin or current_user.has_permission('manage_settings')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_dashboard'))
        
        def _get_rate(src: str, dest: str) -> float:
            """Convertit entre USD et CDF selon les taux fixes définis."""
            if not src or not dest or src == dest:
                return 1.0
            rates = {
                ('USD', 'CDF'): 2200.0,
                ('CDF', 'USD'): 1/2200.0
            }
            return rates.get((src.upper(), dest.upper()), 1.0)

        settings = ShopSettings.query.first()
        if not settings:
            settings = ShopSettings()
            db.session.add(settings)
            db.session.commit()
        
        if request.method == 'POST':
            try:
                old_currency = settings.currency or app.config.get('BASE_CURRENCY', 'USD')
                new_currency = request.form.get('currency', old_currency)
                shipping_cost_input = float(request.form.get('shipping_cost', 0))
                shipping_cost_out_input = float(request.form.get('shipping_cost_out', 0))

                # Convertir les frais si la devise a changé (ex: 6000 CDF -> USD)
                if old_currency != new_currency:
                    rate = _get_rate(old_currency, new_currency)
                    shipping_cost_input = round(shipping_cost_input * rate, 2)
                    shipping_cost_out_input = round(shipping_cost_out_input * rate, 2)

                settings.shop_name = request.form.get('shop_name')
                settings.shop_email = request.form.get('shop_email')
                settings.shop_phone = request.form.get('shop_phone')
                settings.shop_address = request.form.get('shop_address')
                settings.facebook_url = request.form.get('facebook_url')
                settings.whatsapp_number = request.form.get('whatsapp_number')
                settings.whatsapp_group_url = request.form.get('whatsapp_group_url')
                settings.currency = new_currency
                settings.tax_rate = float(request.form.get('tax_rate', 0))
                settings.shipping_cost = shipping_cost_input
                settings.shipping_cost_out = shipping_cost_out_input
                
                # Gestion du logo: upload_media (Supabase) sinon fallback disque local
                if 'shop_logo' in request.files:
                    logo = request.files['shop_logo']
                    if logo and logo.filename:
                        try:
                            # Créer le dossier logos s'il n'existe pas
                            logos_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'logos')
                            os.makedirs(logos_dir, exist_ok=True)

                            filename = secure_filename(logo.filename)
                            name, ext = os.path.splitext(filename)
                            ext = ext.lower().lstrip('.')
                            allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
                            if ext not in allowed:
                                flash('Type de fichier non autorisé pour le logo', 'error')
                            else:
                                uploaded_url = upload_media(logo, 'uploads/logos', logger=app.logger)
                                if uploaded_url:
                                    settings.shop_logo = uploaded_url
                                else:
                                    filename = f"logo_{int(datetime.now().timestamp())}.{ext}"
                                    logo_path = os.path.join(logos_dir, filename)
                                    logo.save(logo_path)
                                    settings.shop_logo = filename
                        except Exception as e:
                            app.logger.error(f"Erreur enregistrement logo: {e}")
                            flash('Erreur lors de l\'upload du logo', 'error')
                # Gestion du logo admin (interface admin)
                if 'admin_logo' in request.files:
                    admin_logo = request.files['admin_logo']
                    if admin_logo and admin_logo.filename:
                        try:
                            logos_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'logos')
                            os.makedirs(logos_dir, exist_ok=True)

                            filename = secure_filename(admin_logo.filename)
                            name, ext = os.path.splitext(filename)
                            ext = ext.lower().lstrip('.')
                            allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
                            if ext not in allowed:
                                flash('Type de fichier non autorisé pour le logo admin', 'error')
                            else:
                                uploaded_url = upload_media(admin_logo, 'uploads/logos', logger=app.logger)
                                if uploaded_url:
                                    settings.admin_logo = uploaded_url
                                else:
                                    filename = f"admin_logo_{int(datetime.now().timestamp())}.{ext}"
                                    logo_path = os.path.join(logos_dir, filename)
                                    admin_logo.save(logo_path)
                                    settings.admin_logo = filename
                        except Exception as e:
                            app.logger.error(f"Erreur enregistrement admin logo: {e}")
                            flash('Erreur lors de l\'upload du logo admin', 'error')

                # Gestion du logo livreur (interface livreur)
                if 'deliverer_logo' in request.files:
                    deliverer_logo = request.files['deliverer_logo']
                    if deliverer_logo and deliverer_logo.filename:
                        try:
                            logos_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'logos')
                            os.makedirs(logos_dir, exist_ok=True)

                            filename = secure_filename(deliverer_logo.filename)
                            name, ext = os.path.splitext(filename)
                            ext = ext.lower().lstrip('.')
                            allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
                            if ext not in allowed:
                                flash('Type de fichier non autorisé pour le logo livreur', 'error')
                            else:
                                uploaded_url = upload_media(deliverer_logo, 'uploads/logos', logger=app.logger)
                                if uploaded_url:
                                    settings.deliverer_logo = uploaded_url
                                else:
                                    filename = f"deliverer_logo_{int(datetime.now().timestamp())}.{ext}"
                                    logo_path = os.path.join(logos_dir, filename)
                                    deliverer_logo.save(logo_path)
                                    settings.deliverer_logo = filename
                        except Exception as e:
                            app.logger.error(f"Erreur enregistrement logo livreur: {e}")
                            flash('Erreur lors de l\'upload du logo livreur', 'error')
                        
                
                db.session.commit()
                flash('Paramètres mis à jour avec succès', 'success')
            except Exception as e:
                flash('Erreur lors de la mise à jour des paramètres', 'error')
                print(f"Erreur mise à jour paramètres: {e}")
            
            return redirect(url_for('admin_settings'))
        
        return render_template('admin/settings.html', settings=settings)
    
    @app.route('/admin/admins')
    @login_required
    def admin_manage_admins():
        if not (current_user.is_super_admin or current_user.has_permission('manage_admins')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_dashboard'))
        
        admins = User.query.filter_by(is_admin=True).all()
        return render_template('admin/admins.html', admins=admins)

    @app.route('/admin/admins/edit/<int:user_id>', methods=['POST'])
    @login_required
    def admin_edit_admin(user_id):
        if not (current_user.is_super_admin or current_user.has_permission('manage_admins')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_manage_admins'))

        admin = User.query.get_or_404(user_id)
        if admin.is_super_admin:
            flash('Impossible de modifier un super administrateur ici.', 'error')
            return redirect(url_for('admin_manage_admins'))

        try:
            admin.first_name = request.form.get('first_name', admin.first_name)
            admin.last_name = request.form.get('last_name', admin.last_name)
            new_email = request.form.get('email', admin.email).strip()
            if new_email != admin.email and User.query.filter_by(email=new_email).first():
                flash('Cet email est déjà utilisé.', 'error')
                return redirect(url_for('admin_manage_admins'))
            admin.email = new_email
            admin.phone = request.form.get('phone', admin.phone)
            perms = request.form.getlist('permissions')
            admin.permissions = ','.join(perms)
            db.session.commit()
            flash('Administrateur mis à jour.', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Erreur modification admin {admin.email}: {e}')
            flash('Erreur lors de la mise à jour.', 'error')

        return redirect(url_for('admin_manage_admins'))

    @app.route('/admin/deliverers')
    @login_required
    @require_permission('manage_deliverers')
    def admin_deliverers():
        deliverers = Deliverer.query.order_by(Deliverer.created_at.desc()).all()
        week_start, week_end = _week_bounds()

        weekly_progress = {}
        for d in deliverers:
            state = _weekly_bonus_state(d)
            count = state['deliveries']
            percent = min(100, int((state['current_block_count'] / 8) * 100)) if count else 0
            eligible = state['outstanding'] > 0
            weekly_progress[d.id] = {
                'count': count,
                'percent': percent,
                'eligible': eligible,
                'outstanding': state['outstanding'],
            }

        return render_template('admin/deliverers.html',
                               deliverers=deliverers,
                               weekly_progress=weekly_progress,
                               week_start=week_start.date())

    @app.route('/admin/deliverers/add', methods=['POST'])
    @login_required
    @require_permission('manage_deliverers')
    def admin_add_deliverer():
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')

        if not all([first_name, last_name, email]):
            flash('Prénom, nom et email sont requis', 'error')
            return redirect(url_for('admin_deliverers'))

        existing = Deliverer.query.filter_by(email=email).first()
        if existing:
            flash('Un livreur existe déjà avec cet email', 'error')
            return redirect(url_for('admin_deliverers'))

        password = secrets.token_hex(4)
        deliverer = Deliverer(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            is_active=True
        )
        deliverer.set_password(password)

        db.session.add(deliverer)
        db.session.commit()

        # Envoyer email avec identifiants
        try:
            deliverer_url = _build_app_url('livreur')
            send_email(
                to=email,
                subject='Vous êtes livreur Manga Store',
                body=(
                    f"Bonjour {first_name},\n\n"
                    f"Un compte livreur a été créé pour vous.\n"
                    f"Espace : {deliverer_url}\n"
                    f"Email : {email}\n"
                    f"Mot de passe temporaire : {password}\n\n"
                    "Merci de vous connecter et changer votre mot de passe dans votre profil."
                )
            )
        except Exception as e:
            app.logger.error(f"Erreur envoi email livreur: {e}")

        flash('Livreur créé et notifié par email', 'success')
        return redirect(url_for('admin_deliverers'))

    @app.route('/admin/deliverers/<int:deliverer_id>/edit', methods=['POST'])
    @login_required
    @require_permission('manage_deliverers')
    def admin_edit_deliverer(deliverer_id):
        deliverer = Deliverer.query.get_or_404(deliverer_id)
        deliverer.first_name = request.form.get('first_name', deliverer.first_name)
        deliverer.last_name = request.form.get('last_name', deliverer.last_name)
        new_email = request.form.get('email', deliverer.email)
        if new_email != deliverer.email:
            conflict = Deliverer.query.filter(Deliverer.email == new_email, Deliverer.id != deliverer.id).first()
            if conflict:
                flash('Un autre livreur utilise déjà cet email.', 'error')
                return redirect(url_for('admin_deliverers'))
            deliverer.email = new_email
        deliverer.phone = request.form.get('phone', deliverer.phone)
        deliverer.is_active = bool(request.form.get('is_active'))
        form_status = request.form.get('status')
        if form_status in ['available', 'busy', 'offline']:
            deliverer.status = form_status

        new_password = request.form.get('password')
        if new_password:
            deliverer.set_password(new_password)
        db.session.commit()
        flash('Livreur mis à jour', 'success')
        return redirect(url_for('admin_deliverers'))

    @app.route('/admin/deliverers/<int:deliverer_id>/delete', methods=['POST'])
    @login_required
    @require_permission('manage_deliverers')
    def admin_delete_deliverer(deliverer_id):
        deliverer = Deliverer.query.get_or_404(deliverer_id)
        try:
            db.session.delete(deliverer)
            db.session.commit()
            flash('Livreur supprimé', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur suppression livreur {deliverer.email}: {e}")
            flash('Erreur lors de la suppression du livreur', 'error')
        return redirect(url_for('admin_deliverers'))

    @app.route('/admin/deliverers/<int:deliverer_id>')
    @login_required
    @require_permission('manage_deliverers')
    def admin_view_deliverer(deliverer_id):
        deliverer = Deliverer.query.get_or_404(deliverer_id)
        assignments = (DeliveryAssignment.query
                       .options(joinedload(DeliveryAssignment.order))
                       .filter_by(deliverer_id=deliverer.id)
                       .order_by(DeliveryAssignment.created_at.desc())
                       .all())
        delivered = [a for a in assignments if a.status == 'delivered']
        pending_payout = [a for a in delivered if a.payout_status != 'paid']
        paid_assignments = [a for a in assignments if a.payout_status == 'paid']
        # Totaux commissions (base + bonus dimanche) hors bonus hebdo
        base_commission_total = sum(_assignment_commission(a) for a in delivered)
        weekly_bonus_total = _weekly_bonus_total(delivered)
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        delivered_month = [a for a in delivered if a.completed_at and a.completed_at >= month_start]
        monthly_base = sum(_assignment_commission(a) for a in delivered_month)
        monthly_weekly_bonus = _weekly_bonus_total(delivered_month)
        bonus_state = _weekly_bonus_state(deliverer)
        return render_template('admin/deliverer_view.html',
                               deliverer=deliverer,
                               assignments=assignments,
                               pending_payout=pending_payout,
                               paid_assignments=paid_assignments,
                               base_commission_total=base_commission_total,
                               weekly_bonus_total=weekly_bonus_total,
                               monthly_base=monthly_base,
                               monthly_weekly_bonus=monthly_weekly_bonus,
                               monthly_deliveries=len(delivered_month),
                               bonus_state=bonus_state)

    @app.route('/admin/deliverers/<int:deliverer_id>/payout', methods=['POST'])
    @login_required
    @require_permission('manage_deliverers')
    def admin_payout_deliverer(deliverer_id):
        deliverer = Deliverer.query.get_or_404(deliverer_id)
        to_pay = (DeliveryAssignment.query
                  .filter(DeliveryAssignment.deliverer_id == deliverer.id)
                  .filter(DeliveryAssignment.payout_status != 'paid')
                  .all())
        try:
            for a in to_pay:
                a.status = a.status or 'delivered'
                a.payout_status = 'paid'
                a.commission_recorded = True
                a.completed_at = a.completed_at or datetime.utcnow()
            # Remettre à zéro le solde dû
            deliverer.commission_due = 0.0
            db.session.commit()
            flash('Commission payée et historique mis à jour', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur payout livreur {deliverer.email}: {e}")
            flash('Erreur lors de la mise à jour des commissions', 'error')
        return redirect(url_for('admin_view_deliverer', deliverer_id=deliverer.id))

    @app.route('/admin/deliverers/<int:deliverer_id>/pay_weekly_bonus', methods=['POST'])
    @login_required
    @require_permission('manage_deliverers')
    def admin_pay_weekly_bonus(deliverer_id):
        deliverer = Deliverer.query.get_or_404(deliverer_id)
        state = _weekly_bonus_state(deliverer)
        if state['outstanding'] <= 0:
            flash('Aucun bonus en attente pour cette semaine.', 'info')
            return redirect(url_for('admin_deliverers'))

        try:
            payout = state['outstanding'] * 5.0
            deliverer.commission_due = (deliverer.commission_due or 0) + payout
            deliverer.last_bonus_week_start = state['week_start']
            deliverer.weekly_bonus_paid_count = state['bonuses_earned']
            db.session.commit()
            flash(f'Bonus hebdomadaire payé (+{payout:.2f}$).', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur paiement bonus hebdo livreur {deliverer.email}: {e}")
            flash('Erreur lors du paiement du bonus hebdomadaire.', 'error')

        return redirect(url_for('admin_deliverers'))

    # Helpers accessibles dans les templates
    app.jinja_env.globals['_assignment_commission'] = _assignment_commission

    @app.route('/admin/admins/delete/<int:user_id>', methods=['POST'])
    @login_required
    def admin_delete_admin(user_id):
        if not (current_user.is_super_admin or current_user.has_permission('manage_admins')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_manage_admins'))

        user = User.query.get_or_404(user_id)

        if user.is_super_admin:
            flash('Impossible de supprimer un super administrateur.', 'error')
            return redirect(url_for('admin_manage_admins'))

        try:
            db.session.delete(user)
            db.session.commit()
            flash('Administrateur supprimé avec succès.', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Erreur suppression admin {user.email}: {e}')
            flash('Erreur lors de la suppression de l\'administrateur.', 'error')

        return redirect(url_for('admin_manage_admins'))

    @app.route('/admin/admins/<int:user_id>')
    @login_required
    def admin_view_admin(user_id):
        if not (current_user.is_super_admin or current_user.has_permission('manage_admins')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_manage_admins'))

        admin = User.query.get_or_404(user_id)
        if not admin.is_admin:
            flash('Utilisateur non administrateur', 'error')
            return redirect(url_for('admin_manage_admins'))

        created_requests = AccessRequest.query.filter_by(admin_id=admin.id).order_by(AccessRequest.created_at.desc()).all()
        processed_requests = AccessRequest.query.filter_by(processed_by=admin.id).order_by(AccessRequest.processed_at.desc()).all()

        # Résumé simple faute de journal détaillé
        activity_summary = {
            'access_requests_created': len(created_requests),
            'access_requests_processed': len(processed_requests)
        }

        return render_template('admin/admin_view.html',
                               admin_user=admin,
                               created_requests=created_requests,
                               processed_requests=processed_requests,
                               activity_summary=activity_summary)


    def _map_feature_to_permission(feature_name: str):
        """Map a human-friendly feature string to an internal permission key."""
        s = feature_name.lower()
        if 'produit' in s or 'export' in s or 'exporter' in s:
            return 'manage_products'
        if 'catégor' in s or 'categorie' in s or 'catégorie' in s:
            return 'manage_categories'
        if 'commande' in s or 'statut' in s or 'facture' in s or 'order' in s:
            return 'manage_orders'
        if 'param' in s or 'setting' in s:
            return 'manage_settings'
        # fallback: return None
        return None


    @app.route('/admin/access-requests')
    @login_required
    def admin_access_requests():
        # Only super-admins can view/handle access requests
        if not current_user.is_super_admin:
            flash('Accès réservé aux super-administrateurs', 'error')
            return redirect(url_for('admin_dashboard'))

        requests_list = AccessRequest.query.order_by(AccessRequest.created_at.desc()).all()
        return render_template('admin/access_requests.html', requests=requests_list)


    @app.route('/admin/access-requests/<int:request_id>/process', methods=['POST'])
    @login_required
    def admin_process_access_request(request_id):
        if not current_user.is_super_admin:
            flash('Accès réservé aux super-administrateurs', 'error')
            return redirect(url_for('admin_dashboard'))

        action = request.form.get('action')  # approve or reject
        response_msg = request.form.get('response_message', '').strip()
        ar = AccessRequest.query.get_or_404(request_id)

        if ar.status != 'pending':
            flash('Cette demande a déjà été traitée', 'info')
            return redirect(url_for('admin_access_requests'))

        try:
            requested_perms = _parse_permissions_field(ar.feature)
            if action == 'approve':
                ar.status = 'approved'
                user = User.query.get(ar.admin_id)
                if user and requested_perms:
                    existing = user.permissions.split(',') if user.permissions else []
                    for perm in requested_perms:
                        if perm not in existing:
                            existing.append(perm)
                    user.permissions = ','.join([p for p in existing if p])
                    db.session.add(user)
                elif user:
                    # Grant inferred permission if possible (fallback legacy)
                    perm = _map_feature_to_permission(ar.feature)
                    if perm:
                        existing = user.permissions.split(',') if user.permissions else []
                        if perm not in existing:
                            existing.append(perm)
                            user.permissions = ','.join([p for p in existing if p])
                            db.session.add(user)
                flash('Demande approuvée', 'success')
            else:
                ar.status = 'rejected'
                flash('Demande rejetée', 'info')

            ar.processed_by = current_user.id
            ar.response_message = response_msg
            ar.processed_at = datetime.utcnow()
            db.session.add(ar)
            db.session.commit()

            # Notify requester by email
            try:
                requester = User.query.get(ar.admin_id)
                if requester and requester.email:
                    subj = f"Votre demande d'accès: {ar.feature} — {ar.status.capitalize()}"
                    body = f"Bonjour {requester.first_name},\n\nVotre demande d'accès à '{ar.feature}' a été {ar.status}.\n\nMessage: {response_msg}\n\nCordialement,\nL'équipe Manga Store"
                    send_email(requester.email, subj, body)
            except Exception as e:
                app.logger.warning(f"Erreur envoi notification demande accès: {e}")

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur traitement AccessRequest: {e}")
            flash('Erreur lors du traitement de la demande', 'error')

        return redirect(url_for('admin_access_requests'))

    # === ROUTES LIVREUR ===
    @app.route('/livreur')
    def deliverer_login_page():
        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            return redirect(url_for('deliverer_dashboard'))
        return render_template('deliverer/login.html')

    @app.route('/livreur/login', methods=['POST'])
    def deliverer_login():
        email = request.form.get('email')
        password = request.form.get('password')
        deliverer = Deliverer.query.filter_by(email=email, is_active=True).first()
        if deliverer and deliverer.check_password(password):
            login_user(deliverer)
            flash('Connexion livreur réussie', 'success')
            return redirect(url_for('deliverer_dashboard'))
        flash('Identifiants invalides', 'error')
        return redirect(url_for('deliverer_login_page'))

    @app.route('/livreur/logout')
    def deliverer_logout():
        if current_user.is_authenticated and getattr(current_user, 'is_deliverer', False):
            logout_user()
            flash('Déconnexion livreur réussie', 'success')
        return redirect(url_for('deliverer_login_page'))

    @app.route('/livreur/dashboard')
    @deliverer_required
    def deliverer_dashboard():
        assignments = (DeliveryAssignment.query
                       .options(joinedload(DeliveryAssignment.order).joinedload(Order.items),
                                joinedload(DeliveryAssignment.order).joinedload(Order.customer))
                       .filter_by(deliverer_id=current_user.id)
                       .order_by(DeliveryAssignment.created_at.desc())
                       .all())
        return render_template('deliverer/dashboard.html', assignments=assignments)

    @app.route('/livreur/status', methods=['POST'])
    @deliverer_required
    def deliverer_update_status():
        status = request.form.get('status')
        allowed = ['available', 'busy', 'offline']
        if status not in allowed:
            flash('Statut invalide.', 'error')
            return redirect(request.referrer or url_for('deliverer_dashboard'))
        current_user.status = status
        db.session.commit()
        flash(f"Statut mis à jour: {status_fr_helper(status, 'deliverer')}", 'success')
        return redirect(request.referrer or url_for('deliverer_dashboard'))

    @app.route('/livreur/assignments/<int:assignment_id>/status', methods=['POST'])
    @deliverer_required
    def deliverer_update_assignment(assignment_id):
        assignment = DeliveryAssignment.query.get_or_404(assignment_id)
        if assignment.deliverer_id != current_user.id:
            flash('Accès interdit', 'error')
            return redirect(url_for('deliverer_dashboard'))

        status = request.form.get('status')
        note = request.form.get('note')
        if status in ['assigned', 'in_progress', 'delivered', 'postponed', 'cancelled']:
            if not note or not note.strip():
                flash('La note est obligatoire et doit indiquer si les frais de livraison ont été perçus.', 'error')
                return redirect(request.referrer or url_for('deliverer_dashboard'))
            assignment.status = status
            assignment.note = note.strip()
            if status == 'delivered' and assignment.order.status == 'delivered' and not assignment.commission_recorded:
                _apply_commission(assignment)
            db.session.commit()
            flash('Statut mis à jour', 'success')
        else:
            flash('Statut invalide', 'error')
        return redirect(url_for('deliverer_dashboard'))

    @app.route('/livreur/profile', methods=['GET', 'POST'])
    @deliverer_required
    def deliverer_profile():
        if request.method == 'POST':
            current_user.first_name = request.form.get('first_name', current_user.first_name)
            current_user.last_name = request.form.get('last_name', current_user.last_name)
            current_user.phone = request.form.get('phone', current_user.phone)
            current_user.address = request.form.get('address', current_user.address)

            password = request.form.get('password')
            if password:
                current_user.set_password(password)

            # Photo de profil
            if 'profile_picture' in request.files:
                profile_pic = request.files['profile_picture']
                if profile_pic and profile_pic.filename:
                    filename = secure_filename(profile_pic.filename)
                    name, ext = os.path.splitext(filename)
                    ext = ext.lower().lstrip('.')
                    allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
                    if ext in allowed:
                        # Photo livreur: priorité Supabase, sinon sauvegarde locale
                        uploaded_url = upload_media(profile_pic, 'uploads/profiles', logger=app.logger)
                        if uploaded_url:
                            current_user.profile_picture = uploaded_url
                        else:
                            filename = f"livreur_{int(datetime.now().timestamp())}.{ext}"
                            dest = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles')
                            os.makedirs(dest, exist_ok=True)
                            profile_pic.save(os.path.join(dest, filename))
                            current_user.profile_picture = filename

            db.session.commit()
            flash('Profil mis à jour', 'success')
            return redirect(url_for('deliverer_profile'))

        now = datetime.utcnow()
        month_start = datetime(now.year, now.month, 1)
        delivered_qs = (DeliveryAssignment.query
                        .options(joinedload(DeliveryAssignment.order))
                        .filter_by(deliverer_id=current_user.id, status='delivered')
                        .filter(DeliveryAssignment.completed_at >= month_start)
                        .all())

        # Commission du mois (base + dimanche + bonus hebdo par tranche de 8)
        commission_this_month = 0.0
        weekly_counts = defaultdict(int)
        for a in delivered_qs:
            commission_this_month += _assignment_commission(a)
            if a.completed_at:
                week_start, _ = _week_bounds(a.completed_at)
                weekly_counts[week_start] += 1
        for week_start, count in weekly_counts.items():
            commission_this_month += (count // 8) * 5.0

        monthly_stats = {
            'deliveries_this_month': len(delivered_qs),
            'commission_this_month': commission_this_month
        }

        # Historique commissions payées par mois (base + bonus hebdo)
        paid_assignments = (DeliveryAssignment.query
                            .options(joinedload(DeliveryAssignment.order))
                            .filter_by(deliverer_id=current_user.id, payout_status='paid')
                            .order_by(DeliveryAssignment.completed_at.desc())
                            .all())
        commission_history = defaultdict(float)
        weekly_paid_counts = defaultdict(int)
        month_counts = defaultdict(int)
        for a in paid_assignments:
            key_month = a.completed_at.strftime('%Y-%m') if a.completed_at else 'N/A'
            commission_history[key_month] += _assignment_commission(a)
            month_counts[key_month] += 1
            if a.completed_at:
                week_start, _ = _week_bounds(a.completed_at)
                weekly_paid_counts[(week_start, key_month)] += 1
        for (week_start, key_month), count in weekly_paid_counts.items():
            commission_history[key_month] += (count // 8) * 5.0

        history_list = [{'month': k, 'amount': v, 'count': month_counts.get(k, 0)} for k, v in commission_history.items()]
        history_list.sort(key=lambda x: x['month'], reverse=True)

        return render_template('deliverer/profile.html', monthly_stats=monthly_stats, commission_history=history_list)

    @app.route('/livreur/about')
    @deliverer_required
    def deliverer_about():
        return render_template('deliverer/about.html')

    @app.route('/admin/profile', methods=['GET', 'POST'])
    @login_required
    def admin_profile():
        if not current_user.is_admin:
            flash('Accès réservé aux administrateurs', 'error')
            return redirect(url_for('admin_login_page'))

        if request.method == 'POST':
            # Mettre à jour les informations de profil de l'admin
            current_user.first_name = request.form.get('first_name', current_user.first_name)
            current_user.last_name = request.form.get('last_name', current_user.last_name)
            new_email = request.form.get('email', current_user.email).strip()
            if new_email != current_user.email:
                # Vérifier unicité
                if User.query.filter_by(email=new_email).first():
                    flash('Cet email est déjà utilisé', 'error')
                    return redirect(url_for('admin_profile'))
                current_user.email = new_email

            current_user.phone = request.form.get('phone', current_user.phone)
            current_user.address = request.form.get('address', current_user.address)

            try:
                db.session.commit()
                flash('Profil administrateur mis à jour', 'success')
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Erreur mise à jour profil admin: {e}")
                flash('Erreur lors de la mise à jour', 'error')

            return redirect(url_for('admin_profile'))

        # Pré-calculer les statistiques à afficher dans la vue profil
        try:
            product_count = Product.query.count()
        except Exception:
            product_count = 0

        try:
            order_count = Order.query.count()
        except Exception:
            order_count = 0

        try:
            user_count = User.query.filter_by(is_admin=False).count()
        except Exception:
            user_count = 0

        try:
            pending_count = Order.query.filter_by(status='pending').count()
        except Exception:
            pending_count = 0

        try:
            current_perms = set(_parse_permissions_field(current_user.permissions))
            missing_permissions = [p for p in PERMISSION_LABELS.keys() if p not in current_perms] if not current_user.is_super_admin else []
        except Exception:
            missing_permissions = []

        try:
            my_requests = (AccessRequest.query
                           .filter_by(admin_id=current_user.id)
                           .order_by(AccessRequest.created_at.desc())
                           .all())
        except Exception:
            my_requests = []

        return render_template('admin/profile.html', 
                               product_count=product_count,
                               order_count=order_count,
                               user_count=user_count,
                               pending_count=pending_count,
                               my_requests=my_requests,
                               missing_permissions=missing_permissions)

    def _forum_author_data():
        """Construit les infos auteur pour le forum en fonction du rôle courant."""
        role = 'deliverer' if getattr(current_user, 'is_deliverer', False) else ('admin' if current_user.is_admin else 'client')
        name = f"{getattr(current_user, 'first_name', '')} {getattr(current_user, 'last_name', '')}".strip() or current_user.email
        avatar = None
        try:
            pic = getattr(current_user, 'profile_picture', None)
            if pic:
                avatar = pic if (str(pic).startswith('http://') or str(pic).startswith('https://')) else url_for('static', filename='uploads/profiles/' + pic)
        except Exception:
            avatar = None
        user_id = getattr(current_user, 'id', None)
        return role, name, avatar, user_id

    def _forum_online_users():
        """Affiche uniquement l'utilisateur connecté comme présent."""
        online = []
        try:
            role, name, avatar, uid = _forum_author_data()
            online.append({
                'name': name,
                'role': role,
                'status': 'En ligne',
                'avatar': avatar,
                'is_me': True,
                'user_id': uid
            })
        except Exception:
            pass
        return online

    @app.route('/forum', methods=['GET', 'POST'])
    @login_required
    def forum():
        """Espace forum communautaire (clients/admins/livreurs)."""
        if request.method == 'POST':
            action = (request.form.get('action') or '').strip().lower()
            msg_id = request.form.get('message_id')

            def _owns_message(message):
                if not message:
                    return False
                try:
                    if getattr(current_user, 'is_super_admin', False):
                        return True
                    if getattr(current_user, 'is_admin', False) and message.role != 'client':
                        return True
                    if getattr(current_user, 'is_deliverer', False) and message.deliverer_id == getattr(current_user, 'id', None):
                        return True
                    if not getattr(current_user, 'is_deliverer', False) and message.user_id == getattr(current_user, 'id', None):
                        return True
                except Exception:
                    return False
                return False

            # Delete message
            if action == 'delete' and msg_id:
                msg = ForumMessage.query.get(msg_id)
                if not _owns_message(msg):
                    flash('Action non autorisée.', 'error')
                    return redirect(request.referrer or url_for('forum'))
                try:
                    db.session.delete(msg)
                    db.session.commit()
                    flash('Message supprimé.', 'success')
                except Exception as e:
                    db.session.rollback()
                    app.logger.error(f"Erreur suppression message forum: {e}")
                    flash('Erreur lors de la suppression du message.', 'error')
                return redirect(request.referrer or url_for('forum'))

            # Edit message
            if action == 'edit' and msg_id:
                msg = ForumMessage.query.get(msg_id)
                new_content = (request.form.get('content') or '').strip()
                if not _owns_message(msg):
                    flash('Action non autorisée.', 'error')
                    return redirect(request.referrer or url_for('forum'))
                if not new_content:
                    flash('Le message ne peut pas être vide.', 'error')
                    return redirect(request.referrer or url_for('forum'))
                try:
                    msg.content = new_content
                    db.session.commit()
                    flash('Message modifié.', 'success')
                except Exception as e:
                    db.session.rollback()
                    app.logger.error(f"Erreur modification message forum: {e}")
                    flash('Erreur lors de la modification du message.', 'error')
                return redirect(request.referrer or url_for('forum'))

            content = (request.form.get('content') or '').strip()
            file = request.files.get('attachment')

            if (not content) and (not file or not file.filename):
                flash('Ajoutez un message ou une pièce jointe.', 'error')
                return redirect(request.referrer or url_for('forum'))

            attachment_path = None
            attachment_type = None

            if file and file.filename:
                filename = secure_filename(file.filename)
                name, ext = os.path.splitext(filename)
                ext = ext.lower().lstrip('.')
                allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp3', 'wav', 'ogg', 'mp4', 'webm', 'mov'}
                if ext not in allowed:
                    flash('Format non supporté. Images, audio ou vidéo courte uniquement.', 'error')
                    return redirect(request.referrer or url_for('forum'))
                # taille max ~15MB
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(0)
                if size > 15 * 1024 * 1024:
                    flash('Fichier trop volumineux (max 15MB).', 'error')
                    return redirect(request.referrer or url_for('forum'))
                # Pièce jointe forum: priorité Supabase, sinon fallback disque local
                uploaded_url = upload_media(file, 'uploads/forum', logger=app.logger, resource_type="auto")
                if uploaded_url:
                    attachment_path = uploaded_url
                else:
                    dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'forum')
                    os.makedirs(dest_dir, exist_ok=True)
                    new_filename = f"forum_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}.{ext}"
                    path = os.path.join(dest_dir, new_filename)
                    try:
                        file.save(path)
                        attachment_path = f"uploads/forum/{new_filename}"
                    except Exception as e:
                        app.logger.error(f"Erreur upload fichier forum: {e}")
                        flash('Impossible de sauvegarder le fichier.', 'error')
                        return redirect(request.referrer or url_for('forum'))
                if ext in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
                    attachment_type = 'image'
                elif ext in {'mp3', 'wav', 'ogg'}:
                    attachment_type = 'audio'
                elif ext in {'mp4', 'webm', 'mov'}:
                    attachment_type = 'video'
                else:
                    attachment_type = 'file'

            role = 'deliverer' if getattr(current_user, 'is_deliverer', False) else ('admin' if current_user.is_admin else 'client')
            msg = ForumMessage(
                user_id=None if getattr(current_user, 'is_deliverer', False) else current_user.id,
                deliverer_id=current_user.id if getattr(current_user, 'is_deliverer', False) else None,
                role=role,
                content=content,
                attachment_path=attachment_path,
                attachment_type=attachment_type
            )
            try:
                db.session.add(msg)
                db.session.commit()
                flash('Message publié', 'success')
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Erreur publication forum: {e}")
                flash('Erreur lors de l\'envoi du message', 'error')
            return redirect(url_for('forum'))

        messages = ForumMessage.query.order_by(ForumMessage.created_at.desc()).limit(100).all()
        online_users = _forum_online_users()
        template = 'client/forum.html'
        if getattr(current_user, 'is_deliverer', False):
            template = 'deliverer/forum.html'
        elif getattr(current_user, 'is_admin', False):
            template = 'admin/forum.html'

        try:
            latest = messages[0].created_at if messages else None
            if latest:
                if getattr(current_user, 'is_deliverer', False):
                    if not current_user.last_forum_seen_at or current_user.last_forum_seen_at < latest:
                        current_user.last_forum_seen_at = latest
                        db.session.commit()
                else:
                    if not current_user.last_forum_seen_at or current_user.last_forum_seen_at < latest:
                        current_user.last_forum_seen_at = latest
                        db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

        return render_template(
            template,
            messages=messages,
            online_users=online_users,
            max_size_mb=15,
            allowed_types="Images, audio, vidéo (20s max)"
        )

    # === SocketIO pour appels/présence ===
    _active_peers = {}
    _active_calls = {}  # uid -> peer uid

    def _user_room(uid):
        return f"user_{uid}"

    @socketio.on('connect')
    def socket_connect():
        if not current_user.is_authenticated:
            return False
        uid = getattr(current_user, 'id', None)
        if not uid:
            return False
        room = _user_room(uid)
        join_room(room)
        name = f"{getattr(current_user, 'first_name', '')} {getattr(current_user, 'last_name', '')}".strip()
        role = 'deliverer' if getattr(current_user, 'is_deliverer', False) else ('admin' if getattr(current_user, 'is_admin', False) else 'client')
        _active_peers[uid] = {'name': name, 'role': role}
        emit('presence:update', {'online': list(_active_peers.values())}, broadcast=True)

    @socketio.on('disconnect')
    def socket_disconnect():
        uid = getattr(current_user, 'id', None)
        if uid in _active_peers:
            _active_peers.pop(uid, None)
            emit('presence:update', {'online': list(_active_peers.values())}, broadcast=True)
        if uid in _active_calls:
            peer = _active_calls.pop(uid, None)
            if peer:
                _active_calls.pop(peer, None)
                emit('call:end', {'from': uid}, room=_user_room(peer))

    @socketio.on('call:init')
    def socket_call_init(data):
        if not current_user.is_authenticated:
            return
        target_id = data.get('to')
        from_id = getattr(current_user, 'id', None)
        if not target_id or str(target_id) == str(from_id):
            return
        # Occupé ?
        if target_id in _active_calls or from_id in _active_calls:
            emit('call:busy', {'to': target_id}, room=_user_room(from_id))
            return
        emit('call:ring', {
            'from': from_id,
            'from_name': data.get('from_name'),
            'with_video': data.get('with_video', False)
        }, room=_user_room(target_id))

    @socketio.on('call:offer')
    def socket_call_offer(data):
        target_id = data.get('to')
        if not target_id:
            return
        emit('call:offer', data, room=_user_room(target_id))

    @socketio.on('call:answer')
    def socket_call_answer(data):
        target_id = data.get('to')
        if not target_id:
            return
        caller = data.get('from') or target_id
        _active_calls[target_id] = caller
        _active_calls[caller] = target_id
        emit('call:answer', data, room=_user_room(target_id))

    @socketio.on('call:candidate')
    def socket_call_candidate(data):
        target_id = data.get('to')
        if not target_id:
            return
        emit('call:candidate', data, room=_user_room(target_id))

    @socketio.on('call:end')
    def socket_call_end(data):
        target_id = data.get('to')
        if not target_id:
            return
        uid = getattr(current_user, 'id', None)
        if uid in _active_calls:
            peer = _active_calls.pop(uid, None)
            if peer:
                _active_calls.pop(peer, None)
        emit('call:end', data, room=_user_room(target_id))

    @socketio.on('call:accept')
    def socket_call_accept(data):
        target_id = data.get('to')
        if not target_id:
            return
        emit('call:ready', data, room=_user_room(target_id))

    @socketio.on('call:reject')
    def socket_call_reject(data):
        target_id = data.get('to')
        if not target_id:
            return
        emit('call:rejected', data, room=_user_room(target_id))
    
    @app.route('/admin/admins/add', methods=['POST'])
    @login_required
    def admin_add_admin():
        if not (current_user.is_super_admin or current_user.has_permission('manage_admins')):
            flash('Accès réservé aux super-administrateurs ou admins autorisés', 'error')
            return redirect(url_for('admin_dashboard'))
        
        try:
            email = request.form.get('email')
            first_name = request.form.get('first_name')
            last_name = request.form.get('last_name')
            password = request.form.get('password')
            permissions = ','.join(request.form.getlist('permissions'))
            
            if not all([email, first_name, last_name, password]):
                flash('Veuillez remplir tous les champs', 'error')
                return redirect(url_for('admin_manage_admins'))
            
            existing_admin = User.query.filter_by(email=email).first()
            if existing_admin:
                flash('Cet email est déjà utilisé', 'error')
                return redirect(url_for('admin_manage_admins'))
            
            admin = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                is_admin=True,
                permissions=permissions
            )
            admin.set_password(password)
            
            db.session.add(admin)
            db.session.commit()
            
            # Envoyer un email de bienvenue au nouvel admin (NE PAS envoyer le mot de passe en clair)
            try:
                admin_url = _build_app_url('admin')
                send_email(
                    to=email,
                    subject='🔧 Vous êtes maintenant administrateur de Manga Store',
                    body=(
                        f"Bonjour {first_name},\n\n"
                        "Vous avez été ajouté comme administrateur de Manga Store.\n\n"
                        f"Identifiants de première connexion :\n"
                        f"- Email : {email}\n"
                        f"- Mot de passe : {password}\n\n"
                        f"Connectez-vous : {admin_url}\n\n"
                        "Pensez à changer votre mot de passe après la première connexion."
                    ),
                )
            except Exception as e:
                app.logger.warning(f"Erreur envoi email admin: {e}")
            
            flash('Administrateur ajouté avec succès. Un email a été envoyé.', 'success')
        except Exception as e:
            flash('Erreur lors de l\'ajout de l\'administrateur', 'error')
            print(f"Erreur ajout admin: {e}")
        
        return redirect(url_for('admin_manage_admins'))

    def _normalize_currency_param(raw_currency):
        if not raw_currency:
            return None
        try:
            code = raw_currency.strip().upper()
        except Exception:
            return None
        allowed = [c.upper() for c in app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])]
        return code if code in allowed else None

    @app.route('/admin/products/export-pdf')
    @login_required
    @require_permission('manage_products')
    def admin_export_products_pdf():
        products = Product.query.all()
        currency_param = _normalize_currency_param(request.args.get('currency'))
        if request.args.get('currency') and not currency_param:
            flash('Devise non supportée, export dans la devise par défaut.', 'warning')
        pdf_buffer = generate_products_pdf(products, target_currency=currency_param)
        
        if pdf_buffer:
            return send_file(
                pdf_buffer,
                as_attachment=True,
                download_name=f"produits_stock_{datetime.now().strftime('%Y%m%d')}.pdf",
                mimetype='application/pdf'
            )
        else:
            flash('Erreur lors de la génération du PDF', 'error')
            return redirect(url_for('admin_products'))
    
    @app.route('/admin/order/<int:order_id>/invoice')
    @login_required
    def admin_download_invoice(order_id):
        if not current_user.is_admin:
            flash('Accès non autorisé', 'error')
            return redirect(url_for('admin_orders'))
        
        order = Order.query.get_or_404(order_id)
        if order.status != 'delivered':
            flash('La facture est disponible uniquement pour les commandes livrées.', 'error')
            return redirect(url_for('admin_order_detail', order_id=order_id))
        currency_param = _normalize_currency_param(request.args.get('currency'))
        if request.args.get('currency') and not currency_param:
            flash('Devise non supportée, facture générée dans la devise par défaut.', 'warning')
        invoice_buffer = generate_invoice_pdf(order, target_currency=currency_param)
        
        if invoice_buffer:
            return send_file(
                invoice_buffer,
                as_attachment=True,
                download_name=f"facture_{order.order_number}.pdf",
                mimetype='application/pdf'
            )
        else:
            flash('Erreur lors de la génération de la facture', 'error')
            return redirect(url_for('admin_order_detail', order_id=order_id))

    @app.route('/order/<int:order_id>/invoice')
    def client_download_invoice(order_id):
        order = Order.query.get_or_404(order_id)
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            return redirect(url_for('admin_download_invoice', order_id=order_id, currency=request.args.get('currency')))

        allowed = False
        if current_user.is_authenticated and not getattr(current_user, 'is_admin', False) and not getattr(current_user, 'is_deliverer', False):
            allowed = order.user_id == current_user.id
        if not allowed:
            try:
                allowed = order.id in session.get('recent_orders', [])
            except Exception:
                allowed = False

        if not allowed:
            flash('Accès non autorisé à cette commande.', 'error')
            redirect_target = url_for('client_orders') if current_user.is_authenticated else url_for('index')
            return redirect(redirect_target)

        if order.status != 'delivered':
            flash('La facture sera disponible une fois la commande livrée.', 'error')
            return redirect(request.referrer or (url_for('client_orders') if current_user.is_authenticated else url_for('index')))

        currency_param = _normalize_currency_param(request.args.get('currency'))
        if request.args.get('currency') and not currency_param:
            flash('Devise non supportée, facture générée dans la devise par défaut.', 'warning')

        invoice_buffer = generate_invoice_pdf(order, target_currency=currency_param)
        if invoice_buffer:
            return send_file(
                invoice_buffer,
                as_attachment=True,
                download_name=f"facture_{order.order_number}.pdf",
                mimetype='application/pdf'
            )
        flash('Erreur lors de la génération de la facture', 'error')
        return redirect(request.referrer or (url_for('client_orders') if current_user.is_authenticated else url_for('index')))

    @app.route('/admin/request-access', methods=['POST'])
    @login_required
    def admin_request_access():
        """Endpoint to handle access requests from admins. Sends an email to super-admins."""
        if not current_user.is_admin:
            flash('Accès non autorisé', 'error')
            return redirect(url_for('index'))
        requested_perms = _parse_permissions_field('|'.join(request.form.getlist('permissions_requested')))
        if not requested_perms:
            flash('Sélectionnez au moins une fonctionnalité à demander.', 'error')
            return redirect(request.referrer or url_for('admin_profile'))
        feature = '|'.join(requested_perms)
        message = request.form.get('message', '').strip()

        # Enregistrer la demande en base
        try:
            req = AccessRequest(admin_id=current_user.id, feature=feature, message=message, status='pending')
            db.session.add(req)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur enregistrement AccessRequest: {e}")

        # Collecter les emails des super-admins
        super_admins = User.query.filter_by(is_super_admin=True).all()
        recipients = [u.email for u in super_admins if u.email]

        readable_perms = [PERMISSION_LABELS.get(p, p) for p in requested_perms]
        subject = f"Demande d'accès: {', '.join(readable_perms)} — {current_user.first_name} {current_user.last_name}"
        body = f"L'administrateur {current_user.first_name} {current_user.last_name} ({current_user.email}) demande l'accès à: {', '.join(readable_perms)}.\n\nMessage: {message}\n\nConsultez le panneau d'administration pour traiter la demande."

        sent_any = False
        if recipients:
            for to in recipients:
                try:
                    if send_email(to, subject, body):
                        sent_any = True
                except Exception as e:
                    app.logger.warning(f"Erreur envoi demande accès à {to}: {e}")

        if sent_any:
            flash("Votre demande d'accès a été envoyée aux super-administrateurs.", 'success')
        else:
            flash("Demande enregistrée, mais impossible d'envoyer l'email (vérifiez la configuration SMTP).", 'warning')

        return redirect(request.referrer or url_for('admin_dashboard'))
    
    # Gestion des erreurs
    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500

    @app.route('/favicon.ico')
    def favicon():
        """Servir une favicon pour éviter les 404."""
        try:
            # Tenter d'utiliser le logo boutique si défini
            settings = ShopSettings.query.first()
            filename = None
            if settings and settings.shop_logo:
                filename = settings.shop_logo
            else:
                filename = 'default_logo.svg'
            logo_dir = os.path.join(project_root, 'frontend', 'static', 'uploads', 'logos')
            return send_from_directory(logo_dir, filename, mimetype='image/svg+xml')
        except Exception:
            return ('', 204)

    return app
