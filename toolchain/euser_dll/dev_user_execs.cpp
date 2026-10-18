/*
 * dev_user_execs.cpp -- the euser exec layer: the generated user-side exec stub BODIES for the from-source kernel.
 * u32exec.h unconditionally pulls <exec_enum.h> + <exec_user.h>; exec_user.h's 281 stub bodies are guarded by
 * __GEN_USER_EXEC_CODE__, so this one TU (compiled with that macro) defines the whole user-side executive:
 * Exec::* (SLOW_EXEC/FAST_EXEC = `svc <n>`), E32Loader::* (all 27 -- the file-server loader boundary:
 * CodeSegCreate/ProcessCreate/CodeSegOpen/... -> ExecHandler::* in the kernel's scodeseg.cpp), and UserSvr::*
 * (RomHeaderAddress/ExecuteInSupervisorMode/FsRegisterThread/...).
 *
 * Build: SYMCC_ARM=1 bash toolchain/symcc toolchain/euser_dll/dev_user_execs.cpp -O2 -D__GEN_USER_EXEC_CODE__ -I toolchain/kinc -I toolchain/generated -o dev_user_execs.o
 */
#include <e32std.h>
#include <e32base.h>
#include <e32svr.h>
#include <e32ldr.h>
#include <e32ldr_private.h>
#include <u32exec.h>   // -> exec_enum.h + exec_user.h; bodies emit under the macro
