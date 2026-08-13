import boto3
from botocore.client import Config

from app.core.config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        use_ssl=settings.S3_USE_SSL,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket_exists() -> None:
    client = get_s3_client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.S3_BUCKET_NAME not in existing:
        client.create_bucket(Bucket=settings.S3_BUCKET_NAME)


def upload_fileobj(fileobj, key: str, content_type: str | None = None) -> None:
    extra_args = {"ContentType": content_type} if content_type else {}
    get_s3_client().upload_fileobj(fileobj, settings.S3_BUCKET_NAME, key, ExtraArgs=extra_args)


def download_fileobj(key: str, fileobj) -> None:
    get_s3_client().download_fileobj(settings.S3_BUCKET_NAME, key, fileobj)


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )


def delete_object(key: str) -> None:
    get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
