#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Sequence

try:
    import serial
except Exception as exc:
    raise SystemExit(f"pyserial required: {exc}")

from listenai_task_support import load_global_tts_config
from voiceTestLite import tts_generate

ROOT = Path(__file__).resolve().parents[2]
CTRL_SCRIPT = ROOT / "scripts/burn/sudo_ctrl.py"
PLAY_SCRIPT = Path("/home/bszheng/.codex/skills/listenai-play/scripts/listenai_play.py")
AUDIO_KEY = "VID_8765&PID_5678:USB_0_1_3_1_0"
DEFAULT_FRAME = "A5 FA 00 81 02 00 22 FB"
AUDIO_ALIASES = {
    # Tea canonical intents such as "开机" are short and synthetic TTS is easy
    # to miss; use configured grammar synonyms while asserting canonical intent.
    "tea": {
        "开机": "打开茶吧机",
        "关机": "关闭茶吧机",
        "开始烧水": "打开烧水",
        "最大音量": "音量最大",
    },
    "heater": {
        "最大音量": "音量最大",
    },
    # "开机" and "关机" are easy to cross-recognize with synthetic TTS. Use a
    # configured grammar synonym while still asserting the canonical intent.
    "table_heater": {"开机": "打开取暖桌"},
}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hex_bytes(frame: str) -> bytes:
    return bytes(int(x, 16) for x in re.findall(r"[0-9A-Fa-f]{2}", frame))


def run_cmd(args: Sequence[str], log_path: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(list(args), cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="ignore") as fp:
            fp.write("$ " + " ".join(map(str, args)) + "\n")
            fp.write(p.stdout or "")
            fp.write(f"\n[exit={p.returncode}]\n")
    return p


def ctrl(port: str, baud: int, commands: Sequence[str], out_dir: Path) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        ["python3", str(CTRL_SCRIPT), "send", port, str(baud), "--delay-ms", "300", *commands],
        out_dir / "control.log",
        timeout=60,
    )


class SerialCapture:
    def __init__(self, port: str, baud: int, log_path: Path):
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self._stop = threading.Event()
        self._chunks: List[bytes] = []
        self._thread: threading.Thread | None = None
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self.error = ""

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with serial.Serial(self.port, self.baud, timeout=0.2) as ser, self.log_path.open("ab") as fp:
                with self._lock:
                    self._ser = ser
                try:
                    while not self._stop.is_set():
                        data = ser.read(4096)
                        if data:
                            self._chunks.append(data)
                            fp.write(data)
                            fp.flush()
                finally:
                    with self._lock:
                        self._ser = None
        except Exception as exc:
            self.error = str(exc)

    def write_line(self, line: str, wait: float = 1.0) -> bool:
        deadline = time.time() + max(wait, 0.1)
        while time.time() < deadline:
            with self._lock:
                ser = self._ser
                if ser is not None:
                    ser.write((line + "\r\n").encode("ascii"))
                    ser.flush()
                    return True
            time.sleep(0.05)
        return False

    def stop(self) -> str:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        raw = b"".join(self._chunks)
        return raw.decode("utf-8", errors="ignore")


def audio_text_for(target: str, word: str) -> str:
    return str(AUDIO_ALIASES.get(target, {}).get(word, word))


def generate_audio(words: Sequence[str], out_dir: Path, target: str = "") -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tts = load_global_tts_config()
    if not tts.get("app_id") or not tts.get("api_key"):
        raise RuntimeError("missing tts_config app_id/api_key")
    audio: Dict[str, str] = {}
    for word in words:
        text = audio_text_for(target, word)
        safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", f"{word}_{text}")[:80]
        path = out_dir / f"{safe}.mp3"
        if not path.exists() or path.stat().st_size == 0:
            tts_generate(text, str(path), tts)
        audio[word] = str(path)
    return audio


def play(word: str, audio: Dict[str, str], out_dir: Path, repeat: int = 1, gap: float = 0.2) -> Dict[str, Any]:
    p = run_cmd(
        [
            "python3", str(PLAY_SCRIPT), "play", "--platform", "linux",
            "--audio-file", audio[word], "--device-key", AUDIO_KEY,
            "--repeat", str(repeat), "--gap", str(gap),
        ],
        out_dir / "playback.log",
        timeout=90,
    )
    return {"word": word, "rc": p.returncode, "audio": audio[word]}


def load_web_config(package_zip: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(package_zip) as zf:
        name = next(n for n in zf.namelist() if n.endswith("Standard_product/web_config.json"))
        return json.loads(zf.read(name).decode("utf-8"))


def first_version(web: Dict[str, Any]) -> Dict[str, Any]:
    return (web.get("_ver_list") or [{}])[0]


def protocol_baud_from_web_config(web: Dict[str, Any], fallback: int = 9600) -> int:
    version = first_version(web)
    uart = ((version.get("firmware") or {}).get("uart_config") or {})
    for value in [uart.get("uport_baud"), (version.get("firmware") or {}).get("general_config", {}).get("baud")]:
        try:
            baud = int(value)
        except Exception:
            continue
        if baud > 0:
            return baud
    return fallback


def pinyin_variants(pinyin: str) -> List[str]:
    text = str(pinyin or "").strip()
    if not text:
        return []
    return list({text, text.replace("-", " "), text.replace(" ", "-"), text.replace("-", "")})


def word_markers(web: Dict[str, Any]) -> Dict[str, List[str]]:
    markers: Dict[str, List[str]] = {}
    for item in first_version(web).get("asr_cmds") or []:
        word = str(item.get("intent") or "").strip()
        if not word:
            continue
        vals = [word]
        vals.extend(pinyin_variants(str(item.get("pinyin") or "")))
        for expand in item.get("expand_words") or []:
            vals.extend(pinyin_variants(str(expand.get("keyword_pinyin") or "")))
            keyword = str(expand.get("keyword") or "").strip()
            if keyword:
                vals.append(keyword)
        for group in item.get("grammar_words") or []:
            for word_item in group.get("word") or []:
                for keyword in str(word_item.get("value") or "").split("|"):
                    keyword = keyword.strip()
                    if keyword:
                        vals.append(keyword)
        markers[word] = [v for v in vals if v]
    for item in first_version(web).get("asr_wakeup") or []:
        word = str(item.get("intent") or "").strip()
        if not word:
            continue
        vals = markers.setdefault(word, [word])
        vals.extend(pinyin_variants(str(item.get("pinyin") or "")))
        for expand in item.get("expand_words") or []:
            vals.extend(pinyin_variants(str(expand.get("keyword_pinyin") or "")))
            keyword = str(expand.get("keyword") or "").strip()
            if keyword:
                vals.append(keyword)
        markers[word] = list(dict.fromkeys(v for v in vals if v))
    return markers


def marker_seen(text: str, markers: Dict[str, List[str]], word: str) -> bool:
    vals = markers.get(word) or [word]
    return any(v and v in text for v in vals)


def send_protocol(port: str, baud: int, frame: str) -> None:
    with serial.Serial(port, baud, timeout=1) as ser:
        ser.write(hex_bytes(frame))
        ser.flush()


def wake_words_from_web(web: Dict[str, Any]) -> List[str]:
    words: List[str] = []
    for item in first_version(web).get("asr_wakeup") or []:
        word = str(item.get("intent") or "").strip()
        if word and word not in words:
            words.append(word)
    return words


def voice_reg_learn_words_from_web(web: Dict[str, Any]) -> List[str]:
    study = ((first_version(web).get("firmware") or {}).get("study_config") or {})
    words: List[str] = []
    for item in study.get("reg_commands") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("condition") or "").strip()
        if word and word not in words:
            words.append(word)
    return words


def smoke_sequence(target: str, web: Dict[str, Any]) -> List[List[str]]:
    wake_words = wake_words_from_web(web)
    reg_words = voice_reg_learn_words_from_web(web)
    default_wake = "小聆小聆"
    alternate_wake = next((w for w in wake_words if w != default_wake and w != "查询唤醒词"), "")
    if target == "tea":
        alternate_wake = alternate_wake or "茶吧管家"
        return [
            ["小聆小聆", "开机"],
            ["小聆小聆", "开始烧水"],
            ["小聆小聆", "最大音量"],
            [default_wake, "切换唤醒词", alternate_wake, alternate_wake],
            [alternate_wake, "恢复默认唤醒词", default_wake],
        ]
    if target == "curtain":
        alternate_wake = alternate_wake or "窗帘管家"
        learn_word = reg_words[0] if reg_words else "打开窗帘"
        return [
            ["小聆小聆", "打开窗帘"],
            ["小聆小聆", "关闭窗帘"],
            ["小聆小聆", "音量最大"],
            [default_wake, "切换唤醒词", alternate_wake, alternate_wake],
            [alternate_wake, "恢复默认唤醒词", default_wake],
            ["小聆小聆", "学习命令词", learn_word, learn_word],
            ["小聆小聆", "删除命令词", learn_word],
        ]
    if target == "fan":
        alternate_wake = alternate_wake or "风扇管家"
        return [
            ["小聆小聆", "打开风扇"],
            ["小聆小聆", "关闭风扇"],
            ["小聆小聆", "最大音量"],
            [default_wake, "切换唤醒词", alternate_wake, alternate_wake],
            [alternate_wake, "恢复默认唤醒词", default_wake],
        ]
    if target == "heater":
        alternate_wake = alternate_wake or "暖风管家"
        return [
            ["小聆小聆", "打开取暖器"],
            ["小聆小聆", "关闭取暖器"],
            ["小聆小聆", "最大音量"],
            [default_wake, "切换唤醒词", alternate_wake, alternate_wake],
            [alternate_wake, "恢复默认唤醒词", default_wake],
        ]
    if target == "table_heater":
        alternate_wake = alternate_wake or "暖桌管家"
        learn_word = reg_words[0] if reg_words else "打开下暖"
        return [
            ["小聆小聆", "开机"],
            ["小聆小聆", "关机"],
            ["小聆小聆", "最大音量"],
            [default_wake, "切换唤醒词", alternate_wake, alternate_wake],
            [alternate_wake, "恢复默认唤醒词", default_wake],
            ["小聆小聆", "学习命令词", learn_word, learn_word],
            ["小聆小聆", "删除命令词", learn_word],
        ]
    raise ValueError(f"unsupported target: {target}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=["tea", "curtain", "fan", "heater", "table_heater"])
    ap.add_argument("--package-zip", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--log-port", default="/dev/ttyACM0")
    ap.add_argument("--log-baud", type=int, default=115200)
    ap.add_argument("--protocol-port", default="/dev/ttyACM2")
    ap.add_argument("--protocol-baud", type=int, default=0, help="0=read uport_baud from package web_config")
    ap.add_argument("--ctrl-port", default="/dev/ttyACM4")
    ap.add_argument("--ctrl-baud", type=int, default=115200)
    ap.add_argument("--power-switch", default="uut-switch1")
    ap.add_argument("--boot-switch", default="uut-switch3")
    ap.add_argument("--protocol-switch", default="uut-switch2")
    ap.add_argument("--probe-frame", default=DEFAULT_FRAME)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    web = load_web_config(Path(args.package_zip))
    protocol_baud = int(args.protocol_baud or 0) or protocol_baud_from_web_config(web, 9600)
    markers = word_markers(web)
    sequences = smoke_sequence(args.target, web)
    words: List[str] = []
    for seq in sequences:
        for w in seq:
            if w not in words:
                words.append(w)
    audio = generate_audio(words, out_dir / "audio", args.target)
    audio_texts = {word: audio_text_for(args.target, word) for word in words}

    probe = run_cmd(["python3", str(PLAY_SCRIPT), "probe", "--platform", "linux", "--device-key", AUDIO_KEY], out_dir / "playback.log", timeout=30)

    cap = SerialCapture(args.log_port, args.log_baud, out_dir / "serial_raw.log")
    cap.start()
    time.sleep(0.5)
    ctrl(
        args.ctrl_port,
        args.ctrl_baud,
        [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"],
        out_dir,
    )
    time.sleep(3)
    ctrl(args.ctrl_port, args.ctrl_baud, [f"{args.protocol_switch}.on"], out_dir)
    time.sleep(8)
    loglevel_sent = cap.write_line("loglevel 4", wait=2.0)
    time.sleep(1)
    send_protocol(args.protocol_port, protocol_baud, args.probe_frame)
    time.sleep(2)

    events: List[Dict[str, Any]] = []
    for seq in sequences:
        for idx, word in enumerate(seq):
            events.append(play(word, audio, out_dir))
            # The device plays an acknowledgement after wakeup; wait long enough
            # before sending the command audio or short commands are easy to mask.
            time.sleep(2.8 if idx == 0 else 3.0)
        if args.target == "table_heater" and "学习命令词" in seq:
            time.sleep(8)
        elif args.target == "table_heater" and "删除命令词" in seq:
            time.sleep(6)
        else:
            time.sleep(3)

    text = cap.stop()
    boot_seen = any(x in text for x in ["APP version", "Running Config", "Engine info", "ai create success"])
    protocol_rx = "[RX]" in text or args.probe_frame in text
    seen = {word: marker_seen(text, markers, word) for word in words}
    reg_seen = any(x in text for x in ["Reg info", "reg success", "reg failed", "voice reg", "VOICE_REGISTER", "学习命令词", "删除命令词"])
    required_words = list(words)
    missing_required = [word for word in required_words if not seen.get(word)]
    summary = {
        "target": args.target,
        "packageZip": args.package_zip,
        "outDir": str(out_dir),
        "audioKey": AUDIO_KEY,
        "probeRc": probe.returncode,
        "serialError": cap.error,
        "loglevelSent": loglevel_sent,
        "serialBytes": len(text.encode("utf-8", errors="ignore")),
        "bootSeen": boot_seen,
        "protocolRxSeen": protocol_rx,
        "protocolBaud": protocol_baud,
        "wordSeen": seen,
        "audioTexts": audio_texts,
        "requiredWords": required_words,
        "missingRequiredWords": missing_required,
        "voiceRegActivitySeen": reg_seen,
        "playEvents": events,
        "logMarkers": {
            "wakeupJson": text.count("Wakeup:"),
            "txCount": text.count("[TX]"),
            "rxCount": text.count("[RX]"),
            "algoRestart": text.count("algo restart"),
            "deviceBootHeaders": text.count("Running Config"),
        },
    }
    summary["verdict"] = "PASS" if boot_seen and protocol_rx and not missing_required else "FAIL"
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
