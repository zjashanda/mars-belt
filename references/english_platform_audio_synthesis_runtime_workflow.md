# 3021 英文固件音频合成与运行态验证规则

## 结论

英文固件运行态识别验证不能继续使用中文 TTS 或本地默认合成脚本。英文测试音频必须优先复用本地正式缓存；缓存缺失时，必须走平台「合成管理 -> 音频合成」正式构建，选择带英文/英语标识的发音人，下载构建产物后再用于声卡播报验证。


通用的中英文音频资产规则见 `references/platform_audio_synthesis_test_assets.md`。

## 资产优先级

1. 优先使用本地正式缓存：`assets/audio/platform_synthesis/en/3021_fan_base/`。
2. 缓存缺失时使用：`PYTHONPATH=scripts/py python3 scripts/py/listenai_platform_audio_synthesis_cache.py ...`。
3. 该脚本走 `/fw/voice/add`、`/fw/voice/output/add`、`/dev/file/download`，对应平台页面是「合成管理 -> 音频合成」。
4. `/fw/common/generateAudio` 只能作为试听/播放链路预检，不能作为英文固件识别验证主证据，因为平台不会出现正式音频合成构建记录。
5. 小体积、可复用的正式音频缓存需要随 git 同步；任务目录、下载 zip、截图、串口日志、平台 token 不同步。

## 发音人规则

- 英文合成必须选择发音人名称、标签或 value 带英文/英语标识的候选。
- 当前已验证发音人：`Luna_x4云（英语） / x4_EnUs_Luna_assist`。
- 可接受候选示例：`x4_EnUs_Luna_assist`、`x4_EnUs_Gavin_assist`、`x4_EnUk_Lydia_edu`、`x4_lingxiaoying_en`。
- 禁止英文固件使用中文默认发音人合成识别音频，否则识别失败不能归因固件。

## 当前已验证缓存

目录：`assets/audio/platform_synthesis/en/3021_fan_base/`

| 文本 | 文件 |
| --- | --- |
| Hello My Dear | hello_my_dear.mp3 |
| Start Fan | start_fan.mp3 |
| Stop Fan | stop_fan.mp3 |
| Volume Up | volume_up.mp3 |
| Set Volume To Max | set_volume_to_max.mp3 |
| Exit Recognition | exit_recognition.mp3 |

来源记录：平台「音频合成」项目 `EN_AUDIO_SYNTH_3021_BASE_20260615_143848`，输出 `2066410076595314689`，保留在平台用于人工查看。

## 生成/复用命令

```bash
PYTHONPATH=scripts/py python3 scripts/py/listenai_platform_audio_synthesis_cache.py \
  --out-dir assets/audio/platform_synthesis/en/3021_fan_base \
  --text 'Hello My Dear' \
  --text 'Start Fan' \
  --text 'Stop Fan' \
  --text 'Volume Up' \
  --text 'Set Volume To Max' \
  --text 'Exit Recognition'
```

需要强制重新发起平台音频合成构建时追加：

```bash
--force-synthesize
```

## 固件运行态验证命令

```bash
PYTHONPATH=scripts/py python3 scripts/py/listenai_3021_english_official_audio_verify.py \
  --package-zip <英文固件zip> \
  --audio-dir assets/audio/platform_synthesis/en/3021_fan_base \
  --out-dir <结果目录> \
  --log-port /dev/ttyACM0 \
  --protocol-port /dev/ttyACM2 \
  --ctrl-port /dev/ttyACM4 \
  --power-switch uut-switch1 \
  --protocol-switch uut-switch2 \
  --boot-switch uut-switch3
```

## 2026-06-15 实测结果

- 英文产品：`3021-风扇-通用英文垂类-打包测试-06151435`
- 版本：`V-2026.06.15_14.35.57`
- 固件包：`artifacts/tasks/3021-english-firmware-audio-20260615-143347/downloads/firmware/english_fan_base_2066409358408835074.zip`
- 烧录：PASS
- 协议口：`ACM2@9600`，发送 `A5 FA 00 81 02 00 22 FB`，RX 可见
- 声卡：`VID_8765&PID_5678:USB_0_1_3_1_0`
- 识别：`Hello My Dear / Start Fan / Stop Fan / Volume Up` 全部 PASS
- 结果文件：`artifacts/tasks/3021-english-firmware-audio-20260615-143347/runtime/english_official_audio_verify/summary.json`

## 2026-06-15 英文全垂类最小规则重测结论

- 实时 UI 扫描口径：`英文 + CSK3021` 当前只开放 `通用垂类` 1 个英文垂类；`风扇` 是通用垂类下的代表品类，不是独立英文风扇垂类。
- 当前能力：`voice_regist=Unsupported`、`multi_wakeup=Unsupported`、`dec_cmd=Optional`，因此本轮只执行基础功能三包：`pkg01_default_base`、`pkg02_left_base`、`pkg03_right_base_protocol`。
- 有效打包：3 个 release；其中右边界包首次回读高值未入包，已修正 UI slider 操作并重打替代包，旧包标记 superseded。
- 真机判定：3/3 PASS。默认包和右边界包要求英文唤醒词 + 命令词识别；左边界 `timeout=1` 包按 timeout-only 规则只要求唤醒和超时 marker。
- 报告必须把“当前英文只有通用垂类”写成范围结论，并说明如果平台后续新增英文独立垂类，需要重新实时扫描后按每垂类代表品类补测。
