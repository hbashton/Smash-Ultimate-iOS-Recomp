#include "module.h"

GuestFn f_7108000100;
GuestFn f_7108000200;

static const RtFunc funcs[] = {
    {0x7108000100ull, f_7108000100},
    {0x7108000200ull, f_7108000200},
};
static const RtSym exports[] = {
    {"boottest_sum", 0x7108000200ull},
};
static const char *const imports[] = {
    "boottest_init_log",
    0
};
static const u8 import_weak[] = {0,0};
static const RtReloc relocs[] = {
    {0x9000ull, 0, 0},
    {0, 0, 0}
};

const RtModule rt_module_subsdk0 = {
    "subsdk0", 0x7108000000ull, 0x20000ull, 0x8000ull, 0x7108000100ull,
    funcs, 2, exports, 1, imports, import_weak, 1, relocs, 1, "B0075B50",
};
