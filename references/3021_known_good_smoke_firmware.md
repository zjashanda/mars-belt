# 3021 已知可用冒烟固件与台架控制逻辑

## 用途

当 3021 设备出现烧录失败、日志无输出、协议无响应或语音识别异常时，先烧录本仓库已验证的基础冒烟固件，用于区分问题来源：

- 已知可用固件通过：优先定位新打包固件、配置参数、平台产物或测试用例。
- 已知可用固件烧录失败：优先定位电源线、boot 线、控制口、烧录口或烧录工具链。
- 已知可用固件烧录成功但无日志：优先定位日志口、波特率、上电恢复流程或设备启动链路。
- 已知可用固件协议失败但语音正常：优先定位协议线、协议门控 `uut-switch2`、协议口选择或协议波特率。
- 已知可用固件语音失败但日志/协议正常：优先定位声卡、音量、播放设备、声学环境或识别链路。

## 固件资产

- 固件包：`assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip`
- 元数据：`assets/firmware/3021-smoke/manifest.json`
- 包 sha256：`a7ccb7c3853cb327e31963a18749c661218bf5671b37f433b56023e5ea0f607f`
- `fw.bin` sha256：`f2f3a199e023848862b151f8482ee7ce394517d2eb757c372976602c0e2128d9`
- 来源：平台 UI 打包风扇垂类 `pkg01_default_multi_specified`
- 验证结论：2026-06-12 单次烧录通过，随后连续重复烧录 2 次均通过。

## 当前 3021 台架映射

- 日志/烧录口：`/dev/ttyACM0`
  - 日志波特率：`115200`
  - 烧录波特率：`460800`
- 协议口：`/dev/ttyACM2@9600`
- 空口：`/dev/ttyACM1`，不要用于验证
- 控制口：`/dev/ttyACM4@115200`
- 声卡：`VID_8765&PID_5678:USB_0_1_3_1_0`
- 电源控制：`uut-switch1.on/off`
- 协议连接门控：`uut-switch2.on/off`
- boot 控制：`uut-switch3.on/off`

## 控制逻辑

### 正常运行态上电

正常上电前先断开协议口，避免协议口影响启动进入 boot：

```bash
python3 scripts/burn/sudo_ctrl.py send /dev/ttyACM4 115200 --delay-ms 500 \
  uut-switch2.off uut-switch3.off uut-switch1.off uut-switch1.on
sleep 3
python3 scripts/burn/sudo_ctrl.py send /dev/ttyACM4 115200 --delay-ms 500 uut-switch2.on
```

### 烧录进入 boot

烧录前先断开协议口，然后只用电源和 boot 线进入烧录模式：

```bash
python3 scripts/burn/sudo_ctrl.py send /dev/ttyACM4 115200 --delay-ms 1000 uut-switch2.off
python3 scripts/burn/sudo_ctrl.py send /dev/ttyACM4 115200 --delay-ms 1000 \
  uut-switch1.off uut-switch3.on uut-switch1.on uut-switch3.off
```

### 推荐自动烧录命令

```bash
python3 scripts/mars_belt.py burn \
  --package-zip assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip \
  --ctrl-port /dev/ttyACM4 \
  --burn-port /dev/ttyACM0 \
  --runtime-log-port /dev/ttyACM0 \
  --ctrl-baud 115200 \
  --baud 460800 \
  --max-retry 1 \
  --cmd-delay-ms 1000 \
  --burn-mode-wait-ms 3000 \
  --boot-switch uut-switch3 \
  --power-switch uut-switch1 \
  --protocol-switch uut-switch2 \
  --protocol-gate-wait-seconds 3
```

`mars_belt.py burn` 必须继续使用固定暂存流程：先清理 `scripts/burn/app.bin`，再从 zip 提取 `fw.bin` 到 `scripts/burn/app.bin`，烧录后清理临时 `app.bin`。不要把任意外部 bin 路径直接传给烧录工具。

## 基础验证项

烧录后必须验证以下最小链路：

1. 日志口 `/dev/ttyACM0@115200` 有启动日志或 shell prompt。
2. 可输入 `loglevel 4`。
3. 协议口 `/dev/ttyACM2@9600` 发送播报语接收帧：

```text
A5 FA 00 82 71 00 92 FB
```

期望日志包含 `[RX]`、该协议帧和 `mini-player play id`，且无 `recv data miss`。

4. 使用指定声卡依次播放：

```text
scripts/wavSource/小聆小聆.mp3
scripts/wavSource/打开风扇.mp3
scripts/wavSource/最大音量.mp3
```

期望日志包含：`Wakeup`、`xiao3 ling2 xiao3 ling2`、`da3 kai1 feng1 shan4`、`zui4 da4 yin1 liang4`、`[TX]`、`play id`。

## 已验证证据

本机验证证据位于：

- 单次烧录与运行态验证：`artifacts/device-check/3021-ui-burn-20260612-145600/`
- 连续两次重复烧录汇总：`artifacts/device-check/3021-repeat-burn-20260612-151452/repeat_burn_2cycles_summary.json`

`artifacts/` 不随 git 发布；后续复现时以本文件、固件资产和 `manifest.json` 为准重新采集证据。
