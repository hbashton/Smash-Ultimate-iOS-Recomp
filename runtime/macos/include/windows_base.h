/* The Windows base types for the macOS build, in place of DXVK's windows_base.h (which the Linux
 * build takes from its Direct3D headers; macOS draws with Metal and has no use for the rest of
 * them). The same definitions the runtime relies on there: DWORD and LONG are 32-bit, WCHAR is
 * the C library's wchar_t (L"..." strings), HANDLE a pointer. runtime/linux/include/windows.h
 * includes this and adds the API on top. */
#pragma once
#ifdef __cplusplus
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cwchar>
#else
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>
#endif

/* ---- integers, characters, pointers ------------------------------------------------------ */
typedef void VOID;
typedef int32_t INT;
typedef uint32_t UINT;
typedef int32_t LONG;
typedef uint32_t ULONG;
typedef int32_t BOOL;
typedef BOOL WINBOOL;
typedef uint8_t BYTE;
typedef uint16_t WORD;
typedef uint32_t DWORD;
typedef int16_t SHORT;
typedef uint16_t USHORT;
typedef unsigned char UCHAR;
typedef int64_t LONGLONG;
typedef uint64_t ULONGLONG;
typedef int8_t INT8;
typedef uint8_t UINT8;
typedef int16_t INT16;
typedef uint16_t UINT16;
typedef int32_t INT32;
typedef uint32_t UINT32;
typedef int64_t INT64;
typedef uint64_t UINT64;
typedef float FLOAT;
typedef intptr_t INT_PTR, LONG_PTR;
typedef uintptr_t UINT_PTR, ULONG_PTR;
typedef size_t SIZE_T;
typedef int32_t HRESULT;

typedef char CHAR;
typedef wchar_t WCHAR;
typedef CHAR *LPSTR, *PSTR;
typedef const CHAR *LPCSTR, *PCSTR;
typedef WCHAR *LPWSTR, *PWSTR;
typedef const WCHAR *LPCWSTR;

typedef void *PVOID, *LPVOID;
typedef const void *LPCVOID;
typedef BYTE *PBYTE, *LPBYTE;
typedef WORD *PWORD, *LPWORD;
typedef DWORD *LPDWORD;
typedef LONG *PLONG, *LPLONG;
typedef INT *PINT, *LPINT;
typedef UINT *PUINT;
typedef UCHAR *PUCHAR;

typedef void *HANDLE;
typedef HANDLE *PHANDLE, *LPHANDLE;
typedef HANDLE HMODULE, HINSTANCE, HWND, HMONITOR, HDC, HKEY;
typedef DWORD COLORREF;

typedef union LARGE_INTEGER {
    struct { DWORD LowPart; LONG HighPart; };
    struct { DWORD LowPart; LONG HighPart; } u;
    LONGLONG QuadPart;
} LARGE_INTEGER, *PLARGE_INTEGER;
typedef union ULARGE_INTEGER {
    struct { DWORD LowPart; DWORD HighPart; };
    struct { DWORD LowPart; DWORD HighPart; } u;
    ULONGLONG QuadPart;
} ULARGE_INTEGER, *PULARGE_INTEGER;

/* ---- small structures -------------------------------------------------------------------- */
typedef struct GUID { uint32_t Data1; uint16_t Data2, Data3; uint8_t Data4[8]; } GUID, UUID, IID, CLSID;
#ifdef __cplusplus
#define REFIID const IID &
#define REFGUID const GUID &
#define REFCLSID const GUID &
#else
#define REFIID const IID *
#define REFGUID const GUID *
#define REFCLSID const GUID *
#endif
#define DEFINE_GUID(name, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8) \
    static const GUID name = {l, w1, w2, {b1, b2, b3, b4, b5, b6, b7, b8}}
typedef struct LUID { DWORD LowPart; LONG HighPart; } LUID;
typedef struct POINT { LONG x, y; } POINT, *PPOINT, *LPPOINT;
typedef struct RECT { LONG left, top, right, bottom; } RECT, *PRECT, *LPRECT;
typedef struct SIZE { LONG cx, cy; } SIZE, *PSIZE, *LPSIZE;
typedef struct SECURITY_ATTRIBUTES { DWORD nLength; void *lpSecurityDescriptor; BOOL bInheritHandle; } SECURITY_ATTRIBUTES, *LPSECURITY_ATTRIBUTES;

/* ---- calling conventions and decorations (nothing, here) -------------------------------- */
#define WINAPI
#define WINUSERAPI
#define STDMETHODCALLTYPE
#define __stdcall
#define __cdecl
#define CONST const
#ifndef __OBJC__   /* (Objective-C's @interface: an Objective-C file that includes the shim keeps it) */
#define interface struct
#endif

/* ---- constants and macros ---------------------------------------------------------------- */
#define TRUE 1
#define FALSE 0
#define MAX_PATH 260
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define WAIT_OBJECT_0 0
#define WAIT_ABANDONED 0x00000080
#define WAIT_TIMEOUT 0x00000102
#define WAIT_FAILED 0xFFFFFFFFu
#define DUPLICATE_CLOSE_SOURCE 0x00000001
#define DUPLICATE_SAME_ACCESS 0x00000002

#define S_OK ((HRESULT)0)
#define S_FALSE ((HRESULT)1)
#define E_NOTIMPL ((HRESULT)0x80004001)
#define E_NOINTERFACE ((HRESULT)0x80004002)
#define E_POINTER ((HRESULT)0x80004003)
#define E_FAIL ((HRESULT)0x80004005)
#define E_OUTOFMEMORY ((HRESULT)0x8007000E)
#define E_INVALIDARG ((HRESULT)0x80070057)
#define MAKE_HRESULT(sev, fac, code) ((HRESULT)(((uint32_t)(sev) << 31) | ((uint32_t)(fac) << 16) | (uint32_t)(code)))
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define SUCCEEDED(hr) ((HRESULT)(hr) >= 0)

#define RtlZeroMemory(p, n) memset((p), 0, (n))
#define ZeroMemory RtlZeroMemory
#define RGB(r, g, b) ((COLORREF)((BYTE)(r) | ((WORD)(BYTE)(g) << 8) | ((DWORD)(BYTE)(b) << 16)))
