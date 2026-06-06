#!/usr/bin/env python3
"""Summarize load test metrics from pytest JSON report or a metrics dict file."""

import json
import sys
from pathlib import Path


def summarize_pytest_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    tests = data.get("tests", [])
    passed = sum(1 for t in tests if t.get("outcome") == "passed")
    failed = sum(1 for t in tests if t.get("outcome") == "failed")
    durations = [t.get("call", {}).get("duration", 0) for t in tests if "call" in t]
    durations.sort()
    p50 = durations[len(durations) // 2] if durations else 0
    p95 = durations[int(len(durations) * 0.95)] if durations else 0
    return {
        "total": len(tests),
        "passed": passed,
        "failed": failed,
        "p50_seconds": round(p50, 3),
        "p95_seconds": round(p95, 3),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: load_report.py <pytest-report.json|metrics.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)
    data = json.loads(path.read_text())
    if "tests" in data:
        summary = summarize_pytest_json(path)
    else:
        summary = data
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
