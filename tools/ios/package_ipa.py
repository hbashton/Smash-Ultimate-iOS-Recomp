#!/usr/bin/env python3
"""Create a private iOS IPA from a built app and the owner's prepared game data.

Game assets are streamed straight from the local prepared folder into a Zip64 archive. They are
not copied into the checkout, added to Git, or sent to the macOS runner.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import struct
import stat
import sys
import zipfile
import zlib
from pathlib import Path


COPYABLE_MODULES = {"romfs.cfg", "romfs_base.cfg", "romfs.layers", "romfs_patch.bin", "aoc.none", "aoc.cfg"}
BLOCK = 8 * 1024 * 1024


def required_romfs_files(module_dir: Path) -> set[str]:
    cfg = module_dir / "romfs.cfg"
    if not cfg.is_file():
        raise SystemExit(f"missing {cfg}")
    lines = [line.strip() for line in cfg.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not lines:
        raise SystemExit("romfs.cfg is empty")
    if lines[0] == "layered":
        if len(lines) != 4:
            raise SystemExit("layered romfs.cfg must contain a layer map and exactly its base and update images")
        required = {Path(line.replace("\\", "/")).name for line in lines[1:]}
    else:
        if len(lines) != 2:
            raise SystemExit("single-image romfs.cfg must contain a file path and byte offset")
        required = {Path(lines[0].replace("\\", "/")).name}
    required.add("romfs.cfg")
    missing = [name for name in sorted(required) if not (module_dir / name).is_file() or (module_dir / name).stat().st_size == 0]
    if missing:
        raise SystemExit("prepared game data is incomplete: " + ", ".join(missing))
    return required


def packaged_assets(game_data: Path, app: Path) -> dict[str, Path]:
    source_modules = game_data / "modules"
    required = required_romfs_files(source_modules)
    inputs: dict[str, Path] = {}
    for name in required | COPYABLE_MODULES:
        source = source_modules / name
        if source.is_file():
            inputs[f"Resources/dump/modules/{name}"] = source
    source_relocs = {source.name for source in source_modules.glob("*.reloc.bin")}
    bundled_relocs = {source.name for source in (app / "Resources" / "dump" / "modules").glob("*.reloc.bin")}
    if not source_relocs or source_relocs != bundled_relocs:
        raise SystemExit("the build's module-image set does not match the local game build")
    for source in source_modules.glob("*.reloc.bin"):
        bundled = app / "Resources" / "dump" / "modules" / source.name
        if not bundled.is_file():
            raise SystemExit(f"built app is missing {source.name}")
        if source.stat().st_size != bundled.stat().st_size or sha256_file(source) != sha256_file(bundled):
            raise SystemExit(f"recompiled image changed during the build: {source.name}")
    fonts = game_data / "fonts"
    if fonts.is_dir():
        for source in fonts.iterdir():
            if source.is_file() and source.suffix.lower() == ".bfttf":
                inputs[f"Resources/dump/fonts/{source.name}"] = source
    return inputs


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(BLOCK):
            digest.update(block)
    return digest.hexdigest()


def app_files(app: Path, overrides: dict[str, Path]):
    """Yield the app bundle's files, with game data read directly from its private source folder."""
    overridden = set(overrides)
    for base, dirs, names in os.walk(app, topdown=True, followlinks=False):
        dirs[:] = sorted(dirs)
        base_path = Path(base)
        for name in list(dirs):
            source = base_path / name
            relative = source.relative_to(app).as_posix()
            if source.is_symlink():
                dirs.remove(name)
                if relative not in overridden:
                    yield relative, source
        for name in sorted(names):
            source = base_path / name
            relative = source.relative_to(app).as_posix()
            if relative not in overridden:
                yield relative, source
    for relative, source in sorted(overrides.items()):
        yield relative, source


def zip_file(zf: zipfile.ZipFile, archive_name: str, source: Path) -> tuple[int, int]:
    st = source.lstat()
    if stat.S_ISLNK(st.st_mode):
        data = os.readlink(source).encode("utf-8")
        info = zipfile.ZipInfo(archive_name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = len(data)
        zf.writestr(info, data)
        return len(data), zlib.crc32(data)

    info = zipfile.ZipInfo(archive_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (st.st_mode & 0o777)) << 16
    info.file_size = st.st_size
    info.compress_type = zipfile.ZIP_STORED
    crc = 0
    total = 0
    with source.open("rb") as src, zf.open(info, "w", force_zip64=True) as dest:
        while block := src.read(BLOCK):
            dest.write(block)
            crc = zlib.crc32(block, crc)
            total += len(block)
    if total != st.st_size:
        raise OSError(f"file changed while packaging: {source}")
    return total, crc & 0xFFFFFFFF


def verify_bundle(app: Path) -> None:
    executable = app / "ssbu"
    framework = app / "Frameworks" / "SDL3.framework" / "SDL3"
    if not executable.is_file() or not framework.is_file():
        raise SystemExit("built app is missing its executable or embedded SDL3 framework")
    with executable.open("rb") as f:
        header = f.read(8)
    if len(header) < 8 or header[:4] != b"\xcf\xfa\xed\xfe" or struct.unpack("<I", header[4:8])[0] != 0x0100000C:
        raise SystemExit("the app executable is not a thin 64-bit arm64 iOS build")
    plist_path = app / "Info.plist"
    with plist_path.open("rb") as f:
        info = plistlib.load(f)
    if info.get("CFBundleExecutable") != "ssbu" or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
        raise SystemExit("Info.plist does not describe an iOS app")
    if info.get("GCSupportsControllerUserInteraction") is not True or not info.get("GCSupportedGameControllers"):
        raise SystemExit("Info.plist is missing controller support declarations")
    if (app / "_CodeSignature").exists():
        raise SystemExit("the app must be signed after all local game assets are added")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path, help="built WindowSmash.app directory")
    parser.add_argument("--game-data", required=True, type=Path, help="owner's prepared dump directory (contains modules/ and optionally fonts/)")
    parser.add_argument("--out", required=True, type=Path, help="destination IPA")
    args = parser.parse_args()
    app = args.app.resolve()
    game_data = args.game_data.resolve()
    output = args.out.resolve()
    if not app.is_dir() or not game_data.is_dir():
        raise SystemExit("app and prepared game-data directories must exist")
    verify_bundle(app)
    overrides = packaged_assets(game_data, app)
    if output.exists():
        raise SystemExit(f"output already exists; choose a new IPA filename: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    partial.unlink(missing_ok=True)
    total = 0
    source_crc: dict[str, tuple[int, int]] = {}
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True) as zf:
            zf.writestr("Payload/", b"")
            zf.writestr("Payload/WindowSmash.app/", b"")
            files = list(app_files(app, overrides))
            for index, (relative, source) in enumerate(files, 1):
                size, crc = zip_file(zf, f"Payload/WindowSmash.app/{relative}", source)
                source_crc[f"Payload/WindowSmash.app/{relative}"] = (size, crc)
                total += size
                if index % 100 == 0 or index == len(files):
                    print(f"IPA packaging: {index}/{len(files)} files, {total / 1_000_000_000:.2f} GB", flush=True)
        with zipfile.ZipFile(partial, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise OSError(f"IPA checksum failed: {bad}")
            for name, (size, crc) in source_crc.items():
                item = zf.getinfo(name)
                if item.file_size != size or item.CRC != crc:
                    raise OSError(f"IPA entry did not match its source: {name}")
            info = plistlib.loads(zf.read("Payload/WindowSmash.app/Info.plist"))
            if info.get("GCSupportsControllerUserInteraction") is not True:
                raise OSError("controller capability was lost from the packaged IPA")
        os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print(f"IPA ready: {output} ({output.stat().st_size / 1_000_000_000:.2f} GB; unsigned; private game assets included)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
