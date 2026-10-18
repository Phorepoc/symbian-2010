// dev_exe_startup.cpp — the EXE thread bootstrap.
//
// The real exexp startup is uc_exe.cia (_E32Startup, REAL source, compiled
// as-is into the EXE) + uc_exe.cpp's RunThread. uc_exe.cpp only has bodies for
// __GCC32__ (2004 GCC: __CTOR_LIST__ walk) and __ARMCC__; our modern-GCC build
// defines neither and hits its #error. This TU is the modern-GCC RunThread:
// identical logic to the __GCC32__ branch, with the static-ctor walk over the
// .init_array table (emitter/efile.ld brackets it with __init_array_start/
// __init_array_end) instead of the extinct __CTOR_LIST__ format.
//
// Static DTORS are not run: globalDestructorFunc is registered exactly like
// the real one (DllSetTls) but is a no-op — the file server never exits, and
// process teardown reclaims everything. (The real __GCC32__ branch walks
// __DTOR_LIST__; wiring .fini_array is a when-needed follow-up.)

#include "u32std.h"
#include <u32exec.h>
#include <e32svr.h>
#include <e32panic.h>

GLREF_C TInt E32Main();

typedef void (*TCtorFn)();
extern TCtorFn __init_array_start[];   // efile.ld
extern TCtorFn __init_array_end[];

extern "C" {

void globalDestructorFunc()
	{
	}

void RunThread(TBool aNotFirst, SThreadCreateInfo& aInfo)
	{
	SStdEpocThreadCreateInfo& cinfo = (SStdEpocThreadCreateInfo&)aInfo;

	TInt r = UserHeap::SetupThreadHeap(aNotFirst, cinfo);

	if (r==KErrNone)
		r = UserSvr::DllSetTls(KGlobalDestructorTlsKey, KDllUid_Special, (TAny*)globalDestructorFunc);

	if (r==KErrNone)
		{
		if (aNotFirst)
			r = (*cinfo.iFunction)(cinfo.iPtr);
		else
			{
			// Init statics for implicitly linked DLLs
			User::InitProcess();

			// Init statics for EXE (.init_array, oldest first)
			for (TCtorFn* f = __init_array_start; f < __init_array_end; ++f)
				(**f)();

			r = E32Main();
			}
		}
	User::Exit(r);
	}
}
