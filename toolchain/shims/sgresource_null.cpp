// sgresource_null.cpp — OURS (not Symbian source): a null Graphics Resource
// provider with the REAL sgresource.def ordinal table.
//
// The 2010 sgresource_sw.dll keeps process-local state in writable static data
// (gPls), which our XIP DLL pipeline doesn't carry yet, and it fronts the
// sgdevice LDD we don't build. Its only client on the text/minigui path is
// fbserv's hardware glyph atlas, which is OPTIONAL by design:
//     TRAP_IGNORE(iGlyphAtlas = CGlyphAtlas::NewL(...))   (FBSTOP.CPP)
// so RSgDriver::Open() returning KErrNotSupported is a legitimate "no GPU
// resource support on this board" answer; every other entry fails the same
// honest way. Replace with the real TU set once DLL WSD + sgdevice exist.
#include <e32std.h>
#include <sgresource/sgresource.h>
#include <sgresource/sgimage.h>

EXPORT_C TInt RSgDriver::Open()                      { return KErrNotSupported; }
EXPORT_C void RSgDriver::Close()                     {}
EXPORT_C TInt RSgDriver::GetInterface(TUid, TAny*& aPtr) const { aPtr = NULL; return KErrExtensionNotSupported; }
EXPORT_C TVersion RSgDriver::Version()               { return TVersion(1, 1, 1); }

EXPORT_C TInt RSgDrawable::Open(TSgDrawableId, const TSgAttributeArrayBase*) { return KErrNotSupported; }
EXPORT_C void RSgDrawable::Close()                   {}
EXPORT_C TSgDrawableId RSgDrawable::Id() const       { return KSgNullDrawableId; }
EXPORT_C TUid RSgDrawable::DrawableType() const      { return KNullUid; }
EXPORT_C TInt RSgDrawable::GetInterface(TUid, TAny*& aPtr) const { aPtr = NULL; return KErrExtensionNotSupported; }

EXPORT_C TInt RSgImage::Create(const TSgImageInfo&, const TAny*, TInt, const TSgAttributeArrayBase*) { return KErrNotSupported; }
EXPORT_C TInt RSgImage::Create(const TSgImageInfo&, const RSgImage&, const TSgAttributeArrayBase*)   { return KErrNotSupported; }
EXPORT_C TInt RSgImage::GetInfo(TSgImageInfo&) const { return KErrBadHandle; }
EXPORT_C TInt RSgImage::GetAttribute(TUid, TInt&) const { return KErrBadHandle; }
EXPORT_C TInt RSgImage::GetPixelFormats(TUint32, RArray<TInt>&, const TSgAttributeArrayBase*) { return KErrNotSupported; }
