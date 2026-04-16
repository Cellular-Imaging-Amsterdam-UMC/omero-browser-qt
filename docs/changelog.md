# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.5] — 2025-01-XX

### Added

- 3D volume viewer (MIP, Translucent, Isosurface, Additive) via vispy — optional `viewer` extra
- Play/pause animation with speed control
- OMERO viewer rendering path for interactive image viewing
- Rendering metadata in `SelectedImageContext`
- `SelectedImageContext` for structured selection with breadcrumbs
- `compute_scale_bar()` helper for image viewers
- `get_image_display_settings()` for normalized channel display metadata
- Documentation site (MkDocs + Material)

### Changed

- Embedded 3D viewer in main window via `QStackedWidget` (was separate window)
- Gain/Threshold slider is now context-aware per render mode
- Lo/Hi/Threshold changes are debounced (3 s) to avoid excessive re-renders

## [0.1.4] — 2024-XX-XX

### Added

- Initial public release
- `OmeroBrowserDialog` with QuPath-style layout
- `OmeroGateway` singleton wrapper around `BlitzGateway`
- `LoginDialog` with server-name history
- ICE-based pixel loading (`load_image_data`, `load_image_lazy`)
- `RegularImagePlaneProvider` and `PyramidTileProvider`
- `OmeroTreeModel` for lazy-loading OMERO hierarchy
