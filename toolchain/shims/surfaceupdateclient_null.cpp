// surfaceupdateclient_null.cpp — OURS (not Symbian source): a null Surface Update
// client with the REAL surfaceupdateclient.def ordinal table.
//
// The generic screen driver (scdv sgeneric/scnew.cpp) renders straight into the
// HAL framebuffer (EDisplayMemoryAddress) and uses RSurfaceUpdateSession only to
// TELL a compositor (the GCE Surface Update Server) which region changed. Our
// board has no compositor: the Syborg CLCD scans that same memory directly, so an
// update is "displayed" the moment it is drawn. This provider therefore accepts
// the session and completes every notification immediately with KErrNone --
// exactly what the real server reports after a composition cycle. Replace with
// the real client + server when a compositor (OpenWF-C) is brought up.
#include <e32std.h>
#include <graphics/surfaceupdateclient.h>

EXPORT_C RSurfaceUpdateSession::RSurfaceUpdateSession()
	: iStatusAvailable(NULL), iStatusDisplayed(NULL), iStatusDisplayedXTimes(NULL),
	  iTimeStamp(NULL), iCount(0)
	{
	}

EXPORT_C TInt RSurfaceUpdateSession::Connect(TInt /*aMessageSlots*/)
	{
	return KErrNone;
	}

EXPORT_C void RSurfaceUpdateSession::Close()
	{
	CancelAllUpdateNotifications();
	}

static void Complete(TRequestStatus*& aStatus, TInt aErr)
	{
	if (aStatus)
		{
		TRequestStatus* s = aStatus;
		aStatus = NULL;
		User::RequestComplete(s, aErr);
		}
	}

EXPORT_C TInt RSurfaceUpdateSession::SubmitUpdate(TInt /*aScreen*/, const TSurfaceId& /*aSurfaceId*/,
                                                  TInt /*aBuffer*/, const TRegion* /*aRegion*/)
	{
	// direct scanout: the pixels are already on the panel
	if (iTimeStamp)
		{
		(*iTimeStamp)() = User::FastCounter();
		iTimeStamp = NULL;
		}
	Complete(iStatusAvailable, KErrNone);
	Complete(iStatusDisplayed, KErrNone);
	Complete(iStatusDisplayedXTimes, KErrNone);
	return KErrNone;
	}

EXPORT_C void RSurfaceUpdateSession::NotifyWhenAvailable(TRequestStatus& aStatus)
	{
	aStatus = KRequestPending;
	iStatusAvailable = &aStatus;
	}

EXPORT_C void RSurfaceUpdateSession::NotifyWhenDisplayed(TRequestStatus& aStatus, TTimeStamp& aTimeStamp)
	{
	aStatus = KRequestPending;
	iStatusDisplayed = &aStatus;
	iTimeStamp = &aTimeStamp;
	}

EXPORT_C void RSurfaceUpdateSession::NotifyWhenDisplayedXTimes(TInt aCount, TRequestStatus& aStatus)
	{
	aStatus = KRequestPending;
	iStatusDisplayedXTimes = &aStatus;
	iCount = aCount;
	}

EXPORT_C void RSurfaceUpdateSession::CancelAllUpdateNotifications()
	{
	Complete(iStatusAvailable, KErrCancel);
	Complete(iStatusDisplayed, KErrCancel);
	Complete(iStatusDisplayedXTimes, KErrCancel);
	}
