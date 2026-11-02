#include "module.h"

GuestFn f_7109000100;
GuestFn f_7109000200;
GuestFn f_7109000300;
GuestFn f_7109007f00;
GuestFn f_7109007f10;
GuestFn f_7109007f20;

static const RtFunc funcs[] = {
    {0x7109000100ull, f_7109000100},
    {0x7109000200ull, f_7109000200},
    {0x7109000300ull, f_7109000300},
    {0x7109007F00ull, f_7109007f00},
    {0x7109007F10ull, f_7109007f10},
    {0x7109007F20ull, f_7109007f20},
};
static const RtSym exports[] = {
    {"_ZN2nn4init5StartEmmPFvvES2_", 0x7109000200ull},
    {"_ZN2nn2os30InitializeVirtualAddressMemoryEv", 0x7109000300ull},
    {"boottest_init_log", 0x7109009000ull},
};
static const char *const imports[] = {
    "nninitInitializeSdkModule",
    "nninitStartup",
    "nnMain",
    0
};
static const u8 import_weak[] = {0,0,0,0};
static const RtReloc relocs[] = {
    {0xA000ull, 0, 0},
    {0xA008ull, 0, 1},
    {0xA010ull, 0, 2},
    {0, 0, 0}
};

const RtModule rt_module_sdk = {
    "sdk", 0x7109000000ull, 0x20000ull, 0x8000ull, 0x7109000100ull,
    funcs, 6, exports, 3, imports, import_weak, 3, relocs, 3, "B0075D4",
};
