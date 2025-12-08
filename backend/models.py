from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    profile_picture = db.Column(db.String(255), default='default_profile.svg')
    selected_currency = db.Column(db.String(10))
    is_admin = db.Column(db.Boolean, default=False)
    is_super_admin = db.Column(db.Boolean, default=False)
    permissions = db.Column(db.Text)  # JSON des permissions
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # permet de bloquer/débloquer un compte
    is_deliverer = False
    
    # Relations
    orders = db.relationship('Order', backref='customer', lazy=True)
    carts = db.relationship('Cart', backref='user', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def has_permission(self, permission):
        if self.is_super_admin:
            return True
        if not self.permissions:
            return False
        return permission in self.permissions.split(',')


class Deliverer(UserMixin, db.Model):
    __tablename__ = 'deliverers'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    profile_picture = db.Column(db.String(255), default='default_profile.svg')
    is_active = db.Column(db.Boolean, default=True)
    commission_due = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='available')  # available, busy, offline
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_bonus_week_start = db.Column(db.Date)  # Date du lundi de la dernière prime hebdo payée
    weekly_bonus_paid_count = db.Column(db.Integer, default=0)  # Nombre de bonus 5$ déjà payés cette semaine

    assignments = db.relationship('DeliveryAssignment', backref='deliverer', lazy=True, cascade='all, delete-orphan')

    # Harmoniser avec current_user checks
    is_admin = False
    is_super_admin = False
    is_deliverer = True
    permissions = ''
    selected_currency = None

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        # Préfixe pour distinguer du modèle User
        return f"d:{self.id}"

    def has_permission(self, permission):
        return False

class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    image = db.Column(db.String(255))
    icon = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relation
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    __tablename__ = 'products'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    compare_price = db.Column(db.Float)  # Prix barré
    quantity = db.Column(db.Integer, default=0)
    images = db.Column(db.Text)  # JSON des images
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Clés étrangères
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    
    # Relations
    order_items = db.relationship('OrderItem', backref='product', lazy=True)
    cart_items = db.relationship('CartItem', backref='product', lazy=True)

class Cart(db.Model):
    __tablename__ = 'carts'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    items = db.relationship('CartItem', backref='cart', lazy=True, cascade='all, delete-orphan')

class CartItem(db.Model):
    __tablename__ = 'cart_items'
    
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey('carts.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, shipped, delivered, cancelled
    shipping_address = db.Column(db.Text, nullable=False)
    billing_address = db.Column(db.Text)
    shipping_latitude = db.Column(db.Float)
    shipping_longitude = db.Column(db.Float)
    shipping_geocoded = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)
    stock_deducted = db.Column(db.Boolean, default=False)
    status_changed_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    delivery_assignments = db.relationship('DeliveryAssignment', backref='order', lazy=True, cascade='all, delete-orphan')

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ShopSettings(db.Model):
    __tablename__ = 'shop_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    shop_name = db.Column(db.String(100), default='Manga Store')
    shop_logo = db.Column(db.String(255), default='default_logo.svg')
    admin_logo = db.Column(db.String(255), default='default_admin_logo.svg')
    deliverer_logo = db.Column(db.String(255), default='default_deliverer_logo.svg')
    shop_email = db.Column(db.String(120))
    shop_phone = db.Column(db.String(20))
    shop_address = db.Column(db.Text)
    facebook_url = db.Column(db.String(255))
    whatsapp_number = db.Column(db.String(30))
    whatsapp_group_url = db.Column(db.String(255))
    currency = db.Column(db.String(10), default='USD')
    tax_rate = db.Column(db.Float, default=0.0)
    shipping_cost = db.Column(db.Float, default=0.0)
    shipping_cost_out = db.Column(db.Float, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(255), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    actor_email = db.Column(db.String(120))
    actor_name = db.Column(db.String(120))
    actor_phone = db.Column(db.String(30))
    extra = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    actor = db.relationship('User', foreign_keys=[actor_id])


class AccessRequest(db.Model):
    __tablename__ = 'access_requests'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    feature = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    processed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    response_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)

    requester = db.relationship('User', foreign_keys=[admin_id], backref='access_requests')
    processor = db.relationship('User', foreign_keys=[processed_by])


class DeliveryAssignment(db.Model):
    __tablename__ = 'delivery_assignments'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    deliverer_id = db.Column(db.Integer, db.ForeignKey('deliverers.id'), nullable=False)
    status = db.Column(db.String(20), default='assigned')  # assigned, in_progress, delivered, postponed, cancelled
    note = db.Column(db.Text)
    payout_status = db.Column(db.String(20), default='pending')  # pending, paid
    commission_recorded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime)


class ForumMessage(db.Model):
    __tablename__ = 'forum_messages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deliverer_id = db.Column(db.Integer, db.ForeignKey('deliverers.id'))
    role = db.Column(db.String(20))  # client, admin, deliverer
    content = db.Column(db.Text)
    attachment_path = db.Column(db.String(255))
    attachment_type = db.Column(db.String(20))  # image, audio, video, file
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    requester = db.relationship('User', foreign_keys=[user_id])
    deliverer = db.relationship('Deliverer', foreign_keys=[deliverer_id])
