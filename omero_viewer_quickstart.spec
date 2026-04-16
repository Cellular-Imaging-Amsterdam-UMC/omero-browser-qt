# omero_viewer_quickstart.spec — PyInstaller one-FOLDER build spec
# Build with:  pyinstaller omero_viewer_quickstart.spec
#              (run from the repository root)
#
# Produces:  dist/OmeroViewer/OmeroViewer.exe  (folder distribution)
#            Run OmeroViewer.exe from inside dist/OmeroViewer/ — it needs
#            the _internal/ sibling folder next to it.
#            For release: zip the dist/OmeroViewer/ folder itself.
#
# Why use this instead of omero_viewer.spec?
#   • Folder builds skip the single-file extraction step → app starts instantly.
#   • Incremental rebuilds are much faster (only changed files are re-written).
#   • Ideal for quick iteration / testing during development.
#   • Ship the whole dist/OmeroViewer/ folder (e.g. zip it) for distribution.

import os
import pkgutil
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all PyQt6, vispy, and OpenGL submodules, data files, and binaries
pyqt6_datas,  pyqt6_binaries,  pyqt6_hiddenimports  = collect_all('PyQt6')
vispy_datas,  vispy_binaries,  vispy_hiddenimports   = collect_all('vispy')
ogl_datas,    ogl_binaries,    ogl_hiddenimports     = collect_all('OpenGL')

# Collect the full OMERO + Ice ecosystem
# omero has hundreds of auto-generated Ice stubs (omero_model_*, Glacier2_*, etc.)
omero_datas, omero_binaries, omero_hiddenimports     = collect_all('omero')
ice_toplevel = [
    name for _, name, _ in pkgutil.iter_modules()
    if name.startswith(('omero_', 'Glacier2', 'IcePatch2', 'IceBox', 'IceGrid', 'IceStorm'))
]

a = Analysis(
    ['src/omero_browser_qt/omero_viewer.py'],
    pathex=['.', 'src'],
    binaries=pyqt6_binaries + vispy_binaries + ogl_binaries + omero_binaries,
    datas=[
        ('src/omero_browser_qt/icons', 'omero_browser_qt/icons'),
    ] + pyqt6_datas + vispy_datas + ogl_datas + omero_datas,
    hiddenimports=[
        'numpy',
        'dask',
        'dask.array',
        'omero',
        'omero.gateway',
        'omero.util',
        'omero.util.sessions',
        'omero.rtypes',
        'omero.model',
        'omero.api',
        'omero.sys',
        'omero.clients',
        'omero.cmd',
        'omero.cmd.graphs',
        'Ice',
        'IcePy',
        'Glacier2',
        'vispy',
        'vispy.scene',
        'vispy.color',
        'vispy.visuals',
        'vispy.visuals.volume',
        'vispy.visuals.transforms',
        'vispy.app.backends._pyqt6',
        'OpenGL',
        'OpenGL.GL',
        'OpenGL.platform.win32',
        'omero_browser_qt',
        'omero_browser_qt.browser_dialog',
        'omero_browser_qt.gateway',
        'omero_browser_qt.image_loader',
        'omero_browser_qt.login_dialog',
        'omero_browser_qt.rendering',
        'omero_browser_qt.scale_bar',
        'omero_browser_qt.selection_context',
        'omero_browser_qt.tree_model',
        'omero_browser_qt.view_backends',
    ] + pyqt6_hiddenimports + vispy_hiddenimports + ogl_hiddenimports
      + omero_hiddenimports + ice_toplevel,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'torch', 'torchvision', 'torchaudio',
        'scipy', 'sklearn', 'IPython', 'matplotlib',
        'pandas', 'pandas.tests',
        'pytest',
        'tkinter', '_tkinter',
        'notebook', 'nbformat', 'jupyter',
        'zmq', 'jedi', 'parso',
        'mkdocs', 'mkdocstrings',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],                         # no binaries/datas embedded — goes into COLLECT
    name='OmeroViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # skip UPX compression for faster builds
    console=True,               # keep console visible for error diagnostics
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='OmeroViewer',         # → dist/OmeroViewer/
)

# ── Post-build: remove the bare bootloader exe that PyInstaller writes to
#    dist/ as a side-effect of the EXE step.  Only the folder in
#    dist/OmeroViewer/ is a working distribution.
import os as _os
_stale = _os.path.join(DISTPATH, 'OmeroViewer.exe')
if _os.path.isfile(_stale):
    _os.remove(_stale)
    print(f'Removed stale bootloader: {_stale}')
