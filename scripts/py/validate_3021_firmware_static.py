#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from listenai_3021_adaptive_runtime_verify import feature_flags, special_shadow_conflicts
from listenai_3021_vertical_runtime_smoke import load_web_config


def validate_package(path: Path, index: int = -1, vertical: str = "", job_id: str = "") -> Dict[str, Any]:
    web = load_web_config(path)
    flags = feature_flags(web)
    conflicts = special_shadow_conflicts(web)
    findings: List[Dict[str, Any]] = []
    multi_conflicts = [
        item for item in conflicts
        if any("多唤醒词切换" in special for special in item.get("specialTypes", []))
    ]
    voice_conflicts = [
        item for item in conflicts
        if any("语音注册控制相关" in special for special in item.get("specialTypes", []))
    ]
    if flags["multiWkeEnable"] and str(flags.get("multiWkeMode") or "") in {"指定切换", "循环切换"} and multi_conflicts:
        findings.append({
            "level": "FAIL",
            "code": "MULTI-WAKE-SPECIAL-SHADOW",
            "message": "普通协议词遮蔽多唤醒 special 控制词",
            "words": [item["word"] for item in multi_conflicts],
        })
    if flags["voiceRegEnable"] and voice_conflicts:
        findings.append({
            "level": "FAIL",
            "code": "VOICE-REG-SPECIAL-SHADOW",
            "message": "普通协议词遮蔽语音注册 special 控制词",
            "words": [item["word"] for item in voice_conflicts],
        })
    return {
        "index": index,
        "vertical": vertical,
        "jobId": job_id,
        "packageZip": str(path),
        "flags": flags,
        "findings": findings,
        "verdict": "FAIL" if any(item["level"] == "FAIL" for item in findings) else "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Static web_config checks for 3021 firmware packages.")
    parser.add_argument("--package-zip", action="append", default=[])
    parser.add_argument("--download-summary", default="")
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()

    items: List[Dict[str, Any]] = []
    for package in args.package_zip:
        items.append({"path": package, "vertical": "", "jobId": ""})
    if args.download_summary:
        summary = json.loads(Path(args.download_summary).read_text(encoding="utf-8"))
        items.extend(summary.get("firmware", {}).get("items") or [])
    if not items:
        raise SystemExit("provide --package-zip or --download-summary")

    results = [
        validate_package(Path(item["path"]), index=i, vertical=str(item.get("vertical") or ""), job_id=str(item.get("jobId") or ""))
        for i, item in enumerate(items)
    ]
    payload = {
        "total": len(results),
        "pass": sum(1 for item in results if item["verdict"] == "PASS"),
        "fail": sum(1 for item in results if item["verdict"] != "PASS"),
        "results": results,
    }
    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
