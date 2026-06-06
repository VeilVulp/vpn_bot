"""Shared filesystem paths for the deployed project."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = PROJECT_ROOT / "locales"
