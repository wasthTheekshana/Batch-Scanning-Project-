# PyInstaller build specification (HLD §21.1).
# Build:  pyinstaller docscanner.spec
# Bundle tesseract.exe, tessdata/ and libzbar-64.dll next to this spec
# before building the release EXE.
import glob
import os

block_cipher = None

datas = []
binaries = []
if os.path.isdir("tessdata"):
    datas.append(("tessdata", "tessdata"))
if os.path.isfile("tesseract.exe"):
    binaries.append(("tesseract.exe", "."))
    # tesseract.exe is an xcopy-deployed binary — it needs every shared
    # library from its install dir alongside it, not just the exe itself.
    for dll in glob.glob("*.dll"):
        binaries.append((dll, "."))
elif os.path.isfile("libzbar-64.dll"):
    binaries.append(("libzbar-64.dll", "."))
    if os.path.isfile("libiconv.dll"):
        binaries.append(("libiconv.dll", "."))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pytesseract", "pyzbar.pyzbar", "imagehash"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="DOKScanner",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico" if os.path.isfile("assets/icon.ico") else None,
)
