# libvpx/config-arm64/

What `../config/` is for the Windows build, for the macOS build (arm64, Apple silicon): the
headers libvpx's `configure` and `make` would generate, for the same VP9-decoder-only
configuration in plain C. `tools/codec_libs.py` (`build_static`) compiles the same sources with
this folder in place of `config/`.

They are `config/`'s, with what differs on this target:

- `vpx_config.h`: `VPX_ARCH_AARCH64 1` and `VPX_ARCH_X86_64 0`; `HAVE_PTHREAD_H 1` and
  `HAVE_UNISTD_H 1` (POSIX threads, as `configure` finds them); `CONFIG_GCC 1` and
  `CONFIG_MSVS 0`; `INLINE inline`. Every `HAVE_NEON*`, `HAVE_SVE*` stays 0 (`--disable-neon`):
  the NEON sources are not vendored.
- `vp9_rtcd.h`, `vpx_dsp_rtcd.h`, `vpx_scale_rtcd.h`: the function table is the same (every
  function is its `_c` version). The set-up at their end is what `build/make/rtcd.pl` writes
  for a target without CPU extensions (`--arch=generic`: `#include "vpx_config.h"` and an empty
  `setup_rtcd_internal`), in place of the x86 one, which includes `vpx_ports/x86.h` and asks the
  CPU what it has with `cpuid`.
- `vpx_version.h`: the same.

To make them again for a new libvpx release, after `config/` (`../../README.md`):

```sh
./configure --target=arm64-darwin23-gcc --disable-neon --disable-neon-dotprod --disable-neon-i8mm \
    --disable-sve --disable-sve2 --disable-vp8 --disable-vp9-encoder --enable-vp9-decoder \
    --disable-examples --disable-tools --disable-docs --disable-unit-tests --disable-install-docs \
    --disable-install-bins --disable-install-srcs --disable-webm-io --disable-libyuv \
    --disable-postproc --disable-vp9-postproc --disable-runtime-cpu-detect
```

then take its `vpx_config.h`, and run `build/make/rtcd.pl --arch=generic --sym=<name>
--config=libs-arm64-darwin23-gcc.mk <defs .pl>` for each of the three tables, as for `config/`.
