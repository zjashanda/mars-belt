#!/usr/bin/env python3
"""Sudo wrapper for serial port control operations."""
import sys
import time
import serial


def send_commands(port_name, baud, commands, cmd_delay_ms=100):
    port = serial.Serial(port_name, baud, timeout=1.0, write_timeout=1.0)
    try:
        for cmd in commands:
            port.write((cmd + "\r\n").encode("ascii"))
            port.flush()
            time.sleep(cmd_delay_ms / 1000.0)
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
            print("Usage: sudo_ctrl.py send <port> <baud> [--delay-ms N] <cmd1> [cmd2] ...")
            sys.exit(1)
        port = sys.argv[2]
        baud = int(sys.argv[3])
        rest = sys.argv[4:]
        cmd_delay_ms = 100
        if len(rest) >= 2 and rest[0] == "--delay-ms":
            try:
                cmd_delay_ms = int(rest[1])
            except ValueError:
                print(f"Invalid --delay-ms value: {rest[1]}")
                sys.exit(1)
            rest = rest[2:]
        if not rest:
            print("Usage: sudo_ctrl.py send <port> <baud> [--delay-ms N] <cmd1> [cmd2] ...")
            sys.exit(1)
        send_commands(port, baud, rest, cmd_delay_ms=cmd_delay_ms)
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
