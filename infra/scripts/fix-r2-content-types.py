"""
One-time script to fix Content-Type on existing R2 objects.
Run manually: python fix-r2-content-types.py

Uses copy_object to update metadata without re-uploading.
"""
import boto3
import mimetypes
import os

R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
BUCKET = os.environ.get("R2_BUCKET", "luminacast")

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
)


def fix_content_types():
    paginator = s3.get_paginator("list_objects_v2")
    fixed = 0
    skipped = 0

    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            head = s3.head_object(Bucket=BUCKET, Key=key)
            current_type = head.get("ContentType", "")

            # Guess correct type
            guessed, _ = mimetypes.guess_type(key)
            if not guessed:
                # Default mappings for common extensions
                ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
                guessed = {
                    "mp4": "video/mp4",
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "png": "image/png",
                    "webp": "image/webp",
                }.get(ext)

            if not guessed or current_type == guessed:
                skipped += 1
                continue

            # Fix via copy-in-place
            print(f"Fixing {key}: {current_type} → {guessed}")
            s3.copy_object(
                Bucket=BUCKET,
                Key=key,
                CopySource={"Bucket": BUCKET, "Key": key},
                ContentType=guessed,
                MetadataDirective="REPLACE",
            )
            fixed += 1

    print(f"\nDone. Fixed: {fixed}, Skipped: {skipped}")


if __name__ == "__main__":
    fix_content_types()
