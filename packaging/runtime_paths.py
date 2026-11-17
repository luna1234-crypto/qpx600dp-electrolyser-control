"""Keep writable packaged outputs beside the one-folder executable."""

from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    from qpx_electrolyser.app import paths

    packaged_root = Path(sys.executable).resolve().parent

    def packaged_default_paths() -> paths.ApplicationPaths:
        return paths.ApplicationPaths(project_root=packaged_root)

    paths.default_paths = packaged_default_paths
    (packaged_root / "Data").mkdir(exist_ok=True)
    (packaged_root / "logs").mkdir(exist_ok=True)
