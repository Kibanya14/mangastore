from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
import qrcode
from io import BytesIO
from datetime import datetime
import os
import requests
from flask import current_app
from backend.models import ShopSettings

def generate_products_pdf(products, target_currency=None):
    buffer = BytesIO()
    # Marges plus serrées pour réduire la pagination et la mémoire
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=40, bottomMargin=40, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    settings = ShopSettings.query.first()
    base_currency = (settings.currency if settings and settings.currency else "USD").upper()
    available_currencies = [c.upper() for c in current_app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])]
    uploads_root = current_app.config.get('UPLOAD_FOLDER', os.path.join('frontend', 'static', 'uploads'))
    shop_name = settings.shop_name if settings and settings.shop_name else "Manga Store"
    logo_reader = None  # logo retiré pour alléger le PDF

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

    qr_image = None
    try:
        qr_data = (
            f"Catalogue produits - {shop_name}\n"
            f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"Devise: {currency}\n"
            f"Articles: {len(products)}"
        )
        qr = qrcode.QRCode(version=1, box_size=2, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buffer = BytesIO()
        qr_img.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)
        qr_image = ImageReader(qr_buffer)
    except Exception:
        qr_image = None

    def _header_footer(canvas, doc):
        width, height = A4
        canvas.saveState()
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.drawString(36, 30, f"© Manga Store — Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        if qr_image:
            qr_size = 60
            canvas.drawImage(qr_image, width - qr_size - 30, 16, width=qr_size, height=qr_size, mask='auto')
        canvas.restoreState()

    # Contenu du PDF
    content = []

    # Titre
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=4,
        alignment=1,
        textColor=colors.HexColor("#4c1d95")
    )
    content.append(Paragraph("LISTE DES PRODUITS EN STOCK", title_style))

    # Tableau des produits avec miniatures réduites
    data = [['Img', 'Nom', 'Catégorie', 'Prix', 'Stock', 'Statut']]
    status_colors = []

    def product_image(prod):
        try:
            first_image = None
            if prod.images:
                first_image = prod.images.split('|')[0]
            if not first_image:
                return Paragraph("—", styles['BodyText'])
            if first_image.startswith('http'):
                # Ignorer les URLs pour éviter le téléchargement et la mémoire
                return Paragraph("—", styles['BodyText'])
            path = os.path.join(uploads_root, 'products', first_image)
            if os.path.exists(path):
                return Image(path, width=0.6*inch, height=0.6*inch)
        except Exception:
            return Paragraph("—", styles['BodyText'])
        return Paragraph("—", styles['BodyText'])

    for product in products:
        status = "En stock" if product.quantity > 0 else "Rupture"
        price_converted = format_amount(convert_amount(product.price))
        data.append([
            product_image(product),
            product.name[:45],
            product.category.name[:25] if product.category else "Non catégorisé",
            f"{price_converted} {currency}",
            str(product.quantity),
            status
        ])
        status_colors.append(status)

    table = Table(data, colWidths=[0.8*inch, 2.2*inch, 1.6*inch, 1.0*inch, 0.8*inch, 1.0*inch])
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4c1d95")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#f8f7ff")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.6, colors.HexColor("#d1d5db")),
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor("#3b0764")),
        ('ALIGN', (1, 1), (2, -1), 'LEFT'),
        ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
        ('ALIGN', (4, 1), (5, -1), 'CENTER')
    ]

    for idx, status in enumerate(status_colors, start=1):
        color = colors.HexColor("#16a34a") if status == "En stock" else colors.HexColor("#dc2626")
        table_style.append(('TEXTCOLOR', (5, idx), (5, idx), color))
        table_style.append(('FONTNAME', (5, idx), (5, idx), 'Helvetica-Bold'))

    table.setStyle(TableStyle(table_style))

    content.append(table)

    doc.build(content, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)
    return buffer
