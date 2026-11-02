/* Memory for the macOS build of the Windows API (runtime/linux/include/windows.h): VirtualAlloc
 * and the rest, and the write watch - what runtime/linux/win32.c does on Linux, for a system
 * with 16 KiB pages and no userfaultfd.
 *
 * The guest's address space. macOS keeps a guard over [0xFC0000000, 0x7000000000) in every
 * process and its allocator takes ranges at random above that before main, so the guest cannot
 * have its own addresses here: it lives RT_MEMBASE (1 TiB) up (recomp.h). rt_guest_window
 * reserves all of [RT_MEMBASE, RT_MEMBASE + 2^39), inaccessible, first thing, with a moat each
 * side (a stray guest pointer just outside faults rather than lands on the system's memory).
 * Inside it a fixed MEM_RESERVE only records the reservation - the pages are ours already;
 * elsewhere it maps (and fails where anything is mapped, as Windows does).
 *
 * Pages. The host's are 16 KiB, the guest's 4 KiB, and what the guest commits and decommits is
 * 4 KiB-granular (thread-local regions, stack mappings, physical memory in the alias area). So
 * each reservation keeps a bit a 4 KiB page: committing opens the host pages it touches
 * (rounding out: a neighbour becoming accessible early harms nothing); decommitting gives back
 * only host pages none of whose 4 KiB pages are left committed (rounding in, so a neighbour
 * keeps its data), as fresh zero pages - madvise does not drop data here - and zeroes the
 * decommitted 4 KiB pages of the others, so that a recommit reads zeros, as on Windows.
 *
 * The write watch is page protection (Linux's fallback): a watched host page is read-only until
 * written; the fault (SIGBUS, host.c) marks it written - every committed 4 KiB page of it, since
 * which one the later writes go to is not seen - and makes it writable. GetWriteWatch reports 4
 * KiB pages, as the renderer expects, clipped to the range asked about; a reset clears just the
 * pages in the range and makes read-only again every host page it touches. Protecting before
 * clearing (under one lock with the fault handler) means no write is lost to a reset racing it:
 * a write that lands first was already marked; one after faults, and is marked after the clear.
 *
 * So the report is more than Windows' - never less: a write anywhere in a host page (a store, or
 * a system call's, rt_ww_prepare_write) reports all of its committed 4 KiB pages. For memory the
 * renderer takes from the guest (textures, buffers) that costs only another upload. Data whose
 * newer copy is the renderer's own (gpu_metal.m's GPU-resident regions: guest memory under them
 * is stale) must not be taken on the report's word in a host page shared with other data: the
 * bytes there tell. A 4 KiB decommit writes nothing: it zeroes through a second mapping of the
 * host page, which stays as it was. */
#include <errno.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#include <windows.h>

#include "recomp.h"   /* RT_MEMBASE */

#define HPAGE 0x4000ull                 /* a host page */
#define GPAGE 0x1000ull                 /* a guest page */
#define SUBPAGES (HPAGE / GPAGE)
#define WINDOW_BASE RT_MEMBASE
#define WINDOW_SIZE (1ull << 39)
#define MOAT_BELOW (64ull << 30)
#define MOAT_ABOVE (512ull << 30)
#define TAG VM_MAKE_TAG(VM_MEMORY_APPLICATION_SPECIFIC_1)   /* (how vmmap shows the guest's memory) */

static u64 round_down(u64 v, u64 to) { return v & ~(to - 1); }
static u64 round_up(u64 v, u64 to) { return (v + to - 1) & ~(to - 1); }

static int bit(const u8 *map, u64 i) { return map[i >> 3] >> (i & 7) & 1; }
static void set_bit(u8 *map, u64 i) { map[i >> 3] |= (u8)(1u << (i & 7)); }
static void clear_bit(u8 *map, u64 i) { map[i >> 3] &= (u8)~(1u << (i & 7)); }
static void set_bits(u8 *map, u64 from, u64 to, int on) {   /* [from, to) */
    for (; from < to && (from & 7); from++) on ? set_bit(map, from) : clear_bit(map, from);
    if (to - from >= 8) {
        memset(map + (from >> 3), on ? 0xFF : 0, (to - from) >> 3);
        from += (to - from) & ~7ull;
    }
    for (; from < to; from++) on ? set_bit(map, from) : clear_bit(map, from);
}

static int prot_of(DWORD protect) {
    switch (protect & 0xFF) {
    case PAGE_NOACCESS: return PROT_NONE;
    case PAGE_READONLY: return PROT_READ;
    case PAGE_EXECUTE: case PAGE_EXECUTE_READ: return PROT_READ | PROT_EXEC;
    case PAGE_EXECUTE_READWRITE: return PROT_READ | PROT_WRITE | PROT_EXEC;
    default: return PROT_READ | PROT_WRITE;
    }
}

/* Fresh, inaccessible pages over [at, at + n) (host pages): what decommitted memory becomes. */
static int fresh(u64 at, u64 n) {
    return mmap((void *)(uintptr_t)at, n, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANON, TAG, 0) != MAP_FAILED;
}

/* ---- the guest's window ------------------------------------------------------------------- */
static int window_ready;

static kern_return_t reserve_fixed(u64 at, u64 size, vm_prot_t max) {
    mach_vm_address_t a = at;
    return mach_vm_map(mach_task_self(), &a, size, 0, VM_FLAGS_FIXED | TAG, MEMORY_OBJECT_NULL, 0, FALSE, VM_PROT_NONE, max, VM_INHERIT_DEFAULT);
}

static int inside_window(u64 lo, u64 hi) { return window_ready && lo >= WINDOW_BASE && hi <= WINDOW_BASE + WINDOW_SIZE && lo < hi; }

int rt_guest_window(void) {
    if (window_ready) return 1;
    if (vm_page_size != HPAGE) {
        fprintf(stderr, "[mem] this Mac's pages are %lu bytes, not 16 KiB: this build is for Apple silicon\n", (unsigned long)vm_page_size);
        return 0;
    }
    kern_return_t kr = reserve_fixed(WINDOW_BASE, WINDOW_SIZE, VM_PROT_READ | VM_PROT_WRITE);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "[mem] cannot reserve the guest's address space [%#llx, %#llx) (512 GiB): %s (%d) - the system has put something there\n",
                WINDOW_BASE, WINDOW_BASE + WINDOW_SIZE, mach_error_string(kr), kr);
        return 0;
    }
    /* (the moats are a safety net, not a need: without one the game still runs) */
    if ((kr = reserve_fixed(WINDOW_BASE - MOAT_BELOW, MOAT_BELOW, VM_PROT_NONE)) != KERN_SUCCESS)
        fprintf(stderr, "[mem] warning: no guard below the guest's address space (%s)\n", mach_error_string(kr));
    if ((kr = reserve_fixed(WINDOW_BASE + WINDOW_SIZE, MOAT_ABOVE, VM_PROT_NONE)) != KERN_SUCCESS)
        fprintf(stderr, "[mem] warning: no guard above the guest's address space (%s)\n", mach_error_string(kr));
    window_ready = 1;
    return 1;
}

/* ---- the write watch's state -------------------------------------------------------------- */
/* Watched areas, read by the fault handler without the reservations' lock: an entry is filled
 * before nareas counts it, and only ever emptied (size 0), under ww_busy. Bits: committed and
 * dirty a guest page each, open (writable, unwatched until reset) a host page each. */
#define AREAS 16
typedef struct WatchArea { u64 base, size; u8 *committed, *dirty, *open; } WatchArea;
static WatchArea areas[AREAS];
static int nareas;
/* The lock over the areas' bits and protection, a spin lock: the fault handler takes it, which
 * may interrupt anything (no mutex, no allocation). Nothing holding it writes watched memory. */
static volatile int ww_busy;

static void ww_lock(void) {
    for (unsigned spins = 0; __atomic_exchange_n(&ww_busy, 1, __ATOMIC_ACQUIRE); spins++) {
        if (spins < 64) __asm__ __volatile__("isb");
        else sched_yield();
    }
}
static void ww_unlock(void) { __atomic_store_n(&ww_busy, 0, __ATOMIC_RELEASE); }

static WatchArea *area_of(u64 lo, u64 hi) {
    int n = __atomic_load_n(&nareas, __ATOMIC_ACQUIRE);
    for (int i = 0; i < n; i++)
        if (areas[i].size && areas[i].base <= lo && hi <= areas[i].base + areas[i].size) return &areas[i];
    return NULL;
}

static int committed_in(const WatchArea *w, u64 h) {   /* any of host page h's guest pages committed */
    for (u64 k = 0; k < SUBPAGES; k++) if (bit(w->committed, h * SUBPAGES + k)) return 1;
    return 0;
}
static int clean_in(const WatchArea *w, u64 h) {       /* any committed but not written */
    for (u64 k = 0; k < SUBPAGES; k++) if (bit(w->committed, h * SUBPAGES + k) && !bit(w->dirty, h * SUBPAGES + k)) return 1;
    return 0;
}

/* Host page h written (or about to be): every committed guest page of it dirty, and writable.
 * (ww_busy held) */
static int ww_open(WatchArea *w, u64 h) {
    for (u64 k = 0; k < SUBPAGES; k++) if (bit(w->committed, h * SUBPAGES + k)) set_bit(w->dirty, h * SUBPAGES + k);
    if (mprotect((void *)(uintptr_t)(w->base + h * HPAGE), HPAGE, PROT_READ | PROT_WRITE)) return 0;
    set_bit(w->open, h);
    return 1;
}

/* Read-only again: the host pages [h0, h1) that are open and hold committed memory. (ww_busy held) */
static void ww_arm(WatchArea *w, u64 h0, u64 h1) {
    for (u64 h = h0; h < h1;) {
        if (!bit(w->open, h)) { h++; continue; }
        u64 e = h;
        while (e < h1 && bit(w->open, e)) clear_bit(w->open, e++);
        mprotect((void *)(uintptr_t)(w->base + h * HPAGE), (e - h) * HPAGE, PROT_READ);
        for (u64 k = h; k < e; k++)   /* (open with nothing committed: nothing to watch) */
            if (!committed_in(w, k)) mprotect((void *)(uintptr_t)(w->base + k * HPAGE), HPAGE, PROT_NONE);
        h = e;
    }
}

/* (from the fault handler) A write to a protected page of a watched area: note it, allow it.
 * Async-signal-safe: no allocation, no thread-locals (whose first use allocates, on macOS). */
int rt_ww_fault(void *address) {
    u64 a = (u64)(uintptr_t)address;
    WatchArea *w = area_of(a, a + 1);
    if (!w) return 0;
    int handled = 0, saved = errno;   /* (the interrupted code's) */
    ww_lock();
    if (w->size && a - w->base < w->size) {
        u64 h = (a - w->base) / HPAGE;
        if (committed_in(w, h)) handled = ww_open(w, h);   /* (open already: another thread's write got there first) */
    }
    ww_unlock();
    errno = saved;
    return handled;
}

void rt_ww_prepare_write(void *p, size_t n) {
    if (!n) return;
    u64 lo = round_down((u64)(uintptr_t)p, HPAGE), hi = round_up((u64)(uintptr_t)p + n, HPAGE);
    for (u64 at = lo; at < hi; at += HPAGE) {
        WatchArea *w = area_of(at, at + HPAGE);
        if (!w) continue;
        ww_lock();
        u64 h = (at - w->base) / HPAGE;
        if (w->size && !bit(w->open, h) && committed_in(w, h)) ww_open(w, h);
        ww_unlock();
    }
}

/* Whether stores can go anywhere in [p, p + n): each host page writable, or committed in a
 * watched area (the fault handler lets them through). (win32.c's ReadFile: before it copies a
 * read that failed on a protected page with stores of its own) */
int rt_ww_storable(const void *p, size_t n) {
    u64 at = round_down((u64)(uintptr_t)p, HPAGE), hi = round_up((u64)(uintptr_t)p + n, HPAGE);
    while (at < hi) {
        WatchArea *w = area_of(at, at + HPAGE);
        if (w) {
            ww_lock();
            int ok = w->size && at - w->base < w->size && committed_in(w, (at - w->base) / HPAGE);
            ww_unlock();
            if (!ok) return 0;
            at += HPAGE;
            continue;
        }
        mach_vm_address_t a = at;
        mach_vm_size_t size = 0;
        vm_region_basic_info_data_64_t vi;
        mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
        mach_port_t object = MACH_PORT_NULL;
        if (mach_vm_region(mach_task_self(), &a, &size, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&vi, &count, &object) != KERN_SUCCESS || a > at ||
            !(vi.protection & VM_PROT_WRITE))
            return 0;
        at = round_up(a + size, HPAGE);
    }
    return 1;
}

const char *rt_ww_mode(void) { return "page protection (16 KiB pages, reported as 4 KiB ones)"; }

UINT GetWriteWatch(DWORD flags, PVOID base, SIZE_T size, PVOID *addresses, ULONG_PTR *count, ULONG *granularity) {
    u64 lo = round_down((u64)(uintptr_t)base, GPAGE), hi = round_up((u64)(uintptr_t)base + size, GPAGE);
    if (granularity) *granularity = (ULONG)GPAGE;
    WatchArea *w = area_of(lo, hi);
    if (!w || !count || hi <= lo) { SetLastError(ERROR_INVALID_PARAMETER); return 1; }
    ULONG_PTR cap = *count, n = 0;
    ww_lock();
    if (flags & WRITE_WATCH_FLAG_RESET) ww_arm(w, (lo - w->base) / HPAGE, (round_up(hi, HPAGE) - w->base) / HPAGE);
    for (u64 i = (lo - w->base) / GPAGE, end = (hi - w->base) / GPAGE; i < end && n < cap; i++) {
        if (!(w->dirty[i >> 3] >> (i & 7))) { i |= 7; continue; }   /* (a byte at a time where nothing is) */
        if (!bit(w->dirty, i)) continue;
        addresses[n++] = (void *)(uintptr_t)(w->base + i * GPAGE);
        if (flags & WRITE_WATCH_FLAG_RESET) clear_bit(w->dirty, i);
    }
    ww_unlock();
    *count = n;
    return 0;
}

UINT ResetWriteWatch(LPVOID base, SIZE_T size) {
    u64 lo = round_down((u64)(uintptr_t)base, GPAGE), hi = round_up((u64)(uintptr_t)base + size, GPAGE);
    WatchArea *w = area_of(lo, hi);
    if (!w || hi <= lo) return 1;
    ww_lock();
    ww_arm(w, (lo - w->base) / HPAGE, (round_up(hi, HPAGE) - w->base) / HPAGE);
    set_bits(w->dirty, (lo - w->base) / GPAGE, (hi - w->base) / GPAGE, 0);
    ww_unlock();
    return 0;
}

/* ---- reservations ------------------------------------------------------------------------- */
/* own: mapped here (released by unmapping); else in the window (released back to it). A watched
 * one's bits are its area's. */
#define RESERVATIONS 4096
typedef struct Reservation { u64 base, size; int own; WatchArea *watch; u8 *committed; } Reservation;
static Reservation reservations[RESERVATIONS];
static int nreservations;
static pthread_mutex_t mem_lock = PTHREAD_MUTEX_INITIALIZER;

static Reservation *reservation_of(u64 lo, u64 hi) {
    for (int i = 0; i < nreservations; i++)
        if (reservations[i].base <= lo && hi <= reservations[i].base + reservations[i].size) return &reservations[i];
    return NULL;
}

static int overlaps(u64 lo, u64 hi) {
    for (int i = 0; i < nreservations; i++)
        if (lo < reservations[i].base + reservations[i].size && reservations[i].base < hi) return 1;
    return 0;
}

static WatchArea *watch_register(u64 base, u64 size, u8 *committed) {
    WatchArea *w = NULL;
    ww_lock();
    for (int i = 0; i < nareas && !w; i++) if (!areas[i].size) w = &areas[i];   /* (one released before) */
    if (!w && nareas < AREAS) w = &areas[nareas];
    if (w) {
        w->committed = committed;
        w->dirty = calloc(1, size / GPAGE / 8 + 1);
        w->open = calloc(1, size / HPAGE / 8 + 1);
        w->base = base;
        __atomic_store_n(&w->size, size, __ATOMIC_RELEASE);
        if (w == &areas[nareas]) __atomic_store_n(&nareas, nareas + 1, __ATOMIC_RELEASE);
    }
    ww_unlock();
    if (!w) fprintf(stderr, "[mem] too many write-watched areas\n");
    return w;
}

/* Commit the guest pages [lo, hi) of r. (mem_lock held) */
static int commit_range(Reservation *r, u64 lo, u64 hi, int prot) {
    u64 h0 = round_down(lo, HPAGE), h1 = round_up(hi, HPAGE);
    u64 g0 = (lo - r->base) / GPAGE, g1 = (hi - r->base) / GPAGE;
    WatchArea *w = r->watch;
    if (!w) {
        if (mprotect((void *)(uintptr_t)h0, h1 - h0, prot)) return 0;
        set_bits(r->committed, g0, g1, 1);
        return 1;
    }
    /* newly committed pages start unwritten, so their host pages read-only; pages committed
     * before keep their state (a host page all of whose pages were written stays as it is) */
    ww_lock();
    for (u64 g = g0; g < g1; g++)
        if (!bit(w->committed, g)) { set_bit(w->committed, g); clear_bit(w->dirty, g); }
    u64 first = (h0 - w->base) / HPAGE, last = (h1 - w->base) / HPAGE;
    for (u64 h = first; h < last;) {
        if (!clean_in(w, h)) { h++; continue; }
        u64 e = h + 1;
        while (e < last && clean_in(w, e)) e++;
        mprotect((void *)(uintptr_t)(w->base + h * HPAGE), (e - h) * HPAGE, PROT_READ);
        set_bits(w->open, h, e, 0);
        h = e;
    }
    ww_unlock();
    return 1;
}

/* Zero [lo, hi) of the host page at `at` through a second mapping of it: the page itself stays
 * read-only, so its neighbours are not written as far as the watch knows, and a store to one
 * meanwhile faults as before (and waits on the lock). 0 if there is no second mapping to be had. */
static int zero_aside(u64 at, u64 lo, u64 hi) {
    mach_vm_address_t alias = 0;
    vm_prot_t cur, max;
    if (mach_vm_remap(mach_task_self(), &alias, HPAGE, 0, VM_FLAGS_ANYWHERE, mach_task_self(), at, FALSE, &cur, &max, VM_INHERIT_NONE) != KERN_SUCCESS)
        return 0;
    int ok = mach_vm_protect(mach_task_self(), alias, HPAGE, FALSE, VM_PROT_READ | VM_PROT_WRITE) == KERN_SUCCESS;
    if (ok) memset((void *)(uintptr_t)(alias + (lo - at)), 0, hi - lo);
    mach_vm_deallocate(mach_task_self(), alias, HPAGE);
    return ok;
}

/* Decommit the guest pages [lo, hi) of r: host pages left with nothing committed become fresh
 * ones; the others keep their committed neighbours, and the pages decommitted are zeroed.
 * (mem_lock held) */
static void decommit_range(Reservation *r, u64 lo, u64 hi) {
    u64 h0 = round_down(lo, HPAGE), h1 = round_up(hi, HPAGE);
    WatchArea *w = r->watch;
    if (w) ww_lock();
    set_bits(r->committed, (lo - r->base) / GPAGE, (hi - r->base) / GPAGE, 0);
    if (w) set_bits(w->dirty, (lo - r->base) / GPAGE, (hi - r->base) / GPAGE, 0);
    for (u64 at = h0; at < h1;) {
        u64 g = (at - r->base) / GPAGE;
        int any = 0;
        for (u64 k = 0; k < SUBPAGES; k++) any |= bit(r->committed, g + k);
        if (!any) {   /* a run of host pages to give back */
            u64 end = at + HPAGE;
            for (; end < h1; end += HPAGE) {
                u64 ge = (end - r->base) / GPAGE;
                int more = 0;
                for (u64 k = 0; k < SUBPAGES; k++) more |= bit(r->committed, ge + k);
                if (more) break;
            }
            fresh(at, end - at);
            if (w) set_bits(w->open, (at - w->base) / HPAGE, (end - w->base) / HPAGE, 0);
            at = end;
            continue;
        }
        /* part of this host page stays: zero what went - in place if it is writable (open: its
         * committed pages are all written already), else aside (armed), else through the watch's
         * write path, its neighbours then written (never a write that could fault here, under the
         * lock the fault handler takes) */
        u64 zlo = lo > at ? lo : at, zhi = hi < at + HPAGE ? hi : at + HPAGE;
        if (!w || bit(w->open, (at - w->base) / HPAGE)) memset((void *)(uintptr_t)zlo, 0, zhi - zlo);
        else if (!zero_aside(at, zlo, zhi) && ww_open(w, (at - w->base) / HPAGE)) memset((void *)(uintptr_t)zlo, 0, zhi - zlo);
        at += HPAGE;
    }
    if (w) ww_unlock();
}

LPVOID VirtualAlloc(LPVOID address, SIZE_T size, DWORD type, DWORD protect) {
    u64 a = (u64)(uintptr_t)address;
    if (!size || a + size < a) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!(type & MEM_RESERVE) && address) {   /* commit inside a reservation */
        if (!(type & MEM_COMMIT)) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
        u64 lo = round_down(a, GPAGE), hi = round_up(a + size, GPAGE);
        pthread_mutex_lock(&mem_lock);
        Reservation *r = reservation_of(lo, hi);
        int ok = r && commit_range(r, lo, hi, prot_of(protect));
        pthread_mutex_unlock(&mem_lock);
        if (!ok) { SetLastError(r ? ERROR_NOT_ENOUGH_MEMORY : ERROR_INVALID_ADDRESS); return NULL; }
        return (void *)(uintptr_t)lo;
    }
    u64 lo = round_down(a, HPAGE), hi = round_up(a + size, HPAGE), bytes = hi - lo;
    int own = 1, commit_all = (type & MEM_COMMIT) && !address && !(type & MEM_WRITE_WATCH);
    pthread_mutex_lock(&mem_lock);
    if (nreservations == RESERVATIONS) {
        pthread_mutex_unlock(&mem_lock);
        fprintf(stderr, "[mem] too many reservations\n");
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    if (!address) {   /* anywhere: an ordinary mapping */
        void *p = mmap(NULL, bytes, commit_all ? prot_of(protect) : PROT_NONE, MAP_PRIVATE | MAP_ANON, TAG, 0);
        if (p == MAP_FAILED) { pthread_mutex_unlock(&mem_lock); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
        lo = (u64)(uintptr_t)p;
        hi = lo + bytes;
    } else if (inside_window(lo, hi)) {
        if (overlaps(lo, hi)) { pthread_mutex_unlock(&mem_lock); SetLastError(ERROR_INVALID_ADDRESS); return NULL; }
        own = 0;
    } else if (reserve_fixed(lo, bytes, VM_PROT_ALL) != KERN_SUCCESS) {   /* (fails where anything is mapped) */
        pthread_mutex_unlock(&mem_lock);
        SetLastError(ERROR_INVALID_ADDRESS);
        return NULL;
    }
    Reservation *r = &reservations[nreservations++];
    *r = (Reservation){lo, bytes, own, NULL, calloc(1, bytes / GPAGE / 8 + 1)};
    if (type & MEM_WRITE_WATCH) r->watch = watch_register(lo, bytes, r->committed);
    if (commit_all) set_bits(r->committed, 0, bytes / GPAGE, 1);
    else if ((type & MEM_COMMIT) && !commit_range(r, address ? round_down(a, GPAGE) : lo, address ? round_up(a + size, GPAGE) : hi, prot_of(protect))) {
        pthread_mutex_unlock(&mem_lock);
        VirtualFree((void *)(uintptr_t)lo, 0, MEM_RELEASE);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    pthread_mutex_unlock(&mem_lock);
    return (void *)(uintptr_t)lo;
}

BOOL VirtualFree(LPVOID address, SIZE_T size, DWORD type) {
    u64 a = (u64)(uintptr_t)address;
    pthread_mutex_lock(&mem_lock);
    if (type & MEM_RELEASE) {
        for (int i = 0; i < nreservations; i++) {
            Reservation *r = &reservations[i];
            if (r->base != a) continue;
            WatchArea *w = r->watch;
            if (w) {   /* (the fault handler may be looking at it: emptied under its lock, freed after) */
                ww_lock();
                __atomic_store_n(&w->size, 0, __ATOMIC_RELEASE);
                ww_unlock();
                free(w->dirty);
                free(w->open);
            }
            if (r->own) munmap((void *)(uintptr_t)r->base, r->size);
            else fresh(r->base, r->size);   /* (back to the window: never unmapped, or the system could take it) */
            free(r->committed);
            *r = reservations[--nreservations];
            pthread_mutex_unlock(&mem_lock);
            return TRUE;
        }
    } else if (type & MEM_DECOMMIT) {
        Reservation *r = reservation_of(a, a + (size ? size : 1));
        if (r && !size && a == r->base) size = r->size;   /* (all of it, as on Windows) */
        u64 lo = round_down(a, GPAGE), hi = round_up(a + size, GPAGE);
        if (r && size && hi <= r->base + r->size) {
            decommit_range(r, lo, hi);
            pthread_mutex_unlock(&mem_lock);
            return TRUE;
        }
    }
    pthread_mutex_unlock(&mem_lock);
    SetLastError(type & (MEM_RELEASE | MEM_DECOMMIT) ? ERROR_INVALID_ADDRESS : ERROR_INVALID_PARAMETER);
    return FALSE;
}

BOOL VirtualProtect(LPVOID address, SIZE_T size, DWORD protect, PDWORD old) {
    if (old) *old = PAGE_READWRITE;
    u64 lo = round_down((u64)(uintptr_t)address, HPAGE), hi = round_up((u64)(uintptr_t)address + size, HPAGE);
    if (mprotect((void *)(uintptr_t)lo, hi - lo, prot_of(protect))) { SetLastError(rt_win32_error(errno)); return FALSE; }
    return TRUE;
}

/* The VM region holding the address (committed, if readable or writable), or the free gap
 * before the next one. */
SIZE_T VirtualQuery(LPCVOID address, MEMORY_BASIC_INFORMATION *info, SIZE_T length) {
    if (length < sizeof *info) return 0;
    u64 a = round_down((u64)(uintptr_t)address, HPAGE);
    mach_vm_address_t at = a;
    mach_vm_size_t size = 0;
    vm_region_basic_info_data_64_t vi;
    mach_msg_type_number_t n = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    memset(info, 0, sizeof *info);
    if (mach_vm_region(mach_task_self(), &at, &size, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&vi, &n, &object) != KERN_SUCCESS) return 0;
    if (at > a) {
        info->BaseAddress = (void *)(uintptr_t)a;
        info->RegionSize = at - a;
        info->State = MEM_FREE;
        info->Protect = PAGE_NOACCESS;
        return sizeof *info;
    }
    info->BaseAddress = info->AllocationBase = (void *)(uintptr_t)at;
    info->RegionSize = size;
    int r = (vi.protection & VM_PROT_READ) != 0, w = (vi.protection & VM_PROT_WRITE) != 0;
    info->State = r || w ? MEM_COMMIT : MEM_RESERVE;
    info->Protect = w ? PAGE_READWRITE : r ? PAGE_READONLY : PAGE_NOACCESS;
    info->AllocationProtect = PAGE_READWRITE;
    info->Type = MEM_PRIVATE;
    return sizeof *info;
}
