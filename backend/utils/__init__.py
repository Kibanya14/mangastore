# backend/utils/__init__.py
# Ce fichier peut être vide ou contenir les imports des modules utils
from .invoice_generator import generate_invoice_pdf
from .pdf_generator import generate_products_pdf

__all__ = ['generate_invoice_pdf', 'generate_products_pdf']