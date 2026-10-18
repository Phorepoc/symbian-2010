// cxx_rt_dev.cpp — the two modern-g++ runtime leaves the DEVICE euser calls
// internally that no 2010 TU provides (they were silent link stubs -> EUSRNIMP):
//   _ZdlPvj            sized operator delete (C++14): every deleting destructor
//                      in euser (CBufFlat, CArray*, CActive...) ends in it. First
//                      hit: ESHELL `dir` -> CDir::Sort -> ~CBufFlat (deleting).
//   __cxa_pure_virtual pure-call trap -> User::Invariant, never a silent return.
// No data/bss on purpose: euser is XIP with zero writable data.
#include <e32std.h>

void operator delete(TAny* aPtr, TUint) __NO_THROW
	{
	operator delete(aPtr);
	}

extern "C" void __cxa_pure_virtual()
	{
	for (;;)
		User::Invariant();
	}
