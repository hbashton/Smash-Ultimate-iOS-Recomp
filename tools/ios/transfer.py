#!/usr/bin/env python3
"""One-time authenticated transfer between the owner's PC and the public macOS runner.

Only generated recompilation C, manifests, and relocatable module images go to the runner. The
owner's ROMFS/NCAs are packaged locally after the compiled app bundle is returned.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import os
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CHUNK_LIMIT = 32 * 1024 * 1024
COPY_BLOCK = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(COPY_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def tar_info(source: Path, arcname: str) -> tarfile.TarInfo:
    item = tarfile.TarInfo(arcname)
    st = source.stat()
    item.size = st.st_size
    item.mode = 0o644
    item.mtime = 0
    item.uid = item.gid = 0
    item.uname = item.gname = ""
    return item


def create_payload(port: Path, archive: Path) -> tuple[int, int]:
    port = port.resolve()
    build = port / "build"
    modules = port / "dump" / "modules"
    if not build.is_dir() or not modules.is_dir():
        raise SystemExit("the local game tree needs build/ and dump/modules/ first")
    generated = [p for p in build.rglob("*") if p.is_file() and (p.suffix == ".c" or p.name == "manifest.tsv")]
    images = list(modules.glob("*.reloc.bin"))
    if not generated or not images:
        raise SystemExit("generated C or relocatable module images are missing")
    version_file = port / "version.txt"
    version = version_file.read_text(encoding="utf-8").splitlines()[0] if version_file.is_file() else "13.0.5"
    archive.parent.mkdir(parents=True, exist_ok=True)
    partial = archive.with_suffix(archive.suffix + ".partial")
    partial.unlink(missing_ok=True)
    total = 0
    try:
        with tarfile.open(partial, "w:gz", compresslevel=1, format=tarfile.PAX_FORMAT) as tf:
            version_data = (version.strip() + "\n").encode("ascii")
            vi = tarfile.TarInfo("version.txt")
            vi.size = len(version_data)
            vi.mode = 0o644
            vi.mtime = 0
            tf.addfile(vi, io.BytesIO(version_data))
            for source in sorted(generated):
                arcname = source.relative_to(port).as_posix()
                with source.open("rb") as stream:
                    tf.addfile(tar_info(source, arcname), stream)
                total += source.stat().st_size
            for source in sorted(images):
                arcname = f"dump/modules/{source.name}"
                with source.open("rb") as stream:
                    tf.addfile(tar_info(source, arcname), stream)
                total += source.stat().st_size
        os.replace(partial, archive)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print(f"Private payload prepared: {len(generated)} generated files, {len(images)} module images, "
          f"{total / 1_000_000_000:.2f} GB before compression; archive {archive.stat().st_size / 1_000_000_000:.2f} GB")
    return len(generated), len(images)


def read_token(args) -> str:
    token = os.environ.get(args.token_env, "") if args.token_env else ""
    token_file = getattr(args, "token_file", None)
    if token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit("a one-time transfer token of at least 32 characters is required")
    return token


def validate_transfer_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".trycloudflare.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise SystemExit("transfer URL must be a plain HTTPS Cloudflare quick-tunnel address")
    return url.rstrip("/")


def make_handler(token: str, payload: Path, chunks: Path, output: Path, payload_hash: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PrivateRecompTransfer/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            safe_path = urllib.parse.urlsplit(self.path).path
            print(f"Transfer {self.command} {safe_path} [{args[1]}]", flush=True)

        def reply(self, status: int, message: str) -> None:
            data = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            value = self.headers.get("Authorization", "")
            return value.startswith("Bearer ") and hmac.compare_digest(value[7:], token)

        def do_GET(self) -> None:
            if urllib.parse.urlsplit(self.path).path != "/payload":
                self.reply(404, "not found")
                return
            if not self.authorized():
                self.reply(403, "forbidden")
                return
            size = payload.stat().st_size
            start, end, status = 0, size - 1, 200
            request_range = self.headers.get("Range", "")
            if request_range:
                try:
                    unit, value = request_range.split("=", 1)
                    left, right = value.split("-", 1)
                    if unit != "bytes" or not left:
                        raise ValueError
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                    if start < 0 or start >= size or end < start:
                        raise ValueError
                    status = 206
                except ValueError:
                    self.reply(416, "invalid byte range")
                    return
            self.send_response(status)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("X-Payload-Sha256", payload_hash)
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with payload.open("rb") as source:
                    source.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = source.read(min(COPY_BLOCK, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self) -> None:
            if urllib.parse.urlsplit(self.path).path != "/upload":
                self.reply(404, "not found")
                return
            if not self.authorized():
                self.reply(403, "forbidden")
                return
            try:
                index = int(self.headers.get("X-Chunk-Index", "-1"))
                count = int(self.headers.get("X-Chunk-Count", "0"))
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                self.reply(400, "invalid chunk metadata")
                return
            if count < 1 or count > 512 or index < 0 or index >= count:
                self.reply(400, "invalid chunk index")
                return
            if length < 0 or length > CHUNK_LIMIT:
                self.reply(413, "chunk too large")
                return
            expected = self.headers.get("X-Chunk-Sha256", "").lower()
            if len(expected) != 64:
                self.reply(400, "missing chunk checksum")
                return
            chunks.mkdir(parents=True, exist_ok=True)
            target = chunks / f"chunk-{index:04d}.part"
            partial = chunks / f"chunk-{index:04d}.tmp"
            digest = hashlib.sha256()
            remaining = length
            try:
                with partial.open("wb") as dest:
                    while remaining:
                        block = self.rfile.read(min(COPY_BLOCK, remaining))
                        if not block:
                            raise ConnectionError("incomplete chunk")
                        dest.write(block)
                        digest.update(block)
                        remaining -= len(block)
                if not hmac.compare_digest(digest.hexdigest(), expected):
                    partial.unlink(missing_ok=True)
                    self.reply(400, "chunk checksum mismatch")
                    return
                os.replace(partial, target)
            except Exception:
                partial.unlink(missing_ok=True)
                self.reply(400, "incomplete chunk")
                return
            if index != count - 1:
                self.reply(200, f"received {index + 1}/{count}")
                return
            output.parent.mkdir(parents=True, exist_ok=True)
            assembled = output.with_suffix(output.suffix + ".partial")
            whole = hashlib.sha256()
            try:
                with assembled.open("wb") as dest:
                    for i in range(count):
                        part = chunks / f"chunk-{i:04d}.part"
                        if not part.is_file():
                            raise FileNotFoundError(part.name)
                        with part.open("rb") as source:
                            while block := source.read(COPY_BLOCK):
                                dest.write(block)
                                whole.update(block)
                os.replace(assembled, output)
            except Exception as error:
                assembled.unlink(missing_ok=True)
                self.reply(409, f"upload incomplete: {error}")
                return
            self.reply(200, f"received {output.stat().st_size} bytes; sha256={whole.hexdigest()}")

    return Handler


def serve(args) -> None:
    token = read_token(args)
    payload = Path(args.payload).resolve()
    output = Path(args.output).resolve()
    chunks = Path(args.chunks).resolve()
    if not payload.is_file():
        raise SystemExit("prepared payload archive is missing")
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(token, payload, chunks, output, sha256_file(payload)))
    print(f"One-time private transfer receiver ready on port {args.port} ({payload.stat().st_size / 1_000_000_000:.2f} GB payload).", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for item in members:
            path = (root / item.name).resolve()
            if path != root and root not in path.parents:
                raise SystemExit("payload contains an unsafe path")
            if item.issym():
                if Path(item.linkname).is_absolute():
                    raise SystemExit("payload contains an unsafe symbolic link")
                target = (path.parent / item.linkname).resolve()
                if target != root and root not in target.parents:
                    raise SystemExit("payload contains an unsafe symbolic link")
            elif not (item.isfile() or item.isdir()):
                raise SystemExit("payload contains an unsupported file type")
        tf.extractall(root, members=members, filter="data")
    print(f"Private build payload verified and unpacked ({len(members)} files).", flush=True)


def fetch(args) -> None:
    token = read_token(args)
    base = validate_transfer_url(args.url)
    output = Path(args.archive).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    digest_expected = None
    for attempt in range(1, 8):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Authorization": f"Bearer {token}"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(base + "/payload", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                digest_expected = response.headers.get("X-Payload-Sha256")
                if not digest_expected or len(digest_expected) != 64:
                    raise OSError("server did not supply a payload checksum")
                if offset and response.status != 206:
                    offset = 0
                mode = "ab" if offset and response.status == 206 else "wb"
                with partial.open(mode) as dest:
                    while block := response.read(COPY_BLOCK):
                        dest.write(block)
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            print(f"payload connection interrupted ({attempt}/7); resuming", flush=True)
            if attempt == 7:
                raise SystemExit(f"could not retrieve the private build payload: {error}")
            time.sleep(min(attempt * 2, 12))
            continue
        size_expected = int(response.headers.get("Content-Range", "").rsplit("/", 1)[-1]) if response.status == 206 else int(response.headers.get("Content-Length", "0"))
        if not size_expected or partial.stat().st_size == size_expected:
            break
        if attempt == 7:
            raise SystemExit("private build payload ended before its declared length")
        time.sleep(min(attempt * 2, 12))
    if sha256_file(partial) != digest_expected:
        partial.unlink(missing_ok=True)
        raise SystemExit("private build payload checksum mismatch")
    os.replace(partial, output)
    print(f"Private payload received and checksum verified ({output.stat().st_size / 1_000_000_000:.2f} GB).", flush=True)
    safe_extract(output, Path(args.extract_to))
    output.unlink()


def upload(args) -> None:
    token = read_token(args)
    app = Path(args.app).resolve()
    archive = Path(args.archive).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz", compresslevel=1, format=tarfile.PAX_FORMAT) as tf:
        tf.add(app, arcname=app.name, recursive=True)
    size = archive.stat().st_size
    count = (size + CHUNK_LIMIT - 1) // CHUNK_LIMIT
    if count > 512:
        raise SystemExit("built app bundle is too large for the private transfer")
    base = validate_transfer_url(args.url) + "/upload"
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for index in range(count):
            data = source.read(CHUNK_LIMIT)
            chunk_hash = hashlib.sha256(data).hexdigest()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream",
                       "Content-Length": str(len(data)), "X-Chunk-Index": str(index),
                       "X-Chunk-Count": str(count), "X-Chunk-Sha256": chunk_hash}
            request = urllib.request.Request(base, data=data, headers=headers, method="POST")
            last_error = None
            for attempt in range(1, 6):
                try:
                    with urllib.request.urlopen(request, timeout=300) as response:
                        response.read()
                    last_error = None
                    break
                except (OSError, urllib.error.URLError, TimeoutError) as error:
                    last_error = error
                    time.sleep(attempt * 2)
            if last_error:
                raise SystemExit(f"private app transfer failed at chunk {index + 1}/{count}: {last_error}")
            digest.update(data)
            print(f"Private app bundle transfer: {index + 1}/{count} chunks", flush=True)
    print(f"Private app bundle sent ({size / 1_000_000_000:.2f} GB; sha256={digest.hexdigest()}).", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    pack = sub.add_parser("prepare-payload")
    pack.add_argument("--port", required=True, type=Path)
    pack.add_argument("--archive", required=True, type=Path)
    server = sub.add_parser("serve")
    server.add_argument("--token-file", required=True)
    server.add_argument("--payload", required=True)
    server.add_argument("--output", required=True)
    server.add_argument("--chunks", required=True)
    server.add_argument("--port", type=int, default=8765)
    server.set_defaults(token_env=None)
    get = sub.add_parser("fetch")
    get.add_argument("--url", required=True)
    get.add_argument("--token-env", default="BUILD_TRANSFER_TOKEN")
    get.add_argument("--archive", required=True)
    get.add_argument("--extract-to", required=True)
    post = sub.add_parser("upload")
    post.add_argument("--url", required=True)
    post.add_argument("--token-env", default="BUILD_TRANSFER_TOKEN")
    post.add_argument("--app", required=True)
    post.add_argument("--archive", required=True)
    args = parser.parse_args()
    if args.mode == "prepare-payload":
        create_payload(args.port, args.archive)
    elif args.mode == "serve":
        serve(args)
    elif args.mode == "fetch":
        fetch(args)
    else:
        upload(args)


if __name__ == "__main__":
    main()
