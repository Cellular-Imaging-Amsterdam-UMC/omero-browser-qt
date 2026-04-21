# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.5] — 2025-01-XX

### Added

- 3D volume viewer (MIP, Attenuated MIP, Translucent, Average, Isosurface, Additive, context-aware MinIP) via vispy — optional `viewer` extra
- Play/pause animation with speed control
- OMERO viewer rendering path for interactive image viewing
- Rendering metadata in `SelectedImageContext`
- `SelectedImageContext` for structured selection with breadcrumbs
- `compute_scale_bar()` helper for image viewers
- `get_image_display_settings()` for normalized channel display metadata
- Documentation site (MkDocs + Material)
- Optional short-lived OMERO session reuse via `Remember me for 10 minutes`
- Smooth/nearest interpolation toggle for supported 3D render modes

### Changed

- Embedded 3D viewer in main window via `QStackedWidget` (was separate window)
- Gain/Threshold slider is now context-aware per render mode
- 3D refresh timing was tightened so gain/contrast updates feel more responsive
- 3D camera uses free Arcball rotation

## [0.1.4] — 2024-XX-XX

### Added

- Initial public release
- `OmeroBrowserDialog` with QuPath-style layout
- `OmeroGateway` singleton wrapper around `BlitzGateway`
- `LoginDialog` with server-name history
- ICE-based pixel loading (`load_image_data`, `load_image_lazy`)
- `RegularImagePlaneProvider` and `PyramidTileProvider`
- `OmeroTreeModel` for lazy-loading OMERO hierarchy
