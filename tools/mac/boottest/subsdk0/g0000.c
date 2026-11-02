#include "recomp.h"

/* The boot test's subsdk0 (tools/mac/boottest): its initialiser, and a function main calls through
 * an import. */

GuestFn f_7108000100;
GuestFn f_7108000200;

void f_7108000100(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(8) = 0x7108009000ull;
    X(8) = ld64(X(8) + 0x0ull);
    X(9) = ld32(X(8) + 0x0ull);
    X(10) = X(8) + X(9);
    X(11) = 0x55ull;
    st8(X(10) + 0x4ull, (u8)X(11));
    X(9) = (u32)((u32)X(9) + 0x1u);
    st32(X(8) + 0x0ull, (u32)X(9));
    return;
}

void f_7108000200(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(0) = X(0) + X(1);
    return;
}
