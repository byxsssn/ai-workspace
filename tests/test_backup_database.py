import importlib.util
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.exceptions import InvalidTag

SPEC = importlib.util.spec_from_file_location(
    "backup_database", Path(__file__).parents[1] / "scripts/backup_database.py"
)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
NOW = datetime(2026, 10, 14, 19, tzinfo=UTC)


def test_archive_roundtrip_spans_chunks_and_refuses_overwrite(tmp_path):
    key = os.urandom(32)
    original = tmp_path / "original.dump"
    encrypted = tmp_path / "encrypted"
    restored = tmp_path / "restored.dump"
    original.write_bytes(os.urandom(backup.CHUNK + 113))
    backup.encrypt(original, encrypted, key)
    backup.decrypt(encrypted, restored, key)
    assert original.read_bytes() == restored.read_bytes()
    with pytest.raises(FileExistsError):
        backup.decrypt(encrypted, restored, key)


@pytest.mark.parametrize("damage", ["body", "tag", "truncated", "key"])
def test_failed_authentication_never_publishes_plaintext(tmp_path, damage):
    key = os.urandom(32)
    source, archive, output = (
        tmp_path / name for name in ("source", "archive", "output")
    )
    source.write_bytes(b"private-message-and-provider-key" * 100)
    backup.encrypt(source, archive, key)
    if damage == "key":
        key = os.urandom(32)
    else:
        data = bytearray(archive.read_bytes())
        if damage == "truncated":
            data = data[:20]
        else:
            data[-1 if damage == "tag" else 30] ^= 1
        archive.write_bytes(data)
    with pytest.raises((InvalidTag, ValueError)):
        backup.decrypt(archive, output, key)
    assert not output.exists()
    assert set(tmp_path.iterdir()) == {source, archive}


class Storage:
    def __init__(self):
        self.rows = {}
        self.deleted = []
        self.upload_error = False
        self.bad_head = False
        self.copy_error = False

    def get_paginator(self, _):
        return self

    def paginate(self, *, Bucket, Prefix):
        rows = [
            {"Key": key, **value}
            for key, value in self.rows.items()
            if key.startswith(Prefix)
        ]
        yield {"Contents": rows[:1]}
        yield {"Contents": rows[1:]}

    def upload_file(self, source, bucket, key, *, ExtraArgs, Config):
        if self.upload_error:
            raise RuntimeError("upload failed")
        self.rows[key] = {
            "LastModified": NOW,
            "ContentLength": Path(source).stat().st_size,
            "Metadata": ExtraArgs["Metadata"],
        }

    def head_object(self, *, Bucket, Key):
        return (
            {**self.rows[Key], "ContentLength": -1} if self.bad_head else self.rows[Key]
        )

    def copy_object(self, *, Bucket, Key, CopySource):
        if self.copy_error:
            raise RuntimeError("copy failed")
        self.rows[Key] = self.rows[CopySource["Key"]].copy()

    def delete_objects(self, *, Bucket, Delete):
        for row in Delete["Objects"]:
            self.deleted.append(row["Key"])
            del self.rows[row["Key"]]
        return {}


def old_object(storage, kind, age, suffix="a"):
    key = backup.PREFIX + kind + "/20260901T190000Z-" + suffix * 32 + ".dump.aesgcm"
    storage.rows[key] = {"LastModified": NOW - timedelta(days=age)}
    return key


def test_retention_respects_each_tier_and_leaves_unrelated_objects(tmp_path):
    storage = Storage()
    old_daily = old_object(storage, "daily", 7)
    keep_daily = old_object(storage, "daily", 6, "b")
    old_weekly = old_object(storage, "weekly", 28)
    keep_weekly = old_object(storage, "weekly", 26, "c")
    unrelated = backup.PREFIX + "daily/other-file"
    storage.rows[unrelated] = {"LastModified": NOW - timedelta(days=99)}
    archive = tmp_path / "archive"
    archive.write_bytes(b"already encrypted")
    result = backup.publish(storage, "bucket", archive, NOW)
    assert set(storage.deleted) == {old_daily, old_weekly}
    assert all(key in storage.rows for key in (keep_daily, keep_weekly, unrelated))
    assert result["expired_deleted"] == 2
    assert not result["weekly_created"]


@pytest.mark.parametrize("failure", ["upload_error", "bad_head", "copy_error"])
def test_upload_or_verification_failure_preserves_old_backups(tmp_path, failure):
    storage = Storage()
    old_daily = old_object(storage, "daily", 40)
    setattr(storage, failure, True)
    archive = tmp_path / "archive"
    archive.write_bytes(b"encrypted")
    with pytest.raises(RuntimeError):
        backup.publish(storage, "bucket", archive, NOW)
    assert old_daily in storage.rows and storage.deleted == []


def test_first_run_creates_both_daily_and_weekly_backups(tmp_path):
    storage = Storage()
    archive = tmp_path / "archive"
    archive.write_bytes(b"encrypted")
    result = backup.publish(storage, "bucket", archive, NOW)
    assert result["weekly_created"]
    assert len(storage.rows) == 2


def test_weekly_copy_runs_on_monday_in_shanghai(tmp_path):
    storage = Storage()
    old_object(storage, "weekly", 4)
    archive = tmp_path / "archive"
    archive.write_bytes(b"encrypted")
    sunday_utc = datetime(2026, 10, 18, 19, tzinfo=UTC)
    assert backup.publish(storage, "bucket", archive, sunday_utc)["weekly_created"]


def test_export_failure_redacts_subprocess_details(monkeypatch, tmp_path):
    for name in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"):
        monkeypatch.setenv(name, "private-value")
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        Mock(
            side_effect=subprocess.CalledProcessError(
                1, "pg_dump", stderr="private-value"
            )
        ),
    )
    with pytest.raises(RuntimeError, match="archive validation failed") as error:
        backup.dump_database(tmp_path / "dump")
    assert "private-value" not in str(error.value)
