from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
import qrcode
from io import BytesIO
import os
from datetime import datetime, timedelta
from flask import current_app
import textwrap
import requests

def generate_invoice_pdf(order, target_currency=None):
    from backend.models import ShopSettings
    
    settings = ShopSettings.query.first()
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    primary_color = colors.HexColor("#0f172a")
    accent_color = colors.HexColor("#14b8a6")
    base_currency = (settings.currency if settings and settings.currency else current_app.config.get('BASE_CURRENCY', 'USD')).upper()
    available_currencies = [c.upper() for c in current_app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])]

    def get_rate(from_currency: str, to_currency: str) -> float:
        if not from_currency or not to_currency or from_currency == to_currency:
            return 1.0
        rates = {
            ('USD', 'CDF'): 2200.0,
            ('CDF', 'USD'): 1/2200.0
        }
        return rates.get((from_currency.upper(), to_currency.upper()), 1.0)

    currency = (target_currency or base_currency or "USD").upper()
    if currency not in available_currencies:
        currency = base_currency

    def convert_amount(amount: float) -> float:
        rate = get_rate(base_currency, currency)
        try:
            return round(float(amount) * rate, 2)
        except Exception:
            return float(amount)

    def format_amount(amount: float) -> str:
        try:
            return f"{amount:,.2f}".replace(",", " ").replace(".", ",")
        except Exception:
            return str(amount)
    
    # Entête en deux colonnes, fond doux
    header_height = 220
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(0, height - header_height, width, header_height, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#e0f2fe"))
    c.circle(width - 120, height - 80, 110, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#cffafe"))
    c.circle(width - 220, height - 150, 80, stroke=0, fill=1)
    c.setFillColor(primary_color)

    shop_name = settings.shop_name if settings and settings.shop_name else "Manga Store"
    shop_address = settings.shop_address if settings and settings.shop_address else ""
    shop_email = settings.shop_email if settings and settings.shop_email else ""
    shop_phone = settings.shop_phone if settings and settings.shop_phone else ""

    margin_x = 40
    left_col_w = (width / 2) - 70
    right_col_x = width / 2 + 10

    # Logo réduit en forme ronde au-dessus des infos boutique
    logo_size = 50
    def _logo_reader():
        uploads = None
        try:
            uploads = current_app.config.get('UPLOAD_FOLDER')
        except RuntimeError:
            uploads = os.path.join('frontend', 'static', 'uploads')
        if not settings or not settings.shop_logo:
            return None
        logo_value = settings.shop_logo
        if str(logo_value).startswith(('http://', 'https://')):
            try:
                resp = requests.get(logo_value, timeout=5)
                resp.raise_for_status()
                return ImageReader(BytesIO(resp.content))
            except Exception:
                return None
        candidate = os.path.join(uploads, 'logos', logo_value)
        if os.path.exists(candidate):
            try:
                return ImageReader(candidate)
            except Exception:
                return None
        return None

    logo_reader = _logo_reader()
    info_y = height - 80

    # Colonne gauche : logo rond puis infos boutique
    text_x = margin_x
    if logo_reader:
        try:
            # Cercle de fond
            c.setFillColor(colors.white)
            c.setStrokeColor(accent_color)
            c.setLineWidth(2)
            c.circle(margin_x + logo_size/2, info_y - logo_size/2, logo_size/2 + 2, stroke=1, fill=1)
            # Image masquée dans le cercle
            c.saveState()
            path = c.beginPath()
            path.circle(margin_x + logo_size/2, info_y - logo_size/2, logo_size/2)
            c.clipPath(path, stroke=0)
            c.drawImage(logo_reader, margin_x, info_y - logo_size, width=logo_size, height=logo_size, preserveAspectRatio=True, mask='auto')
            c.restoreState()
            text_x = margin_x + logo_size + 12
        except Exception:
            text_x = margin_x

    # Ligne supérieure: nom boutique (gauche) et FACTURE PROFORMA (droite)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(text_x, info_y + 6, shop_name)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(right_col_x, info_y + 6, "FACTURE PROFORMA")

    # Informations boutique (sous le nom)
    c.setFont("Helvetica", 10)
    shop_y = info_y - 10
    wrap_width = max(28, int(left_col_w / 5.2))
    for line in textwrap.wrap(shop_address, width=wrap_width):
        c.drawString(text_x, shop_y, line)
        shop_y -= 10
    if shop_email:
        c.drawString(text_x, shop_y, f"Email: {shop_email}")
        shop_y -= 10
    if shop_phone:
        c.drawString(text_x, shop_y, f"Tél: {shop_phone}")
        shop_y -= 10
    shop_y -= 2

    # Calcul de l'échéance
    try:
        due_date = (order.created_at + timedelta(days=7)).strftime('%d/%m/%Y')
    except Exception:
        due_date = ""

    # Bloc facture (à droite)
    meta_y = info_y - 8
    c.setFont("Helvetica", 10)
    c.drawString(right_col_x, meta_y, f"N°: {order.order_number}")
    meta_y -= 12
    c.drawString(right_col_x, meta_y, f"Date: {order.created_at.strftime('%d/%m/%Y')}")
    meta_y -= 12
    c.drawString(right_col_x, meta_y, f"Échéance: {due_date}")
    meta_y -= 18
    c.setFillColor(accent_color)
    status_w = 160
    c.roundRect(right_col_x - 6, meta_y - 6, status_w, 22, 8, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(right_col_x, meta_y + 3, f"Statut: {order.status.upper()}")
    c.setFillColor(primary_color)
    meta_bottom_y = meta_y - 12

    # Bloc CLIENT replacé sous les infos boutique (colonne gauche)
    client_y = shop_y - 4
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, client_y, "CLIENT")
    c.setFont("Helvetica", 10)
    client_y -= 14
    c.drawString(margin_x, client_y, f"{order.customer.first_name} {order.customer.last_name}")
    client_y -= 12
    c.drawString(margin_x, client_y, f"Email: {order.customer.email}")
    client_y -= 12
    for idx, line in enumerate(textwrap.wrap(order.shipping_address or "", width=wrap_width)):
        prefix = "Adresse: " if idx == 0 else "        "
        c.drawString(margin_x, client_y, f"{prefix}{line}")
        client_y -= 10

    left_bottom_y = client_y
    y_position = min(left_bottom_y, meta_bottom_y) - 24

    # Détails commande
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y_position, "DÉTAILS DE LA COMMANDE")
    y_position -= 18

    # En-tête tableau
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y_position, "Produit")
    c.drawString(300, y_position, "Quantité")
    c.drawString(380, y_position, "Prix")
    c.drawString(460, y_position, "Total")

    c.line(50, y_position - 5, 550, y_position - 5)

    # Articles
    y_position -= 18
    c.setFont("Helvetica", 9)
    for item in order.items:
        name = item.product.name if item.product else "Produit"
        for line in textwrap.wrap(name, width=35):
            c.drawString(50, y_position, line)
            y_position -= 10
        y_position += 10  # correct last decrement
        c.drawString(300, y_position, str(item.quantity))
        price_converted = convert_amount(item.price)
        line_total_converted = convert_amount(item.quantity * item.price)
        c.drawString(380, y_position, f"{format_amount(price_converted)} {currency}")
        c.drawString(460, y_position, f"{format_amount(line_total_converted)} {currency}")
        y_position -= 16

    # Total
    y_position -= 10
    c.setFont("Helvetica-Bold", 12)
    total_converted = convert_amount(order.total_amount)
    c.drawString(400, y_position, f"TOTAL: {format_amount(total_converted)} {currency}")

    # QR Code en bas à droite du pied de page
    qr_data = f"""
Boutique: {shop_name}
Client: {order.customer.first_name} {order.customer.last_name}
Commande: {order.order_number}
Total: {format_amount(total_converted)} {currency}
Date: {order.created_at.strftime('%d/%m/%Y')}
    """

    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)

    qr_size = 90
    qr_x = width - qr_size - 40
    qr_y = 20
    c.drawImage(ImageReader(qr_buffer), qr_x, qr_y, width=qr_size, height=qr_size)

    # Pied de page fixe en bas
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 40, f"© Manga Store - Propulsé par Esperdigi")
    c.drawString(50, 28, f"Facture générée le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")

    c.save()
    buffer.seek(0)
    return buffer
