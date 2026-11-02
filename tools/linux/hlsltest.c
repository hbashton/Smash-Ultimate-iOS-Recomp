/* Check vkd3d-shader (the Linux build's HLSL compiler) on this machine: translate every dumped
 * program in a directory (as tools/dev/shadertest.c does), compile the HLSL with vkd3d-shader and
 * with fxc, and have a Direct3D 11 device create a shader from vkd3d's bytecode.
 *
 * Usage: hlsltest <dir> [name filter] [-v] [-list <file of names>] [-nofxc]
 *   -v prints vkd3d's messages for each failure; -list tests only the programs named (one a line);
 *   -nofxc skips fxc (its column then reads 0). */
#define COBJMACROS
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>

#include "gpu_shader.h"

_Noreturn void rt_fatal(const char *fmt, ...) { (void)fmt; abort(); }
HRESULT rt_vkd3d_compile(const void *src, SIZE_T size, const char *name, const char *entry, const char *target,
                         UINT flags, ID3DBlob **code, ID3DBlob **errors);

static double now_ms(void) {
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return t.QuadPart * 1000.0 / f.QuadPart;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    const char *filter = NULL;
    int verbose = 0, nofxc = 0;
    static char list[1 << 20];
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-v")) verbose = 1;
        else if (!strcmp(argv[i], "-nofxc")) nofxc = 1;
        else if (!strcmp(argv[i], "-list") && i + 1 < argc) {
            FILE *lf = fopen(argv[++i], "r");
            if (lf) { list[0] = '\n'; list[fread(list + 1, 1, sizeof list - 2, lf) + 1] = 0; fclose(lf); }
        } else filter = argv[i];
    }
    ID3D11Device *dev = NULL;
    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    if (FAILED(D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, &fl, 1, D3D11_SDK_VERSION, &dev, NULL, NULL))) {
        fprintf(stderr, "no D3D11 device\n");
        return 1;
    }
    char pattern[1024];
    snprintf(pattern, sizeof pattern, "%s\\*.bin", argv[1]);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 1;
    int total = 0, fxc_ok = 0, vk_ok = 0, created = 0, vk_only_fail = 0;
    double fxc_ms = 0, vk_ms = 0, vk_max = 0;
    char vk_max_name[256] = "";
    do {
        if (filter && !strstr(fd.cFileName, filter)) continue;
        if (list[0]) {
            char key[300];
            snprintf(key, sizeof key, "\n%s\n", fd.cFileName);
            if (!strstr(list, key)) continue;
        }
        char path[1200];
        snprintf(path, sizeof path, "%s\\%s", argv[1], fd.cFileName);
        FILE *f = fopen(path, "rb");
        static unsigned char buf[0x40000];
        size_t n = fread(buf, 1, sizeof buf, f);
        fclose(f);
        int stage = fd.cFileName[0] == '5' ? SHADER_PIXEL : fd.cFileName[0] == 'C' ? SHADER_COMPUTE : SHADER_VERTEX;
        u32 local[3] = {1, 1, 1}, shared = 0;
        if (stage == SHADER_COMPUTE) {
            const char *u = strchr(fd.cFileName + 2, '_');
            if (u) sscanf(u + 1, "%ux%ux%u_%u", &local[0], &local[1], &local[2], &shared);
        }
        ShaderInfo info;
        u32 vs_out = 0;
        if (stage == SHADER_PIXEL) {
            free(shader_hlsl(buf, (u32)n, stage, 0xFFFFFFFFu, &info));
            vs_out = info.inputs;
        }
        char *hlsl = stage == SHADER_COMPUTE ? shader_hlsl_cs(buf, (u32)n, local, shared, &info, NULL, NULL)
                                             : shader_hlsl(buf, (u32)n, stage, vs_out, &info);
        const char *target = stage == SHADER_PIXEL ? "ps_5_0" : stage == SHADER_COMPUTE ? "cs_5_0" : "vs_5_0";
        total++;
        ID3DBlob *code = NULL, *errors = NULL;
        double t0 = now_ms();
        HRESULT hr = nofxc ? E_FAIL : D3DCompile(hlsl, strlen(hlsl), fd.cFileName, NULL, NULL, "main", target, D3DCOMPILE_SKIP_OPTIMIZATION, 0, &code, &errors);
        double t1 = now_ms();
        fxc_ms += t1 - t0;
        int fok = SUCCEEDED(hr);
        fxc_ok += fok;
        if (code) ID3D10Blob_Release(code);
        if (errors) ID3D10Blob_Release(errors);
        code = errors = NULL;
        t0 = now_ms();
        hr = rt_vkd3d_compile(hlsl, strlen(hlsl), fd.cFileName, "main", target, 0, &code, &errors);
        t1 = now_ms();
        vk_ms += t1 - t0;
        if (t1 - t0 > vk_max) { vk_max = t1 - t0; snprintf(vk_max_name, sizeof vk_max_name, "%s", fd.cFileName); }
        if (SUCCEEDED(hr)) {
            vk_ok++;
            void *obj = NULL;
            const void *p = ID3D10Blob_GetBufferPointer(code);
            SIZE_T sz = ID3D10Blob_GetBufferSize(code);
            HRESULT cr = stage == SHADER_VERTEX ? ID3D11Device_CreateVertexShader(dev, p, sz, NULL, (ID3D11VertexShader **)&obj)
                       : stage == SHADER_PIXEL  ? ID3D11Device_CreatePixelShader(dev, p, sz, NULL, (ID3D11PixelShader **)&obj)
                                                : ID3D11Device_CreateComputeShader(dev, p, sz, NULL, (ID3D11ComputeShader **)&obj);
            if (SUCCEEDED(cr)) { created++; IUnknown_Release((IUnknown *)obj); }
            else printf("%s: D3D11 REFUSED vkd3d bytecode (%#lx)\n", fd.cFileName, (unsigned long)cr);
        } else {
            if (fok) vk_only_fail++;
            printf("%s: vkd3d FAILED%s\n", fd.cFileName, fok ? " (fxc compiles it)" : "");
            if (verbose && errors) printf("%.3000s\n", (char *)ID3D10Blob_GetBufferPointer(errors));
        }
        if (code) ID3D10Blob_Release(code);
        if (errors) ID3D10Blob_Release(errors);
        free(hlsl);
    } while (FindNextFileA(h, &fd));
    printf("%d shaders: fxc compiled %d (%.0f ms), vkd3d compiled %d (%.0f ms, slowest %.0f ms %s), D3D11 took %d of vkd3d's; "
           "%d that fxc compiles vkd3d does not\n", total, fxc_ok, fxc_ms, vk_ok, vk_ms, vk_max, vk_max_name, created, vk_only_fail);
    return vk_only_fail != 0;
}
