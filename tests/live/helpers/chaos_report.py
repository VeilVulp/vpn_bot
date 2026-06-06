"""Metrics summary for live chaos tests."""

from __future__ import annotations

import json
from pathlib import Path


def new_metrics() -> dict:
    return {
        "ok": 0,
        "fail": 0,
        "errors": [],
        "latencies": [],
        "ops": {},
    }


def record_op(metrics: dict, name: str, success: bool, latency: float, error: str | None = None):
    metrics["ops"].setdefault(name, {"ok": 0, "fail": 0})
    if success:
        metrics["ok"] += 1
        metrics["ops"][name]["ok"] += 1
    else:
        metrics["fail"] += 1
        metrics["ops"][name]["fail"] += 1
        if error:
            metrics["errors"].append(f"{name}: {error}")
    metrics["latencies"].append(latency)


def summarize(metrics: dict) -> dict:
    lat = sorted(metrics.get("latencies") or [])
    total = metrics.get("ok", 0) + metrics.get("fail", 0)
    p50 = lat[len(lat) // 2] if lat else 0.0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
    pool_errs = [e for e in metrics.get("errors", []) if "QueuePool" in e]
    return {
        "total_ops": total,
        "ok": metrics.get("ok", 0),
        "fail": metrics.get("fail", 0),
        "success_rate": round(metrics.get("ok", 0) / total, 3) if total else 0.0,
        "p50_seconds": round(p50, 3),
        "p95_seconds": round(p95, 3),
        "pool_errors": len(pool_errs),
        "errors_sample": metrics.get("errors", [])[:10],
        "ops": metrics.get("ops", {}),
    }


def write_report(metrics: dict, path: str | Path = "reports/live_chaos_last.json") -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize(metrics)
    payload = {"metrics": metrics, "summary": summary}
    path.write_text(json.dumps(payload, indent=2, default=str))
    return summary
