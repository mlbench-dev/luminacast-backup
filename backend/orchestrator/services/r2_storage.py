"""Cloudflare R2 storage service adapter via boto3."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import partial

import boto3
from botocore.config import Config as BotoConfig

from config import settings

logger = logging.getLogger(__name__)

# Cache-Control for immutable per-variant media (editor preview clip mp4s).
# Each clip key is content-addressed by variant id and never mutated in place,
# so it is safe for the CDN (Cloudflare) and browsers to cache it long-term.
# This lets Cloudflare serve cached hits instead of round-tripping to the R2
# origin on every Range request (the source of slow editor preplay from China).
IMMUTABLE_CACHE_CONTROL = "public, max-age=2592000, immutable"


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


async def _retry_async(func, *args, max_retries=3, base_delay=2.0, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            _log(
                "warning",
                func.__module__ or "service",
                f"Retry {attempt + 1}/{max_retries}: {e}",
                delay=delay,
            )
            await asyncio.sleep(delay)


class R2StorageService:
    def __init__(self):
        self.bucket = settings.R2_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )

    async def upload_bytes(self, data: bytes, key: str, content_type: str = "application/octet-stream", cache_control: str | None = None) -> str:
        """Upload raw bytes to R2. Returns the key."""
        _log("info", "r2_storage", "Uploading bytes", key=key, size=len(data))

        put_kwargs = dict(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        if cache_control:
            put_kwargs["CacheControl"] = cache_control

        loop = asyncio.get_event_loop()
        await _retry_async(
            loop.run_in_executor,
            None,
            partial(self.client.put_object, **put_kwargs),
        )

        _log("info", "r2_storage", "Bytes upload complete", key=key)
        return key

    async def upload_file(self, local_path: str, key: str, content_type: str = None, cache_control: str | None = None) -> str:
        """Upload file to R2. Returns the key."""
        if not content_type:
            import mimetypes
            content_type, _ = mimetypes.guess_type(local_path)
            content_type = content_type or "application/octet-stream"

        extra_args = {"ContentType": content_type}
        if cache_control:
            extra_args["CacheControl"] = cache_control

        _log("info", "r2_storage", "Uploading file", key=key, local_path=local_path, content_type=content_type)

        loop = asyncio.get_event_loop()
        await _retry_async(
            loop.run_in_executor,
            None,
            partial(self.client.upload_file, local_path, self.bucket, key, ExtraArgs=extra_args),
        )

        _log("info", "r2_storage", "File upload complete", key=key)
        return key

    async def download_file(self, key: str, local_path: str) -> str:
        """Download from R2. Returns local_path."""
        _log("info", "r2_storage", "Downloading file", key=key, local_path=local_path)

        loop = asyncio.get_event_loop()
        await _retry_async(
            loop.run_in_executor,
            None,
            partial(self.client.download_file, self.bucket, key, local_path),
        )

        _log("info", "r2_storage", "File download complete", key=key, local_path=local_path)
        return local_path

    def get_public_url(self, key: str, cache_bust: bool = False) -> str:
        """Get CDN public URL for browser-facing media (images, audio, video).
        Uses media.luminacast.com custom domain backed by Cloudflare CDN.
        Supports Range requests, caching, and progressive playback.
        Set cache_bust=True to append a timestamp query param that forces CDN
        and browser cache misses (use for mutable objects like cover images).
        """
        import time
        public_base = getattr(settings, 'R2_PUBLIC_URL', None) or 'https://media.luminacast.com'
        url = f"{public_base}/{key}"
        if cache_bust:
            url += f"?v={int(time.time())}"
        return url


    async def key_exists(self, key: str) -> bool:
        """Check if a key exists in R2 (HEAD request)."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    async def head_object(self, key: str):
        """HEAD request for an R2 object. Returns response dict or None."""
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return None

    def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate presigned GET URL — for server-to-server only (RunPod, FFmpeg).
        Do NOT use for browser-facing content — use get_public_url instead.
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url


_instance: R2StorageService | None = None


def get_r2_storage_service() -> R2StorageService:
    global _instance
    if _instance is None:
        _instance = R2StorageService()
    return _instance
