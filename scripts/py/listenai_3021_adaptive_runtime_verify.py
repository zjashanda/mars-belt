#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import subprocess
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
from listenai_platform_audio_synthesis_cache import audio_candidates, safe_audio_name

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


def parse_running_config(text: str) -> Dict[str, Any]:
    """Parse the latest Running Config block from the device log."""
    blocks = re.findall(
        r"=+\s*Running Config\s*=+(.*?)(?:=+\s*$|=+\s*\r?\n)",
        text,
        flags=re.S | re.M,
    )
    if not blocks and "Running Config" in text:
        blocks = text.split("Running Config")[-1:]
    block = blocks[-1] if blocks else ""
    result: Dict[str, Any] = {"raw": block}
    for key in ["volume", "voice", "wkword", "regSaveFlag", "regSaveSize", "reg_cmd_count", "reg_cmd_status"]:
        m = re.search(rf"{re.escape(key)}\s*:\s*(\d+)", block)
        if m:
            result[key] = int(m.group(1))
    return result


def special_shadow_conflicts(web: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect normal protocol commands that shadow special control words."""
    by_word: Dict[str, List[Dict[str, Any]]] = {}
    for item in first_version(web).get("asr_cmds") or []:
        special_type = str(item.get("special_type") or item.get("specialType") or "").strip()
        has_protocol = bool(item.get("snd_protocol") or item.get("rec_protocol"))
        entry = {
            "id": item.get("id"),
            "intent": item.get("intent"),
            "type": item.get("type"),
            "specialType": special_type,
            "hasProtocol": has_protocol,
        }
        words = [str(item.get("intent") or "").strip()]
        for expand in item.get("expand_words") or []:
            words.append(str(expand.get("keyword") or "").strip())
        for word in {w for w in words if w}:
            by_word.setdefault(word, []).append(entry)
    conflicts: List[Dict[str, Any]] = []
    for word, entries in sorted(by_word.items()):
        if len(entries) < 2:
            continue
        has_special = any(e["specialType"] for e in entries)
        has_normal_protocol = any((not e["specialType"]) and e["hasProtocol"] for e in entries)
        if not (has_special and has_normal_protocol):
            continue
        special_types = sorted({e["specialType"] for e in entries if e["specialType"]})
        conflicts.append({"word": word, "specialTypes": special_types, "entries": entries})
    return conflicts


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
    # are only valid registration targets when they are present in reg_commands.
    reg_words: List[str] = []
    for item in study.get("reg_commands") or []:
        word = str(item.get("word") or item.get("condition") or "").strip()
        if word and has_word(markers, word):
            reg_words.append(word)
    for word in preferred:
        if word in reg_words:
            learn = word
            break
    if not learn and reg_words:
        learn = reg_words[0]
    if not learn:
        learn = next((w for w in preferred if has_word(markers, w)), "")
    if not learn:
        learn = next((w for w in command_words(web) if has_word(markers, w)), "")
    controls["target"] = learn
    # Non-specified learning can use a short natural sample. Specified learning
    # overrides this later and repeats the selected target command itself.
    controls["sample"] = continuous_registration_sample(learn)
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
    if "状态" in word or "查询" in word:
        return "启动设备"
    return "启动设备"


def direct_multi_switch_word(markers: Dict[str, List[str]], alt_wake: str) -> str:
    """Prefer the concrete UI-generated switch command when it exists."""
    if not alt_wake:
        return ""
    for word in [f"切换到{alt_wake}", f"切换至{alt_wake}", f"切换为{alt_wake}"]:
        if has_word(markers, word):
            return word
    return ""


def run_multi_wakeup(
    default_wake: str,
    alt_wake: str,
    switch_word: str,
    restore_word: str,
    audio: Dict[str, str],
    out_dir: Path,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if not (default_wake and alt_wake and switch_word):
        return events
    events.append(play(default_wake, audio, out_dir))
    time.sleep(5.5)
    events.append(play(switch_word, audio, out_dir))
    # Wait for switch prompt / acknowledgement before testing the new wake word.
    time.sleep(5.5)
    events.append(play(alt_wake, audio, out_dir))
    time.sleep(4.0)
    if restore_word:
        events.append(play(alt_wake, audio, out_dir))
        time.sleep(5.5)
        events.append(play(restore_word, audio, out_dir))
        time.sleep(5.5)
        events.append(play(default_wake, audio, out_dir))
        time.sleep(4.0)
    return events


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


def run_boundary_timeout(default_wake: str, audio: Dict[str, str], out_dir: Path) -> List[Dict[str, Any]]:
    events = []
    # A timeout-left-boundary package is expected to leave ASR quickly.  Use a
    # short repeated wake-only probe instead of forcing command/multi/voice
    # flows; one missed acoustic playback must not become a false package fail.
    for _ in range(3):
        events.append(play(default_wake, audio, out_dir))
        time.sleep(4.5)
    return events


def marker_count(text: str, markers: Dict[str, List[str]], word: str) -> int:
    vals = markers.get(word) or [word]
    return max((text.count(v) for v in vals if v), default=0)


def wait_marker_count(
    cap: SerialCapture,
    markers: Dict[str, List[str]],
    word: str,
    baseline: int,
    timeout: float = 6.0,
    poll: float = 0.5,
) -> int:
    """Wait until a word marker count increases in the live serial capture."""
    end = time.time() + timeout
    current = baseline
    while time.time() < end:
        current = marker_count(cap.snapshot(), markers, word)
        if current > baseline:
            return current
        time.sleep(poll)
    return marker_count(cap.snapshot(), markers, word)


def wait_text_count(cap: SerialCapture, phrase: str, baseline: int, timeout: float = 8.0, poll: float = 0.5) -> int:
    """Wait until a raw serial phrase count increases."""
    end = time.time() + timeout
    current = baseline
    while time.time() < end:
        current = cap.snapshot().count(phrase)
        if current > baseline:
            return current
        time.sleep(poll)
    return cap.snapshot().count(phrase)


def registration_sample_audio_paths(word: str, audio: Dict[str, str], out_dir: Path) -> List[str]:
    """Build slight acoustic variants for repeated voice-registration samples."""
    src = audio.get(word)
    if not src:
        return []
    result = [src]
    variant_dir = out_dir / "voice_reg_variants"
    variant_dir.mkdir(parents=True, exist_ok=True)
    for label, tempo in [("slow", "0.96"), ("fast", "1.04")]:
        dst = variant_dir / f"{safe_suite(word)}_{label}.mp3"
        if not dst.exists() or dst.stat().st_size == 0:
            p = run_cmd(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", src,
                    "-af", f"atempo={tempo}",
                    "-ar", "16000", "-ac", "1",
                    str(dst),
                ],
                out_dir / "playback.log",
                timeout=60,
            )
            if p.returncode != 0:
                continue
        result.append(str(dst))
    return result


def play_audio_file(word: str, audio_file: str, out_dir: Path) -> Dict[str, Any]:
    p = run_cmd(
        [
            "python3", str(PLAY_SCRIPT), "play", "--platform", "linux",
            "--audio-file", audio_file, "--device-key", AUDIO_KEY,
            "--repeat", "1", "--gap", "0.2",
        ],
        out_dir / "playback.log",
        timeout=90,
    )
    return {"word": word, "rc": p.returncode, "audio": audio_file, "variant": True}


def protocol_multi_mode(flags: Dict[str, Any]) -> bool:
    return bool(flags.get("multiWkeEnable") and "协议" in str(flags.get("multiWkeMode") or ""))


def protocol_wake_switch_frame(web: Dict[str, Any], wake_word: str) -> str:
    """Return the protocol frame that selects a wake word in protocol-switch mode."""
    multi = ((first_version(web).get("firmware") or {}).get("multi_wakeup") or {})
    for item in multi.get("switch_list") or []:
        if str(item.get("word") or "").strip() == wake_word and item.get("snd_protocol"):
            return str(item.get("snd_protocol"))
    return ""


def voice_reg_target_step_required(web: Dict[str, Any]) -> bool:
    """Specified learning with multiple targets requires target selection first."""
    study = ((first_version(web).get("firmware") or {}).get("study_config") or {})
    mode = str(study.get("mode") or "")
    reg_commands = [x for x in study.get("reg_commands") or [] if x.get("word") or x.get("condition")]
    return bool("指定" in mode and len(reg_commands) > 1)


def safe_suite(text: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(text or "")).strip("_")
    return value[:90] or "3021_runtime"


def generate_platform_audio(words: Sequence[str], out_dir: Path, suite: str, audio_root: str = "") -> Dict[str, str]:
    """Generate/reuse formal platform audio-synthesis assets for runtime playback."""
    unique = []
    for word in words:
        text = str(word or "").strip()
        if text and text not in unique:
            unique.append(text)
    if not unique:
        return {}

    suite = safe_suite(suite or "3021_zh_runtime")
    asset_dir = Path(audio_root) / suite if audio_root else ROOT / "assets/audio/platform_synthesis/zh" / suite
    cached: Dict[str, str] = {}
    missing: List[str] = []
    for word in unique:
        path = next((p for p in audio_candidates(word, asset_dir, "zh") if p.exists() and p.stat().st_size > 0), None)
        if path:
            cached[word] = str(path)
        else:
            missing.append(word)
    if not missing:
        return cached

    cmd = [
        sys.executable,
        str(ROOT / "scripts/py/listenai_platform_audio_synthesis_cache.py"),
        "--language", "zh",
        "--suite", suite,
        "--out-dir", str(asset_dir),
        "--project-prefix", f"ZH_AUDIO_SYNTH_{safe_suite(suite).upper()}",
    ]
    for word in unique:
        cmd.extend(["--text", word])
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "platform_audio_synthesis.log"
    with log_path.open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write("$ " + " ".join(cmd) + "\n")
        fp.flush()
        proc = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        fp.write(proc.stdout or "")
        fp.write(f"\n[exit={proc.returncode}]\n")
    if proc.returncode != 0:
        raise RuntimeError(f"platform audio synthesis failed, see {log_path}")

    audio: Dict[str, str] = {}
    for word in unique:
        path = next((p for p in audio_candidates(word, asset_dir, "zh") if p.exists() and p.stat().st_size > 0), None)
        if not path:
            # Keep the failure actionable if the platform zip used an unexpected extension/name.
            expected = asset_dir / f"{safe_audio_name(word, 'zh')}.mp3"
            raise RuntimeError(f"platform audio asset missing for {word}, expected around {expected}")
        audio[word] = str(path)
    return audio


def run_voice_registration(
    default_wake: str,
    learn: str,
    target: str,
    delete: str,
    audio: Dict[str, str],
    out_dir: Path,
    cap: SerialCapture,
    markers: Dict[str, List[str]],
    *,
    boundary_timeout: bool = False,
    sample: str = "",
    speak_target_first: bool = False,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if not (default_wake and learn and target):
        return events
    # Learning prompt audio can be around 6-7 seconds.  Wait for it to finish
    # before speaking the target word, otherwise the sample can be clipped.
    if boundary_timeout:
        events.append(play(f"{default_wake}，{learn}", audio, out_dir))
    else:
        learned = False
        learn_baseline = marker_count(cap.snapshot(), markers, learn)
        for attempt in range(2):
            events.append(play(default_wake, audio, out_dir))
            time.sleep(5.5)
            events.append(play(learn, audio, out_dir))
            learn_seen = wait_marker_count(cap, markers, learn, learn_baseline, timeout=6.0)
            if learn_seen > learn_baseline:
                learned = True
                learn_baseline = learn_seen
                break
            # The learning control word can be swallowed if the previous
            # package phase is still playing. Re-enter wake state and retry.
            time.sleep(3.0)
        if not learned:
            time.sleep(5.0)
    time.sleep(6.5)
    if speak_target_first:
        reg_info_baseline = cap.snapshot().count("Reg info")
        target_baseline = marker_count(cap.snapshot(), markers, target)
        for attempt in range(2):
            events.append(play(target, audio, out_dir))
            target_seen = wait_marker_count(cap, markers, target, target_baseline, timeout=5.0)
            reg_info_seen = wait_text_count(cap, "Reg info", reg_info_baseline, timeout=7.0)
            if target_seen > target_baseline or reg_info_seen > reg_info_baseline:
                break
            # Keep the target-selection window alive; do not restart with a
            # different target or clear registration state.
            time.sleep(2.0)
        time.sleep(4.5)
    sample_word = sample or target
    sample_paths = registration_sample_audio_paths(sample_word, audio, out_dir) or [audio.get(sample_word, "")]
    for idx in range(3):
        sample_path = sample_paths[min(idx, len(sample_paths) - 1)]
        if sample_path:
            events.append(play_audio_file(sample_word, sample_path, out_dir))
        else:
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
    ap.add_argument("--platform-audio-suite", default="", help="Use formal platform audio-synthesis assets under assets/audio/platform_synthesis/zh/<suite> instead of local TTS.")
    ap.add_argument("--platform-audio-root", default="", help="Optional root for platform audio suites. Default: assets/audio/platform_synthesis/zh.")
    ap.add_argument("--allow-special-shadow", action="store_true", help="Run even if web_config contains normal protocol commands shadowing special control words.")
    ap.add_argument("--command-probe-word", action="append", default=[], help="Run a command-level acoustic probe for the given word and skip multi/voice flows.")
    ap.add_argument("--command-probe-repeat", type=int, default=3)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    web = load_web_config(Path(args.package_zip))
    markers = word_markers(web)
    flags = feature_flags(web)
    static_findings: List[Dict[str, Any]] = []
    shadow_conflicts = special_shadow_conflicts(web)
    multi_shadow = [
        item for item in shadow_conflicts
        if any("多唤醒词切换" in special for special in item.get("specialTypes", []))
    ]
    voice_shadow = [
        item for item in shadow_conflicts
        if any("语音注册控制相关" in special for special in item.get("specialTypes", []))
    ]
    if flags["multiWkeEnable"] and str(flags.get("multiWkeMode") or "") in {"指定切换", "循环切换"} and multi_shadow:
        static_findings.append({
            "level": "FAIL",
            "code": "MULTI-WAKE-SPECIAL-SHADOW",
            "message": "normal protocol commands shadow special multi-wakeup controls",
            "words": [item["word"] for item in multi_shadow],
        })
    if flags["voiceRegEnable"] and voice_shadow:
        static_findings.append({
            "level": "FAIL",
            "code": "VOICE-REG-SPECIAL-SHADOW",
            "message": "normal protocol commands shadow special voice-registration controls",
            "words": [item["word"] for item in voice_shadow],
        })
    wakes = wake_words(web)
    default_wake = wakes[0] if wakes else "小聆小聆"
    alt_wake = next((w for w in wakes if w != default_wake), "")
    core_cmds = [w for w in args.command_probe_word if has_word(markers, w)] if args.command_probe_word else pick_commands(web, args.preferred_command)
    reg = voice_reg_words(web, args.preferred_command)
    voice_target_step = voice_reg_target_step_required(web)
    if voice_target_step and reg.get("target"):
        # Specified learning selects the target command first, then repeats the
        # same registered command.  A different alias sample can trigger
        # registration similarity/write errors on some vertical engines.
        reg["sample"] = reg["target"]
    # For specified/loop multi-wakeup flows the generic special control word is
    # the entry into switch mode. Direct "切换到X" entries in current templates
    # are ordinary protocol commands and do not prove the internal wake switch.
    switch_word = "切换唤醒词" if has_word(markers, "切换唤醒词") else ""
    restore_word = "恢复默认唤醒词" if has_word(markers, "恢复默认唤醒词") else ""
    words: List[str] = []
    boundary_mode = int(flags.get("wakeTimeout") or 0) <= 1 and not args.command_probe_word
    for word in [default_wake, *core_cmds]:
        if word and word not in words:
            words.append(word)
    if flags["multiWkeEnable"] and not boundary_mode and not args.command_probe_word:
        for word in [switch_word, alt_wake, restore_word, default_wake]:
            if word and has_word(markers, word) and word not in words:
                words.append(word)
    if flags["voiceRegEnable"] and not boundary_mode and not args.command_probe_word:
        for word in [reg.get("learn"), reg.get("target"), reg.get("sample"), reg.get("delete")]:
            if word and word not in words:
                words.append(word)
        if int(flags.get("wakeTimeout") or 0) <= 1:
            for word in [f"{default_wake}，{reg.get('learn')}", f"{default_wake}，{reg.get('delete')}"]:
                if word and word not in words:
                    words.append(word)
    if args.platform_audio_suite:
        audio = generate_platform_audio(words, out_dir / "audio", args.platform_audio_suite, args.platform_audio_root)
        audio_source = "platform_audio_synthesis"
        audio_asset_dir = str((Path(args.platform_audio_root) / safe_suite(args.platform_audio_suite)) if args.platform_audio_root else (ROOT / "assets/audio/platform_synthesis/zh" / safe_suite(args.platform_audio_suite)))
    else:
        audio = generate_audio(words, out_dir / "audio", "")
        audio_source = "local_tts"
        audio_asset_dir = str(out_dir / "audio")
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
    precheck_config: Dict[str, Any] = {}
    post_recovery_config: Dict[str, Any] = {}
    state_recovery_attempted = False
    state_recovery_ok = True
    dirty_state_reason = ""
    static_blocked = bool(static_findings) and not args.allow_special_shadow

    clear_config_sent = False
    clear_config_skipped_reason = ""
    if args.clear_config and flags["voiceRegEnable"]:
        clear_config_skipped_reason = "voice registration package keeps reg_cmd_count; post-burn clear.configall is forbidden"
    elif args.clear_config:
        clear_config_sent = cap.write_line("clear.configall", wait=2.0)
        time.sleep(2)
        ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"], out_dir)
        time.sleep(3)
        ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.on"], out_dir)
        time.sleep(8)
        loglevel_sent = cap.write_line("loglevel 4", wait=2.0) or loglevel_sent
        time.sleep(1)

    frame = protocol_probe_frame(web, args.preferred_command)
    wake_switch_frame = protocol_wake_switch_frame(web, default_wake) if protocol_multi_mode(flags) else ""
    wake_switch_sent = False
    if wake_switch_frame:
        send_protocol(args.protocol_port, protocol_baud, wake_switch_frame)
        wake_switch_sent = True
        time.sleep(2)
    send_protocol(args.protocol_port, protocol_baud, frame)
    time.sleep(2)

    precheck_config = parse_running_config(cap.snapshot())
    if precheck_config.get("wkword") == 255:
        dirty_state_reason = "wkword=255"
        if protocol_multi_mode(flags):
            # Protocol-switch packages can boot with wkword=255 by design: the
            # wake word is selected by protocol and acoustic wake can still be
            # valid.  Do not pre-block them; use later "cur wk id/not waked"
            # markers as the actual invalid-state evidence.
            dirty_state_reason = "wkword=255(protocol multi mode, defer marker check)"
        elif flags["voiceRegEnable"]:
            state_recovery_ok = False
        else:
            state_recovery_attempted = True
            clear_config_sent = cap.write_line("clear.configall", wait=2.0) or clear_config_sent
            time.sleep(2)
            ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"], out_dir)
            time.sleep(3)
            ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.on"], out_dir)
            time.sleep(8)
            loglevel_sent = cap.write_line("loglevel 4", wait=2.0) or loglevel_sent
            time.sleep(1)
            if wake_switch_frame:
                send_protocol(args.protocol_port, protocol_baud, wake_switch_frame)
                time.sleep(2)
            send_protocol(args.protocol_port, protocol_baud, frame)
            time.sleep(2)
            post_recovery_config = parse_running_config(cap.snapshot())
            state_recovery_ok = post_recovery_config.get("wkword") != 255

    events: List[Dict[str, Any]] = []
    if static_blocked or not state_recovery_ok:
        pass
    elif args.command_probe_word:
        for cmd in core_cmds:
            for _ in range(max(args.command_probe_repeat, 1)):
                events.extend(run_sequence([default_wake, cmd], audio, out_dir))
    elif boundary_mode:
        events.extend(run_boundary_timeout(default_wake, audio, out_dir))
    else:
        for cmd in core_cmds:
            events.extend(run_sequence([default_wake, cmd], audio, out_dir))
    if not static_blocked and state_recovery_ok and flags["voiceRegEnable"] and reg.get("target") and not boundary_mode and not args.command_probe_word:
        events.extend(run_voice_registration(
            default_wake,
            reg["learn"],
            reg["target"],
            reg.get("delete", ""),
            audio,
            out_dir,
            cap,
            markers,
            boundary_timeout=int(flags.get("wakeTimeout") or 0) <= 1,
            sample=reg.get("sample", ""),
            speak_target_first=voice_target_step,
        ))
    if not static_blocked and state_recovery_ok and flags["multiWkeEnable"] and switch_word and alt_wake and not boundary_mode and not args.command_probe_word:
        events.extend(run_multi_wakeup(default_wake, alt_wake, switch_word, restore_word, audio, out_dir))

    text = cap.stop()
    boot_seen = any(x in text for x in ["APP version", "Running Config", "Engine info", "ai create success"])
    protocol_rx = "[RX]" in text or frame in text
    seen = {word: marker_seen(text, markers, word) for word in words}
    if boundary_mode:
        core_required = [default_wake]
    else:
        core_required = [default_wake, *core_cmds]
    core_missing = [w for w in core_required if w and not seen.get(w)]
    multi_required: List[str] = []
    if flags["multiWkeEnable"] and alt_wake:
        mode = str(flags.get("multiWkeMode") or "")
        if "循环" in mode:
            multi_required = [alt_wake]
        elif "指定" in mode and has_word(markers, "切换唤醒词"):
            multi_required = [switch_word or "切换唤醒词", alt_wake]
        elif "协议" in mode:
            # Protocol switch packages are covered by protocol RX plus the
            # ordinary wake/command chain; no UI voice switch command is expected.
            multi_required = []
    multi_missing = [w for w in multi_required if w and not seen.get(w)]
    voice_markers = ["Reg info", "cmdlist get", "wIvwRegist", "reg again", "reg cmd over", "save new voice.bin", "reg failed", "voice reg"]
    voice_activity = any(m in text for m in voice_markers)
    voice_success_markers = [
        "reg cmd over success",
        "save new voice.bin",
        "reg auto next",
        "reg over!",
        "reg status: 3",
        "wIvwRegistArbitrate success",
    ]
    voice_failure_markers = {
        "regFailed": text.count("reg failed"),
        "regLengthError": text.count("reg length error"),
        "wregWriteFailed": text.count("wreg write failed") + text.count("wIvwRegistWrite fail"),
        "errorCntExceeded": text.count("error cnt >"),
    }
    voice_success = any(m in text for m in voice_success_markers) and not any(voice_failure_markers.values())
    timeout_count = text.count("Wk timeout")
    boundary_timeout_observed = boundary_mode and seen.get(default_wake) and timeout_count > 0
    command_probe_counts = {word: marker_count(text, markers, word) for word in core_cmds} if args.command_probe_word else {}
    invalid_wake_state_markers = {
        "curWkMismatch": text.count("cur wk id:"),
        "notWaked": text.count("not waked!"),
    }
    if protocol_multi_mode(flags) and precheck_config.get("wkword") == 255 and any(invalid_wake_state_markers.values()):
        state_recovery_ok = False
        dirty_state_reason = "wkword=255 with cur wk id/not waked markers"
    summary = {
        "packageZip": args.package_zip,
        "outDir": str(out_dir),
        "audioKey": AUDIO_KEY,
        "audioSource": audio_source,
        "audioAssetDir": audio_asset_dir,
        "probeRc": probe.returncode,
        "serialError": cap.error,
        "loglevelSent": loglevel_sent,
        "clearConfigSent": clear_config_sent,
        "clearConfigSkippedReason": clear_config_skipped_reason,
        "staticFindings": static_findings,
        "staticBlocked": static_blocked,
        "precheckConfig": precheck_config,
        "stateRecoveryAttempted": state_recovery_attempted,
        "stateRecoveryOk": state_recovery_ok,
        "stateDirtyReason": dirty_state_reason,
        "invalidWakeStateMarkers": invalid_wake_state_markers,
        "postRecoveryConfig": post_recovery_config,
        "boundaryMode": boundary_mode,
        "commandProbeWords": args.command_probe_word,
        "commandProbeCounts": command_probe_counts,
        "serialBytes": len(text.encode("utf-8", errors="ignore")),
        "bootSeen": boot_seen,
        "protocolRxSeen": protocol_rx,
        "protocolFrame": frame,
        "protocolWakeSwitchFrame": wake_switch_frame,
        "protocolWakeSwitchSent": wake_switch_sent,
        "protocolBaud": protocol_baud,
        "flags": flags,
        "defaultWake": default_wake,
        "coreCommands": core_cmds,
        "voiceRegWords": reg,
        "voiceRegTargetStepRequired": voice_target_step,
        "wordSeen": seen,
        "coreMissing": core_missing,
        "multiMissing": multi_missing,
        "boundaryTimeoutObserved": boundary_timeout_observed,
        "voiceRegActivitySeen": voice_activity,
        "voiceRegSuccessSeen": voice_success,
        "voiceRegFailureMarkers": voice_failure_markers,
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
    pass_multi = bool(boundary_mode or args.command_probe_word or (not flags["multiWkeEnable"]) or not multi_missing)
    pass_voice = bool(boundary_mode or args.command_probe_word or (not flags["voiceRegEnable"]) or voice_success)
    if static_blocked:
        summary["verdict"] = "CONFIG_FAIL"
    elif not state_recovery_ok:
        summary["verdict"] = "ENV_STATE_DIRTY"
    else:
        summary["verdict"] = "PASS" if pass_core and pass_multi and pass_voice else "FAIL"
    summary["gates"] = {"core": pass_core, "multi": pass_multi, "voice": pass_voice}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["verdict"] == "PASS":
        return 0
    if summary["verdict"] == "CONFIG_FAIL":
        return 3
    if summary["verdict"] == "ENV_STATE_DIRTY":
        return 4
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
