/*
 * unimpl_export.cpp — the placeholder every not-yet-from-source euser export
 * points to.
 *
 * The full euser export table (2546 ordinals from eabi/euseru.def) is reproduced
 * so our DLL presents the REAL euser ordinal layout. The ~770 symbols our sources
 * actually compile resolve to their real code at their real ordinal; every other
 * ordinal points here. Calling an unimplemented export therefore fails loud and
 * diagnosable — the current thread is killed (EExitPanic, category "EUSRNIMP") via
 * thread_kill (SVC 0x73) — rather than jumping into unrelated bytes. As more of
 * euser is built from source, ordinals migrate off this stub onto real code.
 */
#include <e32std.h>

extern "C" void __unimpl_export()
	{
	static const char kCat[] = "EUSRNIMP";
	struct SDes8 { TUint32 iInfo; const void* iPtr; }
		cat = { (1u << 28) | 8u, kCat };                    // TPtrC8: type 1, len 8
	// The panic REASON carries the CALLER's return address (the call site of the
	// unimplemented export): the caller jumps here straight through the export
	// table / import veneer with its lr intact, so this names the exact import.
	TInt caller = (TInt)__builtin_return_address(0);
	register TInt  r0 __asm__("r0") = (TInt)0xFFFF8001;     // KCurrentThreadHandle
	register TInt  r1 __asm__("r1") = 2;                    // EExitPanic
	register TInt  r2 __asm__("r2") = caller;
	register void* r3 __asm__("r3") = &cat;
	__asm__ __volatile__("svc #0x73"
		: : "r"(r0), "r"(r1), "r"(r2), "r"(r3) : "r12", "lr", "memory", "cc");
	for (;;) {}
	}
