#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from listenai_3021_vertical_runtime_smoke import (
    AUDIO_KEY,
    DEFAULT_FRAME,
    PLAY_SCRIPT,
    SerialCapture,
    ctrl,
    first_version,
    load_web_config,
    marker_seen,
    play,
    protocol_baud_from_web_config,
    run_cmd,
    send_protocol,
    word_markers,
    write_json,
)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def command_words(web: Dict[str, Any]) -> List[str]:
    words: List[str] = []
    for item in first_version(web).get("asr_cmds") or []:
        word = str(item.get("intent") or "").strip()
        typ = str(item.get("type") or "")
        if not word:
            continue
        if typ in {"命令词", "增大音量", "减小音量", "最大音量", "中等音量", "最小音量", "退出识别"}:
            words.append(word)
    return words


def wake_words(web: Dict[str, Any]) -> List[str]:
    words: List[str] = []
    for item in first_version(web).get("asr_wakeup") or []:
        if item.get("type") != "唤醒词":
            continue
        word = str(item.get("intent") or "").strip()
        if word and word not in words:
            words.append(word)
    return words


def audio_map(words: Sequence[str], audio_dir: Path) -> Dict[str, str]:
    audio: Dict[str, str] = {}
    missing: List[str] = []
    for word in words:
        candidates = [
            audio_dir / f"{slug(word)}.mp3",
            audio_dir / f"{slug(word)}.wav",
        ]
        found = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
        if not found:
            missing.append(f"{word} -> {candidates[0].name}")
            continue
        audio[word] = str(found)
    if missing:
        raise RuntimeError("official synthesized audio missing: " + "; ".join(missing))
    return audio


def protocol_frame(web: Dict[str, Any], preferred: str) -> str:
    for item in first_version(web).get("asr_cmds") or []:
        if str(item.get("intent") or "").strip() == preferred and item.get("snd_protocol"):
            return str(item.get("snd_protocol"))
    return DEFAULT_FRAME


def firmware_timeout(web: Dict[str, Any]) -> int:
    try:
        return int((((first_version(web).get("firmware") or {}).get("timeout_config") or {}).get("time")))
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify 3021 English firmware with official platform audio-synthesis assets.")
    ap.add_argument("--package-zip", required=True)
    ap.add_argument("--audio-dir", default="assets/audio/platform_synthesis/en/3021_fan_base")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--command", action="append", default=[])
    ap.add_argument("--log-port", default="/dev/ttyACM0")
    ap.add_argument("--log-baud", type=int, default=115200)
    ap.add_argument("--protocol-port", default="/dev/ttyACM2")
    ap.add_argument("--protocol-baud", type=int, default=0)
    ap.add_argument("--ctrl-port", default="/dev/ttyACM4")
    ap.add_argument("--ctrl-baud", type=int, default=115200)
    ap.add_argument("--power-switch", default="uut-switch1")
    ap.add_argument("--boot-switch", default="uut-switch3")
    ap.add_argument("--protocol-switch", default="uut-switch2")
    ap.add_argument("--boundary-mode", choices=["auto", "normal", "timeout-only"], default="auto")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    web = load_web_config(Path(args.package_zip))
    markers = word_markers(web)
    timeout_seconds = firmware_timeout(web)
    timeout_only = args.boundary_mode == "timeout-only" or (args.boundary_mode == "auto" and 0 < timeout_seconds <= 1)
    wakes = wake_words(web)
    default_wake = wakes[0] if wakes else "Hello My Dear"
    preferred = args.command or ["Start Fan", "Stop Fan", "Volume Up"]
    available = set(command_words(web))
    commands = [w for w in preferred if w in available][:3]
    if not commands:
        commands = command_words(web)[:3]
    words: List[str] = []
    effective_commands = [] if timeout_only else commands
    for word in [default_wake, *effective_commands]:
        if word and word not in words:
            words.append(word)
    audio = audio_map(words, Path(args.audio_dir))

    probe = run_cmd(["python3", str(PLAY_SCRIPT), "probe", "--platform", "linux", "--device-key", AUDIO_KEY], out_dir / "playback.log", timeout=30)
    protocol_baud = int(args.protocol_baud or 0) or protocol_baud_from_web_config(web, 9600)

    cap = SerialCapture(args.log_port, args.log_baud, out_dir / "serial_raw.log")
    cap.start()
    time.sleep(0.5)
    ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"], out_dir)
    time.sleep(3)
    ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.on"], out_dir)
    time.sleep(8)
    loglevel_sent = cap.write_line("loglevel 4", wait=2.0)
    time.sleep(1)

    frame = protocol_frame(web, commands[0] if commands else "")
    send_protocol(args.protocol_port, protocol_baud, frame)
    time.sleep(2)

    events: List[Dict[str, Any]] = []
    if timeout_only:
        events.append(play(default_wake, audio, out_dir))
        time.sleep(4.0)
    else:
        for cmd in commands:
            events.append(play(default_wake, audio, out_dir))
            time.sleep(5.5)
            events.append(play(cmd, audio, out_dir))
            time.sleep(4.0)

    text = cap.stop()
    boot_seen = any(x in text for x in ["APP version", "Running Config", "Engine info", "ai create success"])
    protocol_rx = "[RX]" in text or frame in text
    seen = {word: marker_seen(text, markers, word) for word in words}
    missing = [word for word in words if not seen.get(word)]
    lower_text = text.lower()
    timeout_seen = any(x in lower_text for x in ["wk timeout", "wakeup timeout", "asr timeout", "timeout!"])
    summary = {
        "packageZip": args.package_zip,
        "audioDir": str(Path(args.audio_dir)),
        "audioSource": "platform synthesis management / audio synthesis output",
        "outDir": str(out_dir),
        "audioKey": AUDIO_KEY,
        "probeRc": probe.returncode,
        "serialError": cap.error,
        "loglevelSent": loglevel_sent,
        "serialBytes": len(text.encode("utf-8", errors="ignore")),
        "bootSeen": boot_seen,
        "protocolRxSeen": protocol_rx,
        "protocolFrame": frame,
        "protocolBaud": protocol_baud,
        "firmwareTimeoutSeconds": timeout_seconds,
        "boundaryMode": args.boundary_mode,
        "timeoutOnly": timeout_only,
        "timeoutSeen": timeout_seen,
        "defaultWake": default_wake,
        "commands": effective_commands,
        "skippedCommands": commands if timeout_only else [],
        "wordSeen": seen,
        "missingWords": missing,
        "playEvents": events,
        "logMarkers": {
            "wakeupJson": text.count("Wakeup:"),
            "rxCount": text.count("[RX]"),
            "txCount": text.count("[TX]"),
            "runningConfig": text.count("Running Config"),
            "algoReady": text.count("ai create success"),
        },
    }
    if timeout_only:
        summary["verdict"] = "PASS" if boot_seen and protocol_rx and probe.returncode == 0 and not missing and timeout_seen else "FAIL"
    else:
        summary["verdict"] = "PASS" if boot_seen and protocol_rx and probe.returncode == 0 and not missing else "FAIL"
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
