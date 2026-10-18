// toolchain/overlay/e32cmn.inl -- include-path overlay in front of the real <e32cmn.inl>
// (e32cmn.h pulls it in with angle brackets, so the overlay directory wins).
//
// _LIT_SECURE_ID / _LIT_VENDOR_ID objects (SSecureId / SVendorId) convert to their T-class with
//     inline SSecureId::operator const TSecureId&() const { return (const TSecureId&)iId; }
// iId is a TUint32 and TSecureId has a TSecureId(TUint32) constructor, so by the language rules
// that cast builds a TEMPORARY TSecureId and returns a reference to it (the source even carries
// a `coverity[return_local_addr]` waiver). RVCT / GCC 3 handed back the still-intact stack slot;
// modern GCC deliberately replaces "address of a local returned" with NULL. Every
// `const TSecureId&` parameter fed from a _LIT_SECURE_ID is then a null reference: hwrmserver
// died in CHWRMPolicy::AddPolicyClientL(.., KAllPolicySid, ..) on its first load through it.
//
// Fix without touching the source: while the real .inl is read, the two class names are
// macro-renamed onto dummy twins (so the original inline definitions land on those, unused),
// then the real classes get the same four operators, returning a reference to the object
// itself -- exactly what their own operator&() already does.
#ifndef SYM_OVERLAY_E32CMN_INL
#define SYM_OVERLAY_E32CMN_INL

class SSecureId_SymOverlayTwin
	{
public:
	inline const TSecureId* operator&() const;
	inline operator const TSecureId&() const;
	inline operator TUint32() const;
	inline operator TUid() const;
public:
	TUint32 iId;
	};
class SVendorId_SymOverlayTwin
	{
public:
	inline const TVendorId* operator&() const;
	inline operator const TVendorId&() const;
	inline operator TUint32() const;
	inline operator TUid() const;
public:
	TUint32 iId;
	};

#define SSecureId SSecureId_SymOverlayTwin
#define SVendorId SVendorId_SymOverlayTwin
#include_next <e32cmn.inl>
#undef SSecureId
#undef SVendorId

inline const TSecureId* SSecureId::operator&() const
	{ return (const TSecureId*)this; }
inline SSecureId::operator const TSecureId&() const
	{ return *(const TSecureId*)this; }
inline SSecureId::operator TUint32() const
	{ return iId; }
inline SSecureId::operator TUid() const
	{ return (TUid&)iId; }

inline const TVendorId* SVendorId::operator&() const
	{ return (const TVendorId*)this; }
inline SVendorId::operator const TVendorId&() const
	{ return *(const TVendorId*)this; }
inline SVendorId::operator TUint32() const
	{ return iId; }
inline SVendorId::operator TUid() const
	{ return (TUid&)iId; }

#endif
