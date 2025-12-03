import os
import secrets
import time

# Uploads persistants : Supabase Storage (public URL). Si les creds sont absents, on retourne None et l'appelant retombe sur le disque local.
try:
    from supabase import create_client
except ImportError:  # pragma: no cover - optional dependency
    create_client = None

_supabase_client_cache = None


def _supabase_configured():
    return (
        create_client is not None
        and os.getenv("SUPABASE_URL")
        and os.getenv("SUPABASE_KEY")
        and os.getenv("SUPABASE_BUCKET")
    )


def _supabase_path(subfolder: str, filename: str) -> str:
    folder = (subfolder or "").strip("/")
    if folder:
        return f"{folder}/{filename}"
    return filename


def _get_supabase_client():
    """Cache le client Supabase pour éviter des reconnections répétées."""
    global _supabase_client_cache
    if _supabase_client_cache:
        return _supabase_client_cache
    if not _supabase_configured():
        return None
    try:
        client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        bucket = os.getenv("SUPABASE_BUCKET")
        _supabase_client_cache = (client, bucket)
        return _supabase_client_cache
    except Exception:
        return None


def upload_media(file_storage, subfolder: str, logger=None, resource_type: str = "auto") -> str | None:
    """
    Upload vers Supabase Storage. Retourne une URL publique ou None si non dispo.
    Le code appelant peut retomber sur le stockage local en cas d'échec.
    """
    client_info = _get_supabase_client()
    if not client_info:
        if logger:
            try:
                logger.warning("Supabase storage non configuré ou client indisponible, fallback local.")
            except Exception:
                pass
    if not client_info or not file_storage or not getattr(file_storage, "filename", None):
        return None

    client, bucket = client_info
    name, ext = os.path.splitext(file_storage.filename or "")
    ext = ext.lower()
    generated = f"{int(time.time())}_{secrets.token_hex(6)}{ext}"
    path = _supabase_path(subfolder, generated)

    try:
        file_storage.stream.seek(0)
        data = file_storage.read()
        file_storage.stream.seek(0)
    except Exception:
        data = None

    if not data:
        return None

    try:
        client.storage.from_(bucket).upload(path, data, file_options={"content-type": file_storage.mimetype})
        url = client.storage.from_(bucket).get_public_url(path)
        return url
    except Exception as exc:  # pragma: no cover - external service
        if logger:
            try:
                logger.warning(f"Supabase upload failed, fallback local: {exc}")
            except Exception:
                pass
        return None
