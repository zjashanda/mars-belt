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
    SerialCapture,
    ctrl,
    first_version,
    generate_audio,
    load_web_config,
    marker_seen,
    play,
    protocol_baud_from_web_config,
    send_protocol,
    word_markers,
    write_json,
    run_cmd,
    PLAY_SCRIPT,
)

ROOT = Path(__file__).resolve().parents[2]


def feature_flags(web: Dict[str, Any]) -> Dict[str, Any]:
    fw = (first_version(web).get("firmware") or {})
    study = fw.get("study_config") or {}
    multi = fw.get("multi_wakeup") or {}
    timeout = fw.get("timeout_config") or {}
    try:
        wake_timeout = int(timeout.get("time") or 0)
    except Exception:
        wake_timeout = 0
    return {
        "voiceRegEnable": bool(study.get("enable")),
        "voiceRegMode": study.get("mode") or "",
        "multiWkeEnable": bool(multi.get("enable")),
        "multiWkeMode": multi.get("mode") or "",
        "wakeTimeout": wake_timeout,
    }


def wake_words(web: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    for item in first_version(web).get("asr_wakeup") or []:
        if item.get("type") != "唤醒词":
            continue
        word = str(item.get("intent") or "").strip()
        if not word or "虚拟" in word or word == "查询唤醒词":
            continue
        if word not in result:
            result.append(word)
    return result


def command_words(web: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    reject = re.compile(r"学习|删除|退出|负性词|切换唤醒词|恢复默认唤醒词|查询唤醒词|虚拟")
    for item in first_version(web).get("asr_cmds") or []:
        typ = str(item.get("type") or "")
        word = str(item.get("intent") or "").strip()
        if not word or reject.search(word):
            continue
        if typ not in {"命令词", "最大音量", "中等音量", "最小音量", "增大音量", "减小音量"}:
            continue
        if word not in result:
            result.append(word)
    return result


def has_word(markers: Dict[str, List[str]], word: str) -> bool:
    return bool(word and markers.get(word))


def pick_commands(web: Dict[str, Any], preferred: Sequence[str]) -> List[str]:
    markers = word_markers(web)
    picked: List[str] = []
    for word in preferred:
        if has_word(markers, word) and word not in picked:
            picked.append(word)
    for word in command_words(web):
        if word not in picked:
            picked.append(word)
        if len(picked) >= 3:
            break
    # keep validation short and deterministic: two business commands plus one volume command if available
    volume = next((w for w in ["最大音量", "音量最大", "中等音量", "增大音量"] if has_word(markers, w)), "")
    core = picked[:2]
    if volume and volume not in core:
        core.append(volume)
    return core[:3]


def voice_reg_words(web: Dict[str, Any], preferred: Sequence[str]) -> Dict[str, str]:
    markers = word_markers(web)
    controls = {
        "learn": next((w for w in ["学习命令词", "学习指令"] if has_word(markers, w)), "学习命令词"),
        "delete": next((w for w in ["删除命令词", "删除指令"] if has_word(markers, w)), "删除命令词"),
    }
    learn = ""
    study = ((first_version(web).get("firmware") or {}).get("study_config") or {})
    # Voice registration must follow UI study_config.  Business preferred words
    # can be used for normal command checks, but not as registration targets.
    for item in study.get("reg_commands") or []:
        word = str(item.get("word") or item.get("condition") or "").strip()
        if word and has_word(markers, word):
            learn = word
            break
    if not learn:
        learn = next((w for w in preferred if has_word(markers, w)), "")
    if not learn:
        learn = next((w for w in command_words(web) if has_word(markers, w)), "")
    controls["target"] = learn
    mode = str((((first_version(web).get("firmware") or {}).get("study_config") or {}).get("mode") or ""))
    if "连续" in mode:
        controls["sample"] = continuous_registration_sample(learn)
    else:
        controls["sample"] = learn
    return controls


def continuous_registration_sample(target: str) -> str:
    """Pick a valid non-built-in phrase for continuous learning samples.

    Continuous learning has already selected the built-in command to be learned.
    Speaking that same built-in command as the sample can be treated as a
    conflict/error by the registration engine, so use a short natural alias.
    """
    word = str(target or "")
    if "风扇" in word:
        return "我要吹风"
    if "窗帘" in word or "布帘" in word or "卷帘" in word:
        return "拉开窗帘"
    if "取暖" in word or "温度" in word:
        return "我要取暖"
    if "垃圾桶" in word or "垃圾" in word:
        return "打开盖子"
    if "茶吧" in word or "烧水" in word or "保温" in word:
        return "我要烧水"
    return "启动设备"


def protocol_probe_frame(web: Dict[str, Any], preferred: Sequence[str]) -> str:
    for word in list(preferred) + command_words(web):
        for item in first_version(web).get("asr_cmds") or []:
            if str(item.get("intent") or "").strip() == word and item.get("snd_protocol"):
                return str(item.get("snd_protocol"))
    return DEFAULT_FRAME


def run_sequence(words: Sequence[str], audio: Dict[str, str], out_dir: Path, gap_after_word: float = 5.5) -> List[Dict[str, Any]]:
    events = []
    for idx, word in enumerate(words):
        events.append(play(word, audio, out_dir))
        time.sleep(gap_after_word if idx == 0 else 3.0)
    time.sleep(2.0)
    return events


def run_voice_registration(default_wake: str, learn: str, target: str, delete: str, audio: Dict[str, str], out_dir: Path, *, boundary_timeout: bool = False, sample: str = "") -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if not (default_wake and learn and target):
        return events
    # Learning prompt audio can be around 6-7 seconds.  Wait for it to finish
    # before speaking the target word, otherwise the sample can be clipped.
    if boundary_timeout:
        events.append(play(f"{default_wake}，{learn}", audio, out_dir))
    else:
        events.append(play(default_wake, audio, out_dir))
        time.sleep(5.5)
        events.append(play(learn, audio, out_dir))
    time.sleep(8.5)
    sample_word = sample or target
    for _ in range(3):
        events.append(play(sample_word, audio, out_dir))
        time.sleep(6.5)
    if delete:
        if boundary_timeout:
            events.append(play(f"{default_wake}，{delete}", audio, out_dir))
        else:
            events.append(play(default_wake, audio, out_dir))
            time.sleep(5.5)
            events.append(play(delete, audio, out_dir))
        time.sleep(5.0)
        events.append(play(sample_word, audio, out_dir))
        time.sleep(4.0)
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description="Adaptive runtime verifier for UI-packaged 3021 firmware.")
    ap.add_argument("--package-zip", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preferred-command", action="append", default=[])
    ap.add_argument("--log-port", default="/dev/ttyACM0")
    ap.add_argument("--log-baud", type=int, default=115200)
    ap.add_argument("--protocol-port", default="/dev/ttyACM2")
    ap.add_argument("--protocol-baud", type=int, default=0)
    ap.add_argument("--ctrl-port", default="/dev/ttyACM4")
    ap.add_argument("--ctrl-baud", type=int, default=115200)
    ap.add_argument("--power-switch", default="uut-switch1")
    ap.add_argument("--boot-switch", default="uut-switch3")
    ap.add_argument("--protocol-switch", default="uut-switch2")
    ap.add_argument("--clear-config", action="store_true", help="Clear persisted device config after boot, then power-cycle before validation.")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    web = load_web_config(Path(args.package_zip))
    markers = word_markers(web)
    flags = feature_flags(web)
    wakes = wake_words(web)
    default_wake = wakes[0] if wakes else "小聆小聆"
    alt_wake = next((w for w in wakes if w != default_wake), "")
    core_cmds = pick_commands(web, args.preferred_command)
    reg = voice_reg_words(web, args.preferred_command)
    words: List[str] = []
    for word in [default_wake, *core_cmds]:
        if word and word not in words:
            words.append(word)
    if flags["multiWkeEnable"]:
        for word in ["切换唤醒词", alt_wake, "恢复默认唤醒词", default_wake]:
            if word and has_word(markers, word) and word not in words:
                words.append(word)
    if flags["voiceRegEnable"]:
        for word in [reg.get("learn"), reg.get("target"), reg.get("sample"), reg.get("delete")]:
            if word and word not in words:
                words.append(word)
        if int(flags.get("wakeTimeout") or 0) <= 1:
            for word in [f"{default_wake}，{reg.get('learn')}", f"{default_wake}，{reg.get('delete')}"]:
                if word and word not in words:
                    words.append(word)
    audio = generate_audio(words, out_dir / "audio", "")
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
    clear_config_sent = False
    if args.clear_config:
        clear_config_sent = cap.write_line("clear.configall", wait=2.0)
        time.sleep(2)
        ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"], out_dir)
        time.sleep(3)
        ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.on"], out_dir)
        time.sleep(8)
        loglevel_sent = cap.write_line("loglevel 4", wait=2.0) or loglevel_sent
        time.sleep(1)
    frame = protocol_probe_frame(web, args.preferred_command)
    send_protocol(args.protocol_port, protocol_baud, frame)
    time.sleep(2)

    events: List[Dict[str, Any]] = []
    for cmd in core_cmds:
        events.extend(run_sequence([default_wake, cmd], audio, out_dir))
    if flags["multiWkeEnable"] and has_word(markers, "切换唤醒词") and alt_wake:
        events.extend(run_sequence([default_wake, "切换唤醒词", alt_wake], audio, out_dir))
        if has_word(markers, "恢复默认唤醒词"):
            events.extend(run_sequence([alt_wake, "恢复默认唤醒词", default_wake], audio, out_dir))
    if flags["voiceRegEnable"] and reg.get("target"):
        events.extend(run_voice_registration(
            default_wake,
            reg["learn"],
            reg["target"],
            reg.get("delete", ""),
            audio,
            out_dir,
            boundary_timeout=int(flags.get("wakeTimeout") or 0) <= 1,
            sample=reg.get("sample", ""),
        ))

    text = cap.stop()
    boot_seen = any(x in text for x in ["APP version", "Running Config", "Engine info", "ai create success"])
    protocol_rx = "[RX]" in text or frame in text
    seen = {word: marker_seen(text, markers, word) for word in words}
    core_missing = [w for w in [default_wake, *core_cmds] if w and not seen.get(w)]
    multi_required: List[str] = []
    if flags["multiWkeEnable"] and alt_wake:
        mode = str(flags.get("multiWkeMode") or "")
        if "循环" in mode:
            multi_required = [alt_wake]
        elif "指定" in mode and has_word(markers, "切换唤醒词"):
            multi_required = ["切换唤醒词", alt_wake]
        elif "协议" in mode:
            # Protocol switch packages are covered by protocol RX plus the
            # ordinary wake/command chain; no UI voice switch command is expected.
            multi_required = []
    multi_missing = [w for w in multi_required if w and not seen.get(w)]
    voice_markers = ["Reg info", "cmdlist get", "wIvwRegist", "reg again", "reg cmd over", "save new voice.bin", "reg failed", "voice reg"]
    voice_activity = any(m in text for m in voice_markers)
    voice_success_markers = ["reg cmd over success", "save new voice.bin", "reg auto next", "reg over!", "voice regging over", "reg status: 3"]
    voice_success = any(m in text for m in voice_success_markers)
    timeout_count = text.count("Wk timeout")
    boundary_timeout_observed = int(flags.get("wakeTimeout") or 0) <= 1 and seen.get(default_wake) and timeout_count > 0
    summary = {
        "packageZip": args.package_zip,
        "outDir": str(out_dir),
        "audioKey": AUDIO_KEY,
        "probeRc": probe.returncode,
        "serialError": cap.error,
        "loglevelSent": loglevel_sent,
        "clearConfigSent": clear_config_sent,
        "serialBytes": len(text.encode("utf-8", errors="ignore")),
        "bootSeen": boot_seen,
        "protocolRxSeen": protocol_rx,
        "protocolFrame": frame,
        "protocolBaud": protocol_baud,
        "flags": flags,
        "defaultWake": default_wake,
        "coreCommands": core_cmds,
        "voiceRegWords": reg,
        "wordSeen": seen,
        "coreMissing": core_missing,
        "multiMissing": multi_missing,
        "boundaryTimeoutObserved": boundary_timeout_observed,
        "voiceRegActivitySeen": voice_activity,
        "voiceRegSuccessSeen": voice_success,
        "playEvents": events,
        "logMarkers": {
            "wakeupJson": text.count("Wakeup:"),
            "txCount": text.count("[TX]"),
            "rxCount": text.count("[RX]"),
            "algoRestart": text.count("algo restart"),
            "runningConfig": text.count("Running Config"),
            "wkTimeout": timeout_count,
            "regInfo": text.count("Reg info"),
            "regFailed": text.count("reg failed"),
            "regLengthError": text.count("reg length error"),
        },
    }
    pass_core = boot_seen and protocol_rx and probe.returncode == 0 and (not core_missing or boundary_timeout_observed)
    pass_multi = (not flags["multiWkeEnable"]) or not multi_missing
    pass_voice = (not flags["voiceRegEnable"]) or voice_success
    summary["verdict"] = "PASS" if pass_core and pass_multi and pass_voice else "FAIL"
    summary["gates"] = {"core": pass_core, "multi": pass_multi, "voice": pass_voice}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
