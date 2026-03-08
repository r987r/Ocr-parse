"""Cloud storage backend for session persistence across server restarts.

Supports any S3-compatible object store:
  - AWS S3            (https://aws.amazon.com/s3/)
  - Backblaze B2      (https://www.backblaze.com/cloud-storage) – free 10 GB
  - Cloudflare R2     (https://developers.cloudflare.com/r2/) – free 10 GB/month

All functions are **no-ops** when S3 is not configured, so the application
continues to work with local-only storage without any changes.

Required environment variables (omit all to disable S3):
  S3_BUCKET              Bucket name
  AWS_ACCESS_KEY_ID      Access key / Key ID
  AWS_SECRET_ACCESS_KEY  Secret key

Optional environment variables:
  S3_ENDPOINT_URL        Custom endpoint URL (required for B2 and R2)
  AWS_DEFAULT_REGION     AWS region (default: us-east-1)
"""

import logging
import os
from pathlib import Path

# Lazily-initialised boto3 S3 client (created on first use)
_s3_client = None


def is_s3_enabled() -> bool:
    """Return True when an S3 bucket and credentials are fully configured."""
    return bool(
        os.environ.get("S3_BUCKET")
        and os.environ.get("AWS_ACCESS_KEY_ID")
        and os.environ.get("AWS_SECRET_ACCESS_KEY")
    )


def _get_client():
    """Return (and lazily create) a boto3 S3 client."""
    global _s3_client
    if _s3_client is None:
        import boto3  # imported lazily – boto3 is only required when S3 is used

        endpoint = os.environ.get("S3_ENDPOINT_URL", "")
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        kwargs: dict = {"region_name": region}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def reset_client() -> None:
    """Force the next call to create a fresh boto3 client (useful in tests)."""
    global _s3_client
    _s3_client = None


def s3_upload(local_path: Path, key: str) -> bool:
    """Upload *local_path* to S3 at *key*.  Returns True on success."""
    if not is_s3_enabled():
        return False
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        _get_client().upload_file(str(local_path), bucket, key)
        logging.debug("S3 upload: %s → s3://%s/%s", local_path, bucket, key)
        return True
    except Exception as exc:
        logging.warning("S3 upload failed [%s]: %s", key, exc)
        return False


def s3_download(key: str, local_path: Path) -> bool:
    """Download the S3 object at *key* to *local_path*.  Returns True on success."""
    if not is_s3_enabled():
        return False
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _get_client().download_file(bucket, key, str(local_path))
        logging.debug("S3 download: s3://%s/%s → %s", bucket, key, local_path)
        return True
    except Exception as exc:
        logging.debug("S3 download failed [%s]: %s", key, exc)
        return False


def s3_exists(key: str) -> bool:
    """Return True when *key* exists in the configured S3 bucket."""
    if not is_s3_enabled():
        return False
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        _get_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def s3_delete_prefix(prefix: str) -> None:
    """Delete every S3 object whose key starts with *prefix*."""
    if not is_s3_enabled():
        return
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        client = _get_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                client.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects}
                )
    except Exception as exc:
        logging.warning("S3 delete_prefix failed [%s]: %s", prefix, exc)


def s3_list_keys(prefix: str) -> list[str]:
    """Return every S3 key that begins with *prefix*."""
    if not is_s3_enabled():
        return []
    bucket = os.environ.get("S3_BUCKET", "")
    try:
        client = _get_client()
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
    except Exception as exc:
        logging.warning("S3 list_keys failed [%s]: %s", prefix, exc)
        return []
