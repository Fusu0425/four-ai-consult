# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_submodules


project_root = os.path.dirname(SPECPATH)
sys.path.insert(0, project_root)
from four_ai_consult import __version__

license_directory = os.environ["FOUR_AI_LICENSE_DIRECTORY"]
hook_directory = os.path.join(project_root, "packaging", "hooks")
resources_directory = os.path.join(project_root, "resources")

a = Analysis(
    [os.path.join(project_root, "launcher.pyw")],
    pathex=[project_root],
    binaries=[],
    datas=[
        (resources_directory, "resources"),
        (os.path.join(project_root, "LICENSE"), "."),
        (os.path.join(project_root, "THIRD_PARTY_NOTICES.md"), "."),
        (os.path.join(project_root, "PRIVACY.md"), "."),
        (os.path.join(project_root, "SECURITY.md"), "."),
        (license_directory, "licenses"),
    ],
    hiddenimports=collect_submodules("keyring.backends"),
    hookspath=[hook_directory],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# The Codex desktop host adds Poppler to its DLL search path. PyInstaller can
# resolve Qt's optional ICU imports against that unrelated runtime; those DLLs
# are incompatible with the Qt bundle and must never ship with FourAIConsult.
blocked_binaries = {"icuuc.dll"}
a.binaries = [
    entry
    for entry in a.binaries
    if os.path.basename(entry[0]).lower() not in blocked_binaries
    and not os.path.basename(entry[0]).lower().startswith("icudt")
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"FourAIConsult-{__version__}-onefile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(resources_directory, "four-ai-consult.ico"),
)
