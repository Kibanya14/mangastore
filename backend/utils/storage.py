import os
import secrets
import time

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # pragma: no cover - optional dependency
    cloudinary = None


def _cloudinary_configured():
    return (
        cloudinary is not None
        and os.getenv("CLOUDINARY_CLOUD_NAME")
        and os.getenv("CLOUDINARY_API_KEY")
        and os.getenv("CLOUDINARY_API_SECRET")
    )


def _cloudinary_folder(subfolder: str = "") -> str:
    base = os.getenv("CLOUDINARY_FOLDER", "manga")
    if not subfolder:
        return base
    return f"{base.strip('/')}/{subfolder.strip('/')}"


def upload_to_cloudinary(file_storage, subfolder: str, logger=None, resource_type: str = "auto") -> str | None:
    """Upload a Werkzeug FileStorage to Cloudinary. Returns secure URL or None on failure."""
    if not _cloudinary_configured() or not file_storage:
        return None

    try:
        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
            secure=True,
        )
        public_id = f"{int(time.time())}_{secrets.token_hex(6)}"
        result = cloudinary.uploader.upload(
            file_storage,
            folder=_cloudinary_folder(subfolder),
            public_id=public_id,
            overwrite=False,
            resource_type=resource_type,
        )
        return result.get("secure_url")
    except Exception as exc:  # pragma: no cover - external service
        if logger:
            try:
                logger.warning(f"Cloudinary upload failed: {exc}")
            except Exception:
                pass
        return None
