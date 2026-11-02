"""RC4 and NEX's Kerberos-style tickets, in pure Python."""
import hashlib
import hmac
import secrets
import struct


class RC4:
    """A running RC4 stream (PRUDP keeps one per direction and substream)."""

    def __init__(self, key):
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self.s, self.i, self.j = s, 0, 0

    def crypt(self, data):
        s, i, j = self.s, self.i, self.j
        out = bytearray(len(data))
        for n, b in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
        self.i, self.j = i, j
        return bytes(out)


def kerberos_encrypt(key, data):
    enc = RC4(key).crypt(data)
    return enc + hmac.new(key, enc, hashlib.md5).digest()


def kerberos_decrypt(key, data):
    """The plaintext, or None if the MAC does not match."""
    if len(data) < 16:
        return None
    enc, mac = data[:-16], data[-16:]
    if not hmac.compare_digest(mac, hmac.new(key, enc, hashlib.md5).digest()):
        return None
    return RC4(key).crypt(enc)


class TicketIssuer:
    """Tickets for the secure server. The client opens the outer ticket with the source key the
    authentication server hands it (as hex); the inner ticket only this server reads.

    Client ticket: session key (32), target PID (u64), Buffer(inner ticket).
    Inner ticket ("new" format): Buffer(random 16), Buffer(kerberos(MD5(server key + random),
    issued u64 | source PID u64 | session key))."""

    SESSION_KEY_SIZE = 32

    def __init__(self, server_key=None):
        self.server_key = server_key or secrets.token_bytes(16)

    def issue(self, source_pid, target_pid, issued):
        session_key = secrets.token_bytes(self.SESSION_KEY_SIZE)
        source_key = secrets.token_bytes(16)
        rnd = secrets.token_bytes(16)
        info = struct.pack("<QQ", issued, source_pid) + session_key
        inner_enc = kerberos_encrypt(hashlib.md5(self.server_key + rnd).digest(), info)
        inner = struct.pack("<I", len(rnd)) + rnd + struct.pack("<I", len(inner_enc)) + inner_enc
        body = session_key + struct.pack("<Q", target_pid) + struct.pack("<I", len(inner)) + inner
        return kerberos_encrypt(source_key, body), source_key.hex(), session_key

    def open_inner(self, inner):
        """(source PID, session key) of an inner ticket this server issued, or None."""
        try:
            n = struct.unpack_from("<I", inner, 0)[0]
            rnd = inner[4:4 + n]
            m = struct.unpack_from("<I", inner, 4 + n)[0]
            enc = inner[8 + n:8 + n + m]
        except struct.error:
            return None
        info = kerberos_decrypt(hashlib.md5(self.server_key + rnd).digest(), enc)
        if info is None or len(info) < 16 + self.SESSION_KEY_SIZE:
            return None
        _issued, pid = struct.unpack_from("<QQ", info, 0)
        return pid, info[16:16 + self.SESSION_KEY_SIZE]
