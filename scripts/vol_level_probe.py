#!/usr/bin/env python3
"""
音量档位探测脚本 v3 - 稳定性判断法
关键发现：
1. 设备音量刻度: 0-10
2. volLevel=5 → 5个档位在固件内部对应 [0, 2, 4, 6, 8, 10]
3. 设备在"最小音量"后输出: set vol: 4 -> 0

逻辑：
1. 先发"最小音量"建立基准（volume→0）
2. 循环发"增大音量"直到音量值连续N次不变 → 记录各档位
3. 循环发"减小音量"直到音量值连续N次不变 → 记录各档位
4. 循环两次，验证一致性
"""

import re, serial, time, sys, subprocess, threading
from pathlib import Path
from typing import List, Tuple

PORT = "/dev/ttyACM0"
BAUD = 115200
WAV_DIR = Path(__file__).parent / "wavSource"
MAX_STABLE = 2
MAX_CYCLES = 30

VOL_REGEX = re.compile(r"set vol:\s*(\d+)\s*->\s*(\d+)")
PLAYID_REGEX = re.compile(r"play id : (\d+)")

def play(word: str) -> bool:
    path = WAV_DIR / f"{word}.mp3"
    r = subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        timeout=10, capture_output=True
    )
    return r.returncode == 0

def read_all_logs(ser, timeout=2.0) -> str:
    """读取所有可用的串口日志"""
    text = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting > 0:
            d = ser.read(ser.in_waiting)
            if d:
                text += d.decode('utf-8', errors='replace')
        time.sleep(0.1)
    return text

def send_command_and_read(ser, command_word: str) -> Tuple[int, int, List[Tuple[int,int]], List[int]]:
    """
    发送：唤醒 → 命令词
    返回：(from_vol, to_vol, all_vol_changes, all_playids)
    """
    ser.reset_input_buffer()
    
    # 唤醒
    play("小聆小聆")
    time.sleep(1.5)
    
    # 命令词
    play(command_word)
    time.sleep(2.5)
    
    # 读取日志
    text = read_all_logs(ser)
    
    vol_changes = VOL_REGEX.findall(text)
    playids = PLAYID_REGEX.findall(text)
    
    if vol_changes:
        from_v = int(vol_changes[-1][0])
        to_v = int(vol_changes[-1][1])
        all_changes = [(int(m[0]), int(m[1])) for m in vol_changes]
        return from_v, to_v, all_changes, [int(p) for p in playids]
    
    return None, None, [], [int(p) for p in playids] if playids else []

def probe_direction(ser, direction: str) -> Tuple[List[int], List[int]]:
    """
    探测一个方向的音量档位
    每次唤醒+命令，直到连续MAX_STABLE次音量值不变
    """
    cmd_word = "增大音量" if direction == "up" else "减小音量"
    print(f"\n  === 探测 {cmd_word} ===")
    
    all_vols = []
    all_pids = []
    stable_count = 0
    last_vol, last_pid = None, None
    
    for cycle in range(1, MAX_CYCLES + 1):
        from_v, to_v, changes, pids = send_command_and_read(ser, cmd_word)
        
        curr_vol = to_v
        curr_pid = pids[-1] if pids else None
        
        all_vols.append(curr_vol)
        if curr_pid is not None:
            all_pids.append(curr_pid)
        
        print(f"    Cycle {cycle:2d}: from={from_v} to={to_v} pid={curr_pid}", flush=True)
        
        if curr_vol is not None and last_vol is not None:
            if curr_vol == last_vol and (curr_pid is None or curr_pid == last_pid):
                stable_count += 1
                if stable_count >= MAX_STABLE:
                    print(f"    → 连续{MAX_STABLE}次稳定(to={curr_vol}, pid={curr_pid})，停止")
                    break
            else:
                stable_count = 0
        
        last_vol = curr_vol
        last_pid = curr_pid
        time.sleep(0.5)
    
    # 去重
    unique_vols = []
    for v in all_vols:
        if v is not None and (not unique_vols or v != unique_vols[-1]):
            unique_vols.append(v)
    
    unique_pids = []
    for p in all_pids:
        if not unique_pids or p != unique_pids[-1]:
            unique_pids.append(p)
    
    print(f"  去重后音量: {unique_vols}")
    print(f"  去重后播报ID: {unique_pids}")
    return unique_vols, unique_pids

def main():
    print("=" * 60)
    print("音量档位探测 v3 - 稳定性判断法")
    print("=" * 60)
    
    ser = serial.Serial(PORT, BAUD, timeout=1.0)
    print(f"[串口连接] {PORT}")
    
    # 建立基准
    print("\n[建立基准] 发送 最小音量...")
    for attempt in range(3):
        from_v, to_v, changes, pids = send_command_and_read(ser, "最小音量")
        if to_v is not None:
            print(f"  最小音量: {from_v} -> {to_v}, pids={pids}")
            break
        print(f"  attempt {attempt+1}: no vol change")
        time.sleep(1)
    
    # 两次探测
    results = []
    for round_num in [1, 2]:
        print(f"\n{'='*60}")
        print(f"第 {round_num} 轮")
        print(f"{'='*60}")
        
        up_vols, up_pids = probe_direction(ser, "up")
        down_vols, down_pids = probe_direction(ser, "down")
        
        results.append({'up': (up_vols, up_pids), 'down': (down_vols, down_pids)})
        
        print(f"\n  第{round_num}轮: up={up_vols}, down={down_vols}")
        
        if round_num == 1:
            print("  重新建立基准...")
            time.sleep(2)
            for _ in range(2):
                send_command_and_read(ser, "最小音量")
                time.sleep(1)
    
    ser.close()
    
    # 分析
    r1_up, r1_down = results[0]['up']
    r2_up, r2_down = results[1]['up']
    
    print(f"\n{'='*60}")
    print("结果分析")
    print(f"{'='*60}")
    
    up_ok = r1_up == r2_up
    down_ok = r1_down == r2_down
    
    print(f"  增大音量: 第1轮={r1_up}, 第2轮={r2_up} → {'✅一致' if up_ok else '❌不一致'}")
    print(f"  减小音量: 第1轮={r1_down}, 第2轮={r2_down} → {'✅一致' if down_ok else '❌不一致'}")
    
    if up_ok and r1_up:
        # 档位分析
        # 最小音量 -> 0, 之后增大
        min_vol = 0
        max_vol = r1_up[-1] if r1_up else 0
        print(f"\n  音量范围: {min_vol} ~ {max_vol}")
        print(f"  增大型序列: {r1_up}")
        
        if len(r1_up) > 1:
            steps = [r1_up[i+1] - r1_up[i] for i in range(len(r1_up)-1)]
            print(f"  相邻档位差: {steps}")
            
            if len(set(steps)) == 1:
                step = steps[0]
                actual_levels = (max_vol - min_vol) // step + 1
                print(f"\n  步进={step}, 计算档位数={actual_levels}")
                print(f"  配置 volLevel=5, 实际={actual_levels}")
                if actual_levels == 5:
                    print(f"  ✅ 音量档位 = 5 档，与配置匹配！")
                else:
                    print(f"  ❌ volLevel=5 但实际检测到 {actual_levels} 档")
            else:
                print(f"\n  ⚠️ 档位差不一致: {steps}")
                print(f"  实际档位序列: {r1_up}")
                print(f"  档位数: {len(r1_up)}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
