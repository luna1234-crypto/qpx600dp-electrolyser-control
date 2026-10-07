"""Source-checkout paths; independent of the launching working directory."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Keep runtime output inside the extracted project by default."""

    project_root: Path

    @property
    def log_directory(self) -> Path:
        return self.project_root / "logs"

    @property
    def data_directory(self) -> Path:
        return self.project_root / "Data"


def default_paths() -> ApplicationPaths:
    """Resolve the source root, including when its Windows path contains spaces."""
    return ApplicationPaths(project_root=Path(__file__).resolve().parents[2])

