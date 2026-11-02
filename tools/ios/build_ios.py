#!/usr/bin/env python3
"""Cross-compile WindowSmash for iPadOS/iOS arm64 on a macOS GitHub runner.

The runner receives only generated recompilation C and relocatable module images. User game
content is added to the app locally by package_ipa.py and never enters the runner payload.
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAC_TOOLS = ROOT / "tools" / "mac"
sys.path.insert(0, str(MAC_TOOLS))
sys.path.insert(1, str(ROOT / "tools"))


def command(*args: str) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def configure(args: argparse.Namespace):
    """Reuse WindowSmash's tested source enumeration and recompilation compile policy."""
    sys.path.insert(0, str(MAC_TOOLS))
    tc = importlib.import_module("toolchain")
    sys.argv = [str(MAC_TOOLS / "build_mac.py"), "--port", str(args.port), "--out", str(args.out), "--jobs", str(args.jobs)]
    bm = importlib.import_module("build_mac")

    sdk = Path(command("xcrun", "--sdk", "iphoneos", "--show-sdk-path"))
    sdl = Path(args.sdl).resolve()
    if sdl.name == "SDL3.framework":
        framework = sdl
        framework_dir = sdl.parent
    else:
        framework = sdl / "ios-arm64" / "SDL3.framework"
        framework_dir = framework.parent
    if not (sdk.is_dir() and framework.is_dir() and (framework / "Headers" / "SDL.h").is_file()):
        raise SystemExit(f"iPhoneOS SDK or SDL3 iOS framework is missing: {sdk}, {framework}")

    target = ["-arch", "arm64", "-miphoneos-version-min=17.0", "-isysroot", str(sdk)]
    cflags = target + ["-mcpu=apple-m1", "-fno-strict-aliasing", "-ffp-contract=off", "-fno-stack-protector",
                       "-fvisibility=hidden", "-w"]
    tc.TARGET = target
    tc.CFLAGS = cflags
    tc.CXXFLAGS = ["-x", "c++", "-std=gnu++20", "-fexceptions"]
    tc.OBJCFLAGS = ["-fobjc-arc"]

    bm.TARGET = target
    bm.CFLAGS = cflags
    bm.CXXFLAGS = tc.CXXFLAGS
    bm.PORT = str(Path(args.port).resolve())
    bm.OUT = Path(args.out).resolve()
    bm.OBJ = bm.OUT / "obj"
    bm.GAME_SOURCES = bm.OBJ / "game_sources.tsv"
    bm.GEN = bm.OUT / "gen"
    bm.APP = bm.OUT / "WindowSmash.app"
    bm.EXE = bm.APP / "ssbu"
    bm.JOBS = args.jobs
    bm.SDL3_DIR = framework_dir
    bm.SDL3_FRAMEWORK = framework
    bm.RUNTIME_INCLUDES = ["-I", str(ROOT / "runtime" / "ios"),
                           "-I", str(ROOT / "runtime" / "macos"),
                           "-I", str(ROOT / "runtime" / "macos" / "include"),
                           "-I", str(ROOT / "runtime" / "linux" / "include"),
                           "-I", str(ROOT / "runtime"), "-I", str(bm.GEN),
                           "-F", str(framework_dir), "-I", str(ROOT / "third_party" / "libvpx")]
    bm.RUNTIME_DEFINES = ["-DSSBU_HAVE_OPUS", "-DSSBU_HAVE_VPX", "-DSSBU_HAVE_SDL3", "-DSSBU_IOS"]
    bm.BUNDLE_ID = "io.github.hbashton.windowsmash.ios"
    bm.IOS_FRAMEWORK_DIR = framework_dir
    bm.IOS_FRAMEWORK = framework
    bm.IOS_SDK = sdk
    bm.runtime_sources = lambda: ios_runtime_sources(bm)
    bm.runtime_headers = lambda: ios_runtime_headers(bm)
    bm.runtime_command = lambda: ios_runtime_command(bm)
    compile_each = bm.compile_each

    def compile_each_redacted(jobs_list, jobs=16, extra=()):
        failures = compile_each(jobs_list, jobs, extra)
        safe = []
        for source, diagnostic in failures:
            lines = [line for line in diagnostic.splitlines()
                     if re.search(r"(?i)(?:^|: )(?:fatal )?error:|undefined (?:reference|symbol)|linker command failed", line)]
            safe.append((source, "\n".join(lines[:20]) or "compiler failed; source context suppressed from public logs"))
        return safe

    bm.compile_each = compile_each_redacted
    return tc, bm


def ios_runtime_sources(bm):
    rt = ROOT / "runtime"
    out = bm.OBJ / "rt"
    own = [rt / n for n in bm.listing(rt) if re.fullmatch(bm.RUNTIME_PATTERN, n) and n not in bm.RUNTIME_LEFT_OUT]
    linux = [rt / "linux" / n for n in bm.LINUX_PARTS]
    ios = [rt / "ios" / n for n in bm.listing(rt / "ios", bm.MAC_SOURCES)]
    apple_names = ("profile.c", "gpu_metal.m", "gpu_metal_overlay.m")
    apple = [rt / "macos" / n for n in apple_names if (rt / "macos" / n).is_file()]
    return ([(str(s), o) for s, o in zip(own, bm.object_names(own, out))]
            + [(str(s), o) for s, o in zip(linux, bm.object_names(linux, out / "linux"))]
            + [(str(s), o) for s, o in zip(ios, bm.object_names(ios, out / "ios"))]
            + [(str(s), o) for s, o in zip(apple, bm.object_names(apple, out / "apple"))])


def ios_runtime_headers(bm):
    rt = ROOT / "runtime"
    found = [rt / n for n in bm.listing(rt, ".h")]
    folders = (rt / "linux" / "include", rt / "ios", rt / "macos")
    for folder in folders:
        for base, _dirs, names in os.walk(folder):
            found.extend(Path(base) / n for n in names if n.endswith(".h") and not n.startswith("._"))
    font = bm.GEN / "overlay_font.h"
    if font.is_file():
        found.append(font)
    return [str(p) for p in found]


def overlay_font_host(bm):
    """Generate the Metal overlay's small font table using a helper executable for the runner OS."""
    header = bm.GEN / "overlay_font.h"
    tool_src = MAC_TOOLS / "make_overlay_font.m"
    if not bm.needs(str(header), str(tool_src)):
        return
    bm.GEN.mkdir(parents=True, exist_ok=True)
    host_arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    host_sdk = command("xcrun", "--sdk", "macosx", "--show-sdk-path")
    tool = bm.GEN / "make_overlay_font"
    clang = bm.CLANG
    cmd = [clang, "-arch", host_arch, "-isysroot", host_sdk, "-O1", "-fobjc-arc", "-w", str(tool_src), "-o", str(tool),
           "-framework", "CoreText", "-framework", "CoreGraphics", "-framework", "Foundation"]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        result = subprocess.run([str(tool), str(header)], text=True, capture_output=True)
    if result.returncode:
        print(f"overlay font generation skipped: {(result.stdout + result.stderr)[:1000]}", flush=True)
        header.write_text("static const unsigned char overlay_font[%d] = {0};\n" % (12 * 16 * 24 * 6), encoding="utf-8")


def ios_runtime_command(bm):
    return subprocess.list2cmdline([bm.CLANG, *bm.CFLAGS, *bm.RUNTIME_INCLUDES, *bm.RUNTIME_DEFINES]) + "\n"


def codecs_for_ios(bm):
    import codec_libs
    libs = []
    for name, (lib, root, folders, includes, defines, have) in codec_libs.static_libraries(str(bm.OUT), "arm64").items():
        if not have():
            raise RuntimeError(f"third-party {name} sources or configuration are missing")
        flags = codec_libs.static_flags(bm.CFLAGS, root, includes, defines)
        current = subprocess.list2cmdline([bm.CLANG, *flags]) + "\n"
        if not codec_libs.static_is_current(lib, root, folders, includes, current):
            print(f"building {name} for iOS", flush=True)
            codec_libs.build_static_lib(name, lib, root, folders, flags, bm.CLANG, bm.AR, bm.JOBS,
                                        lambda line: print(line, flush=True))
            Path(lib + ".flags").write_text(current, encoding="utf-8")
        libs.append(lib)
    return libs


def ios_info_plist(version, has_icon):
    version = version or "13.0.5"
    result = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Smash Ultimate",
        "CFBundleExecutable": "ssbu",
        "CFBundleIdentifier": "io.github.hbashton.windowsmash.ios",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Smash Ultimate",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "LSRequiresIPhoneOS": True,
        "MinimumOSVersion": "17.0",
        "UIDeviceFamily": [1, 2],
        "UIRequiredDeviceCapabilities": ["arm64", "metal"],
        "UIRequiresFullScreen": True,
        "UISupportedInterfaceOrientations": ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
        "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
        "UILaunchScreen": {},
        "UIApplicationSupportsIndirectInputEvents": True,
        "GCSupportsControllerUserInteraction": True,
        "GCSupportedGameControllers": [{"ProfileName": name} for name in ("ExtendedGamepad", "Gamepad", "MicroGamepad", "DirectionalGamepad")],
        "UIFileSharingEnabled": True,
        "LSSupportsOpeningDocumentsInPlace": True,
        "NSLocalNetworkUsageDescription": "Smash Ultimate uses your local network for direct multiplayer sessions.",
    }
    if has_icon:
        result["CFBundleIcons"] = {
            "CFBundlePrimaryIcon": {
                "CFBundleIconFiles": ["AppIcon60x60@2x", "AppIcon60x60@3x", "AppIcon76x76@2x", "AppIcon83.5x83.5@2x"],
                "UIPrerenderedIcon": False,
            }
        }
    return result


def make_ios_icons(bm, resources):
    png = bm.ico_png(str(bm.ICON))
    if not png:
        return False
    source = bm.GEN / "ios-icon-source.png"
    source.write_bytes(png)
    sizes = (("AppIcon60x60@2x.png", 120), ("AppIcon60x60@3x.png", 180), ("AppIcon76x76@2x.png", 152),
             ("AppIcon83.5x83.5@2x.png", 167))
    for name, px in sizes:
        result = subprocess.run(["sips", "-s", "format", "png", "-z", str(px), str(px), str(source),
                                 "--out", str(resources / name)], text=True, capture_output=True)
        if result.returncode:
            print(f"app icon generation skipped: {(result.stdout + result.stderr)[:1000]}", flush=True)
            for written, _ in sizes:
                (resources / written).unlink(missing_ok=True)
            return False
    return True


def link_and_bundle(bm, tc):
    objs = bm.runtime_objects()
    for _name, src_dir, out_dir in bm.modules():
        objs += bm.game_objects(src_dir, out_dir)
    absent = [o for o in objs if not os.path.isfile(o)]
    if absent:
        print(f"not compiled yet: {len(absent)} objects ({', '.join(os.path.relpath(o, bm.OUT) for o in absent[:5])})", flush=True)
        return False
    libs = codecs_for_ios(bm)
    filelist = bm.OUT / "ssbu.filelist"
    filelist.write_text("".join(str(o) + "\n" for o in objs), encoding="utf-8")
    partial = bm.OUT / "ssbu.partial"
    frameworks = [arg for fw in ("SDL3", "Metal", "QuartzCore", "UIKit", "UniformTypeIdentifiers", "Foundation", "CoreFoundation",
                                 "CoreGraphics", "GameController", "AVFoundation", "AudioToolbox", "CoreHaptics")
                  for arg in ("-framework", fw)]
    print(f"linking {len(objs)} arm64 objects for iOS", flush=True)
    start = time.time()
    code, output = bm.run([bm.CLANG, *bm.TARGET, "-Xlinker", "-filelist", "-Xlinker", str(filelist), "-o", str(partial), *libs,
                           "-F", str(bm.IOS_FRAMEWORK_DIR), *frameworks, "-lc++", "-Wl,-rpath,@executable_path/Frameworks",
                           "-Wl,-no_deduplicate"])
    if code:
        # Keep public CI logs free of object paths and source context, but show the names
        # needed to resolve a failed link. The generic "Undefined symbols" header alone
        # is not actionable, and the old error-only filter silently dropped every symbol.
        undefined = False
        symbols = []
        for line in output.splitlines():
            lowered = line.lower()
            if "undefined symbols for architecture" in lowered:
                undefined = True
                continue
            if undefined and "not found for architecture" in lowered:
                break
            if undefined:
                match = re.match(r'\s*"([^"\r\n]+)"\s*,\s*referenced from:', line)
                if match:
                    symbols.append(match.group(1))
        if symbols:
            errors = ["unresolved symbols:", *sorted(set(symbols))]
        else:
            errors = [line for line in output.splitlines() if "error" in line.lower() or "undefined" in line.lower()] or output.splitlines()
        print("iOS link failed\n" + "\n".join(errors)[:8000], flush=True)
        return False

    app = bm.APP
    shutil.rmtree(app, ignore_errors=True)
    resources = app / "Resources"
    frameworks_dir = app / "Frameworks"
    resources.mkdir(parents=True)
    frameworks_dir.mkdir()
    has_icon = make_ios_icons(bm, resources)
    os.replace(partial, bm.EXE)
    source_modules = Path(bm.PORT) / "dump" / "modules"
    bundled_modules = resources / "dump" / "modules"
    bundled_modules.mkdir(parents=True)
    for source in source_modules.glob("*.reloc.bin"):
        shutil.copy2(source, bundled_modules / source.name)
    if not any(bundled_modules.glob("*.reloc.bin")):
        raise SystemExit("the private build payload did not contain any relocatable module images")
    result = subprocess.run(["ditto", str(bm.IOS_FRAMEWORK), str(frameworks_dir / "SDL3.framework")], text=True, capture_output=True)
    if result.returncode:
        print("could not embed SDL3.framework:\n" + (result.stdout + result.stderr)[:2000], flush=True)
        return False
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump(ios_info_plist(bm.tree_version(), has_icon), f, fmt=plistlib.FMT_XML, sort_keys=True)
    (app / "PkgInfo").write_text("APPL????", encoding="ascii")
    bm.EXE.chmod(0o755)
    print(f"iOS app bundle ready ({bm.EXE.stat().st_size / 1048576:,.0f} MB executable; {time.time() - start:,.0f}s link)", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="private payload root containing build/ and dump/modules/")
    parser.add_argument("--out", required=True, help="temporary build output directory")
    parser.add_argument("--sdl", required=True, help="SDL3.xcframework or its ios-arm64 SDL3.framework")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--preserve-objects", action="store_true", help="keep generated game objects for a private incremental cache")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 3:
        raise SystemExit("jobs must be between 1 and 3 on the hosted runner")
    tc, bm = configure(args)
    bm.OUT.mkdir(parents=True, exist_ok=True)
    if not bm.modules():
        raise SystemExit("no generated recompilation C was received")
    print(f"iOS arm64 build: {bm.PORT}", flush=True)
    bm.use_tree()
    overlay_font_host(bm)
    jobs = bm.runtime_jobs()
    if not bm.compile_all(jobs, "runtime iOS"):
        return 1
    (bm.OBJ / "rt" / "cflags.txt").write_text(ios_runtime_command(bm), encoding="utf-8")
    if not bm.compile_all(bm.game_jobs(), "generated game"):
        return 1
    if not link_and_bundle(bm, tc):
        return 1
    for name in ("codecs", "gen"):
        shutil.rmtree(bm.OUT / name, ignore_errors=True)
    if not args.preserve_objects:
        shutil.rmtree(bm.OUT / "obj", ignore_errors=True)
    for name in ("ssbu.filelist", "ssbu.syms", "ssbu.partial"):
        (bm.OUT / name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
