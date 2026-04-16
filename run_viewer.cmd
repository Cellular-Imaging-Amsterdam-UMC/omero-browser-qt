@echo off
setlocal
set PYTHONPATH=%~dp0src

if defined CONDA_EXE (
    "%CONDA_EXE%" run -n deconvolve python -m omero_browser_qt.omero_viewer %*
) else (
    conda run -n deconvolve python -m omero_browser_qt.omero_viewer %*
)
