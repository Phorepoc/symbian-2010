/*
 * desc_compat.cpp — .cia mangling shim for the descriptor .cia files.
 *
 * cdes8/cdes16.cia branch to helpers under mangled names that don't match what
 * modern g++ emits, via CSM macros in e32cia.h. These thunks bridge the gap: a
 * symbol with the exact name the .cia expects that tail-calls the real function.
 * Pure glue, no behaviour of its own. Two cases:
 *
 * 1. Descriptor panics — the CSM path resolves to the OLD GCC (pre-Itanium)
 *    `..__Fv` spelling; des8/des16.cpp define them under the modern Itanium name.
 * 2. Mem::Compare/CompareF/CompareC (16-bit) — cdes16.cia references them via
 *    CSM_ZN3Mem..EPKtiS0_i, whose active block hardcodes the mangled name with
 *    the OLD substitution index `S0_` (`_ZN3Mem8CompareFEPKtiS0_i`). g++ 14
 *    mangles the identical signature with `S1_` (the 2nd `const TUint16*` reuses
 *    the pointer substitution). Those `S0_` strings are valid C identifiers, so
 *    we define them as extern-"C" forwarders to the real Mem:: functions. (des8
 *    never tripped this: its test reached only binary Compare, not CompareF/C.)
 */
#include <e32std.h>

// Real functions defined in common/des8.cpp (GLDEF_C => C++ linkage => _Z...).
void Des8PanicBadDesType();
void Des8PanicPosOutOfRange();
void Des8PanicLengthNegative();
void Des8PanicMaxLengthNegative();
void Des8PanicLengthOutOfRange();
void Des8PanicDesOverflow();
void Des8PanicDesIndexOutOfRange();

// Real functions defined in common/des16.cpp (same legacy-name story as des8;
// cdes16.cia reaches them via CSM_ macros AND some hardcoded __Fv spellings).
void Des16PanicBadDesType();
void Des16PanicPosOutOfRange();
void Des16PanicLengthNegative();
void Des16PanicMaxLengthNegative();
void Des16PanicLengthOutOfRange();
void Des16PanicDesOverflow();
void Des16PanicDesIndexOutOfRange();

extern "C" {
void Des8PanicBadDesType__Fv()        { Des8PanicBadDesType(); }
void Des8PanicPosOutOfRange__Fv()     { Des8PanicPosOutOfRange(); }
void Des8PanicLengthNegative__Fv()    { Des8PanicLengthNegative(); }
void Des8PanicMaxLengthNegative__Fv() { Des8PanicMaxLengthNegative(); }
void Des8PanicLengthOutOfRange__Fv()  { Des8PanicLengthOutOfRange(); }
void Des8PanicDesOverflow__Fv()       { Des8PanicDesOverflow(); }
void Des8PanicDesIndexOutOfRange__Fv(){ Des8PanicDesIndexOutOfRange(); }

void Des16PanicBadDesType__Fv()        { Des16PanicBadDesType(); }
void Des16PanicPosOutOfRange__Fv()     { Des16PanicPosOutOfRange(); }
void Des16PanicLengthNegative__Fv()    { Des16PanicLengthNegative(); }
void Des16PanicMaxLengthNegative__Fv() { Des16PanicMaxLengthNegative(); }
void Des16PanicLengthOutOfRange__Fv()  { Des16PanicLengthOutOfRange(); }
void Des16PanicDesOverflow__Fv()       { Des16PanicDesOverflow(); }
void Des16PanicDesIndexOutOfRange__Fv(){ Des16PanicDesIndexOutOfRange(); }

// cdes16.cia's stale-substitution (S0_) Mem:: names -> the real (S1_) functions.
TInt _ZN3Mem7CompareEPKtiS0_i(const TUint16* aL, TInt aLL, const TUint16* aR, TInt aRL)
	{ return Mem::Compare(aL, aLL, aR, aRL); }
TInt _ZN3Mem8CompareFEPKtiS0_i(const TUint16* aL, TInt aLL, const TUint16* aR, TInt aRL)
	{ return Mem::CompareF(aL, aLL, aR, aRL); }
TInt _ZN3Mem8CompareCEPKtiS0_i(const TUint16* aL, TInt aLL, const TUint16* aR, TInt aRL)
	{ return Mem::CompareC(aL, aLL, aR, aRL); }
}
