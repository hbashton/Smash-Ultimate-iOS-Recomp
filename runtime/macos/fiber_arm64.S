/* Fiber stack switching for macOS on arm64 (runtime/macos/fiber.c; Linux's x86-64 version is in
 * runtime/linux/win32.c).
 *
 * rt_fiber_switch(void **save_sp, void *load_sp): save the callee-saved registers (x19-x28, the
 * frame pointer and return address, d8-d15) and the floating-point control register in a frame
 * on this stack, store the stack pointer in *save_sp, take load_sp and restore that stack's
 * frame the same way, and return into it. 176 bytes: x19-x28 at 0, x29/x30 at 80, d8-d15 at 96,
 * FPCR at 160. x18 is the platform's and never touched.
 *
 * A new fiber's stack holds such a frame "returning" into rt_fiber_trampoline with the fiber
 * in x19 and x29 zero: it calls rt_fiber_entry(fiber), which never returns. Its frame-pointer
 * chain ends there (x29 = 0) and the unwinder's too (no return address), for backtrace(), the
 * profiler and C++ exceptions alike. */
    .text

    .globl _rt_fiber_switch
    .private_extern _rt_fiber_switch
    .p2align 2
_rt_fiber_switch:
    sub sp, sp, #176
    stp x19, x20, [sp, #0]
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x26, [sp, #48]
    stp x27, x28, [sp, #64]
    stp x29, x30, [sp, #80]
    stp d8, d9, [sp, #96]
    stp d10, d11, [sp, #112]
    stp d12, d13, [sp, #128]
    stp d14, d15, [sp, #144]
    mrs x9, fpcr
    str x9, [sp, #160]
    mov x9, sp
    str x9, [x0]
    mov sp, x1
    ldr x9, [sp, #160]
    msr fpcr, x9
    ldp d14, d15, [sp, #144]
    ldp d12, d13, [sp, #128]
    ldp d10, d11, [sp, #112]
    ldp d8, d9, [sp, #96]
    ldp x29, x30, [sp, #80]
    ldp x27, x28, [sp, #64]
    ldp x25, x26, [sp, #48]
    ldp x23, x24, [sp, #32]
    ldp x21, x22, [sp, #16]
    ldp x19, x20, [sp, #0]
    add sp, sp, #176
    ret

    .globl _rt_fiber_trampoline
    .private_extern _rt_fiber_trampoline
    .p2align 2
_rt_fiber_trampoline:
    .cfi_startproc
    .cfi_undefined lr
    mov x29, #0
    mov x0, x19
    bl _rt_fiber_entry
    brk #0x1
    .cfi_endproc
