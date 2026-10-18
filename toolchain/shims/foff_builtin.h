/* foff_builtin.h -- OURS, force-included (-include) AFTER symbian_gcc_prefix.h by components
 * that use _FOFF in a CONSTANT expression (esock's meta-data attribute tables: static
 * initialisers, template arguments). The prefix header's classic pointer-arithmetic _FOFF is
 * not a constant expression for modern g++; __builtin_offsetof (what e32def.h itself picks
 * for __GNUC__>=4) is. Such components never use the runtime-index form the prefix caters for. */
#undef _FOFF
#define _FOFF(c,f) __builtin_offsetof(c,f)
