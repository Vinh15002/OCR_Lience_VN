# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OCR_Plate desktop app (onedir build).

Build with:  pyinstaller packaging/OCR_Plate.spec --noconfirm
or, preferably, via packaging/build_exe.ps1 which also stages the runtime files
(config.json, detector weights, sample videos) next to the .exe.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

PROJECT_ROOT = Path(SPECPATH).parent
# PaddleX gates its predictors on `importlib.metadata.version(<dist>)`, so these
# need their *distribution metadata* in the bundle, not just their modules —
# without it text recognition dies with a DependencyError at predict time.
PADDLEX_RUNTIME_DEPS = {
    "python-bidi": "bidi",
    "opencv-contrib-python": "cv2",
    "imagesize": "imagesize",
    "pyclipper": "pyclipper",
    "shapely": "shapely",
    "pypdfium2": "pypdfium2",
    "paddlepaddle": None,  # checked via find_spec, but keep the metadata anyway
    "paddleocr": None,
}
# Recognition models the UI can switch between; the detector .pt stays outside
# the bundle because config.json points at it by (editable) path.
BUNDLED_OCR_MODELS = ["PP-OCRv6_medium_rec", "PP-OCRv6_tiny_rec"]

datas = []
binaries = []
hiddenimports = ["plate_app", "plate_app.ui"]

# torch is covered by PyInstaller's own hook; these need explicit collection
# because they load data files and submodules dynamically.
for package in ("paddle", "paddlex", "paddleocr", "ultralytics", "qrcode", "reportlab", "serial"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for dist_name, module_name in PADDLEX_RUNTIME_DEPS.items():
    try:
        datas += copy_metadata(dist_name)
    except Exception as exc:  # noqa: BLE001 - a missing dist is worth a warning only
        print(f"[spec] WARNING: no metadata for {dist_name}: {exc}")
    if module_name:
        module_datas, module_binaries, module_hiddenimports = collect_all(module_name)
        datas += module_datas
        binaries += module_binaries
        hiddenimports += module_hiddenimports

paddlex_cache = Path.home() / ".paddlex" / "official_models"
for model_name in BUNDLED_OCR_MODELS:
    model_dir = paddlex_cache / model_name
    if model_dir.is_dir():
        datas.append((str(model_dir), f"paddlex_models/{model_name}"))
    else:
        print(f"[spec] WARNING: OCR model not found in cache, skipping: {model_dir}")

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "tensorflow",
        "paddle.distributed.fleet",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OCR_Plate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OCR_Plate",
)
