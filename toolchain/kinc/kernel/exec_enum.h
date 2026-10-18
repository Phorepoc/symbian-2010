/* kinc forwarder — genexec.pl generates exec_enum.h into toolchain/generated/, but kernel
 * sources include it as <kernel/exec_enum.h>. Forward to the generated header. */
#include "../../generated/exec_enum.h"
