"""components -- the registry build_mmp.py builds from: Symbian's minigui configuration.

Key = the LIBRARY stem clients name in their .mmp (`LIBRARY estor.lib` -> "estor").
Fields:
    mmp     path of the REAL 2010 .mmp, relative to src/
    rom     file name in the ROM / import-block name (default: <key>.dll|.exe by type)
    def     override .def path (relative to src/) when the abld rule can't find it
    defs    extra -D macros          flags   extra g++ flags
    nosrc   True: ignore the mmp SOURCE list (component is extra-only, e.g. a null provider)
    extra   extra source files (repo-relative) -- our own null providers
    rtti    True: compile with -frtti
    heap_max    EXE heap max override (bytes) when the mmp's EPOCHEAPSIZE is too small
    foff_dynamic  True: _FOFF with a run-time index (estor) -- keep the classic _FOFF from the prefix header
    allow_wsd   True to accept writable static data in a DLL

BUILTIN: providers built by their own scripts (euser/efsrv/hal/efile).
"""
import os

import devlink

G = "oss.FCL.sf.os.graphics"
OSSRV = "oss.FCL.sf.os.ossrv"
PDATA = "oss.FCL.sf.os.persistentdata"
TXT = "oss.FCL.sf.os.textandloc"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUILTIN = {
    "euser": ("euser.dll", __import__("build_euser_dll").DEF_PATH),
    "efsrv": ("efsrv.dll", devlink.EFSRV_DEF),
    "hal": ("hal.dll", devlink.HAL_DEF),
    "efile": ("efile.exe", devlink.EFILE_DEF),
}

COMPONENTS = {
    # --- tier 1: euser-only leaves ---
    "palette": dict(mmp=f"{G}/graphicsdeviceinterface/colourpalette/group/palette.mmp"),
    "lookuptable": dict(mmp=f"{G}/graphicsdeviceinterface/gdi/group/lookuptable.mmp"),
    "linebreak": dict(mmp=f"{TXT}/fontservices/textbase/group/LineBreak.mmp"),
    # --- tier 2 ---
    "estor": dict(mmp=f"{PDATA}/persistentstorage/store/BMAKE/ESTOR.MMP", foff_dynamic=True),
    "bsul": dict(mmp=f"{OSSRV}/lowlevellibsandfws/apputils/bsul/group/bsul.mmp"),
    "centralrepository": dict(mmp=f"{PDATA}/persistentstorage/centralrepository/group/cenrepcli.mmp"),
    # --- tier 3 ---
    "bafl": dict(mmp=f"{OSSRV}/lowlevellibsandfws/apputils/group/BAFL.MMP"),
    "ecompatchdata": dict(mmp=f"{OSSRV}/lowlevellibsandfws/pluginfw/Framework/MMPFiles/EComPatchData.mmp"),
    "ecom": dict(mmp=f"{OSSRV}/lowlevellibsandfws/pluginfw/Framework/MMPFiles/ECom.mmp"),
    "ecomserver": dict(mmp=f"{OSSRV}/lowlevellibsandfws/pluginfw/Framework/MMPFiles/EComServer.mmp"),
    # --- graphics ---
    "gdi": dict(mmp=f"{G}/graphicsdeviceinterface/gdi/group/GDI.MMP"),
    "fntstr": dict(mmp=f"{TXT}/fontservices/fontstore/group/FNTSTORE.MMP"),
    "fbscli": dict(mmp=f"{G}/fbs/fontandbitmapserver/group/FBSCLI.MMP", rtti=True),
    "fbserv": dict(mmp=f"{G}/fbs/fontandbitmapserver/group/FBSERV.MMP"),
    "scdv": dict(mmp=f"{G}/graphicsdeviceinterface/screendriver/group/generic.mmp", rom="scdv.dll"),
    "bitgdi": dict(mmp=f"{G}/graphicsdeviceinterface/bitgdi/group/BITGDI.MMP", rom="bitgdi.dll"),
    # no GPU resource provider and no compositor on this board: null providers with the REAL .def ordinal tables
    "sgresource": dict(mmp=f"{G}/graphicsresourceservices/graphicsresourceimplementation/group/sgresource_sw.mmp",
                       rom="sgresource.dll", nosrc=True, extra=["toolchain/shims/sgresource_null.cpp"],
                       **{"def": f"{G}/graphicsresourceservices/graphicsresourceinterface/eabi/sgresourceu.def"}),
    "surfaceupdateclient": dict(mmp=f"{G}/graphicscomposition/surfaceupdate/group/surfaceupdateclient.mmp",
                                nosrc=True, extra=["toolchain/shims/surfaceupdateclient_null.cpp"],
                                **{"def": f"{G}/graphicscomposition/surfaceupdate/eabi/surfaceupdateclientU.def"}),
    # the bitmap font rasterizer plug-in: FBS's own stub (no vector stack in this configuration)
    "fbsrasterizer": dict(mmp=f"{G}/fbs/fontandbitmapserver/group/stubrasterizer.mmp", rom="fbsrasterizer.dll",
                          **{"def": f"{G}/fbs/fontandbitmapserver/eabi/fbsrasterizeru.def"}),
    # --- window server (non-NGA) ---
    "ektran": dict(mmp="oss.FCL.sf.os.kernelhwsrv/kernel/eka/ewsrv/ektran.mmp"),
    "ws32": dict(mmp=f"{G}/windowing/windowserver/group/w32_nonnga.mmp", rom="ws32.dll", rtti=True),
    "wsgraphicdrawer": dict(mmp=f"{G}/windowing/windowserver/group/wsgraphicdrawer_nonnga.mmp",
                            rom="wsgraphicdrawer.dll"),
    "wserv": dict(mmp=f"{G}/windowing/windowserver/group/openwfc/wserv_nonnga.mmp", rom="wserv.exe",
                  heap_max=16777216),
    "wsstdplugin": dict(mmp=f"{G}/windowing/windowserver/group/stdplugin_nonnga.mmp", rom="2001b70b.dll"),
    "domaincli": dict(mmp="oss.FCL.sf.os.kernelhwsrv/userlibandfileserver/domainmgr/group/domaincli.mmp"),
    "domainsrv": dict(mmp="oss.FCL.sf.os.kernelhwsrv/userlibandfileserver/domainmgr/group/domainsrv.mmp"),
    "econswserv": dict(mmp=f"{G}/windowing/windowserver/minigui/group/econswserv.mmp"),
    # --- the 3-DLL locale (SYMBIAN_DISTINCT_LOCALE_MODEL) ---
    "elocl_language_en_gb": dict(
        mmp="oss.FCL.sf.mw.uiresources/locales/loce32/language/group/elocl_language_en_gb.mmp", rom="elocl_lan.001"),
    "elocl_region_gb": dict(
        mmp="oss.FCL.sf.mw.uiresources/locales/loce32/region/group/elocl_region_gb.mmp", rom="elocl_reg.826",
        flags=["-finput-charset=ISO-8859-1"]),
    "elocl_collation_en": dict(
        mmp="oss.FCL.sf.mw.uiresources/locales/loce32/collation/group/elocl_collation_en.mmp", rom="elocl_col.001"),
}


def rom_name(key: str, targettype: str = "dll") -> str:
    c = COMPONENTS[key]
    if "rom" in c:
        return c["rom"]
    return key + (".exe" if targettype in ("exe", "exexp", "stdexe") else ".dll")
