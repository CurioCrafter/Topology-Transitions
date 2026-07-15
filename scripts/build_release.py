"""Build and inspect the installable Blender extension ZIP."""

from __future__ import annotations

import hashlib
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "topology_transitions"
MANIFEST = PACKAGE / "blender_manifest.toml"
DIST = ROOT / "dist"


def source_files() -> list[Path]:
    files = []
    for path in PACKAGE.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return sorted(files)


def build() -> Path:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest["version"]
    extension_id = manifest["id"]
    if extension_id != "topology_transitions":
        raise RuntimeError(f"Unexpected extension id: {extension_id}")

    DIST.mkdir(exist_ok=True)
    archive = DIST / f"topology-transitions-{version}.zip"
    if archive.exists():
        archive.unlink()
    files = source_files()
    if MANIFEST not in files or PACKAGE / "__init__.py" not in files:
        raise RuntimeError("Package source or Blender manifest is missing")

    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as output:
        for path in files:
            output.write(path, path.relative_to(PACKAGE))

    with zipfile.ZipFile(archive) as built:
        names = built.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Archive contains duplicate entries")
        forbidden = [
            name
            for name in names
            if "__pycache__" in name
            or name.endswith((".pyc", ".pyo"))
            or name.startswith(".tools/")
        ]
        if forbidden:
            raise RuntimeError(f"Archive contains forbidden files: {forbidden}")
        required = {
            "__init__.py",
            "blender_manifest.toml",
            "LICENSE",
        }
        if not required.issubset(names):
            raise RuntimeError(f"Archive is missing: {sorted(required - set(names))}")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"QT_PACKAGE_PASS path={archive} entries={len(files)} sha256={digest}")
    return archive


if __name__ == "__main__":
    try:
        build()
    except Exception as exc:
        print(f"QT_PACKAGE_FAIL {exc}", file=sys.stderr)
        raise
