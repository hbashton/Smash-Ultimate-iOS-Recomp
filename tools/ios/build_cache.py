#!/usr/bin/env python3
"""Fingerprint build inputs and preserve generated game objects in an encrypted cache.

The public Actions repository must never store readable game-derived objects. Cache bytes are
encrypted and authenticated with the same private token used by the one-time transfer endpoint.
Only generated-game objects are cached; small platform runtime objects are rebuilt each run.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path


MAGIC = b"WSIOSOBJ1"
SALT_BYTES = 16
IV_BYTES = 16
TAG_BYTES = 32
KDF_ROUNDS = 200_000
BLOCK_BYTES = 4 * 1024 * 1024
FINGERPRINT_FILES = (
    "runtime/recomp.h",
    "runtime/module.h",
    "tools/mac/build_mac.py",
    "tools/mac/toolchain.py",
)


def read_token(env_name: str) -> bytes:
    token = os.environ.get(env_name, "").strip().encode("utf-8")
    if len(token) < 32:
        raise SystemExit("the private build-cache token is missing or too short")
    return token


def digest_file(path: Path, digest) -> None:
    with path.open("rb") as source:
        while block := source.read(BLOCK_BYTES):
            digest.update(block)


def fingerprint(port: Path, repo: Path) -> str:
    """Hash generated C by content so same-size changes cannot reuse stale objects."""
    build = port / "build"
    generated = sorted(
        path for path in build.rglob("*")
        if path.is_file() and (path.suffix == ".c" or path.name == "manifest.tsv")
    )
    if not generated:
        raise SystemExit("generated recompilation C is missing; cannot fingerprint the object cache")
    inputs = [(path.relative_to(port).as_posix(), path) for path in generated]
    for relative in FINGERPRINT_FILES:
        path = repo / relative
        if not path.is_file():
            raise SystemExit(f"required compile input is missing: {relative}")
        inputs.append((relative, path))

    digest = hashlib.sha256()
    for name, path in sorted(inputs):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest_file(path, digest)
        digest.update(b"\0")
    return digest.hexdigest()


def derive_keys(token: bytes, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.pbkdf2_hmac("sha256", token, salt, KDF_ROUNDS, dklen=64)
    return material[:32], material[32:]


def generated_objects(obj: Path) -> list[Path]:
    found = []
    for folder in ("main", "subsdk0", "sdk", "nro"):
        root = obj / folder
        if root.is_dir():
            found.extend(path for path in root.rglob("*") if path.is_file() and path.suffix == ".o")
    return sorted(found)


def pack(args: argparse.Namespace) -> None:
    obj = Path(args.obj).resolve()
    archive = Path(args.archive).resolve()
    record = obj / "game_sources.tsv"
    objects = generated_objects(obj)
    if not record.is_file() or not objects:
        raise SystemExit("no generated game objects are available to preserve")
    archive.parent.mkdir(parents=True, exist_ok=True)
    token = read_token(args.token_env)
    salt, iv = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(IV_BYTES)
    enc_key, mac_key = derive_keys(token, salt)
    header = MAGIC + salt + iv

    with tempfile.TemporaryDirectory(prefix="ssbu-object-cache-", dir=archive.parent) as temp_name:
        temp = Path(temp_name)
        plain = temp / "objects.tar.gz"
        cipher = temp / "objects.enc"
        with tarfile.open(plain, "w:gz", compresslevel=1, format=tarfile.PAX_FORMAT) as tf:
            tf.add(record, arcname="obj/game_sources.tsv", recursive=False)
            for path in objects:
                tf.add(path, arcname=path.relative_to(obj.parent).as_posix(), recursive=False)
        with cipher.open("wb") as encrypted:
            try:
                subprocess.run(
                    ["openssl", "enc", "-aes-256-cbc", "-K", enc_key.hex(), "-iv", iv.hex(),
                     "-in", str(plain)],
                    check=True, stdout=encrypted, stderr=subprocess.PIPE,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                raise SystemExit(f"private cache encryption failed ({type(error).__name__})") from None
        partial = archive.with_suffix(archive.suffix + ".partial")
        metadata = archive.with_suffix(archive.suffix + ".meta")
        metadata_partial = metadata.with_suffix(metadata.suffix + ".partial")
        mac = hmac.new(mac_key, header, hashlib.sha256)
        try:
            with cipher.open("rb") as encrypted:
                while block := encrypted.read(BLOCK_BYTES):
                    mac.update(block)
            os.replace(cipher, partial)
            with metadata_partial.open("wb") as output:
                output.write(header)
                output.write(mac.digest())
            os.replace(partial, archive)
            os.replace(metadata_partial, metadata)
        except Exception:
            partial.unlink(missing_ok=True)
            metadata_partial.unlink(missing_ok=True)
            raise
    print(f"Encrypted game-object cache prepared ({len(objects)} objects, {archive.stat().st_size:,} bytes).")


def verify_archive(archive: Path, token: bytes) -> tuple[bytes, bytes, int]:
    metadata = archive.with_suffix(archive.suffix + ".meta")
    size = archive.stat().st_size
    header_size = len(MAGIC) + SALT_BYTES + IV_BYTES
    if size < 16 or not metadata.is_file() or metadata.stat().st_size != header_size + TAG_BYTES:
        raise SystemExit("encrypted game-object cache is empty or truncated")
    with metadata.open("rb") as source:
        header = source.read(header_size)
        if len(header) != header_size or not header.startswith(MAGIC):
            raise SystemExit("encrypted game-object cache has an unsupported format")
        salt = header[len(MAGIC):len(MAGIC) + SALT_BYTES]
        iv = header[len(MAGIC) + SALT_BYTES:]
        enc_key, mac_key = derive_keys(token, salt)
        actual_tag = source.read(TAG_BYTES)
        mac = hmac.new(mac_key, digestmod=hashlib.sha256)
        mac.update(header)
    with archive.open("rb") as source:
        while block := source.read(BLOCK_BYTES):
            mac.update(block)
    if not hmac.compare_digest(mac.digest(), actual_tag):
        raise SystemExit("encrypted game-object cache authentication failed")
    return enc_key, iv, size


def safe_extract(archive: Path, staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    root = staging.resolve()
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for item in members:
            path = (root / item.name).resolve()
            if path != root and root not in path.parents:
                raise SystemExit("cached object archive contains an unsafe path")
            if not (item.isfile() or item.isdir()):
                raise SystemExit("cached object archive contains an unsupported entry")
            if item.name != "obj/game_sources.tsv" and not item.name.startswith(("obj/main/", "obj/subsdk0/", "obj/sdk/", "obj/nro/")):
                raise SystemExit("cached object archive contains an unexpected entry")
        tf.extractall(root, members=members, filter="data")


def refresh_cached_game_objects(port: Path, obj: Path) -> int:
    """Rebase cached object records to the current extraction's source mtimes.

    The cache key already authenticates the full generated-source contents and all
    compile headers. A fresh runner can extract identical source files with newer
    mtimes, though, which makes build_mac.py reject otherwise valid cached objects.
    Refresh its size/mtime records and object mtimes after the authenticated cache
    has been restored, so the normal incremental checks can safely reuse them.
    """
    record = obj / "game_sources.tsv"
    if not record.is_file():
        raise SystemExit("cached game-object source record is missing")
    source_root = port / "build"
    repo_root = Path(__file__).resolve().parents[2]
    header_paths = [repo_root / "runtime" / "recomp.h", repo_root / "runtime" / "module.h"]
    header_mtime_ns = max((path.stat().st_mtime_ns for path in header_paths if path.is_file()), default=0)
    lines = record.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("# "):
        raise SystemExit("cached game-object compile record has no command header")

    refreshed = [lines[0]]
    count = 0
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 5:
            raise SystemExit("cached game-object compile record is malformed")
        key = fields[0]
        parts = key.split("/")
        if "\\" in key or not parts or parts[0] not in {"main", "subsdk0", "sdk", "nro"} \
                or any(part in ("", ".", "..") for part in parts):
            raise SystemExit("cached game-object compile record contains an unsafe path")
        relative = Path(*parts)
        source = source_root / relative.with_suffix(".c")
        cached_object = obj / relative
        if not source.is_file() or not cached_object.is_file():
            continue

        stat = source.stat()
        fields[3] = str(stat.st_size)
        fields[4] = str(int(stat.st_mtime))
        refreshed.append("\t".join(fields))

        object_stat = cached_object.stat()
        object_mtime_ns = max(object_stat.st_mtime_ns, stat.st_mtime_ns, header_mtime_ns,
                              time.time_ns()) + 1_000_000
        os.utime(cached_object, ns=(object_stat.st_atime_ns, object_mtime_ns))
        count += 1

    record.write_text("\n".join(refreshed) + "\n", encoding="utf-8")
    return count


def restore(args: argparse.Namespace) -> None:
    archive = Path(args.archive).resolve()
    out = Path(args.out).resolve()
    port = Path(args.port).resolve()
    obj = out / "obj"
    if obj.exists():
        raise SystemExit("object-cache destination already exists")
    token = read_token(args.token_env)
    enc_key, iv, _cipher_size = verify_archive(archive, token)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ssbu-object-restore-") as temp_name:
        temp = Path(temp_name)
        plain = temp / "objects.tar.gz"
        with plain.open("wb") as decrypted:
            try:
                subprocess.run(
                    ["openssl", "enc", "-d", "-aes-256-cbc", "-K", enc_key.hex(), "-iv", iv.hex(),
                     "-in", str(archive)],
                    check=True, stdout=decrypted, stderr=subprocess.PIPE,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                raise SystemExit(f"private cache decryption failed ({type(error).__name__})") from None
        staging = temp / "staging"
        safe_extract(plain, staging)
        staged_obj = staging / "obj"
        if not (staged_obj / "game_sources.tsv").is_file() or not generated_objects(staged_obj):
            raise SystemExit("decrypted game-object cache is incomplete")
        reused = refresh_cached_game_objects(port, staged_obj)
        if not reused:
            raise SystemExit("decrypted cache has no objects matching the current generated game")
        shutil.move(str(staged_obj), str(obj))
    print(f"Authenticated encrypted cache restored; refreshed {reused} generated game-object records for incremental linking.")


def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    fp = subs.add_parser("fingerprint")
    fp.add_argument("--port", required=True)
    fp.add_argument("--repo", required=True)
    pk = subs.add_parser("pack")
    pk.add_argument("--obj", required=True)
    pk.add_argument("--archive", required=True)
    pk.add_argument("--token-env", default="BUILD_TRANSFER_TOKEN")
    rs = subs.add_parser("restore")
    rs.add_argument("--archive", required=True)
    rs.add_argument("--out", required=True)
    rs.add_argument("--port", required=True, help="current generated source tree used to rebase cache timestamps")
    rs.add_argument("--token-env", default="BUILD_TRANSFER_TOKEN")
    args = parser.parse_args()
    if args.command == "fingerprint":
        print(fingerprint(Path(args.port).resolve(), Path(args.repo).resolve()))
    elif args.command == "pack":
        pack(args)
    else:
        restore(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
