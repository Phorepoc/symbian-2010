/*
 * symbian_gcc_prefix.h — force-include prefix that lets a MODERN GNU ARM
 * compiler (arm-none-eabi-g++ 14.x) consume unmodified Symbian 2010 source.
 *
 * Strategy mirrors Symbian's own GCCE prefix (kernel/eka/compsupp/gcce/gcce.h):
 * we do NOT define __GCC32__/__VC32__/__CW32__, so every per-compiler branch
 * inside e32def.h / e32des16.h is SKIPPED (those branches use GCCE's
 * Symbian-patched `__declspec(dllexport|naked|notshared)` spellings that a
 * stock upstream GCC/Clang rejects). Instead we supply the very same macros
 * here, first, using standard GNU attributes.
 *
 * Usage:  arm-none-eabi-g++ -include toolchain/symbian_gcc_prefix.h \
 *           -D__SYMBIAN32__ -D__GCCE__ -D__EABI__ -D__MARM__ \
 *           -D__MARM_ARMV5__ -D__EPOC32__ -fshort-wchar ...
 * (Deliberately NO -D__GCC32__.)
 */
#ifndef SYMBIAN_GCC_PREFIX_H
#define SYMBIAN_GCC_PREFIX_H

/* --- 64-bit integer types (e32def.h only defines these under __GCC32__) --- */
typedef long long          Int64;
typedef unsigned long long Uint64;
#ifndef I64LIT
#define I64LIT(x)  x##LL
#define UI64LIT(x) x##ULL
#endif

/* --- wide text type (e32des16.h #errors "no typedef for __TText" otherwise).
 * With -fshort-wchar, wchar_t is 16-bit, matching Symbian TText16. --- */
#ifdef __cplusplus
typedef wchar_t __TText;
#else
/* a C TU (a `cdriver` component, e.g. mbedTLS in the ARM ssl.dll): C has no built-in wchar_t */
typedef __WCHAR_TYPE__ __TText;
#endif
#define __TText_defined

/* --- DLL import/export. Symbian drives the export TABLE by ordinal (.def /
 * our E32Image emitter), not by ELF visibility, so these need mark nothing.
 * The emitter builds the export table from the symbol list. EXPORT_C = `used`: the source has
 * `EXPORT_C inline` definitions (CTextListItemDrawer::Font(TInt) ...) -- RVCT/GCCE dllexport forced an
 * out-of-line copy; plain GCC drops an unused inline, leaving the ordinal an unimpl stub. */
#define IMPORT_C
#define EXPORT_C __attribute__((used))
#define IMPORT_D
#define EXPORT_D

/* --- naked / non-shared / noreturn / throw specifiers --- */
#define __NAKED__ __attribute__((naked))
#define ____ONLY_USE_NAKED_IN_CIA____ __attribute__((naked))
#define NONSHARABLE_CLASS(x)  class x
#define NONSHARABLE_STRUCT(x) struct x
#define __NORETURN__ __attribute__((noreturn))
#define __NO_THROW
#define __THROW(t)

/* --- _FOFF: e32def.h picks __builtin_offsetof for __GNUC__>=4, which rejects a
 * RUNTIME array index like _FOFF(HBufC8, iBuf[aMaxLength]) (used in des8.cpp
 * HBufC allocation). The classic Symbian calculation form allows it. e32def.h
 * guards with #ifndef _FOFF, so defining it here wins. --- */
#define _FOFF(c,f) (((TInt)&(((c *)0x1000)->f))-0x1000)

/* EABI wants explicit template specialisation spelt `template<>` */
#ifndef TEMPLATE_SPECIALIZATION
#define TEMPLATE_SPECIALIZATION template<>
#endif

/* --- varargs. Without __GCC32__, gcc.h (which maps Symbian's VA_LIST/VA_START/
 * VA_ARG onto the compiler builtins) is NOT pulled, leaving e32def.h's LEGACY
 * VA_LIST = `TInt8*[1]` with manual `&pn + sizeof(pn)` stack-walking. That is
 * wrong for the ARM EABI, where the first varargs sit in r0-r3, not on a
 * contiguous stack frame — so TDes::Format(...) reads garbage arguments. Mirror
 * gcc.h exactly and set the guards so e32def.h skips its legacy definitions. --- */
typedef __builtin_va_list va_list;
#define va_start(v, l) __builtin_va_start(v, l)
#define va_arg(v, l)   __builtin_va_arg(v, l)
#define va_end(v)      __builtin_va_end(v)
#define VA_LIST va_list
#define _VA_LIST_DEFINED            /* stdarg.h guard */
#define __VA_LIST_defined           /* e32def.h typedef guard */
#define VA_START(ap,pn) va_start(ap, pn)
#define VA_ARG(ap,type) va_arg(ap, type)
#define VA_END(ap)      va_end(ap)

#endif /* SYMBIAN_GCC_PREFIX_H */
