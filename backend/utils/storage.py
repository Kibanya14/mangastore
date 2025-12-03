import os
import secrets
import time
import logging

# Uploads persistants : Supabase Storage (public URL). Si les creds sont absents, on retourne None et l'appelant retombe sur le disque local.
_supabase_create_client = None
_storage3_create_client = None

_supabase_client_cache = None
_logger = logging.getLogger(__name__)


def _supabase_configured():
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY") and os.getenv("SUPABASE_BUCKET"))


def _import_supabase():
    """Import paresseux des clients supabase/storage3 pour éviter un ImportError au chargement du module."""
    global _supabase_create_client, _storage3_create_client
    if _supabase_create_client and _storage3_create_client:
        return True
    try:
        from supabase import create_client as _cc
        from storage3 import create_client as _sc_create
        _supabase_create_client = _cc
        _storage3_create_client = _sc_create
        return True
    except Exception as exc:
        try:
            _logger.warning(
                "Import Supabase/Storage3 a échoué: %s | url=%s key_set=%s bucket=%s",
                exc,
                os.getenv("SUPABASE_URL"),
                bool(os.getenv("SUPABASE_KEY")),
                os.getenv("SUPABASE_BUCKET"),
            )
        except Exception:
            pass
        return False


def _mask(value: str) -> str:
    """Masque une valeur sensible pour les logs (garde les 4 premiers/derniers caractères)."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


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
        # Pas de logger ici, log côté upload_media
        return None
    if not _import_supabase():
        return None
    try:
        client = _supabase_create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        bucket = os.getenv("SUPABASE_BUCKET")
        _supabase_client_cache = (client, bucket)
        return _supabase_client_cache
    except Exception as exc:
        try:
            _logger.warning(
                "Supabase create_client a échoué: %s | url=%s key_set=%s bucket=%s module=%s",
                exc,
                os.getenv("SUPABASE_URL"),
                bool(os.getenv("SUPABASE_KEY")),
                os.getenv("SUPABASE_BUCKET"),
                bool(create_client),
            )
        except Exception:
            pass
        return None


def _get_storage_only_client():
    """Fallback minimal via storage3 si create_client échoue."""
    if not _supabase_configured():
        return None
    if not _import_supabase():
        return None
    try:
        storage_url = os.getenv("SUPABASE_URL").rstrip("/") + "/storage/v1"
        client = _storage3_create_client(storage_url, os.getenv("SUPABASE_KEY"), is_async=False)
        bucket = os.getenv("SUPABASE_BUCKET")
        return (client, bucket)
    except Exception as exc:
        try:
            _logger.warning(
                "Storage3 fallback a échoué: %s | url=%s key_set=%s bucket=%s module=%s",
                exc,
                os.getenv("SUPABASE_URL"),
                bool(os.getenv("SUPABASE_KEY")),
                os.getenv("SUPABASE_BUCKET"),
                bool(_storage3_create_client),
            )
        except Exception:
            pass
        return None


def upload_media(file_storage, subfolder: str, logger=None, resource_type: str = "auto") -> str | None:
    """
    Upload vers Supabase Storage. Retourne une URL publique ou None si non dispo.
    Le code appelant peut retomber sur le stockage local en cas d'échec.
    """
    client_info = _get_supabase_client()
    if not client_info:
        # tenter fallback storage3
        client_info = _get_storage_only_client()
    if not client_info:
        if logger:
            try:
                logger.warning(
                    "Supabase storage non configuré ou client indisponible, fallback local. "
                    f"url={os.getenv('SUPABASE_URL')} key_set={bool(os.getenv('SUPABASE_KEY'))} "
                    f"bucket={os.getenv('SUPABASE_BUCKET')} module={bool(create_client)}"
                )
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
    except Exception as exc:
        data = None
        if logger:
            try:
                logger.warning(f"Supabase upload: impossible de lire le fichier avant upload: {exc}")
            except Exception:
                pass

    if not data:
        return None

    try:
        # client peut être un client Supabase ou Storage3 (create_client retourne un client storage direct)
        if hasattr(client, "storage"):
            client.storage.from_(bucket).upload(path, data, file_options={"content-type": file_storage.mimetype})
            url = client.storage.from_(bucket).get_public_url(path)
        elif hasattr(client, "from_"):
            client.from_(bucket).upload(path, data, file_options={"content-type": file_storage.mimetype})
            url = client.from_(bucket).get_public_url(path)
        else:
            raise RuntimeError("Client Supabase/Storage invalide (pas de méthode storage/from_)")
        return url
    except Exception as exc:  # pragma: no cover - external service
        if logger:
            try:
                logger.warning(
                    "Supabase upload failed, fallback local: %s | path=%s bucket=%s url=%s key_prefix=%s",
                    exc,
                    path,
                    bucket,
                    os.getenv("SUPABASE_URL"),
                    _mask(os.getenv("SUPABASE_KEY") or "")
                )
            except Exception:
                pass
        return None
