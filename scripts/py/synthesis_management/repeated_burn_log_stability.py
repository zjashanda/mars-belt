#!/usr/bin/env python3
"""Repeat broadcast package -> burn -> serial-log health checks on a local 3021 bench."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import serial
import serial.tools.list_ports

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import ListenAISynthesisClient


ROOT = Path(__file__).resolve().parents[3]
CTRL_SUCCESS_RE = re.compile(r"switch_idx(\d+)_set:\s*([01])")
LOG_MARKERS = (
    "VER:",
    "SDK:",
    "APP version",
    "Running Config",
    "root:/$",
    "PA_MGR",
    "mini-player",
    "play id",
    "[RX]",
)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_ports() -> List[Dict[str, str]]:
    return [
        {
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
        }
        for p in serial.tools.list_ports.comports()
        if "ttyACM" in p.device or "COM" in p.device
    ]


def run_cmd(cmd: List[str], log_path: Path, *, cwd: Path = ROOT, timeout: Optional[int] = None, env: Optional[Dict[str, str]] = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as fp:
        fp.write(f"$ {' '.join(cmd)}\n")
        fp.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                fp.write(line)
                fp.flush()
        except Exception:
            proc.kill()
            raise
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            fp.write(f"\n[TIMEOUT] killed after {timeout}s\n")
            return 124


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_platform_records(manifest: Dict[str, Any], cleanup_log: Path) -> None:
    token = resolve_listenai_token(persist=False)
    client = ListenAISynthesisClient(token, timeout=60)
    prod_id = str(manifest.get("productId") or "")
    release_id = str(manifest.get("releaseId") or "")
    with cleanup_log.open("w", encoding="utf-8") as fp:
        fp.write(f"[{now()}] cleanup start product={prod_id} release={release_id}\n")
        try:
            if release_id:
                resp = client.post_json("/biz/broadcastrelease/delete", [{"id": release_id}])
                fp.write("release delete: " + json.dumps(resp, ensure_ascii=False) + "\n")
            if prod_id:
                resp = client.post_json("/biz/broadcast/delete", [{"id": prod_id}])
                fp.write("product delete: " + json.dumps(resp, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            fp.write(f"cleanup failed: {exc!r}\n")


def _read_until_prompt(port: serial.Serial, timeout_s: float) -> str:
    chunks: List[str] = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            waiting = port.in_waiting
            data = port.read(waiting or 1)
        except Exception as exc:  # noqa: BLE001
            chunks.append(f"\n[SERIAL-ERROR] {exc!r}\n")
            break
        if data:
            text = data.decode("utf-8", errors="replace")
            chunks.append(text)
            joined = "".join(chunks)
            if "root:/$" in joined or "root:/" in joined:
                break
        time.sleep(0.03)
    return "".join(chunks)


def send_ctrl(ctrl_port: str, ctrl_baud: int, commands: List[str], log_path: Path, delay_s: float = 0.35) -> bool:
    ok = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fp:
        fp.write(f"[{now()}] ctrl open {ctrl_port}@{ctrl_baud}\n")
        try:
            with serial.Serial(ctrl_port, ctrl_baud, timeout=0.1, write_timeout=1.0) as port:
                time.sleep(0.2)
                for idx, cmd in enumerate(commands, start=1):
                    fp.write(f"[{now()}] CTRL SEND {idx}/{len(commands)} {cmd}\n")
                    try:
                        port.reset_input_buffer()
                    except Exception:
                        pass
                    port.write((cmd + "\r\n").encode("ascii"))
                    port.flush()
                    time.sleep(delay_s)
                    raw = _read_until_prompt(port, 2.0)
                    fp.write(f"[{now()}] CTRL RAW {idx}/{len(commands)} {raw!r}\n")
                    if "cmd not found" in raw.lower() or "unknown" in raw.lower():
                        ok = False
                    if cmd.startswith("uut-switch"):
                        match = re.fullmatch(r"uut-switch(\d+)\.(on|off)", cmd.strip())
                        if match:
                            expected = f"switch_idx{match.group(1)}_set: {'1' if match.group(2) == 'on' else '0'}"
                            if expected not in raw:
                                ok = False
                fp.write(f"[{now()}] ctrl {'OK' if ok else 'FAIL'}\n")
        except Exception as exc:  # noqa: BLE001
            fp.write(f"[{now()}] ctrl exception: {exc!r}\n")
            ok = False
    return ok


class SerialCapture:
    def __init__(self, port: str, baud: int, out_path: Path):
        self.port_name = port
        self.baud = baud
        self.out_path = out_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional[serial.Serial] = None
        self.error = ""
        self.bytes_read = 0

    def start(self) -> bool:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._serial = serial.Serial(self.port_name, self.baud, timeout=0.1, write_timeout=1.0)
            try:
                self._serial.reset_input_buffer()
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            self.error = repr(exc)
            self.out_path.write_text(f"[{now()}] open failed: {self.error}\n", encoding="utf-8")
            return False
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return True

    def _reader(self) -> None:
        assert self._serial is not None
        with self.out_path.open("a", encoding="utf-8", errors="replace") as fp:
            fp.write(f"[{now()}] capture start {self.port_name}@{self.baud}\n")
            while not self._stop.is_set():
                try:
                    data = self._serial.read(self._serial.in_waiting or 1)
                except Exception as exc:  # noqa: BLE001
                    self.error = repr(exc)
                    fp.write(f"\n[{now()}] read error: {self.error}\n")
                    break
                if data:
                    self.bytes_read += len(data)
                    fp.write(data.decode("utf-8", errors="replace"))
                    fp.flush()
                time.sleep(0.02)
            fp.write(f"\n[{now()}] capture stop bytes={self.bytes_read}\n")

    def write(self, data: bytes) -> bool:
        try:
            if not self._serial:
                return False
            self._serial.write(data)
            self._serial.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = repr(exc)
            return False

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass


def send_protocol(protocol_port: str, protocol_baud: int, frame: str, log_path: Path) -> bool:
    try:
        payload = bytes.fromhex(frame.replace(" ", ""))
        with serial.Serial(protocol_port, protocol_baud, timeout=0.2, write_timeout=1.0) as port:
            port.write(payload)
            port.flush()
        log_path.write_text(f"[{now()}] protocol send {protocol_port}@{protocol_baud}: {frame}\n", encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001
        log_path.write_text(f"[{now()}] protocol send failed: {exc!r}\n", encoding="utf-8")
        return False


def inspect_log(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    markers = [m for m in LOG_MARKERS if m in text]
    rx = "[RX]" in text or "A5 FA" in text
    play = "play id" in text or "mini-player" in text
    boot = any(m in text for m in ("VER:", "SDK:", "APP version", "Running Config", "root:/$"))
    return {
        "bytes": path.stat().st_size if path.exists() else 0,
        "markers": markers,
        "bootMarker": boot,
        "protocolOrPlayMarker": rx or play,
        "rxMarker": rx,
        "playMarker": play,
    }


def scan_candidate_log_ports(candidates: List[str], baud: int, out_dir: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for port in candidates:
        path = out_dir / f"scan_{Path(port).name}_{baud}.log"
        cap = SerialCapture(port, baud, path)
        opened = cap.start()
        if opened:
            cap.write(b"\r\n")
            time.sleep(2.0)
            cap.stop()
        item = {"port": port, "opened": opened, "error": cap.error, **inspect_log(path)}
        results.append(item)
    return results


def health_check(args: argparse.Namespace, round_dir: Path) -> Dict[str, Any]:
    health_dir = round_dir / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    before_ports = list_ports()
    (health_dir / "ports_before.json").write_text(json.dumps(before_ports, ensure_ascii=False, indent=2), encoding="utf-8")
    candidate_ports = sorted(
        {
            p["device"]
            for p in before_ports
            if "ttyACM" in p["device"] and p["device"] != args.ctrl_port
        }
    )
    if args.log_port not in candidate_ports:
        candidate_ports.append(args.log_port)

    # Capture all candidate runtime ports during the power edge.  Capturing only
    # the configured log port can miss the real root cause: USB serial roles may
    # swap while /dev/ttyACM numbers stay present.
    boot_caps: Dict[str, SerialCapture] = {}
    for port in candidate_ports:
        cap_path = health_dir / f"boot_capture_{Path(port).name}.log"
        cap = SerialCapture(port, args.log_baud, cap_path)
        cap.start()
        boot_caps[port] = cap
    ctrl_ok = send_ctrl(
        args.ctrl_port,
        args.ctrl_baud,
        [f"{args.protocol_switch}.off", f"{args.boot_switch}.off", f"{args.power_switch}.off", f"{args.power_switch}.on"],
        health_dir / "ctrl_power_cycle.log",
    )
    time.sleep(3.0)
    ctrl_ok = send_ctrl(
        args.ctrl_port,
        args.ctrl_baud,
        [f"{args.protocol_switch}.on"],
        health_dir / "ctrl_protocol_restore.log",
    ) and ctrl_ok
    time.sleep(args.boot_capture_seconds / 2)
    if args.log_port in boot_caps:
        boot_caps[args.log_port].write(b"loglevel 4\r\n")
    time.sleep(args.boot_capture_seconds / 2)
    for cap in boot_caps.values():
        cap.stop()

    boot_inspections: Dict[str, Dict[str, Any]] = {}
    for port in candidate_ports:
        path = health_dir / f"boot_capture_{Path(port).name}.log"
        item = inspect_log(path)
        item["opened"] = not bool(boot_caps[port].error) or path.exists()
        item["error"] = boot_caps[port].error
        item["path"] = str(path)
        boot_inspections[port] = item

    detected_log_port = ""
    for port, item in sorted(boot_inspections.items(), key=lambda kv: (not kv[1].get("bootMarker"), -int(kv[1].get("bytes", 0)))):
        if item.get("bootMarker") or item.get("markers"):
            detected_log_port = port
            break
    capture_port = detected_log_port or args.log_port

    proto_log_path = health_dir / f"protocol_capture_{Path(capture_port).name}.log"
    proto_cap = SerialCapture(capture_port, args.log_baud, proto_log_path)
    proto_opened = proto_cap.start()
    if proto_opened:
        proto_cap.write(b"loglevel 4\r\n")
        time.sleep(0.5)
    proto_ok = send_protocol(args.protocol_port, args.protocol_baud, args.probe_frame, health_dir / "protocol_send.log")
    time.sleep(args.protocol_capture_seconds)
    if proto_opened:
        proto_cap.stop()
    after_ports = list_ports()
    (health_dir / "ports_after.json").write_text(json.dumps(after_ports, ensure_ascii=False, indent=2), encoding="utf-8")
    primary_boot = boot_inspections.get(args.log_port, {})
    protocol_inspected = inspect_log(proto_log_path)
    candidates = sorted({p["device"] for p in after_ports if "ttyACM" in p["device"]})
    scan: List[Dict[str, Any]] = []
    if not primary_boot.get("bootMarker") and not detected_log_port:
        scan = scan_candidate_log_ports(candidates, args.log_baud, health_dir)
    configured_ok = bool(primary_boot.get("bootMarker")) and (
        capture_port == args.log_port and protocol_inspected["protocolOrPlayMarker"]
    )
    moved_ok = bool(detected_log_port and detected_log_port != args.log_port)
    result = {
        "logPort": args.log_port,
        "logBaud": args.log_baud,
        "detectedLogPort": detected_log_port,
        "logPortMoved": moved_ok,
        "logOpened": bool(primary_boot),
        "logOpenError": str(primary_boot.get("error") or ""),
        "ctrlPowerCycleOk": ctrl_ok,
        "protocolSendOk": proto_ok,
        "portsBefore": before_ports,
        "portsAfter": after_ports,
        "bootInspections": boot_inspections,
        "protocolCapturePort": capture_port,
        "protocolLogInspection": protocol_inspected,
        "logInspection": {
            **primary_boot,
            "protocol": protocol_inspected if capture_port == args.log_port else {},
        },
        "candidateScan": scan,
        "verdict": "PASS" if ctrl_ok and proto_ok and configured_ok else ("MOVED" if ctrl_ok and proto_ok and moved_ok else "FAIL"),
    }
    (health_dir / "health_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repeat 3021 broadcast package/burn/log stability checks.")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--modes", default="active-auto,passive-full", help="Comma-separated modes for round-robin packaging")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--ctrl-port", default="/dev/ttyACM4")
    parser.add_argument("--ctrl-baud", type=int, default=115200)
    parser.add_argument("--burn-port", default="/dev/ttyACM0")
    parser.add_argument("--burn-baud", type=int, default=460800)
    parser.add_argument("--log-port", default="/dev/ttyACM0")
    parser.add_argument("--log-baud", type=int, default=115200)
    parser.add_argument("--protocol-port", default="/dev/ttyACM2")
    parser.add_argument("--protocol-baud", type=int, default=9600)
    parser.add_argument("--power-switch", default="uut-switch1", help="Control switch used for target power; current 3021 bench uses uut-switch1")
    parser.add_argument("--boot-switch", default="uut-switch3", help="Control switch used for boot strap; current 3021 bench uses uut-switch3")
    parser.add_argument("--protocol-switch", default="uut-switch2", help="Protocol gate switch; current 3021 bench must disconnect it before every power edge")
    parser.add_argument("--probe-frame", default="A5 FA 00 81 02 00 22 FB")
    parser.add_argument("--package-timeout-sec", type=int, default=1200)
    parser.add_argument("--boot-capture-seconds", type=float, default=16.0)
    parser.add_argument("--protocol-capture-seconds", type=float, default=6.0)
    parser.add_argument("--keep-platform-records", action="store_true")
    parser.add_argument("--skip-burn", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{stamp}-repeated-burn-log-stability"
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        raise SystemExit("no modes specified")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts" / "py")
    summary: Dict[str, Any] = {
        "startedAt": now(),
        "outDir": str(out_dir),
        "roundsRequested": args.rounds,
        "config": vars(args),
        "rounds": [],
    }
    for index in range(1, args.rounds + 1):
        mode = modes[(index - 1) % len(modes)]
        round_dir = out_dir / f"round_{index:02d}_{mode}"
        package_dir = round_dir / "package"
        round_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{now()}] round {index}/{args.rounds} mode={mode}", flush=True)
        package_cmd = [
            sys.executable,
            "-m",
            "synthesis_management.broadcast_device_matrix",
            "--mode",
            mode,
            "--out-dir",
            str(package_dir),
            "--no-persist-token",
        ]
        if args.keep_platform_records:
            package_cmd.append("--keep-platform-records")
        package_rc = run_cmd(package_cmd, round_dir / "package_command.log", timeout=args.package_timeout_sec, env=env)
        manifest_path = package_dir / "manifest.json"
        manifest: Dict[str, Any] = read_json(manifest_path) if manifest_path.exists() else {}
        sdk_zip = str(manifest.get("sdkZip") or "")
        if manifest and not args.keep_platform_records:
            cleanup_platform_records(manifest, round_dir / "platform_cleanup.log")
        burn_rc: Optional[int] = None
        if package_rc == 0 and sdk_zip and not args.skip_burn:
            burn_cmd = [
                sys.executable,
                "scripts/mars_belt.py",
                "burn",
                "--package-zip",
                sdk_zip,
                "--ctrl-port",
                args.ctrl_port,
                "--burn-port",
                args.burn_port,
                "--ctrl-baud",
                str(args.ctrl_baud),
                "--baud",
                str(args.burn_baud),
                "--runtime-log-port",
                args.log_port,
                "--runtime-log-baud",
                str(args.log_baud),
                "--max-retry",
                "1",
                "--boot-wait-seconds",
                "8",
                "--boot-switch",
                args.boot_switch,
                "--power-switch",
                args.power_switch,
                "--protocol-switch",
                args.protocol_switch,
            ]
            burn_rc = run_cmd(burn_cmd, round_dir / "burn_command.log", timeout=420, env=env)
        health = health_check(args, round_dir) if package_rc == 0 and sdk_zip and not args.skip_burn else {}
        round_result = {
            "round": index,
            "mode": mode,
            "packageRc": package_rc,
            "burnRc": burn_rc,
            "sdkZip": sdk_zip,
            "manifest": str(manifest_path) if manifest_path.exists() else "",
            "health": health,
            "verdict": "PASS" if package_rc == 0 and (burn_rc == 0 if burn_rc is not None else args.skip_burn) and health.get("verdict") == "PASS" else "FAIL",
        }
        summary["rounds"].append(round_result)
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["finishedAt"] = now()
    total = len(summary["rounds"])
    passed = sum(1 for r in summary["rounds"] if r.get("verdict") == "PASS")
    summary["result"] = {"total": total, "passed": passed, "failed": total - passed}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 3021 重复打包-烧录-日志口稳定性验证",
        "",
        f"- startedAt: `{summary['startedAt']}`",
        f"- finishedAt: `{summary['finishedAt']}`",
        f"- rounds: `{passed}/{total}`",
        f"- log port: `{args.log_port}@{args.log_baud}`",
        f"- protocol port: `{args.protocol_port}@{args.protocol_baud}`",
        "",
        "| round | mode | package | burn | health | log bytes | markers | verdict |",
        "|---:|---|---:|---:|---|---:|---|---|",
    ]
    for r in summary["rounds"]:
        h = r.get("health") or {}
        ins = h.get("logInspection") or {}
        lines.append(
            f"| {r.get('round')} | {r.get('mode')} | {r.get('packageRc')} | {r.get('burnRc')} | {h.get('verdict','-')} | "
            f"{ins.get('bytes',0)} | {', '.join(ins.get('markers') or [])} | {r.get('verdict')} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"outDir": str(out_dir), "passed": passed, "total": total}, ensure_ascii=False, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
