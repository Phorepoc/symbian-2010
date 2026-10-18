// ekern_seam.cpp -- the modern-toolchain glue for linking the from-source EKA2 kernel. Everything here bridges a
// difference between the 2010 RVCT/GCCE build and our arm-none-eabi EABI toolchain, or hooks the Syborg board's
// built-in drivers in where a ROM would have loaded them from files; none of it is Symbian logic.
//
//  1. Name-mangling aliases: the hand-written .cia files reference GCC2-mangled names (Foo__Fv) but the .cpp define
//     the same functions with EABI mangling (_Z...). They are mapped at link time with `ld --defsym`
//     (kernel/link_ekern.sh): Des8PanicBadDesType__Fv, Des8PanicPosOutOfRange__Fv, Check__14DBTraceFilter2Ul and
//     _ZN1K9MsgInfoE (the .cia's spelling of K::MsgInfo).
//  2. Sized operator delete (_ZdlPvj): modern GCC emits calls to the C++14 sized delete, which Symbian's gcchelp.cpp
//     (unsized _ZdlPv only) doesn't provide -> forward it. __cxa_pure_virtual likewise.
//  3. HwInit0(): the uniprocessor early hardware-init hook is `extern` in nk_entry.cia but has no definition in the
//     2010 source. Its job here is PP::InitSuperPageFromRom, which kernel/arm/k_entry.cia (kc_exe.lib, used by real
//     ekern builds) would have called.
//  4. TDesC8::Mid(TInt,TInt): the hand-written naked .cia is miscompiled by modern GCC; replaced by plain C++.
//  5. The kernel extensions and the first user process: P::StartExtensions has nothing to start (the Syborg drivers
//     are linked into ekern), K::StartKernelServer brings the built-in drivers up and starts the file server.
//  6. The variant "DLL" entry, the MMU domain default, the entry trampoline.
//  7. A small serial trace: a line per user process created, per thread panic and per kernel fault.

#include <e32def.h>
#include <kernel/kern_priv.h>
#include <memmodel.h>

// --- crash-monitor stubs (the exmon module is a separate ROM component) -----------------
__asm__(
  ".global TheMonitorPtr\n .data\n .balign 4\n TheMonitorPtr: .word 0\n .text\n"
);
extern "C" void _ZN7Monitor5LeaveEi() {}

// --- 2. sized operator delete -> unsized ------------------------------------------------
void operator delete(TAny* aPtr) __NO_THROW;              // provided by gcchelp.cpp
void operator delete(TAny* aPtr, TUint /*aSize*/) __NO_THROW { operator delete(aPtr); }
void operator delete[](TAny* aPtr) __NO_THROW;
void operator delete[](TAny* aPtr, TUint /*aSize*/) __NO_THROW { operator delete[](aPtr); }

// The kernel objects reference __cxa_pure_virtual weakly; with no definition an abstract class's vtable slot holds 0
// and a pure virtual call jumps to address 0. Name the caller and fault properly.
extern "C" void ekseam_puts(const char* s);
extern "C" void ekseam_puthex(unsigned v);
extern "C" void __cxa_pure_virtual()
	{
	ekseam_puts("[EKERN] *** PURE VIRTUAL CALL from LR=");
	ekseam_puthex((unsigned)(unsigned long)__builtin_return_address(0));
	Kern::Fault("PURE-VIRTUAL", 0);
	}

// --- serial output: a direct write to the Syborg UART's virtual address the bootstrap mapped (the kernel's own
//     Kern::Printf needs the debug channel up) -----------------------------------------------
#define EKSEAM_UART 0xC6006004u
#define EKSEAM_UART_S "0xC6006004"
static inline void ekseam_uart_put(unsigned c)
	{
	*(volatile unsigned*)EKSEAM_UART = c;
	}
extern "C" void ekseam_puts(const char* s)
	{
	while (*s) ekseam_uart_put((unsigned char)*s++);
	}
extern "C" void ekseam_puthex(unsigned v)
	{
	char buf[12]; int n=0;
	buf[n++]='0'; buf[n++]='x';
	for (int i=28;i>=0;i-=4){ unsigned d=(v>>i)&0xF; buf[n++] = d<10 ? '0'+d : 'a'+d-10; }
	buf[n++]='\n'; buf[n]=0;
	for (int i=0;i<n;i++) ekseam_uart_put((unsigned char)buf[i]);
	}

// --- 4. TDesC8::Mid(TInt,TInt) -------------------------------------------------------------
// The hand-written naked EABI Mid(aPos,aLength) returns TPtrC8 by sret (r0=&ret, r1=this, r2=aPos, r3=aLength).
// Modern GCC injects `mov r3,r0` at entry (stashing the sret pointer so it can `mov r0,r3` on return, per the C++
// memory-return ABI) -- but that CLOBBERS r3, which the naked asm uses as the incoming aLength. The result is a
// malformed TPtrC8 (DProcess::Create's kernel-process name descriptor: ValidateName panics the initial thread). Only
// the 3-arg Mid is affected (Left/Right/Mid(aPos) put their last arg in r2). A correct plain-C++ replacement,
// callers redirected with `ld --wrap`.
extern "C" const unsigned char* _ZNK6TDesC83PtrEv(const void* aThis);  // TDesC8::Ptr() const
extern "C" void __wrap__ZNK6TDesC83MidEii(unsigned* aRet, const void* aThis, int aPos, int aLength)
	{
	aRet[0] = (unsigned)aLength | 0x10000000u;                              // length | (EPtrC<<28)
	aRet[1] = (unsigned)(unsigned long)(_ZNK6TDesC83PtrEv(aThis) + aPos);   // Ptr() + aPos
	}

// --- 3. HwInit0 early hook -----------------------------------------------------------------
// Our entry is nkern's nk_entry.cia _E32Startup, which stores RomHeaderAddress/SuperPageAddress but -- unlike
// kernel/arm/k_entry.cia -- never calls PP::InitSuperPageFromRom. Without it the superpage iDebugMask (kernel and
// user trace masks), kernel config flags, BTrace filter and disabled capabilities are never copied from the ROM
// header. Call it here, with the same pre-constructor timing as k_entry.
extern "C" unsigned long RomHeaderAddress;
extern "C" unsigned long SuperPageAddress;
extern "C" void _ZN2PP20InitSuperPageFromRomEmm(unsigned long, unsigned long);   // PP::InitSuperPageFromRom
extern "C" void HwInit0()
	{
	ekseam_puts("\n[EKERN] _E32Startup reached HwInit0 -- from-source kernel is running\n");
	_ZN2PP20InitSuperPageFromRomEmm(RomHeaderAddress, SuperPageAddress);
	}

// --- 5. extensions and the first user process -------------------------------------------------
// P::StartExtensions builds `new RArray<SExtInit1EntryPoint>(--K::ExtensionCount)` as the extension entry-point
// array. K::ExtensionCount = number of ROM kernel extensions counted in P::CreateVariant (incl. the variant); the
// pre-decrement excludes the variant. Our ROM carries ONLY the variant (the drivers are linked into ekern), so the
// count is 1 -> --==0 -> RArray granularity 0 trips RArrayBase's `aGranularity>0` assert (panic TCdtPanic 127),
// killing the system-critical supervisor thread. With no non-variant extensions there is nothing to start: skip.
extern "C" { extern int _ZN1K14ExtensionCountE; }
extern "C" void __real__ZN1P15StartExtensionsEv();
extern "C" void __wrap__ZN1P15StartExtensionsEv()
	{
	if (_ZN1K14ExtensionCountE <= 1)
		return;
	__real__ZN1P15StartExtensionsEv();
	}

// exstart.cpp is linked into ekern (StartSecondary + StartSecondaryDfc + InitExtension). Normally a kernel extension
// whose Init1 enqueues StartSecondaryDfc on SvMsgQ; the supervisor DFC loop then runs StartSecondary ->
// Kern::ProcessCreate + Resume of the ROM's iSecondaryFile (the file server). StartSecondaryDfc's global constructor
// runs at kernel static-init, before SvMsgQ exists, so its DfcQ would be bound to NULL and Enque() is a no-op. Just
// call StartSecondary directly from here: we are already on the supervisor thread, the context the DFC would run in.
// The built-in drivers come up first, in ROM extension order.
extern "C" void _Z14StartSecondaryPv(void*);
extern "C" void ekseam_load_fb_driver();      // ekern_fb_glue.cpp: svpframebuffer + the display LDD
extern "C" void ekseam_load_media_drivers();  // ekern_media_glue.cpp: locmedia (ELOCD) + mednvmemory -> drive C:
extern "C" void ekseam_load_input_drivers();  // ekern_input_glue.cpp: keyboard + pointer -> Kern::AddEvent
extern "C" void __real__ZN1K17StartKernelServerEv();
extern "C" void __wrap__ZN1K17StartKernelServerEv()
	{
	ekseam_load_fb_driver();
	ekseam_load_media_drivers();
	ekseam_load_input_drivers();
	ekseam_puts("[EKERN] starting the file server\n");
	_Z14StartSecondaryPv(0);
	__real__ZN1K17StartKernelServerEv();
	}

// --- 7. serial trace ----------------------------------------------------------------------------
// DProcess::Create: one "[proc] create <name>" line per process (the root name inside the process file name).
extern "C" int __real__ZN8DProcess6CreateEiR18TProcessCreateInfoP5HBuf8(void*, int, void*, void*);
extern "C" int __wrap__ZN8DProcess6CreateEiR18TProcessCreateInfoP5HBuf8(void* a, int b, void* c, void* d)
	{
	if (c)
		{
		const TProcessCreateInfo& ci = *(const TProcessCreateInfo*)c;
		const TInt off = ci.iRootNameOffset, len = ci.iRootNameLength;
		if (off >= 0 && len > 0 && off + len <= ci.iFileName.Length() && len < 60)
			{
			char nm[64];
			for (TInt i = 0; i < len; ++i)
				nm[i] = (char)ci.iFileName[off + i];
			nm[len] = '\n'; nm[len + 1] = 0;
			ekseam_puts("[proc] create "); ekseam_puts(nm);
			}
		}
	return __real__ZN8DProcess6CreateEiR18TProcessCreateInfoP5HBuf8(a, b, c, d);
	}

// DThread::Die: a panicking thread, with its category and reason.
extern "C" void __real__ZN7DThread3DieE9TExitTypeiRK6TDesC8(void*, int, int, void*);
extern "C" void __wrap__ZN7DThread3DieE9TExitTypeiRK6TDesC8(void* a, int b, int c, void* d)
	{
	if (b == EExitPanic && a)
		{
		TBuf8<96> fn;
		((DThread*)a)->FullName(fn);
		fn.Append('\0');
		ekseam_puts("[EKERN] panic in '"); ekseam_puts((const char*)fn.Ptr()); ekseam_puts("'");
		if (d)
			{
			const TDesC8& cat = *(const TDesC8*)d;
			TBuf8<40> k;
			k.Copy(cat.Left(Min(cat.Length(), 38)));
			k.Append('\0');
			ekseam_puts(" category='"); ekseam_puts((const char*)k.Ptr()); ekseam_puts("'");
			}
		ekseam_puts(" reason="); ekseam_puthex((unsigned)c);
		}
	__real__ZN7DThread3DieE9TExitTypeiRK6TDesC8(a, b, c, d);
	}

// K::Fault / Kern::Fault: the fault reason and the caller (the crash monitor is stubbed, so a fault otherwise just
// halts silently).
extern "C" void __real__ZN1K5FaultENS_6TFaultE(int);
extern "C" void __wrap__ZN1K5FaultENS_6TFaultE(int aFault)
	{
	ekseam_puts("\n[EKERN] *** K::Fault reason="); ekseam_puthex((unsigned)aFault);
	ekseam_puts("[EKERN]     called from LR="); ekseam_puthex((unsigned)(unsigned long)__builtin_return_address(0));
	__real__ZN1K5FaultENS_6TFaultE(aFault);
	}
extern "C" void __real__ZN4Kern5FaultEPKci(const char*, int);
extern "C" void __wrap__ZN4Kern5FaultEPKci(const char* cat, int reason)
	{
	ekseam_puts("\n[EKERN] *** Kern::Fault: ");
	ekseam_puts(cat ? cat : "(null)");
	ekseam_puts(" reason="); ekseam_puthex((unsigned)reason);
	__real__ZN4Kern5FaultEPKci(cat, reason);
	}

// --- 6. entry trampoline ----------------------------------------------------------------------
// The ROM image's iEntryPoint: print a marker to the virtual UART (proving the bootstrap's jump reaches our kernel
// code in virtual space), preserving r0/r1 (aRomHeader/aSuperPage), then branch to the real _E32Startup.
extern "C" void _E32Startup(unsigned, unsigned);
extern "C" __attribute__((naked, used)) void _ekern_entry()
	{
	__asm__ volatile(
		"stmfd sp!, {r0, r1, lr}\n"
		"ldr   r4, =" EKSEAM_UART_S "\n"        // virtual UART0 data register (EKSEAM_UART)
		"adr   r5, 2f\n"
		"1:\n ldrb r6, [r5], #1\n cmp r6, #0\n beq 3f\n str r6, [r4]\n b 1b\n"
		"3:\n ldmfd sp!, {r0, r1, lr}\n"
		"b     _E32Startup\n"
		".ltorg\n"
		"2:\n .asciz \"\\n[EKERN] entry trampoline reached -- jumping into _E32Startup\\n\"\n"
		".align 2\n");
	}

// --- variant DLL entry (_E32Dll) ----------------------------------------------------------
// The variant's code (VariantInitialise/AsicInitialise/Syborg::*) is statically linked into ekern, so the ROM
// "variant file" is just a TRomImageHeader whose export table points at those ekern functions and whose iEntryPoint
// is this minimal _E32Dll. InitData calls it with KModuleEntryReasonVariantInit0; the return value is the mode
// bitmask A::CreateVariant uses for staged VariantInitialise(i) calls (0 = just create the Asic via ordinal 1).
// CHECK_ROM_ENTRY_POINT only checks the header iFlags (Eka2), not this code, so a plain function suffices.
extern "C" { TInt __Variant_Flags__ = 0; }
extern "C" int _variant_e32dll(int /*aReason*/) { return __Variant_Flags__; }
extern "C" int _E32Dll(int /*aReason*/) { return 0; }

// --- MMU domain-access default (multiple model) ---------------------------------------------
// Arm::DefaultDomainAccess -- supervisor is a client of domain 0 (KSupervisorInitialDomainAccess).
namespace Arm { extern TUint32 DefaultDomainAccess; TUint32 DefaultDomainAccess = 0x00000001u; }
