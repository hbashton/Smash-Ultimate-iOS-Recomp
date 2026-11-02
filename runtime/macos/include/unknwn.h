/* IUnknown for the macOS build, in place of DXVK's unknwn.h: only so that a header naming it
 * compiles (nothing here is COM: the renderer is Metal). C's layout of the interface, which C++
 * reads as the same plain structure. */
#pragma once
#include "windows_base.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct IUnknown IUnknown;
typedef struct IUnknownVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(IUnknown *self, REFIID riid, void **object);
    ULONG (STDMETHODCALLTYPE *AddRef)(IUnknown *self);
    ULONG (STDMETHODCALLTYPE *Release)(IUnknown *self);
} IUnknownVtbl;
struct IUnknown { const IUnknownVtbl *lpVtbl; };
typedef IUnknown *LPUNKNOWN;

#define IUnknown_QueryInterface(p, riid, out) ((p)->lpVtbl->QueryInterface(p, riid, out))
#define IUnknown_AddRef(p) ((p)->lpVtbl->AddRef(p))
#define IUnknown_Release(p) ((p)->lpVtbl->Release(p))

#ifdef __cplusplus
}
#endif
