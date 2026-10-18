/* kinc forwarder — HAL sources include <kernel/hal_int.h>, but in the 2010 tree
 * the header lives at halservices/hal/inc/hal_int.h (the SBS build exports it to
 * epoc32/include/kernel/). Forward to the real header so the user-side HAL client
 * (hal_main.cpp: HAL::Get, which EFILE.EXE imports) compiles from source. */
#include "../../../src/oss.FCL.sf.os.kernelhwsrv/halservices/hal/inc/hal_int.h"
