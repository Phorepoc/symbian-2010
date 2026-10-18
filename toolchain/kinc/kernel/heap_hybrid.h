/* kinc forwarder — the SBS build exports eka/include/heap_hybrid.h to the relocated
 * path epoc32/include/kernel/heap_hybrid.h, so kernel sources include it as
 * <kernel/heap_hybrid.h>. We have no epoc32/include tree, so forward to the real
 * source header by a path relative to this file (portable, no absolute paths).
 * See toolchain/symcc (-k adds -I toolchain/kinc). */
#include "../../../src/oss.FCL.sf.os.kernelhwsrv/kernel/eka/include/heap_hybrid.h"
