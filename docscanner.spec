# PyInstaller build specification (HLD §21.1).
# Build:  pyinstaller docscanner.spec
# Bundle tesseract.exe, tessdata/ and libzbar-64.dll next to this spec
# before building the release EXE.
import glob
import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    # module-adjacent JSON loaded via Path(__file__).parent at import time -
    # PyInstaller only auto-detects Python imports, not data files.
    ("core/project_schema.json", "core"),
    ("separation/rule_schema.json", "separation"),
    ("core/templates", "core/templates"),
    ("ocr/digit_model.npz", "ocr"),     # handwritten-digit model (ocr/digits.py)
    ("ocr/digit_model28.npz", "ocr"),   # the MNIST-trained one it prefers
    ("ocr/digit_cnn.onnx", "ocr"),      # the small convolutional digit network it prefers over both
    ("ocr/branches.txt", "ocr"),        # branch names the handwriting reader chooses from
    ("ocr/names.txt", "ocr"),           # common names it snaps near-misses to
]
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
    # reportlab loads its barcode symbologies by name at run time, which
    # PyInstaller cannot see: without these, "Print cover sheets" fails with
    # "No module named reportlab.graphics.barcode.code93" in the built exe.
    hiddenimports=["onnxruntime", "tokenizers", "pytesseract", "pyzbar.pyzbar", "imagehash", "qrcode",
                  "qrcode.image.pil", "pythoncom", "pywintypes",
                  "win32gui", "win32con", "twain"]
                 + collect_submodules("reportlab.graphics.barcode"),
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
