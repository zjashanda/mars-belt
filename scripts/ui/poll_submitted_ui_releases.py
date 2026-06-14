#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from listenai_task_support import resolve_listenai_token  # type: ignore
from listenai_product_options import ListenAIClient, require_ok  # type: ignore


def extract_release_id(result: Dict[str, Any]) -> str:
    for step in reversed(result.get("steps") or []):
        url = str(step.get("url") or "")
        m = re.search(r"getDepthData\?id=(\d+)", url)
        if m:
            return m.group(1)
    release = result.get("release") or (result.get("poll") or {}).get("release") or {}
    return str(release.get("id") or "")


def compact_release(data: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "id", "prodId", "version", "comments", "status", "pkgUrl", "pkgSDKUrl",
        "pkgPipelineId", "pkgTaskId", "timeout", "volLevel", "defaultVol", "uportBaud",
        "traceUart", "traceBaud", "logLevel", "wakeWordSave", "volSave",
        "voiceRegEnable", "multiWkeEnable", "multiWkeMode", "algoViewMode",
    ]
    return {k: data.get(k) for k in keys if k in data}


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll UI submit-only result.json release ids until backend packaging finishes.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--timeout-sec", type=int, default=3600)
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    result_paths = sorted(root.glob("*/result.json"))
    token = resolve_listenai_token("", allow_missing=False, persist=False)
    client = ListenAIClient(token, timeout=120)
    deadline = time.time() + args.timeout_sec
    last_payload: Dict[str, Any] = {}

    while True:
        rows: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}
        for path in result_paths:
            result = json.loads(path.read_text(encoding="utf-8"))
            rid = extract_release_id(result)
            job = result.get("job") or {}
            row: Dict[str, Any] = {
                "jobId": job.get("jobId"),
                "productName": job.get("productName"),
                "profile": job.get("profile"),
                "resultPath": str(path),
                "releaseId": rid,
                "detailStatus": "missing_release_id" if not rid else "pending_probe",
            }
            if rid:
                try:
                    detail = require_ok(client.get("/fw/release/detail", {"id": rid}), f"release detail {rid}").get("data") or {}
                    release = compact_release(detail)
                    status = str(detail.get("status") or "")
                    row.update({"detailStatus": status, "release": release})
                    result["release"] = release
                    result["poll"] = {**(result.get("poll") or {}), "release": release, "lastPolledAt": datetime.now().isoformat(timespec="seconds")}
                    if status == "success" and detail.get("pkgUrl"):
                        result["status"] = "success"
                    elif status in {"failed", "fail", "error"}:
                        result["status"] = "failed"
                    else:
                        result["status"] = "submitted"
                    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:
                    row.update({"detailStatus": "probe_error", "error": f"{type(exc).__name__}: {exc}"})
            counts[row["detailStatus"]] = counts.get(row["detailStatus"], 0) + 1
            rows.append(row)
        done = all(r.get("detailStatus") == "success" and (r.get("release") or {}).get("pkgUrl") for r in rows)
        failed = [r for r in rows if r.get("detailStatus") in {"failed", "fail", "error", "probe_error", "missing_release_id"}]
        last_payload = {"generatedAt": datetime.now().isoformat(timespec="seconds"), "counts": counts, "total": len(rows), "done": done, "failedLike": failed, "rows": rows}
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(last_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"counts": counts, "done": done, "failedLike": len(failed)}, ensure_ascii=False), flush=True)
        if done:
            return 0
        if time.time() >= deadline:
            return 2
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
