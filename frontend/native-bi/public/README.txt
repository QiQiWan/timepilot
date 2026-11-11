TimePilot native Perspective 5.6.1 distribution folder.
SOURCE RELEASE ONLY: JS/CSS/WASM build output not shipped without a real npm build.
On a Python 3.12.12 workstation, run:
  python scripts/build_native_bi.py
  python scripts/verify_native_bi_release.py --require-build --require-virtual-server
The build script downloads pinned JS dependencies at development/build time,
not when an end user opens the app. It writes perspective-build.json with
SHA256 integrity entries. The application disables native controls if missing.
Never insert placeholder WASM binaries to bypass the release gate.
