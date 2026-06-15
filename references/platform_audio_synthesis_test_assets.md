# 平台音频合成测试资产规则

## 结论

平台「合成管理 -> 音频合成」生成的音频可以作为固件运行态测试音频。它用于通过声卡向设备播放唤醒词、命令词或协议播报触发词，验证固件 ASR、intent、TX/RX、播报和配置生效链路。

这些音频是测试资料，不是临时缓存。小体积、可复用的音频和 manifest 必须放在 `assets/audio/platform_synthesis/` 并随 skill git 同步；平台下载 zip、截图、串口日志、运行结果继续放 `artifacts/`，不随 git 同步。

## 存放结构

```text
assets/audio/platform_synthesis/
├── zh/
│   └── <suite>/
│       ├── xiao_ling_xiao_ling.mp3
│       ├── da_kai_feng_shan.mp3
│       └── manifest.json
└── en/
    └── <suite>/
        ├── hello_my_dear.mp3
        ├── start_fan.mp3
        └── manifest.json
```

- `zh`：中文固件测试音频。
- `en`：英文固件测试音频。
- `<suite>`：测试套件或垂类/品类名，例如 `3021_fan_base`、`3021_curtain_voice_reg`。
- 文件名必须稳定、可读、可跨平台：英文用小写下划线；中文优先用固定拼音别名，无法映射时用 `zh_<hash>`，真实文本必须写入 `manifest.json`。

## 资产生成优先级

1. 先查本地 `assets/audio/platform_synthesis/<lang>/<suite>/manifest.json` 和目标音频文件。
2. 若本地已有且文本、发音人、sha256 匹配，直接复用。
3. 若缺失，使用 `scripts/py/listenai_platform_audio_synthesis_cache.py` 走平台「音频合成」正式构建。
4. 下载正式合成 zip 后，只把最终 mp3/wav 和 manifest 复制到 `assets/audio/platform_synthesis/`；zip 和解压中间目录留在 `artifacts/audio-synthesis-assets/`。
5. `/fw/common/generateAudio` 试听接口只能用于播放链路预检，不能作为固件识别验证主证据。

## 发音人规则

- 中文固件测试音频必须选择中文发音人。
- 中文发音人必须排除名称、标签或 value 中带 `英文`、`英语`、`English`、`EnUs`、`EnUk`、`_en`、`en_`、`JaJp`、`KoKr` 等非中文标识的候选。
- 英文固件测试音频必须选择英文/英语发音人，例如 `Luna_x4云（英语） / x4_EnUs_Luna_assist`。
- 如果发音人与固件语言不匹配，识别失败归因测试音频配置错误，不能归因固件。

## 文本匹配规则

- 音频文本必须与固件算法模板或 `web_config.json` 中的唤醒词/命令词 intent 一致。
- 可以使用配置中的泛化词作为播放文本，但断言必须回到目标 intent。
- 不允许用中文音频测试英文固件，也不允许用英文音频测试中文固件。
- 音频播放成功不等于用例通过，必须结合设备日志判定。

## 运行态判据

测试报告和 summary 必须至少记录：

- 音频资产目录和 manifest。
- 平台音频合成项目/输出 ID，或说明本次复用了已有资产。
- 发音人 label/value。
- 声卡 key。
- 设备日志中的 `Wakeup`、`keyword`/`intentStr`、`[TX]`、`[RX]`、播报/mini-player 标记。
- 未识别词列表。

## 命令示例

英文：

```bash
PYTHONPATH=scripts/py python3 scripts/py/listenai_platform_audio_synthesis_cache.py \
  --language en \
  --suite 3021_fan_base \
  --text 'Hello My Dear' \
  --text 'Start Fan' \
  --text 'Stop Fan' \
  --text 'Volume Up'
```

中文：

```bash
PYTHONPATH=scripts/py python3 scripts/py/listenai_platform_audio_synthesis_cache.py \
  --language zh \
  --suite 3021_fan_base \
  --text '小聆小聆' \
  --text '打开风扇' \
  --text '关闭风扇' \
  --text '增大音量'
```

强制重新生成平台音频合成记录时追加：

```bash
--force-synthesize
```

## Git 同步规则

必须同步：

- `assets/audio/platform_synthesis/<lang>/<suite>/*.mp3`
- `assets/audio/platform_synthesis/<lang>/<suite>/*.wav`
- `assets/audio/platform_synthesis/<lang>/<suite>/manifest.json`
- 生成/验证脚本和参考文档

不得同步：

- `artifacts/` 下的音频合成 zip、解压目录、截图、串口日志、报告中间文件
- `TOOLS.md`、token、设备本地配置
- 烧录暂存 `scripts/burn/app.bin`
