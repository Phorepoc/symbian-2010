// ekern_fb_glue.cpp -- loads the Syborg framebuffer driver.
//
// The driver (baseport/syborg/svpframebuffer/svpframebuffer.cpp, a DECLARE_STANDARD_EXTENSION kernel extension) is
// linked into ekern and brought up by calling the body of its InitExtension() from the supervisor thread -- the
// context the ROM extension-start DFC would run it in. DLcdPowerHandler::Create does the real work:
// Epoc::AllocPhysicalRam + DPlatChunkHw::New for the framebuffer, Kern::AddHalEntry, DisplayOn() -> PowerUpLcd()
// programming the CLCD. The tail of InitExtension -- the DFC queue and the RDisplayChannel physical device -- is
// done here too, plus the generic display logical device (display.cpp, DECLARE_STANDARD_LDD) which a ROM would
// have loaded from display0.ldd (the file server's loader answers for it: toolchain/efile_exe/ldr_builtin_devices.cpp).
#include <e32def.h>
#include <kernel/kernel.h>
#include "svpframebuffer.h"

extern "C" void ekseam_puts(const char* s);
extern "C" void ekseam_puthex(unsigned v);

class DLogicalDevice;
DLogicalDevice* CreateLogicalDevice();

extern "C" void ekseam_load_fb_driver()
	{
	DLcdPowerHandler* h = new DLcdPowerHandler;
	if (!h)
		{ ekseam_puts("[EKERN][fb] out of memory\n"); return; }
	TInt r = h->Create();
	if (r != KErrNone)
		{ ekseam_puts("[EKERN][fb] Create failed r="); ekseam_puthex((unsigned)r); return; }

	// the priority-29 supervisor DFC thread the LDD/PDD run their DFCs on
	r = Kern::DfcQCreate(h->iDfcQ, 29, &KLitLcd);
	if (r != KErrNone)
		{ ekseam_puts("[EKERN][fb] DfcQCreate failed r="); ekseam_puthex((unsigned)r); return; }
	// Create() above bound its power DFCs + message queue to iDfcQ while it was still NULL (in the real
	// InitExtension the queue exists first): the first power transition would fault the kernel in TDfc::DoEnque.
	// Rebind them to the queue now that it exists.
	h->iPowerUpDfc.SetDfcQ(h->iDfcQ);
	h->iPowerDownDfc.SetDfcQ(h->iDfcQ);
	h->iMsgQ.SetDfcQ(h->iDfcQ);

	// register the RDisplayChannel PDD, then the generic display LDD: with both in the kernel object index,
	// RDisplayChannel::Open() can bind a channel -- what WSERV's screen driver needs
	DPhysicalDevice* pdd = new DDisplayPddFactory;
	r = pdd ? Kern::InstallPhysicalDevice(pdd) : KErrNoMemory;
	if (r != KErrNone)
		{ ekseam_puts("[EKERN][fb] InstallPhysicalDevice failed r="); ekseam_puthex((unsigned)r); return; }
	DLogicalDevice* ldd = CreateLogicalDevice();
	r = ldd ? Kern::InstallLogicalDevice(ldd) : KErrNoMemory;
	if (r != KErrNone)
		{ ekseam_puts("[EKERN][fb] InstallLogicalDevice failed r="); ekseam_puthex((unsigned)r); return; }
	ekseam_puts("[EKERN][fb] display driver up\n");
	}
