/* Offline check of the shader translator: translate every dumped program in a directory
 * (<pipeline stage>_<hash>.bin, as SSBU_DUMP_SHADERS writes them) and compile the HLSL with
 * the system's D3DCompiler, reporting failures. With -v, print the HLSL of each.
 *
 * Usage: shadertest <dir> [-v] [name filter] */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <d3dcompiler.h>

#include "gpu_shader.h"

_Noreturn void rt_fatal(const char *fmt, ...) { (void)fmt; abort(); }

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    int verbose = argc > 2 && !strcmp(argv[2], "-v");
    const char *filter = argc > 3 ? argv[3] : argc > 2 && !verbose ? argv[2] : NULL;
    char pattern[1024];
    snprintf(pattern, sizeof pattern, "%s\\*.bin", argv[1]);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    int total = 0, failed = 0, unsupported = 0;
    if (h == INVALID_HANDLE_VALUE) return 1;
    do {
        if (filter && !strstr(fd.cFileName, filter)) continue;
        char path[1200];
        snprintf(path, sizeof path, "%s\\%s", argv[1], fd.cFileName);
        FILE *f = fopen(path, "rb");
        static unsigned char buf[0x40000];
        size_t n = fread(buf, 1, sizeof buf, f);
        fclose(f);
        int stage = fd.cFileName[0] == '5' ? SHADER_PIXEL : fd.cFileName[0] == 'C' ? SHADER_COMPUTE : SHADER_VERTEX;
        u32 local[3] = {1, 1, 1}, shared = 0;
        if (stage == SHADER_COMPUTE) {   /* C_<hash>_<x>x<y>x<z>_<shared>.bin */
            const char *u = strchr(fd.cFileName + 2, '_');
            if (u) sscanf(u + 1, "%ux%ux%u_%u", &local[0], &local[1], &local[2], &shared);
        }
        ShaderInfo info;
        u32 vs_out = 0;
        if (stage == SHADER_PIXEL) {   /* first learn what it reads */
            free(shader_hlsl(buf, (u32)n, stage, 0xFFFFFFFFu, &info));
            vs_out = info.inputs;
        }
        char *hlsl = stage == SHADER_COMPUTE ? shader_hlsl_cs(buf, (u32)n, local, shared, &info, NULL, NULL)
                                             : shader_hlsl(buf, (u32)n, stage, vs_out, &info);
        total++;
        if (info.unsupported[0]) { unsupported++; printf("%s: UNSUPPORTED %s\n", fd.cFileName, info.unsupported); }
        ID3DBlob *code = NULL, *errors = NULL;
        HRESULT hr = D3DCompile(hlsl, strlen(hlsl), fd.cFileName, NULL, NULL, "main", stage == SHADER_PIXEL ? "ps_5_0" : stage == SHADER_COMPUTE ? "cs_5_0" : "vs_5_0",
                                D3DCOMPILE_OPTIMIZATION_LEVEL1, 0, &code, &errors);
        if (FAILED(hr)) {   /* fxc's optimiser has internal errors on some dispatch loops */
            if (errors) errors->lpVtbl->Release(errors);
            errors = NULL;
            hr = D3DCompile(hlsl, strlen(hlsl), fd.cFileName, NULL, NULL, "main", stage == SHADER_PIXEL ? "ps_5_0" : stage == SHADER_COMPUTE ? "cs_5_0" : "vs_5_0",
                            D3DCOMPILE_OPTIMIZATION_LEVEL0, 0, &code, &errors);
        }
        if (verbose) printf("==== %s\n%s\n", fd.cFileName, hlsl);
        if (FAILED(hr)) {
            failed++;
            printf("%s: COMPILE FAILED\n%s\n", fd.cFileName, errors ? (char *)errors->lpVtbl->GetBufferPointer(errors) : "?");
        }
        if (code) code->lpVtbl->Release(code);
        if (errors) errors->lpVtbl->Release(errors);
        free(hlsl);
    } while (FindNextFileA(h, &fd));
    printf("%d shaders: %d failed to compile, %d with unsupported instructions\n", total, failed, unsupported);
    return failed != 0;
}
