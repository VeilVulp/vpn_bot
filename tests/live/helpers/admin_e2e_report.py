"""JSON report for admin panel E2E runs."""

from __future__ import annotations

import json
import time
from pathlib import Path


def new_report() -> dict:
    return {
        "started_at": time.time(),
        "sections": {},
        "errors": [],
        "ok": 0,
        "fail": 0,
    }


def record_section(report: dict, name: str, *, ok: bool, detail: dict | None = None, error: str | None = None):
    report["sections"][name] = {"ok": ok, "detail": detail or {}}
    if error:
        report["errors"].append(f"{name}: {error}")
    if ok:
        report["ok"] += 1
    else:
        report["fail"] += 1


def write_report(report: dict, path: str | Path | None = None) -> Path:
    report["finished_at"] = time.time()
    report["duration_sec"] = round(report["finished_at"] - report["started_at"], 2)
    out = Path(path or Path(__file__).resolve().parents[3] / "reports" / "admin_panel_e2e_last.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
