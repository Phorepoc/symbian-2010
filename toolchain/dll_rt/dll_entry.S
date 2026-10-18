@ dll_entry.s — the DLL entry point (_E32Dll), linked FIRST so it owns code
@ offset 0 (= the image entry point). Returns KErrNone for every attach/detach
@ reason. Keeping offset 0 NON-exported also dodges DEpocCodeSeg::Lookup's
@ "export == entry point => absent ordinal" rule (the EKDATA trap).
@ Layout matches the real uc_dll.cia contract that edllstub relies on:
@ Dll::Tls() reads the DLL's UID3 from *(_E32Dll + 12)  (up_dll_tls.cpp,
@ MODULE_HANDLE). build_mmp.py assembles this with -DDLL_UID3=<uid3>.
@
@ -DDLL_STATIC_CTORS (build_mmp.py, when the image has an .init_array): the real
@ uc_dll.cpp _E32Dll_Body contract -- on KModuleEntryReasonProcessAttach (2) walk
@ .init_array (mmp_dll.ld brackets it). Static dtors are not run (__aeabi_atexit
@ drops them; process teardown reclaims).
	.text
	.arm
	.globl _E32Dll
	.type _E32Dll,%function
_E32Dll:
#ifdef DLL_STATIC_CTORS
	b	1f
	nop
#else
	mov	r0, #0
	bx	lr
#endif
	.word	0
	.word	DLL_UID3
	.size _E32Dll, 16
#ifdef DLL_STATIC_CTORS
1:	cmp	r0, #2
	movne	r0, #0
	bxne	lr
	push	{r4, r5, r6, lr}
	ldr	r4, =__init_array_start
	ldr	r5, =__init_array_end
2:	cmp	r4, r5
	bhs	3f
	ldr	r6, [r4], #4
	blx	r6
	b	2b
3:	mov	r0, #0
	pop	{r4, r5, r6, pc}
	.ltorg
	@ the DSO handle g++ passes to __aeabi_atexit (dropped) -- only its address is taken
	.globl	__dso_handle
	.hidden	__dso_handle
__dso_handle:
	.word	0
#endif
