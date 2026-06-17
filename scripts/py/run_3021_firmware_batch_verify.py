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

from listenai_3021_vertical_runtime_smoke import SerialCapture, ctrl

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


def package_flags(package_zip: Path) -> Dict[str, Any]:
    from listenai_3021_adaptive_runtime_verify import feature_flags
    from listenai_3021_vertical_runtime_smoke import load_web_config

    return feature_flags(load_web_config(package_zip))


def package_static_findings(package_zip: Path) -> List[Dict[str, Any]]:
    from listenai_3021_adaptive_runtime_verify import feature_flags, special_shadow_conflicts
    from listenai_3021_vertical_runtime_smoke import load_web_config

    web = load_web_config(package_zip)
    flags = feature_flags(web)
    conflicts = special_shadow_conflicts(web)
    findings: List[Dict[str, Any]] = []
    multi_shadow = [
        item for item in conflicts
        if any("多唤醒词切换" in special for special in item.get("specialTypes", []))
    ]
    voice_shadow = [
        item for item in conflicts
        if any("语音注册控制相关" in special for special in item.get("specialTypes", []))
    ]
    if flags.get("multiWkeEnable") and str(flags.get("multiWkeMode") or "") in {"指定切换", "循环切换"} and multi_shadow:
        findings.append({
            "level": "FAIL",
            "code": "MULTI-WAKE-SPECIAL-SHADOW",
            "message": "normal protocol commands shadow special multi-wakeup controls",
            "words": [item["word"] for item in multi_shadow],
        })
    if flags.get("voiceRegEnable") and voice_shadow:
        findings.append({
            "level": "FAIL",
            "code": "VOICE-REG-SPECIAL-SHADOW",
            "message": "normal protocol commands shadow special voice-registration controls",
            "words": [item["word"] for item in voice_shadow],
        })
    return findings


def preclean_runtime_state(out_dir: Path) -> Dict[str, Any]:
    """Clear persisted runtime state before burning the next target package.

    Running this before the target burn avoids carrying wkword/regSave residue
    across packages while not damaging the target package's study_config after
    it boots.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = SerialCapture("/dev/ttyACM0", 115200, out_dir / "preclean_serial.log")
    result: Dict[str, Any] = {"attempted": True, "clearSent": False, "error": ""}
    try:
        cap.start()
        time.sleep(0.5)
        ctrl("/dev/ttyACM4", 115200, ["uut-switch2.off", "uut-switch3.off", "uut-switch1.off", "uut-switch1.on"], out_dir)
        time.sleep(3)
        ctrl("/dev/ttyACM4", 115200, ["uut-switch2.on"], out_dir)
        time.sleep(8)
        cap.write_line("loglevel 4", wait=2.0)
        time.sleep(0.5)
        result["clearSent"] = cap.write_line("clear.configall", wait=2.0)
        time.sleep(2)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        text = cap.stop()
        result["serialBytes"] = len(text.encode("utf-8", errors="ignore"))
        result["serialError"] = cap.error
    (out_dir / "preclean_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


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
    ap.add_argument("--preclean-state", action="store_true", help="Clear persisted state before each burn using the currently running firmware.")
    ap.add_argument("--allow-special-shadow", action="store_true", help="Do not block packages whose web_config has special-control shadow conflicts.")
    ap.add_argument("--command-probe-word", action="append", default=[], help="Forward command-level probe words to the runtime verifier.")
    ap.add_argument("--command-probe-repeat", type=int, default=3)
    ap.add_argument("--platform-audio", action="store_true", help="Use formal platform audio-synthesis assets for Chinese runtime playback.")
    ap.add_argument("--platform-audio-root", default="assets/audio/platform_synthesis/zh", help="Root directory for platform audio suites.")
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
        static_findings = package_static_findings(pkg)
        if static_findings and not args.allow_special_shadow:
            print(f"[static] block before burn: {job_id} -> {[x.get('code') for x in static_findings]}")
            result = {
                "index": absolute_index,
                "vertical": vertical,
                "jobId": job_id,
                "packageZip": str(pkg),
                "outDir": str(out_dir),
                "burnRc": None,
                "verifyRc": 3,
                "verdict": "CONFIG_FAIL",
                "preclean": {},
                "flags": package_flags(pkg),
                "gates": None,
                "staticFindings": static_findings,
                "staticBlocked": True,
                "precheckConfig": None,
                "stateRecoveryAttempted": False,
                "stateRecoveryOk": None,
                "stateDirtyReason": "",
                "invalidWakeStateMarkers": None,
                "boundaryMode": None,
                "commandProbeWords": args.command_probe_word,
                "commandProbeCounts": {},
                "coreMissing": None,
                "multiMissing": None,
                "voiceRegWords": None,
                "voiceRegTargetStepRequired": None,
                "voiceRegActivitySeen": None,
                "voiceRegSuccessSeen": None,
                "voiceRegFailureMarkers": None,
                "protocolWakeSwitchFrame": "",
                "protocolWakeSwitchSent": False,
                "logMarkers": None,
            }
            results.append(result)
            out_dir.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
            Path(args.summary).write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        preclean_summary: Dict[str, Any] = {}
        if args.preclean_state:
            print(f"[preclean] clear persisted runtime state before burn: {job_id}")
            preclean_summary = preclean_runtime_state(out_dir)
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
            flags = package_flags(pkg)
            verify_cmd = [
                "python3", "scripts/py/listenai_3021_adaptive_runtime_verify.py",
                "--package-zip", str(pkg),
                "--out-dir", str(out_dir),
            ]
            for word in PREFERRED.get(vertical, ["打开风扇", "关闭风扇", "最大音量"]):
                verify_cmd.extend(["--preferred-command", word])
            if args.verify_clear_config and not flags.get("voiceRegEnable"):
                verify_cmd.append("--clear-config")
            elif args.verify_clear_config and flags.get("voiceRegEnable"):
                print("[verify] skip post-burn clear.configall for voice-registration package")
            if args.allow_special_shadow:
                verify_cmd.append("--allow-special-shadow")
            for word in args.command_probe_word:
                verify_cmd.extend(["--command-probe-word", word])
            if args.command_probe_word:
                verify_cmd.extend(["--command-probe-repeat", str(args.command_probe_repeat)])
            if args.platform_audio:
                suite = f"3021_{safe_name(vertical).strip('_')}_platform_runtime"
                verify_cmd.extend(["--platform-audio-suite", suite, "--platform-audio-root", args.platform_audio_root])
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
            "preclean": preclean_summary,
            "flags": verify_summary.get("flags"),
            "gates": verify_summary.get("gates"),
            "staticFindings": verify_summary.get("staticFindings"),
            "staticBlocked": verify_summary.get("staticBlocked"),
            "precheckConfig": verify_summary.get("precheckConfig"),
            "stateRecoveryAttempted": verify_summary.get("stateRecoveryAttempted"),
            "stateRecoveryOk": verify_summary.get("stateRecoveryOk"),
            "stateDirtyReason": verify_summary.get("stateDirtyReason"),
            "invalidWakeStateMarkers": verify_summary.get("invalidWakeStateMarkers"),
            "boundaryMode": verify_summary.get("boundaryMode"),
            "commandProbeWords": verify_summary.get("commandProbeWords"),
            "commandProbeCounts": verify_summary.get("commandProbeCounts"),
            "coreMissing": verify_summary.get("coreMissing"),
            "multiMissing": verify_summary.get("multiMissing"),
            "voiceRegWords": verify_summary.get("voiceRegWords"),
            "voiceRegTargetStepRequired": verify_summary.get("voiceRegTargetStepRequired"),
            "voiceRegActivitySeen": verify_summary.get("voiceRegActivitySeen"),
            "voiceRegSuccessSeen": verify_summary.get("voiceRegSuccessSeen"),
            "voiceRegFailureMarkers": verify_summary.get("voiceRegFailureMarkers"),
            "protocolWakeSwitchFrame": verify_summary.get("protocolWakeSwitchFrame"),
            "protocolWakeSwitchSent": verify_summary.get("protocolWakeSwitchSent"),
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
