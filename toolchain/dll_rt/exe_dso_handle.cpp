// __dso_handle for EXEs: GCC registers a global object's destructor with
// __aeabi_atexit(obj, dtor, &__dso_handle); DLLs get the symbol from dll_entry.S, EXEs had none. Process exit reclaims everything, so the handle is only an address.
extern "C" { __attribute__((visibility("hidden"))) void* __dso_handle = 0; }
