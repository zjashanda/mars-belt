# Mars-Belt 邮件报告模板

> 模板冻结说明
>
> 当前邮件模板以 `scripts/mars_belt.py` 中的 `generate_email_report()` 与 `scripts/py/listenai_weekly_validation_runner.py` 中的周测汇总报告实现为准。
> 未经用户明确同意，不得修改本模板的结构、字段映射、措辞口径或附件规则。

本文档定义了固件测试报告邮件的格式和数据来源。

- 单包验证：沿用原有“固件测试报告”模板
- 周测/多包验证：使用“周测固件汇总报告”模板

---

## 一、必须包含的四个核心区域

### 1. 基本信息区（info-grid）

| 字段 | 来源 | 示例 |
|------|------|------|
| 产品名称 | summary.json → packageSummary.productName | 3021-风扇灯-风扇垂类-0330 |
| 芯片型号 | summary.json → selected.moduleMark | CSK3021 |
| 固件版本 | summary.json → finalReleaseSubset.version | V-2026.03.31_15.07.47 |
| 语言 | summary.json → selected.language | 中文 |
| 测试时间 | 从 validate 执行日志目录名解析 | 2026-03-31 15:13 |
| 串口配置 | 固定值 | /dev/ttyACM4 + /dev/ttyACM0 |

### 2. 固件配置参数表

| 配置项 | 配置期望值来源 | 实测校验值来源 |
|--------|---------------|----------------|
| 唤醒超时 | summary.json → finalReleaseSubset.timeout | serial_raw.log 观察 "Wk timeout" 或实际测量 |
| 音量档位 | summary.json → finalReleaseSubset.volLevel | serial_raw.log 音量轨迹 (app set max vol / set vol: X -> Y) |
| 默认音量 | summary.json → finalReleaseSubset.defaultVol | 主动断电重启后的启动日志 "volume : X" |
| 唤醒词 | deviceInfo_generated.json → wakeupWord | 同左 |
| Flash / SRAM | summary.json → selected.flash / sram | 同左 |
| 供电电压 | summary.json → selected.powerSupply | 同左 |

### 3. 测试用例详情表

**列**：用例ID | 测试类型 | 命令词 | 配置期望值 | 实测校验值 | 结果

**数据来源**：

| 用例类型 | 配置期望值 | 实测校验值 |
|----------|-----------|-----------|
| 固件版本校验 | summary.json → finalReleaseSubset.version | serial_raw.log 启动日志 "config version: V-xxx" |
| 超时退出 | summary.json → finalReleaseSubset.timeout + "s" | serial_raw.log 观察退出唤醒态耗时 |
| 音量档位验证 | summary.json → finalReleaseSubset.volLevel + "档" | serial_raw.log 音量变化轨迹统计 |
| 默认音量验证 | summary.json → finalReleaseSubset.defaultVol + "档" | 主动断电重启后的启动日志 "volume : X" |
| 唤醒识别 | "唤醒成功" | serial_raw.log 匹配唤醒成功日志 |
| 命令词识别 | testCases.csv → 期望协议 | serial_raw.log → [TX] 实际发送协议 |

**协议帧样式**：
```html
<span class="proto">A5 FA 00 81 02 00 22 FB</span>
```

### 4. 附件说明区

见下方附件打包规则。

---

## 一-B、周测/多包汇总模板（新增）

当执行 `package-weekly` 或同类多包矩阵验证时，邮件正文必须切换为周测汇总模板，至少包含以下四个区域：

### 1. 基本信息区

| 字段 | 来源 |
|------|------|
| 产品名称 | `weekly/manifest.json -> productName` |
| 芯片型号 | `weekly/manifest.json -> selectedMeta.moduleMark` |
| 语言 / 场景 | `weekly/manifest.json -> selectedMeta.language / sceneLabel` |
| 版本 | `weekly/manifest.json -> selectedMeta.versionLabel` |
| 测试时间 | `weekly/manifest.json -> updatedAt` |
| 目标范围 | `weekly/manifest.json -> selectedMeta.productPath` |

### 2. 产品能力裁剪区

| 字段 | 来源 | 用途 |
|------|------|------|
| `voice_regist` | `catalogPaths.parameterCatalog -> featureMap.voice_regist` | 说明是否纳入语音注册专项 |
| `multi_wakeup` | `catalogPaths.parameterCatalog -> featureMap.multi_wakeup` | 说明是否纳入多唤醒专项 |

说明：
- `voice_regist=Unsupported` 时，不得生成语音注册专项包
- `multi_wakeup=Optional` 时，需生成多唤醒专项包

### 3. 包级验证矩阵区

**列**：变体 | 状态 | 关键配置 | 配置断言 | 设备验证 | 异常摘要

| 字段 | 来源 |
|------|------|
| 变体 | `weekly/manifest.json -> variants[*].id` |
| 状态 | `weekly/manifest.json -> variants[*].status` |
| 关键配置 | `resolvedOverrides` 或 `overrides` |
| 配置断言 | `variants[*].configResult` |
| 设备验证 | `variants[*].deviceResult` |
| 异常摘要 | 对应 `pkg-*_testResult.xlsx` / `pkg-*_config_suite_config_assert_result.csv` 中的失败项摘要 |

### 4. 控制变量诊断区

当主矩阵中的某个包出现系统性问题，例如设备反复重启、连续上电无启动日志、运行态协议链路整体失效时，邮件正文必须增加“控制变量诊断”区域。

**列**：定位包 | 变更点 | 设备结果 | 结论

要求：
- 明确说明这些额外包是“问题定位包”，不是新增功能覆盖包
- 必须给出最终归因结论
- 若最终只能收敛到耦合参数组合，也要明确写出组合，而不能只写“怀疑串口有问题”

### 5. 异常汇总与附件说明区

- 异常汇总必须按包列出真实失败项
- `result.zip` 必须至少包含：
  - `test_report.html`
  - `feature_matrix.md`
  - `weekly/manifest.json`
  - `weekly/summary.md`
  - `weekly/control_variable_analysis.md`（如存在控制变量诊断）
  - 各包 `testResult.xlsx`
  - 各包 `serial_raw.log`
  - 各包 `test_tool.log`
  - 各包 `burn.log`
  - 各包 `package_params.txt`
  - 各包 firmware zip
  - 各包 `suite/testCases.csv`

---

## 二、附件打包规则

```
{产品名}_汇总报告_{日期}.zip
└── {产品名}_汇总报告_{日期}/
    ├── testResult.xlsx
    ├── serial_raw.log
    ├── test_*.log
    ├── testCases.csv
    └── {产品名}_firmware.zip
```

> ⚠️ 注意：不包含 `deviceInfo_generated.json`（包含敏感信息，不外发）

---

## 三、关键数据获取脚本

```python
import os, json, csv, re, shutil, zipfile
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

# ── 路径配置 ──
BASE_DIR = "/home/bszheng/.openclaw/skills/mars-belt/scripts/_runtime/{产品}-{版本}-{时间戳}"
SUITE_DIR = os.path.join(BASE_DIR, "suite_cli")
RESULT_DIR = os.path.join(SUITE_DIR, "result", "{时间戳}_ListenAI_xxx")
FIRMWARE_ZIP = "/home/bszheng/.openclaw/skills/mars-belt/scripts/result/{产品}/{配置}/*.zip"

# ── 1. 读取 summary.json ──
with open(os.path.join(BASE_DIR, "summary.json")) as f:
    summary = json.load(f)

product_name   = summary["packageSummary"]["productName"]
version        = summary["finalReleaseSubset"]["version"]
timeout_val    = summary["finalReleaseSubset"]["timeout"]
vol_level      = summary["finalReleaseSubset"]["volLevel"]
default_vol    = summary["finalReleaseSubset"]["defaultVol"]
module         = summary["selected"]["moduleMark"]
language       = summary["selected"]["language"]
flash          = summary["selected"]["flash"]
sram           = summary["selected"]["sram"]
power          = summary["selected"]["powerSupply"]

# ── 2. 读取 deviceInfo_generated.json ──
device_info_path = os.path.join(RESULT_DIR, "deviceInfo_generated.json")
with open(device_info_path) as f:
    device_info = json.load(f)
wake_word = device_info["wakeupWord"]

# ── 3. 读取 testCases.csv ──
csv_path = os.path.join(SUITE_DIR, "testCases.csv")
test_cases = []
with open(csv_path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        test_cases.append(row)

# 构建期望协议映射
EXPECT_PROTO = {row["用例编号"]: row.get("期望协议", "") for row in test_cases}

# ── 4. 从 serial_raw.log 提取实测协议 ──
SERIAL_LOG = os.path.join(RESULT_DIR, "serial_raw.log")
with open(SERIAL_LOG, encoding="utf-8", errors="replace") as f:
    serial_content = f.read()

# 提取固件版本
config_version_match = re.search(r"config version:\s*(V[\d.]+)", serial_content)
config_version = config_version_match.group(1) if config_version_match else version

# 提取音量配置
vol_matches = re.findall(r"volume\s*:\s*(\d+)", serial_content)
actual_default_vol = vol_matches[0] if vol_matches else "N/A"

# 提取命令词协议（配对方式：根据用例顺序找 [TX] 行）
# 简化处理：按命令词出现顺序匹配
tx_protocols = re.findall(r'\[TX\].*?([0-9A-Fa-f ]{10,})', serial_content)

# ── 5. 从 test_*.log 读取测试结果 ──
test_log = os.path.join(RESULT_DIR, "test_*.log")
with open(test_log, encoding="utf-8", errors="replace") as f:
    test_log_content = f.read()

# 提取每个用例最终结果（取最后一次）
results = {}
for line in test_log_content.splitlines():
    m = re.search(r'\[(BASE-[^\]]+)\].*?结果:\s*(\w+)', line)
    if m:
        cid, res = m.group(1), m.group(2)
        results[cid] = res  # 覆盖保存，最后一次为准
```

---

## 四、邮件模板变量速查

| 变量 | 来源 | 示例 |
|------|------|------|
| PRODUCT_NAME | summary.packageSummary.productName | 3021-风扇灯-风扇垂类-0330 |
| VERSION | summary.finalReleaseSubset.version | V-2026.03.31_15.07.47 |
| MODULE | summary.selected.moduleMark | CSK3021 |
| LANGUAGE | summary.selected.language | 中文 |
| TIMEOUT_VAL | summary.finalReleaseSubset.timeout | 10 |
| VOL_LEVEL | summary.finalReleaseSubset.volLevel | 5 |
| DEFAULT_VOL | summary.finalReleaseSubset.defaultVol | 5 |
| WAKE_WORD | deviceInfo_generated.wakeupWord | 小聆小聆 |
| FLASH | summary.selected.flash | 1MB |
| SRAM | summary.selected.sram | 224KB |
| POWER | summary.selected.powerSupply | 3.3~5.0V |

---

## 五、常见异常备注模板

### 1. 默认音量不符
```html
<div class="attn-box">
<strong>⚠️ 异常备注：</strong><br>
· <strong>BASE-DEFAULTVOL-001 默认音量不符：</strong>配置期望第{X}档，实测设备启动音量为第{Y}档。<br>
&nbsp;&nbsp;初步判断：固件 defaultVol 配置未完全生效或设备层音量映射有偏差，不影响命令词识别功能。
</div>
```

### 2. 首轮协议比对失败（时序问题）
```html
<div class="attn-box">
<strong>⚠️ 异常备注：</strong><br>
· <strong>BASE-CMD-XXX</strong> 首轮出现 ConfigFail（未捕获发送协议 / 协议不一致），重测后通过。<br>
&nbsp;&nbsp;初步判断：固件协议发送时机与测试工具读取窗口存在轻微时序偏差，不影响功能判定。
</div>
```

### 3. 唤醒偶发失败
```html
<div class="attn-box">
<strong>⚠️ 异常备注：</strong><br>
· <strong>BASE-WAKE-001</strong> 首次唤醒无响应，重测后通过，属设备偶发唤醒灵敏度波动。
</div>
```

---

## 六、发送邮件函数封装

```python
def send_test_report(to_addr, product_name, version, test_cases_html, attn_box=""):
    """发送 Mars-Belt 测试报告邮件"""
    from_addr   = os.environ["MAIL_FROM_ADDR"]
    password    = os.environ["MAIL_PASSWORD"]
    smtp_server = os.environ["MAIL_SMTP_SERVER"]
    smtp_port   = int(os.environ.get("MAIL_SMTP_PORT", "465"))
    
    HTML = f"""...（见上方模板）..."""
    
    # 构建附件 zip...
    
    msg = MIMEMultipart('alternative')
    msg['From']    = from_addr
    msg['To']      = to_addr
    msg['Subject'] = f"【测试报告】{product_name} 固件测试结果 {version}"
    msg.attach(MIMEText(HTML, 'html', 'utf-8'))
    
    # 添加附件...
    
    with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
        server.login(from_addr, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
```

---

---
