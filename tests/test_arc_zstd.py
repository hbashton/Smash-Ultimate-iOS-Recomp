"""arc.py's zstd: Python 3.14's compression.zstd, or the zstandard package on older Pythons."""
from pathlib import Path
import importlib
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import arc  # noqa: E402

SAMPLE = b'file system table ' * 300


def compress(data):
    try:
        from compression.zstd import compress as c
    except ImportError:
        import zstandard
        c = zstandard.compress
    return c(data)


class ZstdTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(arc.zstd_decompress(compress(SAMPLE)), SAMPLE)

    def test_the_fallback_package(self):
        if importlib.util.find_spec('zstandard') is None:
            self.skipTest('the zstandard package is not installed here')
        saved = {k: sys.modules.get(k) for k in ('compression', 'compression.zstd', 'arc')}
        try:
            sys.modules['compression'] = None       # as on Python 3.13 and older
            sys.modules['compression.zstd'] = None
            del sys.modules['arc']
            fallback = importlib.import_module('arc')
            self.assertEqual(fallback.zstd_decompress(compress(SAMPLE)), SAMPLE)
            self.assertIn('zstandard', fallback.zstd_decompress.__code__.co_names)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


if __name__ == '__main__':
    unittest.main()
