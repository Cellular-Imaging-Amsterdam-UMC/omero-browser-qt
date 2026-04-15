# Contributing

Contributions are welcome! Here's how to get started.

## Development setup

```bash
# Clone the repository
git clone https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt.git
cd omero-browser-qt

# Install ZeroC ICE + omero-py (see Getting Started)
# Then install in editable mode with all extras
pip install -e ".[viewer3d,docs]"
```

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/)
- Use [numpy-style docstrings](https://numpydoc.readthedocs.io/en/latest/format.html) for all public APIs
- Type annotations for function signatures

## Building the documentation

```bash
# Live preview
mkdocs serve

# Production build
mkdocs build --strict
```

## Submitting changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Make your changes
4. Update the [Changelog](changelog.md) under an **Unreleased** section
5. Open a pull request against `main`

## Reporting issues

Please open an issue on [GitHub](https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt/issues)
with:

- Steps to reproduce
- Expected vs. actual behaviour
- Python version, OS, and `omero-browser-qt` version

## License

By contributing you agree that your contributions will be licensed under
the [MIT License](https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt/blob/main/LICENSE).
