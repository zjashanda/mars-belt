#!/usr/bin/env python3
"""Sudo wrapper for serial port control operations."""
import re
import sys
import time

import serial


PROMPT_MARKERS = ("root:/$", "root:/")
FAILURE_MARKERS = (
    "cmd not found",
    "unknown command",
    "not support",
    "invalid command",
)


def _drain(port):
    try:
        waiting = port.in_waiting
    except Exception:
        waiting = 0
    if waiting:
        return port.read(waiting)
    return b""


def _read_until_prompt(port, prompt_timeout_ms):
    chunks = []
    deadline = time.time() + max(float(prompt_timeout_ms or 0) / 1000.0, 0.2)
    prompt_seen = False
    while time.time() < deadline:
        try:
            waiting = port.in_waiting
        except Exception:
            waiting = 0
        if waiting:
            chunk = port.read(waiting)
            if chunk:
                text = chunk.decode("utf-8", errors="replace")
                chunks.append(text)
                if any(marker in text for marker in PROMPT_MARKERS) or any(
                    marker in "".join(chunks) for marker in PROMPT_MARKERS
                ):
                    prompt_seen = True
                    break
        time.sleep(0.05)
    return "".join(chunks), prompt_seen


def _expected_success_marker(command):
    match = re.fullmatch(r"uut-switch(\d+)\.(on|off)", command.strip())
    if not match:
        return ""
    index = match.group(1)
    state = "1" if match.group(2) == "on" else "0"
    return f"switch_idx{index}_set: {state}"


def send_commands(port_name, baud, commands, cmd_delay_ms=100, prompt_timeout_ms=2000):
    port = serial.Serial(port_name, baud, timeout=0.1, write_timeout=1.0)
    try:
        time.sleep(0.2)
        total = len(commands)
        for index, cmd in enumerate(commands, start=1):
            _drain(port)
            print(f"CTRL SEND {index}/{total}: {cmd}")
            port.write((cmd + "\r\n").encode("ascii"))
            port.flush()
            time.sleep(cmd_delay_ms / 1000.0)
            raw_output, prompt_seen = _read_until_prompt(port, prompt_timeout_ms)
            print(f"CTRL RAW {index}/{total}: {raw_output!r}")
            lowered = raw_output.lower()
            failure = next((marker for marker in FAILURE_MARKERS if marker in lowered), "")
            if failure:
                raise RuntimeError(f"command '{cmd}' failed: saw '{failure}'")
            if not prompt_seen:
                raise RuntimeError(f"command '{cmd}' failed: prompt not detected")
            expected_marker = _expected_success_marker(cmd)
            if expected_marker and expected_marker not in lowered:
                raise RuntimeError(
                    f"command '{cmd}' failed: missing success marker '{expected_marker}'"
                )
            print(f"CTRL OK {index}/{total}: {cmd}")
        print(f"CTRL SUMMARY OK {total}/{total}")
    finally:
        port.close()

def set_loglevel(port_name, baud):
    port = serial.Serial(port_name, baud, timeout=1.0, write_timeout=1.0)
    try:
        # Wait for device boot
        time.sleep(2)
        # Clear buffer
        port.read_all()
        
        boot_ready = False
        for _ in range(50):  # up to 10 seconds
            data = port.read(port.in_waiting)
            if data:
                try:
                    text = data.decode("utf-8", errors="replace")
                    if "root:/$" in text or "root:/" in text:
                        boot_ready = True
                        print("Device boot detected")
                        break
                except Exception:
                    pass
            time.sleep(0.2)
        
        if not boot_ready:
            print("Device boot not confirmed, still sending loglevel 4")
        
        port.write(b"loglevel 4\r\n")
        port.flush()
        time.sleep(0.5)
        print("loglevel command sent")
    finally:
        port.close()

def read_version(port_name, baud):
    """Read firmware version from device log"""
    port = serial.Serial(port_name, baud, timeout=0.2, write_timeout=0.2)
    try:
        time.sleep(1)
        # Clear buffer
        port.read_all()
        
        boot_output = []
        start = time.time()
        while time.time() - start < 15:
            data = port.read(port.in_waiting)
            if data:
                try:
                    text = data.decode("utf-8", errors="replace")
                    boot_output.append(text)
                    print(text, end='', flush=True)
                except Exception:
                    pass
            time.sleep(0.1)
        
        full_log = ''.join(boot_output)
        return full_log
    finally:
        port.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: sudo_ctrl.py <action> [args...]")
        print("  send <port> <baud> <cmd1> [cmd2] ... - send commands to serial port")
        print("  setloglevel <port> <baud>           - set device loglevel")
        print("  readversion <port> <baud>           - read version from device")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "send":
        if len(sys.argv) < 5:
            print("Usage: sudo_ctrl.py send <port> <baud> [--delay-ms N] [--prompt-timeout-ms N] <cmd1> [cmd2] ...")
            sys.exit(1)
        port = sys.argv[2]
        baud = int(sys.argv[3])
        rest = sys.argv[4:]
        cmd_delay_ms = 100
        prompt_timeout_ms = 2000
        while len(rest) >= 2 and rest[0] in {"--delay-ms", "--prompt-timeout-ms"}:
            flag = rest[0]
            value = rest[1]
            try:
                parsed = int(value)
            except ValueError:
                print(f"Invalid {flag} value: {value}")
                sys.exit(1)
            if flag == "--delay-ms":
                cmd_delay_ms = parsed
            else:
                prompt_timeout_ms = parsed
            rest = rest[2:]
        if not rest:
            print("Usage: sudo_ctrl.py send <port> <baud> [--delay-ms N] [--prompt-timeout-ms N] <cmd1> [cmd2] ...")
            sys.exit(1)
        try:
            send_commands(port, baud, rest, cmd_delay_ms=cmd_delay_ms, prompt_timeout_ms=prompt_timeout_ms)
        except Exception as exc:
            print(f"CTRL SUMMARY FAIL: {exc}")
            sys.exit(2)
    elif action == "setloglevel":
        if len(sys.argv) < 4:
            print("Usage: sudo_ctrl.py setloglevel <port> <baud>")
            sys.exit(1)
        port = sys.argv[2]
        baud = int(sys.argv[3])
        set_loglevel(port, baud)
    elif action == "readversion":
        if len(sys.argv) < 4:
            print("Usage: sudo_ctrl.py readversion <port> <baud>")
            sys.exit(1)
        port = sys.argv[2]
        baud = int(sys.argv[3])
        read_version(port, baud)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
