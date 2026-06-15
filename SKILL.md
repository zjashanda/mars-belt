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
  16. 烧录文件必须先走固定暂存流程：
    - 先清空 `scripts/burn/app.bin`
    - 再把目标固件复制到 `scripts/burn/app.bin`
    - `Uart_Burn_Tool` 只允许烧录 `scripts/burn/app.bin`
    - 禁止把任意外部 `.bin` 路径直接喂给烧录工具
  17. 当前本地 3021 台架默认口径：日志 `/dev/ttyACM0@115200`、协议 `/dev/ttyACM2@9600`、控制 `/dev/ttyACM4@115200`、烧录 `/dev/ttyACM0@460800`；`/dev/ttyACM1` 为空口不要使用；电源控制 `uut-switch1`，协议连接门控 `uut-switch2`，boot 控制 `uut-switch3`。
  18. 3021 正常运行态上电必须使用协议口门控：`uut-switch2.off -> uut-switch3.off -> uut-switch1.off -> uut-switch1.on -> wait 3s -> uut-switch2.on`。脚本化配置中使用 `sleep:3` 表示该等待点；烧录进 boot 前先执行 `uut-switch2.off` 断开协议口，再执行 `uut-switch1.off -> uut-switch3.on -> uut-switch1.on -> uut-switch3.off`，烧录后按正常门控上电恢复。
  19. 根目录 `orion.skilltest.json` 是 Augur/Orion 展示“可测模块 -> 测试方案 -> 自然语言用例 -> 执行证据”的结构化索引；新增、删除或调整平台功能测试模块、入口脚本、证据口径、风险等级、默认用例时，必须同步更新该 JSON，并执行 `python3 -m json.tool orion.skilltest.json` 校验。
  20. 同步 git 前必须先构建可迁移发布副本：包含 `SKILL.md`、`orion.skilltest.json`、必要脚本、模板、参考资料和工具；排除 `TOOLS.md`、`deviceInfo_generated.json`、`plan.md`、`artifacts/`、烧录临时 `app.bin`、缓存和本机结果。其他 PC 拉取后应能基于 `TOOLS.example.md` 与 `deviceInfo_generated.example.json` 补齐本机配置后直接使用。
  21. 生成报告、JSON、CSV、Markdown、HTML、xlsx、zip 或其他交付文件后，必须做可打开性和编码校验，避免其他环境打开乱码或文件损坏；校验结果要写入结果目录或 `plan.md`。
  22. 平台固件打包从 2026-06-12 起强制采用 UI-only 路径：产品创建/复用、基础配置、算法导入、深度调优、生成打包必须由浏览器 UI 触发；禁止用历史 API 参数创建隐藏产品、强写 UI 不可选垂类/版本/配置或直接 API 发起打包。接口只允许用于登录态注入、只读枚举交叉确认、release 状态轮询和证据采集，不能作为主打包动作。
  23. 后续平台打包测试默认使用“固定产品 + 同产品多 release + 最小规则矩阵”：每个垂类先固定一个代表品类，按产品能力生成 3/4 个组合包；若只支持基础/多唤醒通常 3 包，若同时支持语音注册和多唤醒通常 4 包。打包完成后必须按 release 实际参数生成真机验证方案。
  24. UI 打包时如果平台为空或目标产品不存在，必须通过 UI 新建一个产品；同一产品不同配置必须继续在该产品下生成多个 release，不得因为配置不同再新建产品。
  25. 每个 release 必须填写简短版本描述，描述当前配置向量即可，例如 `默认+指定唤醒`、`左边界+循环唤醒`、`右边界+协议唤醒`、`默认+指定学习+指定唤醒`、`关闭隔离`。描述要短，不写产品名、长版本号或冗余说明；若 UI 当前不暴露版本描述输入，必须记录为 UI 限制，禁止用 API 补写后冒充 UI 结果。
  26. 3021 设备/烧录/协议/语音链路异常时，优先烧录已验证的基础冒烟固件 `assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip` 做对照。该固件和台架控制逻辑必须随 git 同步，不能作为 artifacts 临时文件删除；详细隔离思路、命令和期望标记见 `references/3021_known_good_smoke_firmware.md`。
  27. 平台打包/固件/SDK 真机验证报告必须按“标题下测试结论 -> 测试目的 -> 测试方案 -> 测试用例和结果 -> 测试问题与分析 -> 证据文件”结构输出；详细规则见 `references/platform_test_report_writing_standard.md`。报告必须量化覆盖范围和结果，不能只写“全部通过”。
  28. 固件运行态语音识别音频优先使用平台「合成管理 -> 音频合成」正式构建产物：先查 `assets/audio/platform_synthesis/<lang>/<suite>/`，缺失时用 `scripts/py/listenai_platform_audio_synthesis_cache.py` 走正式音频合成项目/输出构建并下载；禁止把 `/fw/common/generateAudio` 试听接口产物作为识别主证据。中文合成必须选择中文发音人并排除英文/英语标识候选；英文合成必须选择英文/英语标识发音人。音频资产属于测试资料，必须随 skill git 同步。详细流程见 `references/platform_audio_synthesis_test_assets.md` 和 `references/english_platform_audio_synthesis_runtime_workflow.md`。
  29. 3021 英文版本验证必须先实时扫描 UI 当前 `英文 + CSK3021` 支持矩阵，不能从中文垂类外推。若 UI 仅开放 `通用垂类`，则“每垂类一个代表品类”只需在通用垂类下选一个代表品类，例如 `风扇`；报告必须写清 `风扇` 是品类不是英文风扇垂类。基础英文能力按 3 包最小规则执行，`timeout<=1` 左边界包只验证唤醒+超时，不作为命令词正例；详细规则见 `references/3021_english_ui_minrule_validation_lessons.md`。

---


# 🧭 Orion SkillTest Profile 维护规则

## 适用范围
- `orion.skilltest.json` 位于 skill 根目录，供 Augur/Orion 扫描当前 skill 支持的平台化测试能力。
- 该文件不是示例文档，而是当前能力清单。任何测试模块变更都必须同步维护。

## 必须同步更新的场景
1. 新增或下线功能模块，例如新增合成管理子模块、语音注册策略、平台接口验证、设备验证或专项需求验证。
2. 修改模块执行入口，例如脚本路径、命令参数、runner 类型、默认模式或结果目录。
3. 修改证据口径，例如 `browser_ui`、`ui_equivalent_api`、`api_probe`、`device_evidence` 的归类规则。
4. 修改真实执行副作用，例如新增烧录、上下电、播放音频、平台记录保留或 SDK 发布动作。
5. 修改默认自然语言用例、PASS/FAIL/BLOCKED 判定、前置条件或风险等级。

## 更新要求
- `capabilities[].id` 必须稳定且唯一，不能随意改名；确需改名时要保留迁移说明。
- 每个能力必须包含可展示的自然语言 `test_cases`，不得只写命令行。
- 设备相关能力必须明确串口、声卡、烧录、上下电等副作用和阻塞条件。
- UI-only 相关能力必须明确主结论只能来自浏览器/人工 UI 触发；直连接口只能作为只读辅助探测或非 UI 健壮性附录。
- UI-only 固件打包必须每次重新读取平台 UI 当前页面、下拉选项和联动结果；历史导出的产品/芯片/语言/SDK 矩阵只能作为参考，不能作为脚本内置死数据或后续打包输入源。详细流程见 `references/ui_firmware_packaging_workflow.md`。
- 平台固件打包默认按“最小组合包”设计矩阵：首包默认配置验证主链路，后续包组合覆盖基础边界、协议、保存项、多唤醒模式、语音注册模式和模板数/重试次数；详细参考 `references/platform_firmware_minimal_packaging_strategy.md`。
- 平台 UI 异常参数过滤验证必须使用真实浏览器 UI 输入和点击，专项策略见 `references/ui_invalid_input_validation_strategy.md`。主结论按 `PASS_REJECTED`、`PASS_SANITIZED`、`RISK_ACCEPTED`、`RISK_SILENT`、`UNSUPPORTED_REASONABLE`、`SCRIPT_LIMITATION` 分类；直接写接口或伪造 UI 不可填字段不能计入 UI 主结论。
- 3021 已验证冒烟固件、台架控制逻辑和问题隔离流程见 `references/3021_known_good_smoke_firmware.md`；该 zip 资产位于 `assets/firmware/3021-smoke/`，必须随 git 发布。
- 固件打包算法模板必须按“参数能力/测试类型”选择，不能只按中文/英文或基础/多唤醒/语音注册粗分。模板覆盖矩阵见 `references/platform_firmware_template_requirement_matrix.md`，生成资产见 `assets/templates/template_manifest.json`。
- 3021 UI-only 全量打包必须按“同产品多固件版本 + 配置向量包”执行：`base_*`、`multi_*`、`voice_*` 是同一产品下的多个 release profile，不是每个配置新建产品，也不是一参一包。最终报告必须展开 `coveragePoints` 证明单包覆盖基础参数、串口/日志、掉电保存、播报、算法模板和专项能力。
- 平台打包、固件真机、SDK 编译和 app.bin 运行态验证报告必须先按证据生成详细 Markdown，再按 `references/platform_test_report_writing_standard.md` 生成结构化 HTML：结论放标题下，之后依次写测试目的、测试方案、测试用例和结果、测试问题与分析、证据文件。
- 中英文固件运行态验证必须优先使用 `assets/audio/platform_synthesis/<lang>/<suite>/` 中的平台音频合成正式产物；若资产缺失，先调用 `scripts/py/listenai_platform_audio_synthesis_cache.py` 生成平台可见的「音频合成」项目和输出，再执行对应运行态验证脚本。试听接口只可作为预检，不能计入主结论。
- 3021 英文版本专项口径见 `references/3021_english_ui_minrule_validation_lessons.md`：先实时扫描英文支持垂类，当前若只开放 `通用垂类` 则选一个代表品类执行基础 3 包；`风扇` 等名称必须标为品类，不能写成英文独立垂类。
- 严格 UI-only 打包中，产品创建也必须通过 UI；平台为空或目标产品不存在时，按页面实时联动新建产品，不允许使用旧 API 参数创建隐藏产品壳。API/options 只能作为只读排查；非严格兼容性兜底必须单独标记，不能计入 UI-only 主结论。
- 同一产品下生成多个 release 时，版本描述使用短配置摘要，便于平台列表直接区分；不得留空、不得写长段说明。若页面确实没有输入入口，记录 `version_description_ui_not_exposed`。
- V1.0 老版本若配置可到完成页但生成后 release 列表为空，标记 `legacy_v1_generate_no_release`，不能按成功或未测处理；需要附 result、截图、产品 id 和 release 列表为空证据。
- 更新后必须执行：

```bash
python3 -m json.tool orion.skilltest.json >/tmp/orion.skilltest.check.json
```

- 如果 `SKILL.md`、`SYNTHESIS_MANAGEMENT_VALIDATION.md`、`语音注册专项测试说明.md` 或脚本能力发生变化，而 `orion.skilltest.json` 未同步，视为 skill 资料不完整。

# 生成文件可用性与编码校验规则

## 适用范围
- 所有对外交付或后续流程会复用的文件：报告、用例表、测试数据、JSON、CSV、Markdown、HTML、xlsx、zip、固件/SDK 索引、邮件正文附件。
- 本地临时缓存不需要交付时可不校验，但不得混入最终结果目录或 git 发布副本。

## 生成要求
- 文本类文件统一使用 UTF-8 写入；面向 Excel 打开的 CSV 优先使用 `utf-8-sig`，或直接生成 xlsx。
- JSON 必须保证可被标准 JSON parser 读取，不允许含注释、尾逗号或半截写入内容。
- xlsx 必须用标准库或 `openpyxl`/`xlsxwriter` 生成，不能把 CSV 改后缀伪装成 xlsx。
- zip/固件/SDK 包必须保持二进制原样写入，不能经过文本编码转换。

## 必做校验
1. 文本/Markdown/HTML/CSV：生成后立即用 `encoding="utf-8"` 或 CSV 约定编码重新读取；如含中文，抽样确认关键字段未变成乱码。
2. JSON：执行 `python3 -m json.tool <file> >/tmp/<name>.json.check` 或等价 parser 校验。
3. xlsx：使用 `openpyxl.load_workbook(<file>, read_only=True)` 或等价方式打开并读取表头/首行。
4. zip：执行 `unzip -t <file>` 或 Python `zipfile.ZipFile.testzip()`。
5. 邮件/报告交付：发送前打开最终生成文件或读取正文，确认标题、中文章节名、表格字段可正常显示。

## 记录要求
- 校验通过要在对应结果目录写入 `validation_summary.json`、`README.md` 或报告附录；临时任务也要同步到 `plan.md`。
- 校验失败必须先修复文件生成逻辑，再交付或同步 git；不得只在回复中说明“本机可用”。

# 🎙️ 合成管理验证规则

## 适用范围
- 用户要求验证平台「合成管理」「音频合成」「播报合成」时，必须使用独立模块 `scripts/py/synthesis_management/`。
- 兼容入口仍保留：`scripts/py/listenai_synthesis_validation.py`，内部只转调 `synthesis_management.validation`。
- 标准命令：`python3 scripts/py/listenai_synthesis_validation.py --publish-broadcast`。
- 模块命令：`PYTHONPATH=scripts/py python3 -m synthesis_management.validation --publish-broadcast`。
- 若用户明确要求在账号页面查看新生成物，使用：`python3 scripts/py/listenai_synthesis_validation.py --publish-broadcast --keep-platform-records`，并在回复中明确临时记录名称和 ID。
- Token 仍按本 skill 规则从 `TOOLS.md` 的 `LISTENAI_TOKEN=` 读取。
- 结果统一写入 `artifacts/synthesis-validation/<YYYYMMDD-HHMMSS>/`，不得散落到其他目录。
- 固件识别用音频必须走「音频合成」正式构建而不是试听接口：`PYTHONPATH=scripts/py python3 scripts/py/listenai_platform_audio_synthesis_cache.py --language en --suite 3021_fan_base --text 'Hello My Dear' ...` 或 `--language zh --suite 3021_fan_base --text '小聆小聆' ...`。该脚本先复用本地资产，缺失时创建平台可见项目并下载 zip；需要人工在平台查看时使用 `--force-synthesize` 重新生成并保留项目记录。
- `assets/audio/platform_synthesis/<lang>/<suite>/` 属于小体积可复用测试资产，需要随 git 同步；`artifacts/` 下的音频合成 zip、截图、运行日志和 token 不同步。

## 必测链路
1. 菜单和字典巡检：确认 `合成管理/音频合成/播报合成`、发音人、压缩比存在。
2. 音频合成：模板下载、`generateAudio` 试听、临时项目创建/查询/详情/编辑、Excel 导入、草稿保存、产物详情、备注编辑、草稿转合成、手填产物合成、zip 下载、产物/项目清理。
3. 播报合成：芯片/版本选项、临时产品创建/查询/详情/编辑、自动播报版本创建、SDK 发布并轮询 `status=success`、SDK zip 下载、协议播报版本创建、版本编辑、版本复制、版本/产品清理。
4. 自定义音频：生成小 WAV、上传、查询、备注编辑、下载校验、Excel+目录批量导入、删除闭环。

## 播报批量导入
- 播报合成版本配置里的“批量导入”必须以真实 UI 为准：前端选择文件夹后会读取 `.xlsx`，只保留 `.mp3/.wav/.xlsx`，前端表格校验通过后才调用 `/biz/audiofile/batchImportItems`。`/biz/audiofile/batchImport` 只作为旧接口/后端健壮性探测，不能直接写成 UI 路径结论。
- 必须用 `.mp3 + .xlsx` 组合验证：mp3 要满足 `<=20KB`、`16K` 单通道、`16bit`、码率 `<=32kbps`；xlsx 必须包含 `播报内容`、`音频描述`、`接收协议`。
- `音频描述` 必须与 mp3 文件名去扩展名一致；导入成功后返回的 `reply/comments/recProtocol` 要继续用于创建播报版本并发布 SDK，不能只停留在接口返回。
- 异常矩阵必须覆盖：文件过大、采样率 `32000/48000/64000`、多通道、码率超限、损坏/空文件、音频后缀 `.wav/.txt/.aac`、xlsx 文件名不匹配、缺列、空字段、非法协议、缺少 xlsx、缺少音频。
- 异常矩阵命令：`PYTHONPATH=scripts/py python3 -m synthesis_management.batch_import_negative`。
- 严格 UI 结论必须由浏览器 UI 或人工 UI 操作触发；直接调 `/biz/audiofile/validate`、`/biz/audiofile/batchImport`、`/biz/audiofile/batchImportItems` 只能标为“UI 等价 API”或“后端健壮性探测”。特别是 `.wav`、MP3 码率、WAV bit depth 等上传限制，若没有浏览器证据，不得写成前端 UI 缺陷。
- 当前已知 API 探测风险：后端接口曾放行 `.wav`/伪后缀/mp3 文件名不匹配/缺列空值/非法协议/缺少音频，且这些异常行还能继续创建播报版本；报告时必须明确标为后端校验缺失或待 UI 复核，不能混入 UI 主结论。
- 若要把播报 SDK 下发到 3021 设备复核，先静态检查 SDK 内 `cfg.json/ring_cfg.json/fw.bin`，再按固定 `scripts/burn/app.bin` 流程烧录；当前默认日志/烧录口使用 `/dev/ttyACM0`，协议口使用 `/dev/ttyACM2`，运行态上电使用 `uut-switch2` 协议门控。烧录后必须看到日志串口、协议口或实际播报证据，不能只凭 SDK zip 可下载或烧录工具成功判定设备侧通过。若 `fw.bin/fw.img` 烧录成功但设备无日志/无协议响应，记录为当前 SDK 产物或烧录路径不适配，并恢复已知可用固件。

## 合成导入与边界异常
- 用户要求验证“音频合成/播报合成从文件导入表、异常兜底、合成上限、条数上限、单条字符上限”时，必须补跑专项边界脚本，不得只跑正常全链路。
- 数据来源口径：
  - 表格导入内容可以按模板自动构造正常/异常数据，用于验证导入解析和异常兜底。
  - UI 页面元素、下拉枚举、发音人、压缩比、芯片/版本等不能自造，必须来自平台菜单、字典、options 或页面已有数据。
  - UI 可能更新，保存到本地的 options/CSV/JSON/截图只能提供测试设计思路；执行测试时必须通过浏览器 UI 或当前 UI 同源 options 重新确认可选项。
  - 若直接调用 API 传入 UI 不可能选择的枚举值，只能标为“接口健壮性探测”，不能写成 UI 可执行用例失败。
  - V4.0.5 起主结论必须模拟“正常人在 UI 上可完成的操作”：严格 UI 结论必须由浏览器 UI/人工 UI 触发；调用 UI 同款接口但未经过前端组件的，只能写成“UI 等价 API 辅助验证”。UI 会拦截的负例只记录为前端校验，不得绕过 UI 强行提交后端并混入主结论。
  - 禁止为了覆盖异常而直接修改 API payload，强行写入 UI 页面不可填写、不可选择、不可提交的字段或参数；这类内容只能单独列为非 UI 路径接口健壮性探测。
- 专项命令：`PYTHONPATH=scripts/py python3 -m synthesis_management.import_boundary_validation`。
- V4.0.5 播报固件专项命令：`PYTHONPATH=scripts/py python3 -m synthesis_management.v405_validation --publish-broadcast --keep-platform-records --no-persist-token`，覆盖音频合成导入、播报控制导入/新增、控制配置新增、播报音频上传异常、SDK 发布和可选 3021 烧录。
- 合成管理异常参数验证必须优先走 UI：音频合成项目新增、播报合成产品新增、播报版本快速创建、播报控制新增/导入、自定义音频上传均要通过页面按钮、文件选择和保存动作触发；`webkitdirectory` 批量导入若自动化无法注入真实目录，必须标为 `SCRIPT_LIMITATION`，不能拿接口结果代替 UI 结论。
- V4.0.5 完成审计必须补充逐需求报告，参考 `artifacts/platform-validation/20260528-v405-completion-audit/v405_completion_audit.md`；深定制要至少覆盖全词条打包、词条子集打包、深度调优保存/打包，并明确产物是否真的体现调优参数。
- V4.0.5 当前已知风险：菜单/页面文案未完全改名；WAV bit depth 与 MP3 码率直连接口校验放行但缺少浏览器 UI 复核证据；深度调优阈值保存后未落入下载产物；3021 设备运行态需日志/协议/播报证据。
- 音频合成导入表必须覆盖：空表、缺列、空字段、仅空格、序号非数字、重复音频名/序号、非法文件名字符、音频名长度、单条文本长度、导入行数、损坏 xlsx、csv 后缀。
- 播报合成导入表必须覆盖：合法 1/10 行、50/100/200/500 行、播报内容长度、音频描述长度、仅空格、重复音频描述、重复接收协议；音频文件格式异常仍由 `batch_import_negative.py` 覆盖。
- 试听合成边界必须覆盖：空文本、长文本、语速/音量 `1/100` 边界与 `0/101/负数/999` 越界、非法发音人。
- 判定时不能只看接口是否失败：预期拒绝却 `code=200` 是风险；失败但只返回“服务器异常/空信息”也要标为错误信息不合格。
- 对导入阶段被错误放行的异常行，还必须执行下游闭环复核：`PYTHONPATH=scripts/py python3 -m synthesis_management.import_downstream_validation --source-report <synthesis_import_boundary_result.json>`，确认这些异常行是否还能继续创建音频合成产物或播报版本。

## 安全约束
- 所有写入记录必须使用 `AUTO_TEST_*` 前缀。
- 中间失败也必须先尝试清理已创建的临时项目、产物、版本、产品和自定义音频。
- 认证失败、接口失败、报告文件中不得输出 token 明文。
- SDK 发布没有轮询到 `success` 不能算播报合成完整通过。
- 只读巡检或 smoke 不等同于全功能验证；用户要求“像固件打包一样覆盖每个功能点”时，必须跑完整 40 项左右功能点并在报告中写明覆盖范围。
- `--keep-platform-records` 只用于人工页面复核；复核完成后需要再执行标准命令触发初始清理，避免长期残留 `AUTO_TEST_*` 数据。

详细说明见 `SYNTHESIS_MANAGEMENT_VALIDATION.md`。

# 🧪 平台接口验证规则

## 适用范围
- 用户要求扫描或验证平台业务接口时，优先使用独立模块 `scripts/py/platform_api_validation/`。
- 兼容入口：`scripts/py/listenai_platform_api_validation.py`。
- 标准命令：`python3 scripts/py/listenai_platform_api_validation.py`。
- 模块命令：`PYTHONPATH=scripts/py python3 -m platform_api_validation.validation`。
- Token 统一从 `TOOLS.md` 的 `LISTENAI_TOKEN=` 读取，报告中不得输出 token 明文。
- 结果统一写入 `artifacts/platform-validation/<YYYYMMDD-HHMMSS>/`。

## 当前已落地链路
1. 我的发音词典：分页、模板下载、中文分词、受控新增、详情、导出、TXT 导入、清理。
2. 协议模板：分页、受控新增、详情、配置查询、受控刷新协议字段、记录查询、清理。
3. 算法/补丁打包：算法词条分页/详情、音频配置模板下载、模板导入解析、深度配置读取、关联固件详情与算法配置读取。

## 安全约束
- 所有平台写入必须使用 `AUTO_TEST_*` 或 `自动测试*` 受控前缀，默认执行清理。
- `--keep-platform-records` 只允许用于人工页面复核，复核后必须再次跑标准命令清理。
- 未创建受控 release 时，不允许对历史共享记录执行 `depthConfigSave`、`saveAlgoConfig`、`rewriteAlgoWakeupAndCmdConfigs`、`releaseAlgo/delete`。
- 下载接口若当前样本无打包产物或日志，按条件跳过记录，不得误判为平台接口失败。

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
      - 当前 3021 台架进入烧录模式: 先 `uut-switch2.off` 断开协议口，再执行 `uut-switch1.off` → `uut-switch3.on` → `uut-switch1.on` → `uut-switch3.off`
      - 当前 3021 台架烧录后恢复运行态: `uut-switch2.off` → `uut-switch3.off` → `uut-switch1.off` → `uut-switch1.on` → 等待 3 秒 → `uut-switch2.on`
      - 自动化 `powerOnCmds` 中用 `sleep:3` 固化“等待 3 秒”步骤，不能把 `uut-switch1.on` 与 `uut-switch2.on` 连续紧贴下发
      - `uut-switch2` 是协议口开关，不是 3021 boot 线；烧录进 boot 阶段不要操作 `uut-switch2`
      - 历史 3122 台架可能使用 `uut-switch2` 作为 boot 线；未确认前不得把 3122 的 `uut-switch2` 逻辑套到 3021
      默认按单次连续会话下发完整序列，不拆成其他替代流程
      若 ROM 握手异常，先恢复正常上电基线，再按当前 `switch2.off + switch1/switch3` 烧录链路复现；
      若新打包固件异常，优先烧录 `assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip` 做已知可用对照，
      以区分新包配置问题、设备/线序问题、协议门控问题或声卡/识别链路问题
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

# 3021 垂类最小覆盖验证

- 3021 全垂类 UI-only 打包、固件包真机验证、SDK 编译产物验证、语音注册连续学习收敛经验见 `references/3021_ui_only_runtime_validation_lessons.md`。遇到同类任务时先读取该文档，再设计包矩阵和运行态验证。
- 3021 英文版本按中文思路重测时，同时读取 `references/3021_english_ui_minrule_validation_lessons.md` 和 `references/english_platform_audio_synthesis_runtime_workflow.md`；英文支持范围以 UI 实时扫描为准，英文音频必须来自平台音频合成正式产物。
- 平台垂类验证不要堆全量词表。默认使用“默认唤醒 + 2 个代表业务命令 + 1 个音量命令 + 多唤醒切换/恢复 + 语音注册（仅支持垂类）”做最小高价值覆盖；静态校验再确认所选词、同义词、协议和能力开关已落入 `web_config.json`。
- 当前已沉淀的 3021 垂类覆盖口径：
  - 风扇：`打开风扇`、`关闭风扇`、`最大音量`、`风扇管家` 多唤醒；语音注册不支持则跳过。
  - 取暖器：`打开取暖器`、`关闭取暖器`、`最大音量`、`暖风管家` 多唤醒；语音注册不支持则跳过。
  - 取暖桌：`开机/关机`、`最大音量`、`暖桌管家` 多唤醒、`学习命令词/删除命令词` 语音注册。
  - 茶吧机/窗帘沿用对应垂类代表业务命令、音量、多唤醒、语音注册能力门控。
- 对极短或强同音命令（如取暖桌 `开机`/`关机`）如果 TTS 容易串扰，可以使用 `web_config.json` 中同一 intent 的配置同义词作为播报文本，但报告必须写明“播报同义词 -> 校验规范 intent”，不能伪造 UI 不支持的词。
- 连续学习类垂类删除命令词必须按双确认链路验证：`小聆小聆 -> 删除命令词 -> 删除命令词`，并等待算法重建完成；只播一次删除词没有形成 `reg del`/`del voice` 证据时，优先按用例时序问题收敛。
- SDK 验证不能只看 zip 可下载或本地可编译。每个垂类至少抽 1 个 SDK 完成 `readme -> build.sh -r all -> build/bin/app.bin -> 烧录 -> 启动/协议/声卡运行态验证` 闭环；若平台成功打包但 `pkgSDKUrl` 为空或 artifact 无 `MarsSDK_product`，归为平台 SDK 产物缺失，不得写成设备验证失败。

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
- 语音注册验证前优先执行 `clear.configall` 并重新上电，清理历史 `wkword/regSave/reg_cmd_count`；不清历史配置会导致模板已满、删除状态残留或学习词已存在等假失败。
- 语音注册控制词不得在算法导入模板里作为普通协议命令重复出现；`学习命令词/删除命令词/学习唤醒词/删除唤醒词/删除全部命令词/退出学习/退出删除` 等入口必须只来自 UI 语音注册配置生成的 `special_type=语音注册控制相关` 词条。若 `web_config.json` 同时存在普通协议命令和 special 控制词，运行时会优先命中普通协议命令并只发送 `snd_protocol`，不会进入 `Reg info/cmdlist get/wIvwRegist` 学习态，应直接归为配置构造问题。
- 进入 `学习命令词`、`学习唤醒词`、`删除命令词`、`删除唤醒词` 等交互态后，必须等待当前提示播报结束（以 `play stop` 为准）且算法状态恢复，再允许下一句交互
- 语音注册专项只能使用平台当前支持的触发词、控制词和功能词；`references/语音注册.log` 只可用于理解状态机和日志，不可直接拿其中历史调试命令词做测试输入
- 除字数上下限外，其余平台语音注册配置都要覆盖正例和反例；学习语料必须本地自定义合成，不能直接使用命令词、唤醒词或提示词内容
- 学习成功后必须使用学习语料验证真实生效；学习失败后必须使用同一学习语料验证不生效
- `specificLearn` 和 `contLearn` 必须按当前固件的阶段流转自适应：
  - 若 `学习命令词` 后已直接出现 `cmdlist get[...]` / `Reg info` / `reg status:1`，说明已直入学习态，不得再强行补说目标命令
  - 若未直入学习态，再按当前阶段配置补说目标命令
- `contLearn` 命令词学习若出现 `reg over!` 或学习模板已满，先走两步 `删除命令词` 清当前模板，再重新进入 `学习命令词`；重新进入学习态后直接说注册语料，不再额外重复目标命令
- `contLearn` 注册样本不得直接使用内置命令词本身；进入学习态后使用合法非内置别名样本，例如目标 `打开风扇` 使用 `我要吹风`，否则容易被算法判为已有命令/冲突样本并出现 `reg failed` 或 `reg length error`。
- `wakeTimeout=1 + contLearn + retryCount=1` 属于左边界/超时观察组合，只验证超时、唤醒窗口和边界行为，不作为语音注册正向学习成功包；正向连续学习必须使用正常超时纠偏包闭环。
- 负向用例的错误语料选择必须避免误触发真实语音注册功能：
  - 语音注册控制词/删除词只允许用于“保留词冲突”“删除链路”“重试耗尽”这类明确场景
  - 普通恢复/耗尽负例优先使用平台支持的普通功能词
- `retryCount=1` 或单遍学习配置下，第一次错误输入直接 `reg failed!` 可能就是正确固件语义，不能误判成执行器失败
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
- 删除命令词/删除唤醒词会刷新算法配置；`algo restart`、`ai create`、`AADC STOP/START` 这类算法重启或引擎重建属于正常配置刷新，不等于设备重启，不能单独判失败
- 只有捕获到整机启动特征（如 boot banner、`APP version` 重新输出、串口断连重连、设备自行上电日志等）才按设备重启异常处理

## 未测/裁剪范围披露
- 任何因边界配置、产品能力、执行时序限制而未执行的用例，都必须在 `summary.md` 和邮件正文中明确写出“未测范围 + 未测原因 + 由哪个包覆盖”
- `pkg-02-left-boundary` 若 `timeout=1s`，不能宣称全功能通过：
  - 普通命令词/全功能用例可以裁剪不执行
  - 原因是自动链路需要等待唤醒确认后再播命令，命令到达时可能已超过 1s 命令窗口，容易形成假失败
  - 报告必须写明：该包只验证 timeout 左边界、配置边界、唤醒、播报和串口观察项；命令词全功能由 `pkg-01`/`pkg-03` 覆盖

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

### UI 算法模板选择规则
- 本地 `assets/templates/` 中的 `algo_*.xlsx` 是 fallback 测试数据模板，不代表平台永远最新格式；正式 UI 打包若页面支持下载最新模板，必须优先用 UI 最新模板作为底板，再按同类 profile 生成/导入数据。
- 模板按测试类型选择：
  - 基础/边界/普通垂类：`algo_<lang>_base_core.xlsx`
  - 主动/被动协议：`algo_<lang>_protocol_active_passive.xlsx`
  - 多唤醒循环/指定/协议切换：`algo_<lang>_multi_wakeup_loop.xlsx`、`algo_<lang>_multi_wakeup_specified.xlsx`、`algo_<lang>_multi_wakeup_protocol.xlsx`
  - 语音注册指定学习/连续学习/边界删除：`algo_<lang>_voice_reg_specific.xlsx`、`algo_<lang>_voice_reg_continuous.xlsx`、`algo_<lang>_voice_reg_boundary_delete.xlsx`
  - 深度调优：`algo_<lang>_depth_tuning.xlsx`
  - 全功能耦合冒烟：`algo_<lang>_full_feature_stateful.xlsx`，若编译容量异常必须拆回专项模板定位
- 每个模板都要满足最低基础能力：默认唤醒、至少两个业务命令、音量上/下/最大/最小/中等、退出识别、负性词、欢迎语、被动播报、休息语、心跳协议、发送协议和接收协议。
- 语音注册模板只提供基础宿主动作和可学习目标动作；学习/删除/退出等控制词必须通过 UI 语音注册配置表生成 special 控制词，不能在 `词条预处理` 中再导入同名普通协议命令。字数上下限、重复次数、重试次数、模板上限等边界语料在运行时按配置合成，不允许把语音注册控制词当普通负例词使用。
- 多唤醒模板只提供候选唤醒词和切换/查询/恢复触发数据；`isDefault`、`isFrozen`、`sndProtocol`、`recProtocol` 等字段必须通过当前 UI 多唤醒表格真实配置。
- 更新或新增平台可配置参数后，必须同步 `scripts/ui/generate_algo_template_variants.py`、`assets/templates/template_manifest.json`、`assets/templates/template_requirement_matrix.md` 和 `references/platform_firmware_template_requirement_matrix.md`，并完成 xlsx/JSON/编码校验。

### 平台配置取值压缩规则
- 本规则只适用于 UI-only 固件打包主链路；历史 API 打包不得再作为平台固件打包测试入口，除非用户明确要求做“接口健壮性探测”，且结果必须单独标注，不能混入 UI 主结论。
- 不允许把列表型参数逐项枚举打包。类似音量挡位 `[1..10]`，默认只取 `最小/中间/最大` 三个代表值；如果平台列表不是数字，取首项、典型项、末项。
- 布尔参数必须覆盖两个状态，但应放入不同配置向量包里组合验证，不为单个布尔值单独扩包。
- 字符串参数只取符合 UI 校验和业务语义的合法样例；除非要验证前端异常提示，否则不做多字符串枚举。
- 普通数字输入参数不机械取极限边界。语音注册模板数、重试次数、次数上限这类字段优先取 `1/3/5` 或 UI 允许范围内最接近的低/中/高代表值；若 UI 最大值小于 5，则取范围内的代表值，例如只允许 `1/2` 时覆盖 `1/2`。
- 依赖型参数必须组合到同一个 release 中验证，例如 `voiceRegEnable + registMode + 模板数/重试次数 + 学习命令配置`，或 `multiWkeEnable + multiWkeMode + wakeWordSave + 候选词配置`。
- 只有出现 FAIL/BLOCK/重启/构包异常时，才追加控制变量包；控制变量包必须把其他参数恢复默认，只保留问题参数和最小依赖。
- UI-only 批量结果汇总使用 `scripts/ui/summarize_3021_packaging_results.py` 生成 JSON/Markdown/CSV；CSV 必须用 `utf-8-sig`，报告中按产品列出 release、创建路径、profile、覆盖点和失败分类。

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
**配置向量打包原则：不是枚举所有组合，而是用最少的包覆盖最多的等价类、代表值与依赖链路。**
- 固件打包主链路必须走 UI：页面上新建/查询产品，进入同一产品详情，在 UI 中连续生成多个 release；脚本不得使用 `biz/prod/add`、旧 `defId/versionLabel/type/scene/mode` 参数或其它写接口替代 UI。
- 产品不存在或平台被清空时，必须走 UI `新增` 创建一个产品；后续所有配置包都落在这个产品下。
- 每个 release 在生成前必须填写短版本描述，描述配置向量而不是产品信息，例如 `默认+多唤醒指定`、`左边界+连续学习+循环`。
- 同一垂类先固定一个代表品类，再根据 UI 当前能力裁剪包矩阵；支持多唤醒/语音注册时打开对应开关并选择对应模板，不支持时不得硬测。
- 生成 release 后不要逐包阻塞等待编译完成；全部提交后统一轮询 release 状态，再按每个 release 的配置参数生成烧录和真机验证计划。
- 大多数产品应先按“约 `5` 个包”规划，不要无节制扩包
- 只有组合包出现 `FAIL`、`BLOCK`、重启、打包异常或系统性异常时，才允许追加控制变量包
- 列表型参数覆盖首项/中间项/末项；类似音量 `[1..10]` 只取 `1/5/10`，禁止一级一级打包
- 普通数字输入参数优先覆盖 `1/3/5` 或 UI 范围内近似的低/中/高代表值，不机械使用极限边界
- 字符串参数通常验证 `1` 次即可
- 布尔参数覆盖 `true/false`
- 依赖型参数必须放在同一包里联动验证
- 单个 release 应尽量组合多个配置点；不要为了覆盖一个音量、一个开关或一个字符串单独打一包
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
**每次使用 send-email skill 发送 mars-belt 测试报告时，必须同时参考 `EMAIL_TEMPLATE.md`、`FULL_CHAIN_VALIDATION_RULES.md` 和 `references/platform_test_report_writing_standard.md`！**

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
- 平台打包/固件/SDK 验证报告必须使用固定结构：标题下先写“测试结论”，然后依次写“测试目的、测试方案、测试用例和结果、测试问题与分析、证据文件”
- 测试结论必须简短量化，写清覆盖了多少垂类/产品/release/固件/SDK，当前是否有未闭环问题
- 测试目的要说明本报告验证哪些需求功能是否正常，不能只写“完成测试”
- 测试方案要说明每类功能怎么验证：UI 打包、基础配置、协议、声卡识别、多唤醒、语音注册、SDK 编译和 app.bin 真机
- 测试用例和结果必须用表格呈现数量、执行结果和结论；逐包表必须包含 profile、releaseId、版本描述、关键配置向量和最终结论
- 测试问题有问题才写；必须包含影响范围、现象、处理动作、最终分析。没有未闭环问题时写“未发现未闭环问题”即可
- 邮件正文以“功能点结果”为主，只写 `PASS / FAIL / BLOCK`
- `FAIL / BLOCK` 必须说明：哪个包、哪些实际参数值、出现了什么异常、与预期不符在哪里
- 算法配置结果必须单独展示，不能只写基础配置
- 配置期望值必须从 `summary.json` 或 `testCases.csv` 获取
- 实测校验值必须从 `serial_raw.log` 或 `test_*.log` 提取
- 执行包名称必须与附件中的目录名一致
- 附件必须打包成 `result.zip`，结构以 `FULL_CHAIN_VALIDATION_RULES.md` 为准
