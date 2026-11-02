/* The C library's memory and string functions, natively.
 *
 * The game imports them from the SDK, whose ARM versions otherwise run recompiled - memcpy
 * alone was about 1.5% of the main thread in an 8-player match. Guest pointers are host
 * pointers (on macOS, RT_MEMBASE above: P() and H2G()), so the host's own (vectorised) functions
 * do the same work. memcpy is memmove: the same result wherever the guest's would be defined,
 * and no surprise where it isn't. */
#include "host.h"

HLE(memcpy) { memmove(P(void, c->x[0]), P(const void, c->x[1]), (size_t)c->x[2]); }
HLE(memmove) { memmove(P(void, c->x[0]), P(const void, c->x[1]), (size_t)c->x[2]); }
HLE(memset) { memset(P(void, c->x[0]), (int)(u8)c->x[1], (size_t)c->x[2]); }
HLE(memcmp) { c->x[0] = (u64)(i64)memcmp(P(const void, c->x[0]), P(const void, c->x[1]), (size_t)c->x[2]); }
HLE(memchr) {
    const void *p = memchr(P(const void, c->x[0]), (int)(u8)c->x[1], (size_t)c->x[2]);
    c->x[0] = p ? H2G(p) : 0;
}
HLE(strlen) { c->x[0] = strlen(P(const char, c->x[0])); }
HLE(strcmp) { c->x[0] = (u64)(i64)strcmp(P(const char, c->x[0]), P(const char, c->x[1])); }
HLE(strncmp) { c->x[0] = (u64)(i64)strncmp(P(const char, c->x[0]), P(const char, c->x[1]), (size_t)c->x[2]); }
HLE(strchr) {
    const char *p = strchr(P(const char, c->x[0]), (int)(u8)c->x[1]);
    c->x[0] = p ? H2G(p) : 0;
}
HLE(strcpy) { strcpy(P(char, c->x[0]), P(const char, c->x[1])); }
