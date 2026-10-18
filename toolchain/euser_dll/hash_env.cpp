/*
 * hash_env.cpp — the two util leaves the real hash table (us_htab.cpp) reaches
 * that live ONLY in euser/epoc/arm/uc_utl.cia.
 *
 * us_htab.cpp defines DefaultHash::IntegerPtr/Des* and DefaultIdentity::Integer/*
 * itself, but the scalar DefaultHash::Integer and Mem::Swap are hand-written ARM
 * in uc_utl.cia — a heterogeneous grab-bag (TRegion, CObjectIx, CServer2 dispatch,
 * RFastLock, …) that can't be pulled in whole without dragging half of euser, and
 * whose internal cross-function branches also defeat -ffunction-sections GC. So we
 * supply just these two, with the exact algorithm the .cia/.cpp implement:
 *   - DefaultHash::Integer — Knuth multiplicative hash, key * 2^32/phi (0x9E3779B9),
 *     the low 32 bits (uc_utl.cpp: `mov eax,9E3779B9h / mul [key]`).
 *   - Mem::Swap — exchange aLength bytes between two buffers.
 * Same pattern as unicode_env.cpp's verbatim adapters. DefaultIdentity::Integer
 * (the matching equality relation) is the REAL one from us_htab.cpp.
 */
#include <e32std.h>
#include <e32hashtab.h>

EXPORT_C TUint32 DefaultHash::Integer(const TInt& aInt)
	{
	return (TUint32)aInt * 0x9E3779B9u;   // Knuth golden-ratio hash (low 32 bits)
	}

EXPORT_C void Mem::Swap(TAny* aPtr1, TAny* aPtr2, TInt aLength)
	{
	TUint8* p1 = (TUint8*)aPtr1;
	TUint8* p2 = (TUint8*)aPtr2;
	while (aLength-- > 0)
		{
		TUint8 t = *p1;
		*p1++ = *p2;
		*p2++ = t;
		}
	}
