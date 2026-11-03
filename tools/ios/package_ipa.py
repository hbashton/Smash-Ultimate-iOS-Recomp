#!/usr/bin/env python3
"""Create a small private iOS IPA and a separate archive from the owner's prepared game data.

Game assets are streamed straight from the local prepared folder into a separate Zip64 archive.
They are not copied into the checkout, added to Git, included in the IPA, or sent to the macOS runner.
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


def separate_game_data(game_data: Path, app: Path) -> dict[str, Path]:
    source_modules = game_data / "modules"
    required = required_romfs_files(source_modules)
    inputs: dict[str, Path] = {}
    for name in required | COPYABLE_MODULES:
        source = source_modules / name
        if source.is_file():
            inputs[f"dump/modules/{name}"] = source
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
                inputs[f"dump/fonts/{source.name}"] = source
    return inputs


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(BLOCK):
            digest.update(block)
    return digest.hexdigest()


def keep_in_ipa(relative: str) -> bool:
    """Keep compiled module images; store owner game assets in the separate import archive."""
    parts = relative.split("/")
    if parts[:3] == ["Resources", "dump", "fonts"]:
        return False
    if parts[:3] == ["Resources", "dump", "modules"] and len(parts) > 3:
        return parts[3].endswith(".reloc.bin")
    return True


def app_files(app: Path):
    """Yield only app/runtime files, excluding game content even from an older full bundle."""
    for base, dirs, names in os.walk(app, topdown=True, followlinks=False):
        dirs[:] = sorted(dirs)
        base_path = Path(base)
        for name in list(dirs):
            source = base_path / name
            relative = source.relative_to(app).as_posix()
            if source.is_symlink() and keep_in_ipa(relative):
                dirs.remove(name)
                yield relative, source
        for name in sorted(names):
            source = base_path / name
            relative = source.relative_to(app).as_posix()
            if keep_in_ipa(relative):
                yield relative, source


def write_game_data_archive(output: Path, files: dict[str, Path]) -> None:
    partial = output.with_suffix(output.suffix + ".partial")
    partial.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True) as zf:
            zf.writestr("dump/", b"")
            zf.writestr("dump/modules/", b"")
            if any(name.startswith("dump/fonts/") for name in files):
                zf.writestr("dump/fonts/", b"")
            for name, source in sorted(files.items()):
                zip_file(zf, name, source)
            zf.writestr("README.txt", (
                "Super Smash Bros. Ultimate 13.0.5 game data\n\n"
                "The small IPA does not contain the game files. Transfer this ZIP to the iPad and "
                "extract it in Files. Launch the app and tap Choose dump folder. Select the extracted "
                "top-level dump folder (the one containing modules). The app copies the files into "
                "its Documents folder and starts the game when the import is complete. Keep the app "
                "open and leave the folder names unchanged.\n"
            ).encode("utf-8"))
        with zipfile.ZipFile(partial, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise OSError(f"game-data archive checksum failed: {bad}")
            if "dump/modules/romfs.cfg" not in zf.namelist():
                raise OSError("game-data archive is missing dump/modules/romfs.cfg")
        os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def zip_file(
    zf: zipfile.ZipFile,
    archive_name: str,
    source: Path,
    *,
    compression: int = zipfile.ZIP_STORED,
) -> tuple[int, int]:
    st = source.lstat()
    if stat.S_ISLNK(st.st_mode):
        data = os.readlink(source).encode("utf-8")
        info = zipfile.ZipInfo(archive_name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        info.compress_type = compression
        info.file_size = len(data)
        zf.writestr(info, data)
        return len(data), zlib.crc32(data)

    info = zipfile.ZipInfo(archive_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (st.st_mode & 0o777)) << 16
    info.file_size = st.st_size
    info.compress_type = compression
    crc = 0
    total = 0
    # Most iOS app files, including the 1 GB recomp executable, fit in classic ZIP
    # headers. Only force Zip64 for a single entry when its declared size needs it;
    # unnecessary Zip64 local headers confuse some third-party IPA signing tools.
    force_zip64 = st.st_size >= zipfile.ZIP64_LIMIT
    with source.open("rb") as src, zf.open(info, "w", force_zip64=force_zip64) as dest:
        while block := src.read(BLOCK):
            dest.write(block)
            crc = zlib.crc32(block, crc)
            total += len(block)
    if total != st.st_size:
        raise OSError(f"file changed while packaging: {source}")
    return total, crc & 0xFFFFFFFF


def zip_directory(zf: zipfile.ZipFile, archive_name: str) -> None:
    info = zipfile.ZipInfo(archive_name.rstrip("/") + "/")
    info.create_system = 3
    info.external_attr = (stat.S_IFDIR | 0o755) << 16
    info.compress_type = zipfile.ZIP_STORED
    zf.writestr(info, b"")


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
    if not (executable.stat().st_mode & 0o111):
        raise SystemExit("the app executable is missing its executable permission")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path, help="built WindowSmash.app directory")
    parser.add_argument("--game-data", type=Path, help="owner's prepared dump directory (contains modules/ and optionally fonts/)")
    parser.add_argument("--out", required=True, type=Path, help="destination IPA")
    parser.add_argument("--game-data-out", type=Path, help="separate ZIP for importing game data from Files")
    args = parser.parse_args()
    app = args.app.resolve()
    game_data = args.game_data.resolve() if args.game_data is not None else None
    output = args.out.resolve()
    game_output = args.game_data_out.resolve() if args.game_data_out is not None else None
    if not app.is_dir():
        raise SystemExit("the built app directory must exist")
    if (args.game_data is None) != (args.game_data_out is None):
        raise SystemExit("--game-data and --game-data-out must be supplied together")
    if game_data is not None and not game_data.is_dir():
        raise SystemExit("the prepared game-data directory must exist")
    if output == game_output and game_output is not None:
        raise SystemExit("the IPA and game-data archive must have different output paths")
    verify_bundle(app)
    data_files = separate_game_data(game_data, app) if game_data is not None else None
    if output.exists() or (game_output is not None and game_output.exists()):
        raise SystemExit("an output already exists; choose new filenames for both files")
    output.parent.mkdir(parents=True, exist_ok=True)
    if game_output is not None:
        game_output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    partial.unlink(missing_ok=True)
    total = 0
    source_crc: dict[str, tuple[int, int]] = {}
    try:
        with zipfile.ZipFile(
            partial,
            "w",
            allowZip64=True,
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=3,
        ) as zf:
            zip_directory(zf, "Payload/")
            zip_directory(zf, "Payload/WindowSmash.app/")
            files = list(app_files(app))
            for index, (relative, source) in enumerate(files, 1):
                size, crc = zip_file(
                    zf,
                    f"Payload/WindowSmash.app/{relative}",
                    source,
                    compression=zipfile.ZIP_DEFLATED,
                )
                source_crc[f"Payload/WindowSmash.app/{relative}"] = (size, crc)
                total += size
                if index % 100 == 0 or index == len(files):
                    print(f"IPA packaging: {index}/{len(files)} files, {total / 1_000_000_000:.2f} GB", flush=True)
        with zipfile.ZipFile(partial, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise OSError(f"IPA checksum failed: {bad}")
            if "Payload/" not in zf.namelist() or "Payload/WindowSmash.app/" not in zf.namelist():
                raise OSError("IPA is missing explicit Payload directory entries")
            for item in zf.infolist():
                if item.file_size < zipfile.ZIP64_LIMIT and item.extract_version >= 45:
                    raise OSError(f"IPA entry unnecessarily requires Zip64: {item.filename}")
            for name, (size, crc) in source_crc.items():
                item = zf.getinfo(name)
                if item.file_size != size or item.CRC != crc:
                    raise OSError(f"IPA entry did not match its source: {name}")
            info = plistlib.loads(zf.read("Payload/WindowSmash.app/Info.plist"))
            if info.get("GCSupportsControllerUserInteraction") is not True:
                raise OSError("controller capability was lost from the packaged IPA")
            executable_info = zf.getinfo("Payload/WindowSmash.app/ssbu")
            if not ((executable_info.external_attr >> 16) & 0o111):
                raise OSError("the IPA executable is missing its executable permission")
            if executable_info.compress_type != zipfile.ZIP_DEFLATED:
                raise OSError("the IPA executable must use standard DEFLATE compression")
            bundled_user_data = [item.filename for item in zf.infolist()
                                 if item.filename.startswith("Payload/WindowSmash.app/Resources/dump/")
                                 and "/modules/" in item.filename and not item.is_dir()
                                 and not item.filename.endswith(".reloc.bin")]
            if bundled_user_data:
                raise OSError("game data was unexpectedly included in the small IPA")
        if game_output is not None and data_files is not None:
            write_game_data_archive(game_output, data_files)
        os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        if game_output is not None:
            game_output.unlink(missing_ok=True)
        raise
    print(f"Small IPA ready: {output} ({output.stat().st_size / 1_000_000_000:.2f} GB; unsigned)", flush=True)
    if game_output is not None:
        print(f"Game data ready: {game_output} ({game_output.stat().st_size / 1_000_000_000:.2f} GB; never sent to the build runner)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
