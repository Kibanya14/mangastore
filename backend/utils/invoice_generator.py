from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
from flask import current_app
import textwrap


def generate_invoice_pdf(order, target_currency=None):
    from backend.models import ShopSettings

    settings = ShopSettings.query.first()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    primary_color = colors.HexColor("#111111")
    accent_color = colors.HexColor("#e53935")
    light_gray = colors.HexColor("#e5e7eb")
    border_gray = colors.HexColor("#cbd5e1")
    muted_text = colors.HexColor("#4b5563")

    base_currency = (settings.currency if settings and settings.currency else current_app.config.get('BASE_CURRENCY', 'USD')).upper()
    available_currencies = [curr.upper() for curr in current_app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])]

    def get_rate(from_currency: str, to_currency: str) -> float:
        if not from_currency or not to_currency or from_currency == to_currency:
            return 1.0
        rates = {
            ('USD', 'CDF'): 2200.0,
            ('CDF', 'USD'): 1 / 2200.0,
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

    shop_name = settings.shop_name if settings and settings.shop_name else "Manga Store"
    shop_address = settings.shop_address if settings and settings.shop_address else ""
    shop_email = settings.shop_email if settings and settings.shop_email else ""
    shop_phone = settings.shop_phone if settings and settings.shop_phone else ""
    customer_fn = getattr(order.customer, 'first_name', '') or ""
    customer_ln = getattr(order.customer, 'last_name', '') or ""
    customer_name = f"{customer_fn} {customer_ln}".strip()
    customer_email = getattr(order.customer, 'email', "") or ""

    # Mise en page principale inspirée du modèle fourni
    bar_width = 42
    top_bar_height = 46
    content_left = bar_width + 28
    content_right = width - 40
    content_top = height - top_bar_height - 24

    # Bande verticale à gauche
    pdf.setFillColor(primary_color)
    pdf.rect(0, 0, bar_width, height, stroke=0, fill=1)
    pdf.setFillColor(accent_color)
    pdf.rect(0, height - 110, bar_width, 24, stroke=0, fill=1)

    # Bande horizontale grise en haut
    pdf.setFillColor(light_gray)
    pdf.rect(bar_width, height - top_bar_height, width - bar_width, top_bar_height, stroke=0, fill=1)

    # Accents en bas à droite
    pdf.setFillColor(colors.HexColor("#d9d9d9"))
    pdf.rect(width - 78, 0, 60, 32, stroke=0, fill=1)
    pdf.setFillColor(primary_color)
    pdf.circle(width - 55, 46, 26, stroke=0, fill=1)
    pdf.setFillColor(accent_color)
    pdf.circle(width - 28, 96, 18, stroke=0, fill=1)

    # Nom boutique (texte simple, sans logo)
    pdf.setFillColor(primary_color)
    shop_title_y = content_top - 6
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(content_left, shop_title_y, shop_name)

    # Titre facture
    title_y = content_top + 10
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawRightString(content_right, title_y, "FACTURE")

    # Calcul de l'échéance
    try:
        due_date = (order.created_at + timedelta(days=7)).strftime('%d/%m/%Y')
    except Exception:
        due_date = ""

    # Meta facture (numéro, dates)
    meta_y = shop_title_y - 12
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(content_right, meta_y, f"FACTURE N° : {order.order_number}")
    status_value = (getattr(order, "status", "") or "").upper()
    pdf.drawRightString(content_right, meta_y - 14, f"STATUT : {status_value or '-'}")
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(content_left, meta_y, f"DATE : {order.created_at.strftime('%d/%m/%Y')}")
    pdf.drawString(content_left, meta_y - 14, f"ÉCHÉANCE : {due_date or '-'}")

    line_y = meta_y - 32
    pdf.setStrokeColor(primary_color)
    pdf.setLineWidth(1)
    pdf.line(content_left, line_y, content_right, line_y)

    # Colonnes Émetteur / Destinataire
    col_gap = (content_right - content_left) / 2
    emitter_x = content_left
    dest_x = content_left + col_gap + 12
    emitter_y = line_y - 14
    dest_y = emitter_y

    wrap_width = max(28, int(col_gap / 5.4))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(emitter_x, emitter_y, "ÉMETTEUR :")
    pdf.drawString(dest_x, dest_y, "DESTINATAIRE :")
    emitter_y -= 14
    dest_y -= 14
    pdf.setFont("Helvetica", 10)
    if shop_phone:
        pdf.drawString(emitter_x, emitter_y, shop_phone)
        emitter_y -= 12
    if shop_email:
        pdf.drawString(emitter_x, emitter_y, shop_email)
        emitter_y -= 12
    for line in textwrap.wrap(shop_address, width=wrap_width):
        pdf.drawString(emitter_x, emitter_y, line)
        emitter_y -= 12

    if customer_name:
        pdf.drawString(dest_x, dest_y, customer_name)
        dest_y -= 12
    if customer_email:
        pdf.drawString(dest_x, dest_y, customer_email)
        dest_y -= 12
    for line in textwrap.wrap(order.shipping_address or "", width=wrap_width):
        pdf.drawString(dest_x, dest_y, line)
        dest_y -= 12

    section_bottom = min(emitter_y, dest_y) - 18

    # Tableau des articles
    desc_x = content_left
    unit_x = desc_x + 270
    qty_x = unit_x + 90
    total_x = content_right
    y_position = section_bottom

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(desc_x, y_position, "Description :")
    pdf.drawRightString(unit_x + 70, y_position, "Prix Unitaire :")
    pdf.drawRightString(qty_x + 32, y_position, "Quantité :")
    pdf.drawRightString(total_x, y_position, "Total :")
    pdf.setStrokeColor(border_gray)
    pdf.line(content_left, y_position - 6, content_right, y_position - 6)

    y_position -= 18
    pdf.setFont("Helvetica", 10)
    for item in order.items:
        name = item.product.name if item.product else "Produit"
        lines = textwrap.wrap(name, width=45) or ["Produit"]
        price_converted = convert_amount(item.price)
        line_total_converted = convert_amount(item.quantity * item.price)
        row_y = y_position
        for idx, line in enumerate(lines):
            pdf.setFillColor(primary_color)
            pdf.drawString(desc_x, row_y, line)
            if idx == 0:
                pdf.drawRightString(unit_x + 70, row_y, f"{format_amount(price_converted)} {currency}")
                pdf.drawRightString(qty_x + 32, row_y, str(item.quantity))
                pdf.drawRightString(total_x, row_y, f"{format_amount(line_total_converted)} {currency}")
            row_y -= 12
        y_position = row_y - 6
        pdf.setStrokeColor(border_gray)
        pdf.line(content_left, y_position + 2, content_right, y_position + 2)
        y_position -= 6

    # Totaux
    tax_rate = float(getattr(settings, "tax_rate", 0.0) or 0.0)
    total_ttc = convert_amount(order.total_amount)
    if tax_rate > 0:
        total_ht = total_ttc / (1 + tax_rate / 100)
        tax_amount = total_ttc - total_ht
    else:
        total_ht = total_ttc
        tax_amount = 0.0

    totals_y = y_position - 6
    label_x = total_x - 160
    value_x = total_x
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(primary_color)
    pdf.drawRightString(label_x, totals_y, "TOTAL HT :")
    pdf.drawRightString(value_x, totals_y, f"{format_amount(total_ht)} {currency}")
    totals_y -= 14
    tva_label = f"TVA {tax_rate:.0f}% :" if tax_rate else "TVA :"
    pdf.drawRightString(label_x, totals_y, tva_label)
    pdf.drawRightString(value_x, totals_y, f"{format_amount(tax_amount)} {currency}")
    totals_y -= 14
    pdf.drawRightString(label_x, totals_y, "REMISE :")
    pdf.drawRightString(value_x, totals_y, "-")
    totals_y -= 18
    pdf.drawRightString(label_x, totals_y, "TOTAL TTC :")
    pdf.drawRightString(value_x, totals_y, f"{format_amount(total_ttc)} {currency}")

    # QR Code en bas à droite du pied de page
    qr_data = f"""
Boutique: {shop_name}
Client: {customer_fn} {customer_ln}
Commande: {order.order_number}
Total: {format_amount(total_ttc)} {currency}
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
    pdf.drawImage(ImageReader(qr_buffer), qr_x, qr_y, width=qr_size, height=qr_size)

    # Bloc règlement placé proche du bas, juste au-dessus du pied de page
    footer_safe_y = 60
    regl_y = max(qr_y + qr_size + 12, footer_safe_y + 60)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(primary_color)
    pdf.drawString(content_left, regl_y, "RÈGLEMENT :")
    regl_y -= 12
    pdf.setFont("Helvetica", 9)
    pdf.drawString(content_left, regl_y, "Par virement bancaire ou paiement à la livraison.")
    regl_y -= 12
    if shop_name:
        pdf.drawString(content_left, regl_y, f"Bénéficiaire : {shop_name}")
        regl_y -= 12
    if shop_email:
        pdf.drawString(content_left, regl_y, f"Contact : {shop_email}")
        regl_y -= 12
    if shop_phone:
        pdf.drawString(content_left, regl_y, f"Tél : {shop_phone}")
        regl_y -= 12
    regl_y -= 2
    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(muted_text)
    pdf.drawString(content_left, regl_y, "En cas de retard, des frais peuvent s'appliquer conformément aux conditions de vente.")
    regl_y -= 10
    pdf.drawString(content_left, regl_y, "Merci pour votre confiance.")
    pdf.setFillColor(primary_color)

    # Pied de page fixe
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColor(muted_text)
    pdf.drawString(content_left, 36, f"© {shop_name} - Propulsé par Esperdigi")
    pdf.drawString(content_left, 24, f"Facture générée le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")

    pdf.save()
    buffer.seek(0)
    return buffer
