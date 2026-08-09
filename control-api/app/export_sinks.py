"""
export_sinks.py - upload a completed local Parquet file to S3 or ADLS, with
real byte-level progress.

This deliberately duplicates (in spirit, not by import) data-plane/export/
sinks/s3.py and sinks/adls.py: that package ships in the data-plane Docker
image, not control-api's, and the two aren't on a shared Python path, so
importing across them isn't viable without a build restructure. Both places
implement the exact same upload shape on purpose - if you fix a bug in one,
fix it in the other. A follow-on worth doing: hoist both onto a small shared
package mounted into both images.

Functions here take an already-written local Parquet file (control-api's
export job writes the whole result in one shot via pyarrow, then uploads -
see export_jobs.py) rather than the batch-writer interface data-plane/export
uses for its streaming HDB-history CLI path; the destination and progress
reporting are otherwise identical.
"""
from __future__ import annotations

import os


class SinkError(RuntimeError):
    pass


class SinkNotConfigured(SinkError):
    """Credentials/destination not configured. Message says exactly what to set."""


def upload_to_s3(local_path: str, bucket: str, key: str, region: str | None = None, progress_cb=None) -> dict:
    """progress_cb(bytes_done, bytes_total), called as boto3 streams the upload."""
    if not bucket or not key:
        raise SinkNotConfigured("S3 destination needs both a bucket and a key.")
    try:
        import boto3
    except ImportError as exc:
        raise SinkNotConfigured("boto3 is not installed (pip install boto3).") from exc

    region = region or os.environ.get("S3_REGION")
    client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    size = os.path.getsize(local_path)
    done = {"bytes": 0}

    def _cb(chunk_bytes):
        done["bytes"] += chunk_bytes
        if progress_cb:
            progress_cb(done["bytes"], size)

    try:
        client.upload_file(local_path, bucket, key, Callback=_cb)
    except Exception as exc:  # noqa: BLE001 - never pretend it uploaded
        raise SinkError(f"S3 upload failed: {exc}") from exc

    return {"sink": "s3", "uri": f"s3://{bucket}/{key}", "bucket": bucket, "key": key, "bytes": size}


def upload_to_adls(local_path: str, container: str, path: str, progress_cb=None) -> dict:
    """progress_cb(bytes_done, bytes_total), via azure-storage-blob's progress_hook."""
    if not container or not path:
        raise SinkNotConfigured("ADLS destination needs both a container and a path.")
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    account_url = os.environ.get("ADLS_ACCOUNT_URL")
    if not connection_string and not account_url:
        raise SinkNotConfigured(
            "ADLS sink not configured - set AZURE_STORAGE_CONNECTION_STRING, "
            "or ADLS_ACCOUNT_URL to authenticate via DefaultAzureCredential.")
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise SinkNotConfigured("azure-storage-blob is not installed (pip install azure-storage-blob).") from exc

    if connection_string:
        service = BlobServiceClient.from_connection_string(connection_string)
    else:
        from azure.identity import DefaultAzureCredential
        service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())

    blob_client = service.get_blob_client(container=container, blob=path)
    size = os.path.getsize(local_path)

    def _progress(current, total):
        if progress_cb:
            progress_cb(current, total or size)

    try:
        with open(local_path, "rb") as fh:
            blob_client.upload_blob(fh, overwrite=True, progress_hook=_progress)
    except Exception as exc:  # noqa: BLE001
        raise SinkError(f"ADLS upload failed: {exc}") from exc

    return {"sink": "adls", "uri": f"{container}/{path}", "container": container, "path": path, "bytes": size}
