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
    # Marges réduites pour rapprocher le tableau du haut et limiter le vide
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=40, bottomMargin=40, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()
    settings = ShopSettings.query.first()
    base_currency = (settings.currency if settings and settings.currency else "USD").upper()
    available_currencies = [c.upper() for c in current_app.config.get('AVAILABLE_CURRENCIES', ['USD', 'CDF'])]
    uploads_root = current_app.config.get('UPLOAD_FOLDER', os.path.join('frontend', 'static', 'uploads'))
    shop_name = settings.shop_name if settings and settings.shop_name else "Manga Store"
    logo_reader = None

    def _image_reader(path: str | None):
        if not path:
            return None
        if str(path).startswith(('http://', 'https://')):
            try:
                resp = requests.get(path, timeout=5)
                resp.raise_for_status()
                return ImageReader(BytesIO(resp.content))
            except Exception:
                return None
        if os.path.exists(path):
            try:
                return ImageReader(path)
            except Exception:
                return None
        return None

    if settings and settings.shop_logo:
        # Logo peut venir d'une URL (Supabase/public) ou du disque local uploads/logos
        if str(settings.shop_logo).startswith(('http://', 'https://')):
            logo_reader = _image_reader(settings.shop_logo)
        else:
            candidate = os.path.join(uploads_root, 'logos', settings.shop_logo)
            logo_reader = _image_reader(candidate)

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
        logo_size = 60
        if logo_reader:
            try:
                x = (width - logo_size) / 2
                y = height - logo_size - 16
                # cercle fond resserré
                canvas.setFillColor(colors.white)
                canvas.setStrokeColor(colors.HexColor("#4c1d95"))
                canvas.setLineWidth(2)
                canvas.circle(x + logo_size/2, y + logo_size/2, logo_size/2 + 2, stroke=1, fill=1)
                # image masquée
                canvas.saveState()
                path = canvas.beginPath()
                path.circle(x + logo_size/2, y + logo_size/2, logo_size/2)
                canvas.clipPath(path, stroke=0)
                canvas.drawImage(logo_reader, x, y, width=logo_size, height=logo_size, preserveAspectRatio=True, mask='auto')
                canvas.restoreState()
            except Exception:
                pass
        # Pied de page aligné sur invoice_generator
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.drawString(50, 40, "© Manga Store - Propulsé par Esperdigi")
        canvas.drawString(50, 28, f"Liste générée le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
        if qr_image:
            qr_size = 70
            canvas.drawImage(qr_image, width - qr_size - 36, 18, width=qr_size, height=qr_size, mask='auto')
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
    content.append(Paragraph("LISTE DES PRODUITS EN STOCKS", title_style))

    # Styles texte pour forcer les retours à la ligne dans les cellules
    text_style = ParagraphStyle(
        'ProdText',
        parent=styles['BodyText'],
        fontSize=10,
        leading=12,
        wordWrap='CJK'
    )

    # Tableau des produits
    data = [['Image', 'Nom', 'Catégorie', 'Prix', 'Stock', 'Statut']]
    status_colors = []

    def product_image(prod):
        try:
            first_image = None
            if prod.images:
                first_image = prod.images.split('|')[0]
            if first_image:
                path = first_image if first_image.startswith('http') else os.path.join(uploads_root, 'products', first_image)
                if first_image.startswith('http') or os.path.exists(path):
                    img_path = path
                    # ReportLab Image accepte les URL, mais on privilégie les fichiers locaux si existants
                    return Image(img_path, width=0.8*inch, height=0.8*inch)
        except Exception:
            return Paragraph("—", styles['BodyText'])
        return Paragraph("—", styles['BodyText'])

    for product in products:
        status = "En stock" if product.quantity > 0 else "Rupture"
        price_converted = format_amount(convert_amount(product.price))
        data.append([
            product_image(product),
            Paragraph(product.name or "Produit", text_style),
            Paragraph(product.category.name if product.category else "Non catégorisé", text_style),
            f"{price_converted} {currency}",
            str(product.quantity),
            status
        ])
        status_colors.append(status)

    table = Table(data, colWidths=[0.9*inch, 2.2*inch, 1.6*inch, 1.1*inch, 0.9*inch, 1.0*inch])
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
