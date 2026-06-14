#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]
PREFERRED = {
    "取暖器垂类": ["打开取暖器", "关闭取暖器", "最大音量"],
    "取暖桌垂类": ["打开取暖桌", "关闭取暖桌", "最大音量"],
    "窗帘垂类": ["打开窗帘", "关闭窗帘", "最大音量"],
    "茶吧机垂类": ["打开茶吧机", "关闭茶吧机", "最大音量"],
    "通用垂类": ["打开垃圾桶", "关闭垃圾桶", "最大音量"],
    "风扇垂类": ["打开风扇", "关闭风扇", "最大音量"],
}


def safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", text)[:180]


def run_logged(args: List[str], log_path: Path, timeout: int) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log_path.open("w", encoding="utf-8", errors="ignore") as fp:
        fp.write("$ " + " ".join(args) + "\n")
        fp.flush()
        proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="ignore")
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                fp.write(line)
                fp.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
                if time.time() - started > timeout:
                    proc.kill()
                    fp.write(f"\n[TIMEOUT after {timeout}s]\n")
                    return 124
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            fp.write(f"\n[TIMEOUT after {timeout}s]\n")
            return 124


def main() -> int:
    ap = argparse.ArgumentParser(description="Burn and runtime-verify all downloaded 3021 firmware packages.")
    ap.add_argument("--download-summary", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--skip-existing-pass", action="store_true")
    ap.add_argument("--max-burn-retry", type=int, default=1)
    ap.add_argument("--indices", default="", help="Comma-separated absolute firmware item indices to run.")
    ap.add_argument("--verify-clear-config", action="store_true", help="Clear persisted device config before runtime validation.")
    args = ap.parse_args()

    download_summary = json.loads(Path(args.download_summary).read_text(encoding="utf-8"))
    items = download_summary["firmware"]["items"]
    if args.indices.strip():
        selected = {int(x.strip()) for x in args.indices.split(",") if x.strip()}
        items = [(idx, item) for idx, item in enumerate(items) if idx in selected]
    elif args.limit:
        items = items[args.start_index:args.start_index + args.limit]
        items = list(enumerate(items, start=args.start_index))
    else:
        items = items[args.start_index:]
        items = list(enumerate(items, start=args.start_index))

    out_root = Path(args.out_dir)
    results: List[Dict[str, Any]] = []
    for absolute_index, item in items:
        vertical = item.get("vertical") or ""
        job_id = item.get("jobId") or Path(item["path"]).stem
        pkg = Path(item["path"])
        out_dir = out_root / f"{absolute_index:02d}_{safe_name(job_id)}"
        summary_path = out_dir / "summary.json"
        if args.skip_existing_pass and summary_path.exists():
            try:
                old = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                old = {}
            if old.get("verdict") == "PASS":
                results.append({
                    "index": absolute_index,
                    "vertical": vertical,
                    "jobId": job_id,
                    "packageZip": str(pkg),
                    "outDir": str(out_dir),
                    "burnRc": 0,
                    "verifyRc": 0,
                    "verdict": "PASS",
                    "skippedExistingPass": True,
                })
                continue

        print(f"\n========== [{absolute_index + 1}] {vertical} / {job_id} ==========")
        burn_cmd = [
            "python3", "scripts/mars_belt.py", "burn",
            "--package-zip", str(pkg),
            "--ctrl-port", "/dev/ttyACM4",
            "--burn-port", "/dev/ttyACM0",
            "--runtime-log-port", "/dev/ttyACM0",
            "--boot-switch", "uut-switch3",
            "--power-switch", "uut-switch1",
            "--protocol-switch", "uut-switch2",
            "--max-retry", str(args.max_burn_retry),
        ]
        burn_rc = run_logged(burn_cmd, out_dir / "burn.log", timeout=240)
        verify_rc = None
        verdict = "BURN_FAIL" if burn_rc != 0 else "VERIFY_NOT_RUN"
        verify_summary: Dict[str, Any] = {}
        if burn_rc == 0:
            verify_cmd = [
                "python3", "scripts/py/listenai_3021_adaptive_runtime_verify.py",
                "--package-zip", str(pkg),
                "--out-dir", str(out_dir),
            ]
            for word in PREFERRED.get(vertical, ["打开风扇", "关闭风扇", "最大音量"]):
                verify_cmd.extend(["--preferred-command", word])
            if args.verify_clear_config:
                verify_cmd.append("--clear-config")
            verify_rc = run_logged(verify_cmd, out_dir / "run.log", timeout=420)
            if summary_path.exists():
                try:
                    verify_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    verify_summary = {"summaryParseError": str(exc)}
            verdict = verify_summary.get("verdict") or ("PASS" if verify_rc == 0 else "VERIFY_FAIL")
        result = {
            "index": absolute_index,
            "vertical": vertical,
            "jobId": job_id,
            "packageZip": str(pkg),
            "outDir": str(out_dir),
            "burnRc": burn_rc,
            "verifyRc": verify_rc,
            "verdict": verdict,
            "flags": verify_summary.get("flags"),
            "gates": verify_summary.get("gates"),
            "coreMissing": verify_summary.get("coreMissing"),
            "multiMissing": verify_summary.get("multiMissing"),
            "voiceRegWords": verify_summary.get("voiceRegWords"),
            "voiceRegActivitySeen": verify_summary.get("voiceRegActivitySeen"),
            "voiceRegSuccessSeen": verify_summary.get("voiceRegSuccessSeen"),
            "logMarkers": verify_summary.get("logMarkers"),
        }
        results.append(result)
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    totals = {
        "total": len(results),
        "pass": sum(1 for x in results if x.get("verdict") == "PASS"),
        "burnFail": sum(1 for x in results if x.get("verdict") == "BURN_FAIL"),
        "verifyFail": sum(1 for x in results if x.get("verdict") not in {"PASS", "BURN_FAIL"}),
    }
    output = {"totals": totals, "results": results}
    Path(args.summary).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 0 if totals["pass"] == totals["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
