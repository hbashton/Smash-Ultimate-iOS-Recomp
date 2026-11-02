"""NEX's data encoding, as SSBU (NEX 4.6, Switch) uses it: little-endian; PIDs are 64-bit;
structures carry a version header (u8 version, u32 length) for each class of their hierarchy."""
import struct


class StreamError(Exception):
    pass


class StreamOut:
    def __init__(self):
        self.b = bytearray()

    def get(self):
        return bytes(self.b)

    def write(self, data):
        self.b += data

    def u8(self, v): self.b += struct.pack("<B", v & 0xFF)
    def u16(self, v): self.b += struct.pack("<H", v & 0xFFFF)
    def u32(self, v): self.b += struct.pack("<I", v & 0xFFFFFFFF)
    def u64(self, v): self.b += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
    def s32(self, v): self.b += struct.pack("<i", v)
    def s64(self, v): self.b += struct.pack("<q", v)
    def f64(self, v): self.b += struct.pack("<d", v)
    def bool(self, v): self.u8(1 if v else 0)
    def pid(self, v): self.u64(v)
    def result(self, v): self.u32(v)
    def datetime(self, v): self.u64(v)

    def string(self, s):
        if s is None:
            self.u16(0)
            return
        data = s.encode("utf-8") + b"\0"
        self.u16(len(data))
        self.b += data

    def buffer(self, data):
        self.u32(len(data))
        self.b += data

    def qbuffer(self, data):
        self.u16(len(data))
        self.b += data

    def list(self, items, fn):
        self.u32(len(items))
        for item in items:
            fn(self, item)

    def map(self, items, kfn, vfn):
        self.u32(len(items))
        for k, v in items.items():
            kfn(self, k)
            vfn(self, v)

    def struct(self, fn, version=0):
        """One class level of a structure: version header, then what fn writes."""
        sub = StreamOut()
        fn(sub)
        self.u8(version)
        self.u32(len(sub.b))
        self.b += sub.b

    def anydata(self, name, levels):
        """AnyDataHolder: the class name, then the object - levels: [(version, fn), ...] from
        the base class (nn::nex::Data) down."""
        body = StreamOut()
        for version, fn in levels:
            body.struct(fn, version)
        self.string(name)
        self.u32(len(body.b) + 4)
        self.u32(len(body.b))
        self.b += body.b


class StreamIn:
    def __init__(self, data, pos=0, end=None):
        self.d = data
        self.p = pos
        self.end = len(data) if end is None else end

    def left(self):
        return self.end - self.p

    def eof(self):
        return self.p >= self.end

    def read(self, n):
        if n < 0 or self.p + n > self.end:
            raise StreamError(f"read past the end ({n} bytes at {self.p} of {self.end})")
        v = self.d[self.p:self.p + n]
        self.p += n
        return bytes(v)

    def _un(self, fmt, n):
        return struct.unpack(fmt, self.read(n))[0]

    def u8(self): return self._un("<B", 1)
    def u16(self): return self._un("<H", 2)
    def u32(self): return self._un("<I", 4)
    def u64(self): return self._un("<Q", 8)
    def s32(self): return self._un("<i", 4)
    def s64(self): return self._un("<q", 8)
    def f64(self): return self._un("<d", 8)
    def bool(self): return self.u8() != 0
    def pid(self): return self.u64()
    def result(self): return self.u32()
    def datetime(self): return self.u64()

    def string(self):
        n = self.u16()
        return self.read(n).split(b"\0", 1)[0].decode("utf-8", "replace") if n else ""

    def buffer(self): return self.read(self.u32())
    def qbuffer(self): return self.read(self.u16())

    def list(self, fn):
        n = self.u32()
        if n > 100000:
            raise StreamError(f"list of {n}")
        return [fn(self) for _ in range(n)]

    def map(self, kfn, vfn):
        n = self.u32()
        if n > 100000:
            raise StreamError(f"map of {n}")
        out = {}
        for _ in range(n):
            k = kfn(self)
            out[k] = vfn(self)
        return out

    def struct(self, fn=None):
        """One class level: returns (version, fn(substream) or the raw content)."""
        version = self.u8()
        n = self.u32()
        sub = StreamIn(self.d, self.p, self.p + n)
        if sub.end > self.end:
            raise StreamError("structure past the end")
        self.p += n
        return version, (fn(sub) if fn else sub.read(n))

    def anydata(self):
        """AnyDataHolder: (class name, raw object bytes)."""
        name = self.string()
        total = self.u32()
        n = self.u32()
        if total != n + 4:
            raise StreamError(f"AnyDataHolder lengths {total}/{n}")
        return name, self.read(n)


def datetime_of(t):
    """A time.struct_time (UTC) as NEX's DateTime."""
    return (t.tm_year << 26 | t.tm_mon << 22 | t.tm_mday << 17 | t.tm_hour << 12 | t.tm_min << 6 | t.tm_sec)


def datetime_now():
    import time
    return datetime_of(time.gmtime())


def clean(text, limit=80):
    """A client's string as it may go into the log: printable characters only, cut to limit."""
    text = str(text)
    out = "".join(ch if ch.isprintable() else "?" for ch in text[:limit])
    return out + ("..." if len(text) > limit else "")


def hexdump(data, limit=256):
    data = bytes(data)
    s = data[:limit].hex(" ")
    return s + (f" ... ({len(data)} bytes)" if len(data) > limit else "")
