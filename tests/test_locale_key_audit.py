"""Ensure static LanguageManager keys used in code exist in locale files."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _collect_static_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "vpn_bot").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"LanguageManager\.get\(\s*['\"]([^'\"]+)['\"]", text):
            keys.add(m.group(1))
    return keys


@pytest.fixture(scope="module")
def locale_keys():
    fa = _flatten(json.loads((ROOT / "locales" / "fa.json").read_text(encoding="utf-8")))
    en = _flatten(json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8")))
    return fa, en


def test_static_keys_exist_in_fa_and_en(locale_keys):
    fa, en = locale_keys
    used = _collect_static_keys()
    missing_fa = sorted(k for k in used if k not in fa)
    missing_en = sorted(k for k in used if k not in en)
    assert not missing_fa, f"Missing in fa.json: {missing_fa}"
    assert not missing_en, f"Missing in en.json: {missing_en}"
