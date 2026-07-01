from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Callable, Optional


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    endpoint = re.sub(r"^https?://", "", endpoint, flags=re.IGNORECASE)
    return endpoint.rstrip("/")


def _safe_object_name(filename: str) -> str:
    name = Path(filename).name.strip()
    stem = Path(name).stem
    ext = Path(name).suffix.lower() or ".pdf"
    normalized = unicodedata.normalize("NFKD", stem)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip("-._")
    return f"{ascii_name or 'document'}{ext}"


def build_oss_object_key(config: dict, filename: str) -> str:
    oss_cfg = config.get("parser", {}).get("oss", {})
    prefix = str(oss_cfg.get("prefix", "mineru-uploads")).strip().strip("/")
    upload_name = _safe_object_name(filename)
    return f"{prefix}/{upload_name}" if prefix else upload_name


def _get_oss_bucket(config: dict):
    try:
        import oss2
    except ImportError as exc:
        raise ImportError("oss2 is not installed. Run: pip install 'oss2>=2.18.0'") from exc

    access_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    endpoint_raw = os.getenv("OSS_ENDPOINT", "")
    bucket_name = os.getenv("OSS_BUCKET", "")

    missing = [
        key
        for key, value in {
            "OSS_ACCESS_KEY_ID": access_key_id,
            "OSS_ACCESS_KEY_SECRET": access_key_secret,
            "OSS_ENDPOINT": endpoint_raw,
            "OSS_BUCKET": bucket_name,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing OSS credentials: {', '.join(missing)}")

    endpoint = _normalize_endpoint(endpoint_raw)
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, f"https://{endpoint}", bucket_name)
    return bucket, endpoint, bucket_name


def sign_oss_download_url(object_key: str, config: dict, expiry: Optional[int] = None) -> str:
    bucket, _, _ = _get_oss_bucket(config)
    oss_cfg = config.get("parser", {}).get("oss", {})
    url_expiry = int(expiry if expiry is not None else oss_cfg.get("url_expiry", 3600))
    return str(bucket.sign_url("GET", object_key, url_expiry))


def oss_object_exists(object_key: str, config: dict) -> bool:
    bucket, _, _ = _get_oss_bucket(config)
    try:
        bucket.head_object(object_key)
        return True
    except Exception:
        return False


def upload_to_oss(
    file_path: str,
    config: dict,
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> str:
    """Upload a file to Aliyun OSS and return a signed temporary URL."""

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    oss_cfg = config.get("parser", {}).get("oss", {})
    url_expiry = int(oss_cfg.get("url_expiry", 3600))

    upload_name = _safe_object_name(original_name or path.name)
    object_key = build_oss_object_key(config, original_name or path.name)
    file_size_mb = path.stat().st_size / 1024 / 1024

    bucket, endpoint, bucket_name = _get_oss_bucket(config)
    _log(f"OSS endpoint: {endpoint}  bucket: {bucket_name}")
    _log(f"Uploading file: {upload_name} ({file_size_mb:.2f} MB) -> {object_key}")

    bucket.put_object_from_file(object_key, str(path))
    _log(f"Upload complete; signed URL expiry={url_expiry}s")

    return str(bucket.sign_url("GET", object_key, url_expiry))
