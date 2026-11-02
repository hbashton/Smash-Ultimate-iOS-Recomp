/* Fibers for the macOS build of the Windows API (Linux's are in runtime/linux/win32.c): the same
 * design, on arm64. A fiber is a stack and the stack pointer it stopped at; SwitchToFiber saves
 * this one's registers on its stack and resumes the other's (fiber_arm64.S). A new fiber's
 * stack is reserved whole with a 64 KiB inaccessible guard below it, and starts with a frame
 * that "returns" into the trampoline, which calls rt_fiber_entry. */
#include <mach/vm_statistics.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#include <windows.h>

typedef uint8_t u8;
typedef uint64_t u64;

typedef struct Fiber {
    void *sp;              /* the stack pointer it stopped at (while not running) */
    void *param;           /* GetFiberData */
    LPFIBER_START_ROUTINE fn;
    void *stack;
    size_t stack_size;
} Fiber;
static __thread Fiber *current_fiber;

void rt_fiber_switch(void **save_sp, void *load_sp);
void rt_fiber_trampoline(void);

__attribute__((used)) void rt_fiber_entry(Fiber *f) {
    f->fn(f->param);
    fprintf(stderr, "a fiber's start routine returned\n");
    abort();
}

LPVOID ConvertThreadToFiberEx(LPVOID param, DWORD flags) {
    (void)flags;
    if (current_fiber) return current_fiber;
    Fiber *f = calloc(1, sizeof *f);
    if (!f) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    f->param = param;
    current_fiber = f;
    return f;
}
LPVOID ConvertThreadToFiber(LPVOID param) { return ConvertThreadToFiberEx(param, 0); }

#define GUARD 0x10000

LPVOID CreateFiberEx(SIZE_T commit, SIZE_T reserve, DWORD flags, LPFIBER_START_ROUTINE fn, LPVOID param) {
    (void)flags;
    size_t size = reserve ? reserve : commit ? commit : (1u << 20);
    size = (size + 0xFFFF) & ~(size_t)0xFFFF;
    u8 *stack = mmap(NULL, size + GUARD, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, VM_MAKE_TAG(VM_MEMORY_STACK), 0);
    if (stack == MAP_FAILED) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    mprotect(stack, GUARD, PROT_NONE);   /* a guard below */
    Fiber *f = calloc(1, sizeof *f);
    if (!f) { munmap(stack, size + GUARD); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    f->fn = fn;
    f->param = param;
    f->stack = stack;
    f->stack_size = size + GUARD;
    /* rt_fiber_switch's frame: x19-x28, x29, x30, d8-d15, FPCR (and a spare word), 16-aligned */
    u64 *sp = (u64 *)(((uintptr_t)(stack + size + GUARD) - 128) & ~(uintptr_t)15);
    sp -= 22;
    memset(sp, 0, 22 * sizeof *sp);
    sp[0] = (u64)(uintptr_t)f;                       /* x19: the trampoline's argument */
    sp[10] = 0;                                      /* x29: the end of the frame chain */
    sp[11] = (u64)(uintptr_t)rt_fiber_trampoline;    /* x30: where the switch returns to */
    u64 fpcr;
    __asm__ volatile("mrs %0, fpcr" : "=r"(fpcr));
    sp[20] = fpcr;                                   /* (the creator's rounding and flush modes) */
    f->sp = sp;
    return f;
}
LPVOID CreateFiber(SIZE_T stack, LPFIBER_START_ROUTINE fn, LPVOID param) { return CreateFiberEx(stack, stack, 0, fn, param); }

void SwitchToFiber(LPVOID fiber) {
    Fiber *me = current_fiber, *next = fiber;
    if (!me || me == next) return;
    current_fiber = next;
    rt_fiber_switch(&me->sp, next->sp);
    /* resumed: possibly on another thread (whose switch set current_fiber) */
}

void DeleteFiber(LPVOID fiber) {
    Fiber *f = fiber;
    if (!f || f == current_fiber) return;
    if (f->stack) munmap(f->stack, f->stack_size);
    free(f);
}
BOOL IsThreadAFiber(void) { return current_fiber != NULL; }
PVOID GetFiberData(void) { return current_fiber ? current_fiber->param : NULL; }
PVOID GetCurrentFiber(void) { return current_fiber; }
