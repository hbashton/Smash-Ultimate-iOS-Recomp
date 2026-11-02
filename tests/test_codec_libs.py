"""tools/codec_libs.py: the vendored codec sources and when their libraries are stale. No compiler needed."""
from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import codec_libs  # noqa: E402


class VendoredSourcesTests(unittest.TestCase):
    def test_sources_are_there(self):
        self.assertTrue(codec_libs.have_opus())
        self.assertTrue(codec_libs.have_vpx())
        opus = codec_libs.sources(codec_libs.OPUS, codec_libs.OPUS_FOLDERS)
        vpx = codec_libs.sources(codec_libs.VPX, codec_libs.VPX_FOLDERS)
        self.assertGreater(len(opus), 140)
        self.assertGreater(len(vpx), 40)
        for src in opus + vpx:
            self.assertTrue(src.endswith('.c'), src)
            self.assertTrue(src.startswith(codec_libs.THIRD_PARTY), src)
        self.assertIn(os.path.join(codec_libs.OPUS, 'src', 'opus_decoder.c'), opus)
        self.assertIn(os.path.join(codec_libs.VPX, 'vp9', 'vp9_dx_iface.c'), vpx)
        self.assertFalse(any(os.sep + 'encoder' + os.sep in s or 'vp9_encoder' in s for s in vpx))   # the decoder only

    def test_no_assembly_and_generated_headers(self):
        for folder, _dirs, files in os.walk(codec_libs.VPX):
            self.assertFalse([f for f in files if f.endswith('.asm')], folder)
        for name in ('vpx_config.h', 'vpx_version.h', 'vp9_rtcd.h', 'vpx_dsp_rtcd.h', 'vpx_scale_rtcd.h'):
            self.assertTrue(os.path.isfile(os.path.join(codec_libs.VPX, 'config', name)), name)
        config = Path(codec_libs.VPX, 'config', 'vpx_config.h').read_text()
        self.assertIn('#define CONFIG_VP9_DECODER 1', config)
        self.assertIn('#define CONFIG_VP9_ENCODER 0', config)
        self.assertIn('#define HAVE_SSE2 0', config)
        self.assertIn('#define CONFIG_RUNTIME_CPU_DETECT 0', config)

    def test_inputs_include_headers_and_this_module(self):
        inputs = codec_libs.inputs(codec_libs.OPUS, codec_libs.OPUS_FOLDERS, codec_libs.OPUS_INCLUDES)
        self.assertIn(os.path.join(codec_libs.OPUS, 'include', 'opus.h'), inputs)
        self.assertIn(os.path.abspath(codec_libs.__file__), inputs)


class StalenessTests(unittest.TestCase):
    def test_is_current(self):
        with tempfile.TemporaryDirectory(prefix='codec-libs-test-') as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            source = root / 'src' / 'a.c'
            source.write_text('int a;')
            lib = root / 'a.lib'
            old = time.time() - 100
            os.utime(source, (old, old))
            self.assertFalse(codec_libs.is_current(str(lib), str(root), ('src',), ()))
            lib.write_bytes(b'lib')
            # (the library must also be newer than codec_libs.py itself, which is older than this test run)
            self.assertTrue(codec_libs.is_current(str(lib), str(root), ('src',), ()))
            now = time.time() + 100
            os.utime(source, (now, now))
            self.assertFalse(codec_libs.is_current(str(lib), str(root), ('src',), ()))


if __name__ == '__main__':
    unittest.main()
