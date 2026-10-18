// crt_shim.cpp — the tiny C++-runtime shim for from-source EXEs.
//
// EFILE.EXE's 12 "C++ runtime" link-surface symbols split three ways:
//   * __aeabi_ldivmod / ddiv / dmul / d2iz / i2d ...  -> libgcc (in the link)
//   * operator new/new[]/delete/delete[]              -> REAL source: common/gcchelp.cpp
//     (User::Alloc-backed, same TU the euser DLL builds link)
//   * the three below — items modern g++ emits that neither provides.
//
// __cxa_pure_virtual: called through a vtable slot of a partially-constructed
// object; loops on User::Invariant (panic) — never a silent return.
// __aeabi_atexit / __dso_handle: static-dtor registration; we run no static
// dtors (see dev_exe_startup.cpp), so registration is accepted and dropped.
// _ZdlPvj (sized operator delete, C++14): forward to the real unsized one.

#include <e32std.h>

extern "C" {

void* __dso_handle = 0;

int __aeabi_atexit(void* /*aObject*/, void (*/*aDtor*/)(void*), void* /*aDso*/)
	{
	return 0;   // accepted, never run (process teardown reclaims)
	}

void __cxa_pure_virtual()
	{
	for (;;)
		User::Invariant();
	}
}

// sized operator delete — forward to gcchelp.cpp's real unsized one.
void operator delete(TAny* aPtr, TUint) __NO_THROW
	{
	operator delete(aPtr);
	}
