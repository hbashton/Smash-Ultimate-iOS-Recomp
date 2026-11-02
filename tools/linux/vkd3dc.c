/* Compile an HLSL file with vkd3d-shader (as the Linux build does): vkd3dc <file> <profile> [entry].
 * Prints vkd3d's messages and the bytecode size; exit status 0 if it compiled. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <d3dcompiler.h>

HRESULT rt_vkd3d_compile(const void *src, SIZE_T size, const char *name, const char *entry, const char *target,
                         UINT flags, ID3DBlob **code, ID3DBlob **errors);

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    static char src[1 << 22];
    size_t n = fread(src, 1, sizeof src - 1, f);
    fclose(f);
    ID3DBlob *code = NULL, *errors = NULL;
    HRESULT hr = rt_vkd3d_compile(src, n, argv[1], argc > 3 ? argv[3] : "main", argv[2], 0, &code, &errors);
    if (errors) printf("%s\n", (const char *)errors->lpVtbl->GetBufferPointer(errors));
    printf("%s", SUCCEEDED(hr) ? "compiled" : "FAILED");
    if (code) printf(": %zu bytes of DXBC", (size_t)code->lpVtbl->GetBufferSize(code));
    printf("\n");
    return FAILED(hr);
}
