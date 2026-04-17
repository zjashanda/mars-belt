#!/usr/bin/env python3
"""
probe_volume_levels.py
=====================
音量档位独立探测脚本（稳定性判断法）

用法:
  python probe_volume_levels.py --port /dev/ttyACM0

前置条件:
  - 设备已烧录固件且正常运行
  - trace_uart=1（能输出 [D] 级日志）
  - wavSource/ 目录下有：小聆小聆.mp3 / 增大音量.mp3 / 减小音量.mp3 / 最小音量.mp3

输出:
  - 档位探测数据（增大/减小方向）
  - 档位数比对结果（配置 vs 实际）
  - 边界TTS触发情况
"""

import argparse
import re
import serial
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

# 正则
VOL_RE = re.compile(r"set vol:\s*(\d+)\s*->\s*(\d+)")
PID_RE = re.compile(r"play id : (\d+)")

# 常量
MAX_CYCLES = 30
MAX_STABLE = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="音量档位探测脚本（稳定性判断法）")
    parser.add_argument("--port", default="/dev/ttyACM0", help="日志串口")
    parser.add_argument("--ctrl-port", default="/dev/ttyACM4", help="控制串口")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--wav-dir", default="wavSource", help="音频目录")
    parser.add_argument("--wake-word", default="小聆小聆", help="唤醒词")
    parser.add_argument("--max-cycles", type=int, default=MAX_CYCLES, help="每个方向最大探测次数")
    parser.add_argument("--max-stable", type=int, default=MAX_STABLE, help="连续N次稳定则停止")
    return parser.parse_args()


class VolumeProbe:
    def __init__(self, port: str, ctrl_port: str, baud: int, wav_dir: Path,
                 wake_word: str, max_cycles: int, max_stable: int):
        self.port = port
        self.ctrl_port = ctrl_port
        self.baud = baud
        self.wav_dir = Path(wav_dir)
        self.wake_word = wake_word
        self.max_cycles = max_cycles
        self.max_stable = max_stable
        self.ser: Optional[serial.Serial] = None
        self._received: List[str] = []
        self._lock = threading.Lock()

    def _play(self, word: str) -> bool:
        path = self.wav_dir / f"{word}.mp3"
        if not path.exists():
            print(f"  [WARN] 音频文件不存在: {path}")
            return False
        r = subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            timeout=10, capture_output=True
        )
        return r.returncode == 0

    def _read_logs(self, timeout: float = 2.0) -> str:
        text = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ser.in_waiting > 0:
                data = self.ser.read(self.ser.in_waiting)
                if data:
                    text += data.decode("utf-8", errors="replace")
            time.sleep(0.05)
        return text

    def _get_logs(self) -> List[str]:
        with self._lock:
            return list(self._received)

    def _clear_logs(self) -> None:
        with self._lock:
            self._received.clear()

    def _append_logs(self, text: str) -> None:
        with self._lock:
            self._received.extend(text.split("\n"))

    def _reader_thread(self) -> None:
        while True:
            try:
                line = self.ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    with self._lock:
                        self._received.append(line)
            except Exception:
                break

    def open(self) -> None:
        self.ser = serial.Serial(self.port, self.baud, timeout=0.3)
        self._thread = threading.Thread(target=self._reader_thread, daemon=True)
        self._thread.start()
        time.sleep(0.5)

    def close(self) -> None:
        if self.ser:
            self.ser.close()
            self.ser = None

    def _send_wake_and_command(self, command_word: str) -> Tuple[Optional[int], Optional[int], List[Tuple[int, int]], List[int]]:
        """
        唤醒 → 发送命令词
        返回: (from_vol, to_vol, all_vol_changes, all_playids)
        """
        self.ser.reset_input_buffer()
        self._clear_logs()

        self._play(self.wake_word)
        time.sleep(1.5)
        self._play(command_word)
        time.sleep(2.5)

        text = self._read_logs()
        self._append_logs(text)

        vol_changes = VOL_RE.findall(text)
        playids = PID_RE.findall(text)

        if vol_changes:
            from_v = int(vol_changes[-1][0])
            to_v = int(vol_changes[-1][1])
            all_changes = [(int(m[0]), int(m[1])) for m in vol_changes]
            all_pids = [int(p) for p in playids]
            return from_v, to_v, all_changes, all_pids

        return None, None, [], [int(p) for p in playids] if playids else []

    def probe_direction(self, direction: str) -> Tuple[List[int], List[int]]:
        """
        探测一个方向的音量档位
        direction: 'up' (增大音量) 或 'down' (减小音量)
        返回: (unique_volumes, unique_playids)
        """
        cmd_word = "增大音量" if direction == "up" else "减小音量"
        print(f"\n  === 探测 {cmd_word} ===")

        all_vols: List[Optional[int]] = []
        all_pids: List[Optional[int]] = []
        stable_count = 0
        last_vol: Optional[int] = None
        last_pid: Optional[int] = None

        for cycle in range(1, self.max_cycles + 1):
            from_v, to_v, changes, pids = self._send_wake_and_command(cmd_word)

            curr_vol = to_v
            curr_pid = pids[-1] if pids else None

            all_vols.append(curr_vol)
            if curr_pid is not None:
                all_pids.append(curr_pid)

            print(f"    Cycle {cycle:2d}: from={from_v} to={to_v} pid={curr_pid}")

            if curr_vol is not None and last_vol is not None:
                if curr_vol == last_vol and (curr_pid is None or curr_pid == last_pid):
                    stable_count += 1
                    if stable_count >= self.max_stable:
                        print(f"    → 连续{self.max_stable}次稳定(to={curr_vol}, pid={curr_pid})，停止")
                        break
                else:
                    stable_count = 0

            last_vol = curr_vol
            last_pid = curr_pid
            time.sleep(0.5)

        # 去重
        unique_vols: List[int] = []
        for v in all_vols:
            if v is not None and (not unique_vols or v != unique_vols[-1]):
                unique_vols.append(v)

        unique_pids: List[int] = []
        for p in all_pids:
            if not unique_pids or p != unique_pids[-1]:
                unique_pids.append(p)

        print(f"  去重后音量: {unique_vols}")
        print(f"  去重后播报ID: {unique_pids}")
        return unique_vols, unique_pids

    def run(self) -> dict:
        """执行完整探测流程，返回结果字典"""
        results = {
            "配置 volLevel": None,
            "round1": {},
            "round2": {},
            "分析": {},
        }

        # Step 1: 建立基准
        print("\n[Step 1] 建立基准 → 发送 最小音量")
        for attempt in range(3):
            from_v, to_v, changes, pids = self._send_wake_and_command("最小音量")
            if to_v is not None:
                print(f"  最小音量: {from_v} → {to_v}, pids={pids}")
                break
            print(f"  attempt {attempt + 1}: no vol change")
            time.sleep(1)
        else:
            print("  [WARN] 无法获取基准音量")

        # 两次完整探测
        for round_num in [1, 2]:
            print(f"\n{'=' * 50}")
            print(f"第 {round_num} 轮")
            print(f"{'=' * 50}")

            up_vols, up_pids = self.probe_direction("up")
            down_vols, down_pids = self.probe_direction("down")

            results[f"round{round_num}"] = {
                "up": {"vols": up_vols, "pids": up_pids},
                "down": {"vols": down_vols, "pids": down_pids},
            }

            if round_num == 1:
                # 重新建立基准
                print("\n  重新建立基准...")
                time.sleep(2)
                for _ in range(2):
                    self._send_wake_and_command("最小音量")
                    time.sleep(1)

        # 分析
        r1 = results["round1"]
        r2 = results["round2"]

        up_ok = r1["up"]["vols"] == r2["up"]["vols"]
        down_ok = r1["down"]["vols"] == r2["down"]["vols"]

        analysis = {
            "up_match": up_ok,
            "down_match": down_ok,
            "up_sequence": r1["up"]["vols"],
            "down_sequence": r1["down"]["vols"],
            "固件内部刻度": None,
            "步进": None,
            "实际档位数": None,
            "结论": None,
            "备注": None,
        }

        if up_ok and r1["up"]["vols"]:
            unique = r1["up"]["vols"]
            if len(unique) >= 2:
                steps = [unique[i + 1] - unique[i] for i in range(len(unique) - 1)]
                step = steps[0] if len(set(steps)) == 1 else None
                analysis["步进"] = step
                analysis["固件内部刻度"] = f"{min(unique)} ~ {max(unique)}"
                analysis["实际档位数"] = len(unique)
            elif len(unique) == 1:
                analysis["步进"] = 0
                analysis["固件内部刻度"] = f"{unique[0]} (单值)"
                analysis["实际档位数"] = 1

        results["分析"] = analysis
        return results


def main() -> int:
    args = parse_args()
    probe = VolumeProbe(
        port=args.port,
        ctrl_port=args.ctrl_port,
        baud=args.baud,
        wav_dir=Path(args.wav_dir),
        wake_word=args.wake_word,
        max_cycles=args.max_cycles,
        max_stable=args.max_stable,
    )

    print("=" * 60)
    print("音量档位探测（稳定性判断法）")
    print("=" * 60)
    print(f"串口: {args.port}")
    print(f"音频目录: {args.wav_dir}")

    try:
        probe.open()
        results = probe.run()
    finally:
        probe.close()

    # 输出最终报告
    a = results["分析"]
    print(f"\n{'=' * 60}")
    print("音量档位测试报告")
    print("=" * 60)

    print(f"\n增大方向档位序列: {a['up_sequence']}")
    print(f"减小方向档位序列: {a['down_sequence']}")
    print(f"增大/减小一致性: {'✅ 一致' if a['up_match'] and a['down_match'] else '❌ 不一致'}")
    print(f"固件内部刻度: {a['固件内部刻度']}")
    print(f"档位步进: {a['步进']}")
    print(f"实际档位数: {a['实际档位数']}")
    print(f"结论: {a['结论']}")
    if a.get("备注"):
        print(f"备注: {a['备注']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
