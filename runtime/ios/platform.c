/* iOS platform services for the Windows API shim. The Mach clock, stack walking and timers are
 * shared with the Apple Silicon macOS port. Address waits use a small pthread condition table so
 * this target does not depend on macOS-only os_sync APIs or private ulock entry points. */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/vm_map.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/event.h>
#include <time.h>
#include <unistd.h>

#include <windows.h>

typedef uint64_t u64;

/* ---- waiting on an address ---------------------------------------------------------------- */
typedef struct FutexBucket { pthread_mutex_t lock; pthread_cond_t ready; u64 epoch; } FutexBucket;
enum { FUTEX_BUCKETS = 256 };
static FutexBucket futex_buckets[FUTEX_BUCKETS];
static pthread_once_t futex_once = PTHREAD_ONCE_INIT;

static void futex_init(void) {
    for (int i = 0; i < FUTEX_BUCKETS; i++) {
        pthread_mutex_init(&futex_buckets[i].lock, NULL);
        pthread_cond_init(&futex_buckets[i].ready, NULL);
    }
}

static FutexBucket *futex_bucket(const volatile int *addr) {
    uintptr_t x = (uintptr_t)addr;
    x ^= x >> 17;
    x *= 0x9E3779B97F4A7C15ull;
    return &futex_buckets[(x >> 56) & (FUTEX_BUCKETS - 1)];
}

const char *rt_futex_mode(void) { return "pthread condition buckets"; }

u64 rt_uptime_ns(void);

/* Matches WaitOnAddress: 1 when the value changed or the wait was woken, 0 on timeout. */
int rt_futex_wait(volatile int *addr, int value, DWORD ms) {
    if (!ms) return __atomic_load_n(addr, __ATOMIC_ACQUIRE) != value;
    pthread_once(&futex_once, futex_init);
    FutexBucket *b = futex_bucket(addr);
    struct timespec until = {0};
    if (ms != INFINITE) {
        clock_gettime(CLOCK_REALTIME, &until);
        until.tv_sec += ms / 1000;
        until.tv_nsec += (long)(ms % 1000) * 1000000;
        if (until.tv_nsec >= 1000000000) { until.tv_sec++; until.tv_nsec -= 1000000000; }
    }
    pthread_mutex_lock(&b->lock);
    u64 epoch = b->epoch;
    while (__atomic_load_n(addr, __ATOMIC_ACQUIRE) == value && b->epoch == epoch) {
        int e = ms == INFINITE ? pthread_cond_wait(&b->ready, &b->lock)
                               : pthread_cond_timedwait(&b->ready, &b->lock, &until);
        if (e == ETIMEDOUT) break;
    }
    int woke = __atomic_load_n(addr, __ATOMIC_ACQUIRE) != value || b->epoch != epoch;
    pthread_mutex_unlock(&b->lock);
    return woke;
}

/* Wake one waiter for this address, or every waiter when `all` is nonzero. Hash collisions may
 * wake a waiter for another address; it rechecks its value while holding the bucket lock. */
void rt_futex_wake(volatile int *addr, int all) {
    pthread_once(&futex_once, futex_init);
    FutexBucket *b = futex_bucket(addr);
    pthread_mutex_lock(&b->lock);
    b->epoch++;
    if (all) pthread_cond_broadcast(&b->ready); else pthread_cond_signal(&b->ready);
    pthread_mutex_unlock(&b->lock);
}

/* ---- the clock ---------------------------------------------------------------------------- */
/* mach_absolute_time: the processor's counter, as Linux's CLOCK_MONOTONIC stopping while the
 * Mac sleeps (24 MHz on Apple silicon); a few nanoseconds a read. */
static mach_timebase_info_data_t timebase;   /* (read before main: no thread asks before it is) */
static u64 counter_hz;

__attribute__((constructor)) static void timebase_init(void) {
    mach_timebase_info(&timebase);
    u64 scaled = 1000000000ull * timebase.denom;
    counter_hz = scaled % timebase.numer ? 0 : scaled / timebase.numer;
}

u64 rt_uptime_ns(void) { return (u64)((unsigned __int128)mach_absolute_time() * timebase.numer / timebase.denom); }

/* Counter ticks a second, when that is a whole number (it is on every Mac): QueryPerformanceFrequency. */
u64 rt_uptime_hz(void) { return counter_hz; }

/* Sleep until rt_uptime_ns reaches t: on a timer of the thread's own kqueue marked critical,
 * which the system never puts off to wake with others (a plain sleep's may come several
 * milliseconds late - tens, while the system coalesces timers - which a frame's pacing cannot
 * take: Windows' high-resolution waitable timers are this kind). */
static pthread_key_t sleep_key;
static pthread_once_t sleep_once = PTHREAD_ONCE_INIT;
static void close_queue(void *kq) { close((int)(intptr_t)kq - 1); }
static void sleep_key_init(void) { pthread_key_create(&sleep_key, close_queue); }

static int thread_queue(void) {
    pthread_once(&sleep_once, sleep_key_init);
    intptr_t v = (intptr_t)pthread_getspecific(sleep_key);
    if (!v) {
        int kq = kqueue();
        if (kq < 0) return -1;
        fcntl(kq, F_SETFD, FD_CLOEXEC);
        v = kq + 1;
        pthread_setspecific(sleep_key, (void *)v);
    }
    return (int)v - 1;
}

void rt_sleep_until_ns(u64 t) {
    u64 at = (u64)((unsigned __int128)t * timebase.denom / timebase.numer);
    int kq = thread_queue();
    for (u64 now; (now = mach_absolute_time()) < at;) {   /* (again if a signal cut it short) */
        struct kevent64_s ev, out;
        u64 left = (u64)((unsigned __int128)(at - now) * timebase.numer / timebase.denom);
        EV_SET64(&ev, 1, EVFILT_TIMER, EV_ADD | EV_ONESHOT, NOTE_NSECONDS | NOTE_CRITICAL, (int64_t)(left ? left : 1), 0, 0, 0);
        if (kq < 0 || (kevent64(kq, &ev, 1, &out, 1, 0, NULL) < 0 && errno != EINTR)) mach_wait_until(at);
    }
}

/* ---- the program -------------------------------------------------------------------------- */
static char exe_path[PATH_MAX];
static pthread_once_t exe_once = PTHREAD_ONCE_INIT;

static void find_exe(void) {
    char raw[PATH_MAX];
    uint32_t n = sizeof raw;
    if (_NSGetExecutablePath(raw, &n)) return;   /* (longer than PATH_MAX: left empty) */
    if (!realpath(raw, exe_path)) snprintf(exe_path, sizeof exe_path, "%s", raw);
}

const char *rt_exe_path(void) {
    pthread_once(&exe_once, find_exe);
    return exe_path;
}

/* ---- stack walks -------------------------------------------------------------------------- */
/* Frame records ({caller's x29, return address}, x29 pointing at them) are copied off the stack
 * by the kernel (mach_vm_read_overwrite: an address that cannot be read fails the copy, never
 * faults), up to a host page's end at a time - so a walk usually takes a call or two, not one a
 * frame. A record that cannot be read, or that is not above the last, ends the walk. (arm64
 * macOS code keeps x29 a frame pointer; the fiber trampoline zeroes it, ending a fiber's chain.) */
int rt_frame_walk(uint64_t pc, uint64_t lr, uint64_t fp, void **frames, int max) {
    u64 copy[2048];   /* (16 KiB: a host page) */
    u64 from = 0, to = 0;   /* what copy holds */
    int n = 0, first = 1;
    if (max <= 0) return 0;
    frames[n++] = (void *)(uintptr_t)pc;
    for (; n < max && fp && !(fp & 7);) {
        if (fp < from || fp + 16 > to) {
            u64 end = (fp + 16 + 0x3FFF) & ~0x3FFFull;
            vm_size_t got = 0;
            if (end - fp > sizeof copy) end = fp + sizeof copy;
            if (vm_read_overwrite(mach_task_self(), (vm_address_t)fp, (vm_size_t)(end - fp),
                                  (vm_address_t)(uintptr_t)copy, &got) != KERN_SUCCESS || got < 16) break;
            from = fp;
            to = fp + got;
        }
        const u64 *record = copy + (fp - from) / 8;
        u64 next = record[0], ra = record[1];
        if (first && lr && lr != ra) frames[n++] = (void *)(uintptr_t)lr;   /* (not saved yet: a leaf, or it called since) */
        first = 0;   /* (a record read: lr is dealt with, even if the walk ends here) */
        if (!ra || n == max) break;
        frames[n++] = (void *)(uintptr_t)ra;
        if (next <= fp) break;
        fp = next;
    }
    if (first && lr && n < max) frames[n++] = (void *)(uintptr_t)lr;   /* (no record at all) */
    return n;
}

/* Another thread's frames: stopped, its registers read and its stack walked, then let go on -
 * nothing between that allocates or locks, which the stopped thread might hold. pc and lr: its
 * registers (when not NULL). 0 frames if it could not be stopped (it ended). One thread at a
 * time stops another, in the whole process: two stopping each other at once would both stay
 * stopped (host.c's thread dumps and the profiler both come here). */
static pthread_mutex_t stop_lock = PTHREAD_MUTEX_INITIALIZER;

int rt_thread_frames(unsigned port, void **frames, int max, uint64_t *pc, uint64_t *lr) {
    int n = 0;
    arm_thread_state64_t st;
    mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
    if (!port || port == pthread_mach_thread_np(pthread_self())) return 0;
    pthread_mutex_lock(&stop_lock);
    if (thread_suspend(port) == KERN_SUCCESS) {
        if (thread_get_state(port, ARM_THREAD_STATE64, (thread_state_t)&st, &count) == KERN_SUCCESS) {
            u64 p = arm_thread_state64_get_pc(st), l = arm_thread_state64_get_lr(st);
            n = max > 0 ? rt_frame_walk(p, l, arm_thread_state64_get_fp(st), frames, max) : 1;
            if (pc) *pc = p;
            if (lr) *lr = l;
        }
        thread_resume(port);
    }
    pthread_mutex_unlock(&stop_lock);
    return n;
}

/* The settings menu (gpu_menu.c, drawn with the Direct3D overlay) is not in the macOS program yet:
 * the game always has its input. */
int menu_blocks_input(void) { return 0; }
