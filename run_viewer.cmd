@echo off
setlocal
set PYTHONPATH=%~dp0src

if defined CONDA_EXE (
    "%CONDA_EXE%" run -n deconvolve python "%~dp0examples\viewer_demo.py" %*
) else (
    conda run -n deconvolve python "%~dp0examples\viewer_demo.py" %*
)
