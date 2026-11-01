import os
from pathlib import Path

from . import ROOT


def workdir() -> Path:
    """Generated data, warehouse file, proposals and run reports live here (gitignored)."""
    return Path(os.environ.get("CDOP_WORKDIR") or ROOT / "work")


def mappings_dir() -> Path:
    """Reviewed, versioned mapping files (committed)."""
    return Path(os.environ.get("CDOP_MAPPINGS_DIR") or ROOT / "mappings")
