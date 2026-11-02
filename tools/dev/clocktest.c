/* Offline checks of the port's clock (runtime/clock.h): which timer it picks, that it never
 * goes back (on one thread, or behind what another thread saw first), its rate and distance
 * against the system timer, and what a read costs.
 *
 *   python -c "import sys; sys.path.insert(0,'tools'); import cc, subprocess; subprocess.run([cc.CLANG, '/nologo', '/O2', *cc.BASE_CFLAGS, 'tools/dev/clocktest.c', '/Febuild/clocktest.exe'], check=True)"
 *   build\clocktest.exe [seconds, 3]
 *
 * Built with /DSLOW_QPC, every system timer read takes about a microsecond, as on a PC timed
 * by the HPET: the CPU's counter should take over. SSBU_CLOCK=qpc|tsc as in the game. Exit
 * code 0 when every check passes. tests/test_clock.py runs all three. */
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>

#ifdef SLOW_QPC
static BOOL slow_qpc(LARGE_INTEGER *t) {
    LARGE_INTEGER a, b, f;
    QueryPerformanceFrequency(&f);
    (QueryPerformanceCounter)(&a);
    do (QueryPerformanceCounter)(&b); while ((b.QuadPart - a.QuadPart) * 1000000 < f.QuadPart);
    *t = a;
    return TRUE;
}
#define QueryPerformanceCounter(t) slow_qpc(t)
#endif
#include "../../runtime/clock.h"

static int failures, checks;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { failures++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static uint64_t real_ns(void) {   /* the system timer itself, never slowed */
    LARGE_INTEGER t, f;
    (QueryPerformanceCounter)(&t);
    QueryPerformanceFrequency(&f);
    return (uint64_t)t.QuadPart / (uint64_t)f.QuadPart * 1000000000ull + (uint64_t)t.QuadPart % (uint64_t)f.QuadPart * 1000000000ull / (uint64_t)f.QuadPart;
}

static volatile LONG64 seen;   /* the latest time any thread has read */
static uint64_t until_ns;
static struct { uint64_t reads, own_back, cross_back; } per[4];

static DWORD WINAPI reader(void *arg) {
    int i = (int)(intptr_t)arg;
    uint64_t last = 0;
    for (;;) {
        uint64_t before = (uint64_t)seen, v = clock_now_ns();
        if (last > v && last - v > per[i].own_back) per[i].own_back = last - v;
        if (before > v && before - v > per[i].cross_back) per[i].cross_back = before - v;
        last = v;
        for (LONG64 s = seen; (uint64_t)s < v && InterlockedCompareExchange64(&seen, (LONG64)v, s) != s; s = seen) {}
        if (++per[i].reads % 1024 == 0 && real_ns() > until_ns) return 0;
    }
}

int main(int argc, char **argv) {
    double secs = argc > 1 ? atof(argv[1]) : 3;
    clock_now_ns();   /* (its set-up: 20 ms with the CPU's counter) */
    uint64_t r0 = real_ns(), c0 = clock_now_ns();
    int tsc = clock_scale != NULL;
    const char *want = getenv("SSBU_CLOCK");
    printf("system timer read %.0f ns, CPU counter %s, clock: %s\n", clock_read_ns, clock_steady ? "steady" : "not steady", tsc ? "CPU counter" : "system timer");
    int expect = want && !strcmp(want, "tsc") ? clock_steady : want && !strcmp(want, "qpc") ? 0 : clock_read_ns > 250 && clock_steady;
    CHECK(tsc == expect, "picked %s", tsc ? "the CPU counter" : "the system timer");
#ifdef SLOW_QPC
    CHECK(clock_read_ns > 250, "the slowed system timer measured %.0f ns a read", clock_read_ns);
#endif
    CHECK(c0 + 50000000 > r0 && r0 + 50000000 > c0, "the clock starts %.3f ms from the system timer", ((double)c0 - (double)r0) / 1e6);

    /* four threads read it for the time given */
    until_ns = r0 + (uint64_t)(secs * 1e9);
    HANDLE t[4];
    for (int i = 0; i < 4; i++) t[i] = CreateThread(NULL, 0, reader, (void *)(intptr_t)i, 0, NULL);
    /* the rate from past the first rescale (some 2 s) on, in a run long enough; else from the start */
    int late = secs >= 3;
    uint64_t ra = r0, ca = c0;
    if (late) { Sleep(2500); ra = real_ns(); ca = clock_now_ns(); }
    WaitForMultipleObjects(4, t, TRUE, INFINITE);
    uint64_t rb = real_ns(), cb = clock_now_ns();
    uint64_t reads = 0, own = 0, cross = 0;
    for (int i = 0; i < 4; i++) {
        reads += per[i].reads;
        if (per[i].own_back > own) own = per[i].own_back;
        if (per[i].cross_back > cross) cross = per[i].cross_back;
    }
    printf("%llu reads on 4 threads; back on a thread at most %llu ns, behind another thread %llu ns; %d rescales\n",
           (unsigned long long)reads, (unsigned long long)own, (unsigned long long)cross, clock_refines);
    CHECK(own <= 1000, "went back %llu ns on one thread", (unsigned long long)own);
    CHECK(cross <= 1000, "read %llu ns before what another thread had already read", (unsigned long long)cross);
    double rate = (double)(cb - ca) / (double)(rb - ra) - 1, apart = ((double)cb - (double)rb) / 1e6;
    printf("rate against the system timer %+.2f ppm over the last %.1f s; %.3f ms from it\n", rate * 1e6, (rb - ra) / 1e9, apart);
    if (tsc && secs >= 3) CHECK(clock_refines >= 1, "never rescaled");
    double bound = late ? 20e-6 : 100e-6;   /* (the first scale is over 20 ms: up to some 50 ppm off with a slow timer) */
    CHECK(rate < bound && rate > -bound, "runs %+.2f ppm off the system timer", rate * 1e6);
    CHECK(apart < 2 && apart > -2, "%.3f ms from the system timer", apart);

    /* what a read costs */
    uint64_t sum = 0, n = 2000000, k0 = real_ns();
    for (uint64_t i = 0; i < n; i++) sum += clock_now_ns();
    double cost = (double)(real_ns() - k0) / (double)n;
    printf("a read: %.1f ns (%llu)\n", cost, (unsigned long long)(sum & 1));
    if (tsc) CHECK(cost < 100, "a read takes %.1f ns", cost);

    printf("%d checks, %d failed\n", checks, failures);
    return failures != 0;
}
