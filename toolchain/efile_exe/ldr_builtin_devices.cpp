// ldr_builtin_devices.cpp -- the loader's answer for a device driver that is BUILT INTO the kernel. In a ROM every
// LDD/PDD is a file in Z:\sys\bin, so User::LoadLogicalDevice("display0.ldd") returns KErrNone (first load) or
// KErrAlreadyExists -- and that is all its callers accept. Our kernel links its drivers into ekern (the display
// LDD, the media drivers ...), so there is no file and the real loader returns KErrNotFound. Linked with
// --wrap=_ZN8E32Image11LoadCodeSegERK7RLdrReq (emitter/build_efile_exe.py): for a device-driver load that found no
// file, a logical (physical) device already installed under the file's root name (or its alias below) means
// "already loaded" = KErrAlreadyExists. Everything else is unchanged.
#include "sf_std.h"
#include "sf_image.h"

extern "C" TInt __real__ZN8E32Image11LoadCodeSegERK7RLdrReq(E32Image* aThis, const RLdrReq& aReq);

static TBool DeviceInstalled(const TDesC8& aFileName, TBool aPhysical)
	{
	// the root name: drive/path and extension off ("z:\sys\bin\display0.ldd" -> "display0")
	TPtrC8 name(aFileName);
	const TInt slash = name.LocateReverse('\\');
	if (slash >= 0)
		name.Set(name.Mid(slash + 1));
	const TInt dot = name.Locate('.');
	if (dot >= 0)
		name.Set(name.Left(dot));
	if (name.Length() == 0 || name.Length() > KMaxKernelName - 2)
		return EFalse;
	// a driver file whose factory registers under another name (the file -> the device it installs)
	static const struct { const char* iFile; const char* iDevice; } KAliases[] =
		{
		{ "display0", "displaychannel" },		// display.cpp: DDisplayLddFactory::Install -> RDisplayChannel::Name()
		};
	for (TUint i = 0; i < sizeof(KAliases) / sizeof(KAliases[0]); ++i)
		if (name.CompareF(TPtrC8((const TUint8*)KAliases[i].iFile)) == 0)
			{
			name.Set((const TUint8*)KAliases[i].iDevice, User::StringLength((const TUint8*)KAliases[i].iDevice));
			break;
			}
	TBuf<KMaxKernelName> pattern;
	pattern.Copy(name);
	TFullName found;
	if (aPhysical)
		{
		pattern.Append(_L(".*"));		// a PDD is named "<ldd name>.<variant>"
		TFindPhysicalDevice f(pattern);
		return f.Next(found) == KErrNone;
		}
	TFindLogicalDevice f(pattern);
	return f.Next(found) == KErrNone;
	}

extern "C" TInt __wrap__ZN8E32Image11LoadCodeSegERK7RLdrReq(E32Image* aThis, const RLdrReq& aReq)
	{
	TInt r = __real__ZN8E32Image11LoadCodeSegERK7RLdrReq(aThis, aReq);
	if (r != KErrNotFound || !aReq.iFileName)
		return r;
	const TUint32 type = aReq.iRequestedUids[1].iUid;
	if (type != KLogicalDeviceDriverUidValue && type != KPhysicalDeviceDriverUidValue)
		return r;
	if (DeviceInstalled(*aReq.iFileName, type == KPhysicalDeviceDriverUidValue))
		return KErrAlreadyExists;
	return r;
	}
