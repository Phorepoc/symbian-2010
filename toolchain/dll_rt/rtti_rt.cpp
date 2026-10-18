// rtti_rt.cpp -- the two libc symbols libsupc++'s RTTI support (type_info name
// comparison in __dynamic_cast) needs. Linked only into RTTI-enabled images
// (emitter/build_mmp.py `rtti`). No libc on Symbian: keep it freestanding.
extern "C" int strcmp(const char* a, const char* b)
	{
	while (*a && *a == *b)
		{
		++a;
		++b;
		}
	return (unsigned char)*a - (unsigned char)*b;
	}

// A failed dynamic_cast to a REFERENCE calls __cxa_bad_cast (typeid of a null pointer: __cxa_bad_typeid).
// libsupc++'s versions THROW std::bad_cast, which drags the whole exception runtime in -- and its thread-local
// exception globals, which an E32Image cannot carry. These images build without C++ exceptions, so an uncaught
// throw is all it could be: panic instead (euser's User::Invariant, USER 0). Defined here, the archive members are never pulled.
class User { public: static void Invariant(); };		// euser.dll, _ZN4User9InvariantEv
extern "C" void __cxa_bad_cast() { User::Invariant(); for (;;) {} }
extern "C" void __cxa_bad_typeid() { User::Invariant(); for (;;) {} }
