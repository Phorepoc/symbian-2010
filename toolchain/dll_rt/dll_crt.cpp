// dll_crt.cpp — the modern-g++ C++ runtime leaves for a from-source DLL/EXE built
// by emitter/build_mmp.py (operator new/delete come from the REAL common/gcchelp.cpp).
//   _ZdlPvj / _ZdaPvj   sized operator delete (C++14) -> the unsized one
//   __cxa_pure_virtual  pure-call trap -> User::Invariant (never a silent return)
//   __aeabi_atexit      static-dtor registration: accepted and dropped (we run no
//                       static dtors; process teardown reclaims)
// Deliberately NO data/bss here: Symbian DLLs must carry no writable static data.
#include <e32std.h>

void operator delete(TAny* aPtr, TUint) __NO_THROW
	{
	operator delete(aPtr);
	}

void operator delete[](TAny* aPtr, TUint) __NO_THROW
	{
	operator delete[](aPtr);
	}

extern "C" {

void __cxa_pure_virtual()
	{
	for (;;)
		User::Invariant();
	}

int __aeabi_atexit(void* /*aObject*/, void (* /*aDtor*/)(void*), void* /*aDso*/)
	{
	return 0;
	}

}
