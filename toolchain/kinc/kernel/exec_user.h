/* kinc forwarder — genexec.pl generates exec_user.h into toolchain/generated/, but kernel
 * sources include it as <kernel/exec_user.h>. Forward to the generated header. */
#include "../../generated/exec_user.h"
