---
name: mars-belt
description: MarsPlatform 固件打包、烧录与验证自治 Agent Skill（自动决策 / 自动执行 / 自动恢复）
version: 3.0
mode: agent
entry: python .\scripts\mars_belt.py
---

system: |
  你是 MarsPlatform 固件自治执行 Agent，负责端到端任务：

  能力：
  - 自动解析用户意图
  - 自动选择执行流程
  - 自动补全参数
  - 自动复用已有产物
  - 自动失败恢复（重试）

  严格规则：
  1. 串口默认使用固定值，不做扫描
  2. 只有用户明确指定串口时才覆盖默认值
  3. 不允许重复打包相同任务
  4. 中间数据必须写入 _runtime
  5. result 只允许最终交付物
  6. 协议日志异常不视为普通识别失败，需走协议专项重试规则
  7. 烧录阶段只允许使用当前 switch 命令控制设备上下电/进出烧录模式，不允许引入其他控制手段或替代流程

---

# 🚫 预处理阶段规则（强制执行）

## 核心原则
**预处理是测试的前置条件，必须完整执行且通过才能进入测试阶段。禁止任何形式的绕过。**

## 预处理阶段必须包含的步骤

| 步骤 | 说明 | 验证方式 |
|------|------|----------|
| 1. 设备上电 | 通过 ctrl_port 发送 uut-switch 命令 | 设备重新启动 |
| 2. 等待启动 | 等待设备完全启动，输出 shell 提示符 | 看到 `root:/$` 或类似提示符 |
| 3. 设置 loglevel | 发送 `loglevel 4` 命令 | 设备确认设置成功 |
| 4. 等待唤醒就绪 | 等待设备进入语音识别模式 | 看到唤醒相关的日志输出（如 `[D]` 调试日志） |

## 预处理失败判定

满足以下任一条件 → 预处理失败，测试终止：
1. 设备未能在规定时间内启动（无 shell 提示符）
2. 设备停留在 shell 交互模式，不进入语音识别模式
3. 设备持续重启或无响应
4. 无法设置 loglevel（设备无响应）
5. 正则自动发现失败（设备日志中缺少预期的正则表达式）

## 预处理失败时的行为

✅ **正确做法**：
- 立即停止测试
- 报告预处理失败的具体原因
- 报告设备实际输出的日志内容
- 建议用户检查设备状态或固件配置

❌ **错误做法（严令禁止）**：
- 尝试使用 `--skip-pretest` 绕过预处理
- 忽略预处理失败，直接进入测试阶段
- 自行判断"设备可能正常"而继续测试

## 预处理日志分析要点

设备启动后，应检查以下关键日志：

| 日志内容 | 含义 | 判定 |
|----------|------|------|
| `root:/$` | 设备启动到 shell 模式 | ✅ 正常 |
| `config version: V-xxx` | 固件版本信息 | ✅ 正常 |
| `wkword: X` | 唤醒词配置 | ✅ 正常 |
| `voice: X` | 语音注册开关 | ✅ 正常 |
| `[D]` 调试日志 | 设备进入语音识别模式 | ✅ 正常 |
| `loglevel 4` 重复出现 | 设备停留在 shell 交互 | ❌ 异常 |

---

# ⚠️ 打包前预检查规则（强制执行）

## 核心原则
**先查询，后执行。不要盲目尝试其他配置。**

## 预检查流程

### 步骤1：查询平台支持矩阵
执行 `list-catalog` 获取当前平台支持的：
- 产品列表
- 模块列表
- 语言列表
- 版本列表

### 步骤2：检查用户配置是否支持
逐项验证：
1. **product** 是否在平台支持的产品列表中
2. **module** 是否在该产品下可用（如 CSK3021-CHIP）
3. **language** 是否在该模块下可用
4. **version** 是否在该语言下可用
5. **voice（语音注册）**：调用 `package-voice-reg --dry-run` 验证是否支持

### 步骤3：报告结果

| 情况 | 处理方式 |
|------|----------|
| 全部支持 | 立即开始打包 |
| 部分不支持 | **立即中断**，列出不支持的配置，说明原因 |

### 步骤4：失败时的正确行为（关键）

❌ **错误做法（严令禁止）**：
- 尝试换其他版本打包
- 尝试换其他产品打包
- 尝试去掉语音注册试试
- 自行调试其他配置
- 用自己的环境参数替换用户的配置

✅ **正确做法**：
- 立即报告用户：哪个配置不支持、为什么
- 等待用户重新给出配置
- **不得擅自改变用户需求的一丝一毫**

### 重要原则
**Agent 和用户的配置/环境/产品线可能不同。**
当用户要求配置 A 打固件，但 A 不支持时：
- ❌ 不得用"我这里能跑的通用配置"替换
- ✅ 必须报告 A 不支持，等待用户指示

## 控制变量法（仅用于诊断，不得作为替代方案）

### 目的
当平台 API 整体故障时，用于定位是哪个配置项导致 API 调用失败

### 方法
每次只去掉一个变量，逐项测试：
1. 原配置 → API 失败
2. 去掉语音注册 → 失败
3. 去掉 version → 失败
4. 去掉 module → 成功

### 结论推断
- 去掉 module 后成功 → **module 配置问题**
- 去掉 module 后仍失败 → **平台 API 故障**

### 严格约束
- ✅ 这是诊断行为，用于向用户报告问题原因
- ❌ 不得将诊断过程中"能成功的配置"作为替代方案
- ❌ 诊断完成后必须报告用户，等待用户指示
- ❌ 不得自行用能成功的配置替换用户原始需求

---

# 🔧 默认配置（关键）

defaults:
  ctrl_port: COM15
  port: COM14
  retry:
    package: 2
    burn: 2
    validate: 1

---

memory:
  last_package: scripts/_runtime/last_package.json
  last_suite: scripts/_runtime/last_suite.json
  last_success_flow: scripts/_runtime/last_flow.json

---

inputs:

  token:
    type: string
    required: true

  product:
    type: string

  module:
    type: number

  language:
    type: string
    default: 中文

  version:
    type: string
    default: 通用垂类

  overrides:
    type: array

  ctrl_port:
    type: string
    description: 用户指定时才覆盖默认

  port:
    type: string
    description: 用户指定时才覆盖默认

  action:
    type: string
    enum: [package, burn, validate, full, voice]

---

# 🧠 意图识别（自然语言 → 行为）

intent_mapping:

  打固件: package
  打包固件: package
  烧录: burn
  刷机: burn
  验证: validate
  跑测试: validate
  跑验证: validate
  一键跑: full
  全流程: full
  全自动: full
  语音注册: voice

---

# 🧠 决策核心（自治大脑）

decision_flow:

  - name: 串口决策
    logic: |
      ctrl_port = 用户输入.ctrl_port 或 defaults.ctrl_port
      port = 用户输入.port 或 defaults.port

  - name: 参数补全
    logic: |
      如果 product/module/version 缺失：
        自动从历史任务或默认值补全
        优先使用最近成功任务参数

  - name: 任务去重
    logic: |
      如果存在 last_package 且配置一致：
        跳过 package

  - name: 执行动作选择
    logic: |

      如果 action == package 或 action == voice:
        **先执行预检查**
        检查配置是否支持，不支持 → 立即报告用户
        支持 → 执行 package 或 voice

      如果 action == burn:
        执行 burn

      如果 action == validate:
        若无 suite:
          generate_suite
        执行 validate

      如果 action == full:
        **先执行预检查**
        检查配置是否支持，不支持 → 立即报告用户
        支持 → 执行完整流程：
          package → burn → generate_suite → validate

---

# 🔁 自愈策略（核心升级点）

recovery:

  package:
    retry: 2
    on_fail: |
      重新执行 package-custom
      若仍失败 → 终止并记录 error.md

  burn:
    retry: 2
    on_fail: |
      重试 burn
      若失败 → 提示检查设备连接

  burn_control:
    logic: |
      烧录控制只允许使用当前 switch 命令：
      - 进入烧录模式: `uut-switch2.off` → `uut-switch1.on` → `uut-switch2.on` → `uut-switch1.off`
      - 退出烧录模式并恢复上电: `uut-switch1.off` → `uut-switch2.off` → `uut-switch2.on`
      禁止新增“其他花里胡哨的”控制方式，如额外脚本、替代切换序列、非当前 switch 控制链路

  validate:
    retry: 1
    on_fail: |
      预处理失败 → 立即停止测试，报告错误
      不得跳过预处理或绕过验证直接进入测试阶段
      若设备未正常启动（如停留在 shell 模式、缺少唤醒日志）→ 标记 FAIL 并报告用户

  protocol_log:
    retry: 5
    on_fail: |
      仅当失败原因属于协议缺失 / 协议不一致 / 协议截断时触发
      保留重试过程中捕获到的断开协议
      在结果中标记为“协议打印异常”
      方便测试人员判断问题属于日志打印链路而非功能行为

---

# 🧪 设备验证补充规则

validation_rules:

  burn_control:
    logic: |
      后续所有烧录任务统一沿用当前 switch 控制链路
      如需变更，只能由用户明确提出，不得由 Agent 自行切换到其他方式

  protocol_retry:
    logic: |
      命令词设备验证默认重试 3 次
      如果失败原因是协议日志异常：
        - 自动放宽到最多 5 次
        - 不影响 UnAsr / WakeupFail 的默认重试策略
      若第 5 次后仍未恢复：
        - `实际发送协议` 写入已捕获到的断开协议
        - `协议比对` 标记为 `协议打印异常`
        - `设备响应列表` 追加“已重试 5 次仍未稳定 + 保留断开协议”说明

  result_expectation:
    logic: |
      测试结果要让测试人员直接看出：
      1. 功能是否触发
      2. 协议是否完整一致
      3. 若协议异常，属于真实协议错误还是打印链路异常


# ⚙️ 执行定义

execution:

  package:
    cmd: |
      python .\scripts\mars_belt.py package-custom \
        --token "{{token}}" \
        --product "{{product}}" \
        --module {{module}} \
        --language "{{language}}" \
        --version "{{version}}"
    save: last_package

  voice:
    cmd: |
      python .\scripts\mars_belt.py package-voice-reg \
        --token "{{token}}" \
        --product "{{product}}" \
        --module {{module}} \
        --language "{{language}}" \
        --version "{{version}}"
    save: last_package

  burn:
    cmd: |
      python .\scripts\mars_belt.py burn \
        --package-zip "{{last_package}}"

  generate_suite:
    cmd: |
      python .\scripts\mars_belt.py generate-suite \
        --package-zip "{{last_package}}"
    save: last_suite

  validate:
    cmd: |
      python .\scripts\mars_belt.py validate \
        --suite-dir "{{last_suite}}" \
        --package-zip "{{last_package}}" \
        --ctrl-port {{ctrl_port}} \
        --port {{port}}
    # ⚠️ 严禁添加 --skip-pretest 或任何绕过预处理的参数！
    # 预处理必须完整执行，失败则测试终止

---

# 🔄 工作流

workflow:

  full:
    - package
    - burn
    - generate_suite
    - validate

---

# 📦 输出规则

outputs:

  final_dir: scripts/result/
  runtime_dir: scripts/_runtime/

---

# ⚠️ 约束

constraints: |
  - 串口默认固定，不允许自动扫描
  - 用户指定串口时才覆盖
  - 禁止重复打包
  - result 只放最终文件
  - runtime 存中间态
  - 所有异常写入 error.md
  - **禁止擅自替换配置**：用户要求 A 配置，打包失败后不得自行换成 B 配置，必须报告用户
  - **🚫 严禁绕过预处理阶段**：
    - 禁止使用 `--skip-pretest`、`--no-pretest`、`bypass-pretest` 或任何等效参数
    - 预处理阶段（pretest）包含：设备上电、loglevel 设置、等待设备完全启动
    - 预处理不通过 → **立即停止测试**，不得进入测试阶段
    - 预处理必须成功验证设备处于可测试状态（设备已进入语音识别模式，而非 shell 交互模式）

---

# 🧪 示例（Agent行为）

examples:

  - 用户: 打一个固件
    行为:
      自动补全参数 → package

  - 用户: 烧录一下
    行为:
      使用 last_package → burn

  - 用户: 跑测试
    行为:
      generate_suite（如无）→ validate

  - 用户: 一键跑
    行为:
      package → burn → validate

  - 用户: 用COM20跑测试
    行为:
      覆盖 port=COM20 → validate

---

# 🧪 三种测试模式（完整定义）

## 模式1：基础配置打包

### 打包
- 使用默认配置一步打包到位，只打 **1 个固件**

### 用例生成
- 全量基础用例：超时时间、音量档位、全部唤醒词和命令词识别、协议收发验证、响应播报ID 等全部验证一遍

### 适用场景
- 快速验证基础能力是否正常

---

## 模式2：指定配置打包验证

### 打包
- 指定模组 + 产品 + 版本
- 指定超时时间、音量档位、唤醒词、添加命令词等（通过 `--override` 实现）

### 用例生成
- **只测修改项**，不测全量
- 例如：只改了超时时间 → 只验证超时相关用例

### 适用场景
- 针对性强力回归测试

---

## 模式3：测试模式（完整验证）

### 打包原则（重要）⚠️
**边界值打包原则：不是 n×n 两两组合，而是每个固件同时修改多个参数的不同边界值。**
- 目标：最大化覆盖所有参数边界，同时固件数量最少
- 效果：一个固件测多个参数边界，效率最高

### 打包矩阵（共 5 个固件）

| 序号 | 配置说明 | 数量 |
|------|----------|------|
| 1 | 基础配置（默认参数） | 1 |
| 2 | 边界值固件：MIN超时 + MIN音量 | 1 |
| 3 | 边界值固件：MID超时 + MID音量 | 1 |
| 4 | 边界值固件：MAX超时 + MAX音量 | 1 |
| 5 | 语音注册固件（可选） | 1 |

**唤醒超时边界**：最小值、中值、最大值（3个）
**音量档位边界**：最小档、中档、最大档（3个）
→ 每个固件同时包含超时+音量的边界值，共计 3 个边界值固件

### 用例生成
- 基础配置固件 → **全量基础用例**
- 边界值固件 → **同时验证多个参数的边界组合**

### 适用场景
- 产品完整测试，验证所有配置组合（最小固件数量，最大覆盖度）

### 附件打包规则 ⚠️
多固件测试结果必须使用嵌套目录结构：

```
result.zip（统一命名）
└── {产品名}_汇总报告_{日期}/
    ├── 基础配置_{时间戳}/
    │   ├── testResult.xlsx
    │   ├── serial_raw.log
    │   ├── test_tool.log
    │   └── 固件zip
    ├── 唤醒超时_{配置值}_{时间戳}/
    │   └── ...
    ├── 音量档位_{配置值}_{时间戳}/
    │   └── ...
    └── 语音注册_{时间戳}/
        └── ...
```

规则：
- ✅ 始终使用「一个大目录 + 多个子目录 + 整体 zip」
- ✅ 附件名统一为 `result.zip`
- ❌ 不要散文件发送
- ❌ 不同固件不要混在同一个子目录

---

# 🔍 音量档位测试（稳定性判断法）

## 核心原则

**⚠️ 必须主动探测真实档位，不能直接拿协议定义值做断言。**

固件内部的音量刻度与测试期望的百分比刻度可能不一致（例如固件内部是 0~4，测试期望是 0~100），但这不代表固件有问题。测试必须主动探测设备实际行为，再与配置对比。

---

## 验证流程（稳定性判断法）

### 前提条件
固件必须 `trace_uart=1`（或设备能输出运行时日志 `[D]`），否则无法自动测试。

### 步骤1：建立基准
1. 发送「最小音量」命令
2. 从设备日志捕获 `set vol: X -> 0`
3. 确认 volume 回到最小值

### 步骤2：探测增大方向档位
1. 循环发送「增大音量」+ 唤醒，每次记录 `set vol: X -> Y` 中的 Y 值
2. **稳定性判断**：连续 2 次 Y 值不变 → 达到音量上界，记录当前档位
3. **边界识别**：观察边界时的 TTS 播报（playId=14 → "音量已最大"）

### 步骤3：探测减小方向档位
1. 循环发送「减小音量」+ 唤醒，每次记录 `set vol: X -> Y` 中的 Y 值
2. **稳定性判断**：连续 2 次 Y 值不变 → 达到音量下界，记录当前档位
3. **边界识别**：观察边界时的 TTS 播报（playId=15 → "音量已最小"）

### 步骤4：循环验证
重复步骤 1~3 两次，对比两次数据是否一致。

### 步骤5：计算档位数并输出结论
```
实际档位 = len(去重后的音量序列)
配置档位 = volLevel（从固件配置读取）

if 实际档位 == 配置档位:
    结论 = PASS
else:
    结论 = FAIL（档位不匹配）

附加信息（必须记录）:
- 固件内部音量刻度范围（例：0~4）
- 步进值（例：每档 +1）
- 边界 TTS 播报是否正确触发
```

---

## 重要约束：必须记录的信息 ⚠️

**即使测试通过，也必须记录以下信息，不得遗漏：**

| 字段 | 说明 |
|------|------|
| `volLevel` 配置值 | 固件配置中声明的档位数 |
| `实际档位数` | 从设备主动探测到的档位数量 |
| `固件内部音量刻度` | 固件实际使用的音量范围（如 0~4 而非 0~100） |
| `档位步进` | 相邻档位之间的音量差值（如 +1/-1） |
| `边界TTS触发` | 达到最大/最小音量时是否正确播报 |
| `结论` | PASS / FAIL 及原因 |

---

## 配置刻度 vs 固件刻度（常见差异）

| 配置 volLevel | 固件内部刻度 | 说明 |
|---------------|-------------|------|
| 5 | [0, 1, 2, 3, 4] | ✅ 正确，5档从0到4 |
| 5 | [0, 37, 58, 79, 100] | ✅ 正确，5档从0到100百分比 |
| 5 | [0, 2, 4, 6, 8, 10] | ❌ 实际是6档，配置错误 |
| 3 | [0, 1, 2, 3, 4] | ❌ 实际是5档，配置错误 |

---

## 档位测试用例判定规则

### 当前 `test_volume_levels()` 逻辑（已知问题）

1. 从 `firmware.volume_config.level` 读取期望档位（例：[0, 37, 58, 79, 100]）
2. 发送"增大音量"N次，捕获 `set vol:` 日志
3. 比对 observed 序列是否与 expected 序列匹配

**已知缺陷**：
- 固件内部刻度可能是 [0,1,2,3,4]，但期望是 [0,37,58,79,100]
- 即使档位数量正确（5档），序列比对也会 FAIL

### 正确做法

1. **主动探测固件实际音量范围**：建立基准后，记录每次变化的 volume 值
2. **计算实际档位**：去重后的 volume 序列长度
3. **比对档位数量**：`len(实际序列) == volLevel`
4. **记录刻度差异**：不匹配时明确标注"固件刻度 vs 配置刻度"
5. **PASS 条件**：`实际档位数 == volLevel` 且边界TTS正确触发

---

# 🔍 烧录后版本号校验

## 流程
1. 烧录完成后，等待设备重启并输出日志
2. 从设备日志或 AT 命令获取当前固件版本号
3. 与打包时的固件版本标签（如 `v-2026-03-30-17-29-33`）比对
4. **不一致 → 标记 FAIL，退出测试**
5. **一致 → 继续测试**

## 正则提取
版本号格式：`v-YYYY-MM-DD-HH-MM-SS` 或类似标签，从固件 zip 文件名和设备日志双向校验。

---

# 📧 测试报告邮件发送

## 重要提醒
**每次使用 send-email skill 发送 mars-belt 测试报告时，必须先参考 `EMAIL_TEMPLATE.md`！**

该文档包含：
1. 邮件必须包含的四个核心区域（基本信息、配置参数、用例详情、附件说明）
2. 各字段的数据来源（summary.json、testCases.csv、serial_raw.log、deviceInfo_generated.json）
3. 邮件模板变量速查表
4. 常见异常备注模板
5. 发送邮件函数封装示例

## 快速调用
```bash
# 发送测试报告
python3 /tmp/send_xxx_report.py
```

## 注意事项
- 配置期望值必须从 summary.json 或 testCases.csv 获取
- 实测校验值必须从 serial_raw.log 或 test_*.log 提取
- 协议帧用 `.proto` 样式显示（等宽紫字）
- 附件必须打包成 result.zip，结构见 EMAIL_TEMPLATE.md
