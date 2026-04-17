---
name: mars-belt
description: MarsPlatform 固件打包、烧录与验证自治 Agent Skill（自动决策 / 自动执行 / 自动恢复）
version: 3.1
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
  8. `vcn` 必须与产品语言匹配：
     - 中文产品只能选择中文发音人
     - 英文产品必须选择英文发音人
     - 若语种不匹配导致构包失败，归类为配置错误，不得误报为平台通用故障
  9. 重启异常优先级最高：
     - 发现重启迹象后，必须先区分“用例主动断电/重上电”与“设备测试过程中自行重启”
     - 任何非用例预期的重启，或无法证明是主动断电导致的重启，一律按 `FAIL` 处理
     - 不得因重试恢复、后续 case 通过、设备最终恢复可用或顶层汇总正常而掩盖重启事实
  10. 执行完整产品验证、边界值方案、控制变量复测、结果汇总、邮件发送前，必须先阅读 `FULL_CHAIN_VALIDATION_RULES.md`
  11. 同一产品同一轮验证必须复用同一产品标号/周标，不得因失败、阻塞或中途调整策略另起新标号
  12. 必须先按当前产品能力裁剪范围，只测试当前产品 `Supported / Optional / directly_editable` 的功能点
  13. `欢迎语 TTS 文案(word)` 不属于固件运行验证项，不得写入固件功能通过结论
  14. 常规包默认保持平台串口选择，仅验证 `uportBaud` 与 `logLevel`；只有用户明确要求或为控制变量定位时才单独修改串口路由
  15. 组合包出现 `FAIL`、`BLOCK` 或系统性异常后，必须将其他参数回默认，仅保留当前问题点及最小依赖重新打包复测，不得靠猜测归因

---

# 🚨 重启异常判定规则（最高优先级）

## 核心原则
- 只要在测试过程中观察到重启迹象，必须先判定重启类型，再继续后续分析
- 所有重启事件都必须写入结果和报告，禁止被重试、恢复或汇总 `PASS` 吞掉
- 用例显式要求的断电/上电，只能记为“主动断电重启”，不得与设备自行重启混淆
- 任何非用例预期的重启，或证据不足无法证明是主动断电导致的重启，一律判定为 `FAIL`

## 判定流程
1. 先核对当前步骤是否明确要求断电/重上电
2. 若是用例要求的控制动作，记录为“主动断电重启”，并保留上下电证据
3. 若不是用例要求，则直接归类为“设备自行重启”
4. 对“设备自行重启”必须继续定位到触发该重启的动作、步骤或具体 case
5. 报告中必须明确写出：重启类型、触发 case、触发动作、证据日志位置、最终判定

## 结果约束
- “主动断电重启”只允许出现在用例显式要求的步骤中，且只能作为控制动作记录，不能拿来冲抵异常结论
- “设备自行重启”一律 `FAIL`
- 若当前包同时改了多个参数，且出现设备自行重启，必须按控制变量法继续缩到参数组、单参数或最小依赖组合
- 禁止把包含重启的 case、专项或整包写成“最终无遗留失败项”

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

## Token 读取规则（新增强制）

- ListenAI token 统一从当前 skill 根目录 `TOOLS.md` 读取，键名固定为 `LISTENAI_TOKEN=`
- 当用户提供新 token 时，必须先同步更新当前 skill 下的 `TOOLS.md`，再继续执行任何打包/查询
- 后续执行 `list-catalog`、`package-custom`、`package-voice-reg` 等平台接口时，默认优先使用 `TOOLS.md` 中最新 token
- 若 `TOOLS.md` 缺失或 token 无效，立即中断并向用户报告，不得继续沿用旧 token

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
6. **vcn（合成发音人）** 是否与产品语言匹配：
   - 中文产品禁止选择英文发音人
   - 英文产品禁止选择中文发音人
   - 若用户给定的 `vcn` 与产品语言冲突，必须在打包前直接报告

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

# 📚 全链路规则入口

- 执行完整周测、边界值+中值打包、状态型专项、多唤醒/语音注册、控制变量复测、结果目录整理、邮件发送前，必须先阅读 [`FULL_CHAIN_VALIDATION_RULES.md`](FULL_CHAIN_VALIDATION_RULES.md)
- `FULL_CHAIN_VALIDATION_RULES.md` 是当前生效的全链路 SOP；若与历史说明、旧报告模板或旧专项文档冲突，以该文件为准
- `MARS_BELT_WORKFLOW.md`、`platform_feature_test_plan.md`、`3021_zh_heater_vertical_scope_and_validation.md` 可作为案例和补充背景，但不应覆盖本 skill 的现行规则

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

---

# 🎙️ 语音注册与多唤醒专项规则

## 产品能力前置门控
- 任何产品开始打包前，必须先读取当前产品对应的 `parameter_catalog.json` 或实时 feature map
- 只允许对当前产品 `feature_gate=Optional` 或当前前端 `directly_editable=true` 的功能生成专项包
- 若 `voice_regist=Unsupported`：
  - 禁止生成语音注册专项包
  - 禁止把 `voiceRegEnable`、`releaseRegist.*`、`releaseRegistConfig.*` 写进当前产品结论
- 若 `multi_wakeup=Unsupported`：
  - 禁止生成多唤醒专项包
  - 禁止把 `multiWkeEnable`、`multiWkeMode`、`releaseMultiWke.*`、`wakeWordSave` 的多唤醒链路写进当前产品结论
- 只读字段只能做“观察项”，不能伪装成可配置验证项
  - 典型只读项：`traceBaud`、`ctlIoPad`、`ctlIoNum`、`holdTime`、`paConfigEnableLevel`、`protocolConfig`

## 语音注册
- 只有打开 `voiceRegEnable` 后才生成并执行语音注册专项；未打开时一律跳过语音注册相关用例
- 进入 `学习命令词`、`学习唤醒词`、`删除命令词`、`删除唤醒词` 等交互态后，必须等待当前提示播报结束（以 `play stop` 为准）且算法状态恢复，再允许下一句交互
- 除字数上下限外，其余平台语音注册配置都要覆盖正例和反例；学习语料必须本地自定义合成，不能直接使用命令词、唤醒词或提示词内容
- 学习成功后必须使用学习语料验证真实生效；学习失败后必须使用同一学习语料验证不生效
- 删除命令词、删除唤醒词必须做闭环验证：
  - 先学习成功
  - 再验证学习词当前确实可用
  - 执行删除动作
  - 删除成功后验证该学习词不可用
  - 若走“退出删除 / 删除失败”分支，必须验证该学习词仍可用
- 删除相关场景若出现设备重启、不识别、不播报或其他系统级异常，按“重启异常判定规则”优先处理：
  - 先区分是否为用例要求的主动断电
  - 若为设备自行重启，必须定位触发 case/动作并直接记 `FAIL`
  - 不得靠重试、恢复或顶层汇总把该异常降级为 `PASS`

## 多唤醒切换
- 只有打开 `multiWkeEnable` 后才生成并执行多唤醒专项；未打开时一律跳过多唤醒相关用例
- 做切换验证前，必须先按平台默认唤醒词格式协议新增 2 个额外唤醒词；只有 1 个唤醒词时不得进入切换验证
- 打包什么配置就验证什么配置：当前固件启用哪一种切换模式，就只验证该模式下可用的切换、恢复、查询、默认唤醒词、冻结唤醒词等能力，并覆盖正反例
- `specified`、`loop`、`protocol` 三种模式需要分别独立打包、独立验证，不能混成一套结论
- 切换、恢复、查询过程中若出现设备自动重启、不识别、不播报或协议链路整体异常，按“重启异常判定规则”处理：
  - 自行重启一律 `FAIL`
  - 必须定位到触发重启的动作、步骤或具体 case
  - 不做无限重试，不得把异常包记成通过


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

### 语言模板规则
- 中文基础配置继续使用平台实时返回的 `sourceReleaseId` 与 `getAlgoData` 基线
- 英文基础配置在命中以下目标时，`package-custom` / `package-voice-reg` 默认自动切到本地内置英文模板：
  - 产品：`取暖器`
  - 场景：`纯离线`
  - 模组：`CSK3021-CHIP`
  - 语言：`英文`
  - 版本：`通用垂类-V2.0_F2.0.3_A1.7.1.0`
- 该英文内置模板会自动执行：
  - 默认共享产品名：`3021-取暖器-英文通用版本-0408`
  - 默认英文标量参数源版本优先：`2041795582273081345`
  - 若该源版本失效，则回退到该英文共享产品下最新可用 release
  - 默认算法词表模板：`scripts/config/base_algo/csk3021_heater_en_generic_v2_0_f2_0_3_a1_7_1_0.json`
- 原始参考模板保留在：
  - `scripts/聆思科技_算法配置英文模板.xlsx`

### 用例生成
- 全量基础用例：超时时间、音量档位、全部唤醒词和命令词识别、协议收发验证、响应播报ID 等全部验证一遍

### 适用场景
- 快速验证基础能力是否正常

---

## 模式2：指定配置打包验证

### 打包
- 指定模组 + 产品 + 版本
- 指定超时时间、音量档位、唤醒词、添加命令词等（通过 `--override` 实现）

### 命令词新增逻辑（已验证）
当用户要求“在现有可打包基础配置上新增命令词”时，必须严格按下面逻辑执行：

1. **先取真实算法基线**
   - 不允许根据 `web_config.json` 反推词条结构
   - 必须先调用平台真实接口：`/fw/release/getAlgoData?id=<sourceReleaseId>`
   - 这份返回值就是平台算法词条的真实编辑态基线

2. **在真实基线上增量追加**
   - 不允许清空重建整个 `releaseAlgoList`
   - 不允许整体替换原始算法词条
   - 必须保留基线原始词条，只在列表尾部追加新增命令词

3. **新增命令词对象必须复用真实命令词模板结构**
   - 选一条现有 `type=命令词` 的真实词条作为模板
   - 允许修改：`word`、`extWord`、`reply`、`sndProtocol`、`recProtocol`、`idx`
   - 其他平台字段保持模板兼容结构
   - 新增项的 `children` 默认置空：`children=[]`

4. **泛化词生成规则（强约束）**
   - 默认只保留主词条 `word`
   - `extWord` 最多保留 1 个，且只能是该主词条的直接别名
   - 禁止批量生成跨命令复用的 children 模板
   - 禁止使用类似 `<请/帮我>[打开/开启/关闭][暖风/取暖/摇头]` 这类会跨多个命令词展开冲突的组合模板
   - 默认禁止给新增命令词生成 `children[*].extWord`，除非用户明确要求且需要单独验证容量

5. **保存与打包路径**
   - 将完整的增量后列表以 `releaseAlgoList=<json>` 形式作为 `--override` 传入 `package-custom`
   - 必须显式追加 `--enable-algo-words`
   - 未显式传 `--enable-algo-words` 时，普通打包必须忽略 `releaseAlgoList/releaseDepthList` 覆盖，避免误触发算法词条修改
   - `listenai_custom_package.py` 会自动进入 `algoUnifiedSave` 路径后再执行正式打包

6. **容量校验规则（关键）**
   - 即使配置格式正确，3021 机型也可能因为词量增加导致算法实例内存超限
   - 如果日志出现：`算法实例内存已超出(... bytes)，请减少词数量`
   - 结论应判定为：**新增逻辑正确，但词量/泛化量超出当前机型容量**
   - 此时优先减少新增词数量，其次再减少 `extWord`

7. **失败诊断优先级**
   - `415 参数格式错误` → 说明 `releaseAlgoList` 结构不对
   - `500 服务器异常` 且未进入编译 → 继续检查词条对象结构/字段兼容性
   - 编译日志出现内存超限 → 说明结构已走通，问题是词量容量

### 用例生成
- **只测修改项**，不测全量
- 例如：只改了超时时间 → 只验证超时相关用例
- 若指定配置包出现 `FAIL` / `BLOCK` / 系统性异常，不允许直接猜测是“组合包干扰”
- 必须立即生成“其他参数默认 + 当前问题点 + 最小依赖”的控制变量包复测

### 适用场景
- 针对性强力回归测试

---

## 模式3：测试模式（完整验证）

### 打包原则（重要）⚠️
**边界值打包原则：不是枚举所有组合，而是用最少的包覆盖最多的边界值与依赖链路。**
- 大多数产品应先按“约 `5` 个包”规划，不要无节制扩包
- 只有组合包出现 `FAIL`、`BLOCK`、重启、打包异常或系统性异常时，才允许追加控制变量包
- 数值/枚举参数必须覆盖左边界、中值、右边界
- 字符串参数通常验证 `1` 次即可
- 布尔参数覆盖 `true/false`
- 依赖型参数必须放在同一包里联动验证
- 当前产品不支持的能力必须裁掉，不能为了凑模板硬测

### 推荐打包矩阵

| 包类型 | 作用 | 常见配置 |
|------|------|------|
| 基础中值稳定包 | 建立稳定基线，一次性覆盖字符串项和中值 | `timeout/volLevel/defaultVol` 中值，`speed/vol/compress` 中值，兼容 `vcn`，上下溢播报语，`paConfigEnable` 默认 |
| 左边界组合包 | 覆盖低边界 | `timeout` 最小、`volLevel` 最小、`defaultVol` 最小、`uportBaud` 最小、`logLevel` 最小、TTS 低边界 |
| 右边界组合包 | 覆盖高边界 | `timeout` 最大、`volLevel` 最大、`defaultVol` 最大、`uportBaud` 最大、`logLevel` 最大、TTS 高边界、`paConfigEnable=true`、必要时 `volSave=true` |
| 状态/依赖开启包 | 覆盖掉电保持和算法依赖链路 | `multiWkeEnable=true`、`wakeWordSave=true`、`voiceRegEnable=true`（仅产品支持时）、新增 `2` 个唤醒词 |
| 状态/依赖关闭或隔离包 | 覆盖另一布尔值或单链路隔离 | `wakeWordSave=false`、`volSave=false/true`、多唤醒单模式隔离、语音注册单专项 |
| 控制变量包 | 仅在问题出现后追加 | 其他参数全部默认，只保留问题参数和最小依赖 |

### 关键依赖
- `wakeWordSave` 不是孤立项，必须与 `multiWkeEnable=true` 同测，并新增 `2` 个额外唤醒词后再做切换和断电验证
- `voiceRegEnable` 仅在当前产品支持时加入；不支持时不得打语音注册专项包
- `vcn` 只需保证与产品语言匹配；若默认发音人已匹配当前产品语言，不要为了覆盖而每包切换发音人
- `speed`、`vol`、`compress` 可按边界值和中值变化，但不要求每次跟着切 `vcn`
- `欢迎语 TTS 文案(word)` 是页面对服务器合成的验证项，不纳入固件运行态打包验证
- 串口选择默认保持平台默认；常规只验证 `uportBaud` 和 `logLevel`
- 若用户明确要求验证串口路由，或为定位异常必须单独验证串口路由，则必须同步修改本地串口映射和波特率；否则会误判为通信失败
- 算法配置的验证结果必须进入最终报告，不能只写基础配置结果

### 用例生成
- 基础中值/边界组合包：运行当前包涉及功能点 + 必要基础烟测
- 状态/依赖包：运行对应功能点的完整闭环验证
- 控制变量包：只验证当前问题点，不重跑全量用例

### 适用场景
- 产品完整测试，用最少的包数证明每个“当前产品支持的功能点”最终是 `PASS`、`FAIL` 还是 `BLOCK`

### 附件打包规则 ⚠️
多固件测试结果必须做到“一个固件包，对应一个同名验证目录”，并统一汇总到 `result.zip`：

```text
result.zip
└── result/
    ├── 包01-基础中值稳定包0413xxxx/
    │   ├── 固件zip
    │   ├── burn.log
    │   ├── serial_raw.log
    │   ├── test_tool.log
    │   ├── testResult.xlsx
    │   ├── test_report.html
    │   └── 其他断言结果文件
    ├── 包02-左边界组合包0413xxxx/
    │   └── ...
    └── 包03-控制变量-音量保持0413xxxx/
        └── ...
```

规则：
- ✅ 每打一个包，就必须有一个对应的结果目录
- ✅ 报告里“执行包”名称必须与目录名一一对应
- ✅ 目录内必须同时包含该包固件、日志、结果文件和必要断言产物
- ✅ `result.zip` 里只放实际执行过的包目录，不要把无关临时目录混进去
- ❌ 不要在报告里只写“左边界/右边界”，必须写实际参数值
- ❌ 不要把多个包的结果混在同一个目录

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
**每次使用 send-email skill 发送 mars-belt 测试报告时，必须同时参考 `EMAIL_TEMPLATE.md` 和 `FULL_CHAIN_VALIDATION_RULES.md`！**

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
- 邮件正文以“功能点结果”为主，只写 `PASS / FAIL / BLOCK`
- `FAIL / BLOCK` 必须说明：哪个包、哪些实际参数值、出现了什么异常、与预期不符在哪里
- 算法配置结果必须单独展示，不能只写基础配置
- 配置期望值必须从 `summary.json` 或 `testCases.csv` 获取
- 实测校验值必须从 `serial_raw.log` 或 `test_*.log` 提取
- 执行包名称必须与附件中的目录名一致
- 附件必须打包成 `result.zip`，结构以 `FULL_CHAIN_VALIDATION_RULES.md` 为准
