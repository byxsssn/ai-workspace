"""将 PostgreSQL 导出加密后上传私有 S3 桶；不依赖应用模块。"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"AIWBK1\x00"
CHUNK = 1024 * 1024
PREFIX = "ai-workspace/v1/"
OBJECT_NAME = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{32}\.dump\.aesgcm$")
RETENTION = {"daily": 6, "weekly": 27}


def encryption_key(key_file: Path | None = None) -> bytes:
    value = (
        key_file.read_text().strip()
        if key_file
        else os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    )
    try:
        key = base64.b64decode(value, altchars=b"-_", validate=True)
    except ValueError, TypeError:
        raise ValueError("Invalid backup encryption key") from None
    if len(key) != 32:
        raise ValueError("Backup encryption requires a 32-byte key")
    return key


def encrypt(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(MAGIC)
    with source.open("rb") as src, destination.open("xb") as dst:
        dst.write(MAGIC + nonce)
        while block := src.read(CHUNK):
            dst.write(cipher.update(block))
        dst.write(cipher.finalize())
        dst.write(cipher.tag)


def decrypt(source: Path, destination: Path, key: bytes) -> None:
    if destination.exists():
        raise FileExistsError("Restore destination already exists")
    # 完整性认证通过前，明文仅保存在私有临时文件中，失败时不生成目标文件。
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        temporary = Path(work) / "verified.dump"
        with source.open("rb") as src, temporary.open("xb") as dst:
            if src.read(len(MAGIC)) != MAGIC:
                raise ValueError("Unrecognized backup format")
            nonce = src.read(12)
            remaining = source.stat().st_size - len(MAGIC) - 12 - 16
            if remaining < 0:
                raise ValueError("Truncated backup")
            src.seek(-16, 2)
            tag = src.read(16)
            src.seek(len(MAGIC) + 12)
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            cipher.authenticate_additional_data(MAGIC)
            while remaining:
                block = src.read(min(CHUNK, remaining))
                if not block:
                    raise ValueError("Truncated backup")
                remaining -= len(block)
                dst.write(cipher.update(block))
            dst.write(cipher.finalize())
        # 以独占方式创建文件，避免覆盖解密期间新出现的同名目标文件。
        with temporary.open("rb") as src, destination.open("xb") as dst:
            while block := src.read(CHUNK):
                dst.write(block)


def dump_database(destination: Path) -> None:
    if not all(
        os.environ.get(name)
        for name in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    ):
        raise ValueError("Missing PostgreSQL backup configuration")
    env = dict(
        os.environ, PGCONNECT_TIMEOUT="15", PGOPTIONS="-c statement_timeout=240000"
    )
    try:
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--file",
                str(destination),
            ],
            env=env,
            capture_output=True,
            check=True,
            timeout=300,
        )
        subprocess.run(
            ["pg_restore", "--list", str(destination)],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except subprocess.SubprocessError, OSError:
        raise RuntimeError("PostgreSQL export or archive validation failed") from None


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("BACKUP_S3_ENDPOINT", "")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected an HTTPS storage endpoint")
    required = (
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_REGION",
        "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY",
    )
    if not all(os.environ.get(name) for name in required):
        raise ValueError("Missing private storage configuration")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ["BACKUP_S3_REGION"],
        aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["BACKUP_S3_SECRET_ACCESS_KEY"],
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3},
            s3={"addressing_style": "virtual"},
        ),
    )


def objects(client, bucket: str, kind: str) -> list[dict]:
    prefix = PREFIX + kind + "/"
    result = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        result.extend(
            item
            for item in page.get("Contents", [])
            if OBJECT_NAME.fullmatch(item["Key"].removeprefix(prefix))
        )
    return result


def verify_object(client, bucket: str, name: str, size: int, checksum: str) -> None:
    head = client.head_object(Bucket=bucket, Key=name)
    if (
        head["ContentLength"] != size
        or head.get("Metadata", {}).get("sha256") != checksum
    ):
        raise RuntimeError("Uploaded backup verification failed")


def publish(client, bucket: str, archive: Path, now: datetime) -> dict:
    from boto3.s3.transfer import TransferConfig

    name = now.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex + ".dump.aesgcm"
    daily = PREFIX + "daily/" + name
    size, checksum = archive.stat().st_size, digest(archive)
    client.upload_file(
        str(archive),
        bucket,
        daily,
        ExtraArgs={
            "ContentType": "application/octet-stream",
            "Metadata": {"sha256": checksum, "format": "pgdump-aes256gcm-v1"},
        },
        Config=TransferConfig(
            multipart_threshold=16 * CHUNK,
            multipart_chunksize=16 * CHUNK,
            max_concurrency=2,
        ),
    )
    verify_object(client, bucket, daily, size, checksum)
    weekly_rows = objects(client, bucket, "weekly")
    # 按新加坡／中国时区在周一保存周备份，首次运行也建立周恢复点。
    weekly = (now + timedelta(hours=8)).weekday() == 0 or not weekly_rows
    if weekly:
        weekly_name = PREFIX + "weekly/" + name
        client.copy_object(
            Bucket=bucket, Key=weekly_name, CopySource={"Bucket": bucket, "Key": daily}
        )
        verify_object(client, bucket, weekly_name, size, checksum)
    # 所有必需的上传和元信息检查成功后，才清理过期备份。
    deleted = 0
    for kind, days in RETENTION.items():
        rows = objects(client, bucket, kind)
        newest = max(rows, key=lambda row: row["LastModified"], default=None)
        expired = [
            {"Key": row["Key"]}
            for row in rows
            if row is not newest and row["LastModified"] < now - timedelta(days=days)
        ]
        for offset in range(0, len(expired), 1000):
            batch = expired[offset : offset + 1000]
            result = client.delete_objects(
                Bucket=bucket, Delete={"Objects": batch, "Quiet": True}
            )
            if result.get("Errors"):
                raise RuntimeError("Expired backup cleanup failed")
            deleted += len(batch)
    return {
        "daily": daily,
        "weekly_created": weekly,
        "encrypted_bytes": size,
        "sha256": checksum,
        "expired_deleted": deleted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decrypt",
        nargs=2,
        type=Path,
        metavar=("SOURCE", "DESTINATION"),
        help="校验并解密备份，依次指定源文件和目标文件",
    )
    parser.add_argument("--key-file", type=Path, help="存放备份加密密钥的受保护文件")
    parser.add_argument(
        "--local-backup", type=Path, help="在本地导出并加密，不上传到 S3"
    )
    args = parser.parse_args()
    try:
        key = encryption_key(args.key_file)
        if args.decrypt:
            decrypt(*args.decrypt, key)
            print("通过：备份完整性认证与解密完成")
            return 0
        client = None if args.local_backup else make_client()
        with tempfile.TemporaryDirectory(prefix="ai-workspace-backup-") as work:
            dump, archive = (
                Path(work) / "database.dump",
                Path(work) / "database.dump.aesgcm",
            )
            dump_database(dump)
            encrypt(dump, args.local_backup or archive, key)
            if args.local_backup:
                print("通过：PostgreSQL 归档导出与加密完成")
            else:
                result = publish(
                    client, os.environ["BACKUP_S3_BUCKET"], archive, datetime.now(UTC)
                )
                print(json.dumps({"status": "backup_complete", **result}), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - 命令行出口必须屏蔽 SDK 错误中的敏感内容。
        # SDK 或子进程异常可能包含签名 URL 或数据库凭据。
        print(
            json.dumps({"status": "backup_failed", "error_type": type(error).__name__}),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
