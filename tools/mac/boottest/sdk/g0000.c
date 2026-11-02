#include "recomp.h"

/* The boot test's sdk (tools/mac/boottest): hand-written as tools/recomp.py writes a module's code,
 * at the sdk's address. Its nn::init::Start does what the real one does for the loader - the SDK's
 * own start-up (nninitInitializeSdkModule, the runtime's, which calls back in), the modules'
 * initialisers (the loader's call_initializers, in x3), nninitStartup, then nnMain. A string is
 * at the address before it ("..."): tools/mac/boottest/boottest.py puts it in the image there. */

GuestFn f_7109000100;
GuestFn f_7109000200;
GuestFn f_7109000300;
GuestFn f_7109007f00;
GuestFn f_7109007f10;
GuestFn f_7109007f20;

void f_7109000100(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(8) = 0x7109009000ull;
    X(9) = ld32(X(8) + 0x0ull);
    X(10) = X(8) + X(9);
    X(11) = 0x53ull;
    st8(X(10) + 0x4ull, (u8)X(11));
    X(9) = (u32)((u32)X(9) + 0x1u);
    st32(X(8) + 0x0ull, (u32)X(9));
    return;
}

void f_7109000200(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    { u64 a_ = SP + 0xFFFFFFFFFFFFFFE0ull; st64(a_, (u64)X(29)); st64(a_ + 8, (u64)X(30)); SP = a_; }
    { u64 a_ = SP + 0x10ull; st64(a_, (u64)X(19)); st64(a_ + 8, (u64)X(20)); }
    X(29) = SP + 0x0ull;
    X(19) = X(3);
    X(20) = X(0);
    X(0) = 0x7109008100ull; X(1) = 42;   /* "boottest: nn::init::Start runs (the sdk's)" */
    rt_svc(c, 0x27);
    X(30) = 0x7109000230ull; f_7109007f00(c);
    X(8) = 0x7109009000ull;
    X(9) = ld32(X(8) + 0x100ull);
    { u32 a_ = (u32)X(9), b_ = 0x1u, r_ = a_ - b_; nf = r_ >> 31; zf = r_ == 0; cf = a_ >= b_; vf = ((a_ ^ b_) & (a_ ^ r_)) >> 31; (void)(r_); }
    if (!zf) goto L_2c0;
    X(0) = 0x7109008180ull; X(1) = 76;   /* "boottest: nninitInitializeSdkModule (the runtime's) called back into the sdk" */
    rt_svc(c, 0x27);
    { u64 t_ = X(19); X(30) = 0x7109000258ull; rt_lookup(t_)(c); }
    X(30) = 0x710900025Cull; f_7109007f10(c);
    X(0) = 0x0ull;
    X(1) = 0x0ull;
    X(30) = 0x7109000268ull; f_7109007f20(c);
    X(0) = 0x7109008200ull; X(1) = 25;   /* "boottest: nnMain returned" */
    rt_svc(c, 0x27);
    X(0) = 0x1ull;
    X(1) = 0x7109008200ull;
    X(2) = 0x0ull;
    rt_svc(c, 0x26);
    rt_trap(c, 0x7109000288ull, "udf");
L_2c0:;
    X(0) = 0x1ull;
    X(1) = 0x7109008180ull;
    X(2) = 0x0ull;
    rt_svc(c, 0x26);
    rt_trap(c, 0x71090002D0ull, "udf");
}

void f_7109000300(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(8) = 0x7109009000ull;
    X(9) = 0x1ull;
    st32(X(8) + 0x100ull, (u32)X(9));
    return;
}

void f_7109007f00(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(16) = 0x710900A000ull;
    X(17) = ld64(X(16) + 0x0ull);
    X(16) = X(16) + 0x0ull;
    { static RtCache k_; GuestFn *t_ = rt_target(X(17), &k_); __attribute__((musttail)) return t_(c); }
}

void f_7109007f10(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(16) = 0x710900A000ull;
    X(17) = ld64(X(16) + 0x8ull);
    X(16) = X(16) + 0x8ull;
    { static RtCache k_; GuestFn *t_ = rt_target(X(17), &k_); __attribute__((musttail)) return t_(c); }
}

void f_7109007f20(Cpu *c) {
    u32 nf = 0, zf = 0, cf = 0, vf = 0; u64 ex_a = 0, ex_v = 0;
    X(16) = 0x710900A000ull;
    X(17) = ld64(X(16) + 0x10ull);
    X(16) = X(16) + 0x10ull;
    { static RtCache k_; GuestFn *t_ = rt_target(X(17), &k_); __attribute__((musttail)) return t_(c); }
}
