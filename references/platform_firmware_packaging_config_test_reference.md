# 平台固件打包配置测试参考

更新时间：2026-06-11

本文档沉淀当前 mars-belt 对 ListenAI 平台“产品管理 -> 固件打包”可配置参数的测试口径。用于后续设计平台打包、固件产物、SDK 产物和真机验证用例时快速对齐范围。

## 1. 适用范围

- 适用：平台固件打包配置、固件 zip、SDK zip、设备烧录后的运行态验证。
- 不适用：音频合成、播报合成等非固件打包页面功能；这些应归入独立模块。
- 本文不把 xlsx 导入作为必要前置。导入属于 UI 通道验证，配置结果仍以平台最终保存的 release 配置为准。

## 2. 证据分层

| 层级 | 证据 | 用途 |
| --- | --- | --- |
| 配置层 | release detail、web_config、summary、配置断言 JSON/CSV | 证明参数是否被平台保存并进入包配置 |
| 产物层 | 固件 zip、SDK zip、fw.bin/app.bin hash、解包文件 | 证明打包产物是否生成且内容一致 |
| 设备层 | 烧录日志、启动日志、识别日志、协议口 TX/RX、声卡播报结果 | 证明配置在设备运行态生效 |
| UI 层 | Playwright/Puppeteer 操作录像、前端提示、按钮状态、表单值 | 证明用户通过 UI 通道可正确配置和提交 |

## 3. ABCD 分类定义

- A：可配置 + 可入包断言 + 可真机自动验证，适合常规全链路回归。
- B：可配置 + 可入包断言，但真机验证需要专项场景、统计口径或隔离包。
- C：可配置或可见，但设备侧直接观测弱，主要做配置断言/专项人工辅助。
- D：偏 UI 通道/前端交互，需要 UI 自动化单测，不能用 API 打包结果替代。

## 4. A 类：常规全链路可验证

| 序号 | 参数 | 测试方式 |
| --- | --- | --- |
| A-01 | timeout | 解包确认超时时间；声卡唤醒后不发命令，计时等待退出唤醒/ASR 状态，日志校验超时退出。 |
| A-02 | volLevel | 解包确认音量档位数组；真机连续“增大音量/减小音量”，统计档位变化和边界行为。 |
| A-03 | defaultVol | 解包确认默认档；烧录/断电重启后看启动配置日志或音量初始化日志。 |
| A-04 | volMaxOverflow | 解包确认文案；真机调到最大音量后继续增大，检查边界播报/播放日志。 |
| A-05 | volMinOverflow | 解包确认文案；真机调到最小音量后继续减小，检查边界播报/播放日志。 |
| A-06 | uportBaud | 解包确认协议波特率；脚本按该波特率打开协议口，发送/接收协议验证通信正常。 |
| A-07 | logLevel | 解包确认日志等级；启动和识别链路观察日志输出等级、关键日志是否符合预期。 |
| A-08 | volSave | 修改到非默认音量，断电重启，校验音量是否保持或恢复默认。 |
| A-09 | wakeWordSave | 切换到非默认唤醒词，断电重启，校验当前唤醒词是否保持或恢复默认。 |
| A-10 | releaseAlgoList[*].word | 解包确认词条；声卡播放对应词，日志匹配 keyword/intentStr。 |
| A-11 | releaseAlgoList[*].extWord | 解包确认扩展词；播放扩展词，校验识别归一到主命令功能。 |
| A-12 | releaseAlgoList[*].sndProtocol | 解包确认协议；触发命令词后协议口抓取发送帧。 |
| A-13 | releaseAlgoList[*].recProtocol | 解包确认协议；协议口输入接收帧，日志/播报/状态确认设备响应。 |
| A-14 | releaseAlgoList[*].reply | 解包确认回复语；触发命令后检查播报日志、play id 或音频输出。 |
| A-15 | releaseAlgoList[*].replyMode | 解包确认模式；按主动/被动播报或协议触发路径分别验证是否符合模式。 |
| A-16 | releaseAlgoList[*].type | 解包确认类型；按唤醒词/命令词/播报类分别走对应触发链路。 |
| A-17 | multiWkeEnable | 解包确认开关；打开后播放新增唤醒词，校验能进入唤醒状态。 |
| A-18 | multiWkeMode=specified | 解包确认模式；执行指定切换，验证当前生效唤醒词和非当前唤醒词行为。 |
| A-19 | releaseMultiWke.common[*].condition | 播放查询/恢复/切换指令，检查日志命中和流程进入。 |
| A-20 | releaseMultiWke.common[*].reply | 触发多唤醒公共指令，检查对应回复播报。 |
| A-21 | releaseMultiWke.wkelist[*].condition | 播放候选唤醒词，校验唤醒识别和当前唤醒词状态。 |
| A-22 | releaseMultiWke.wkelist[*].reply | 切换到候选词后，检查切换提示/回复播报。 |
| A-23 | releaseMultiWke.wkelist[*].sndProtocol | 触发多唤醒切换/查询后检查协议发送帧。 |
| A-24 | releaseMultiWke.wkelist[*].recProtocol | 协议口输入确认帧，检查多唤醒状态变化。 |
| A-25 | voiceRegEnable | 解包确认开关；真机播放学习入口，检查进入语音注册流程。 |
| A-26 | releaseRegist.registMode=specificLearn | 解包确认模式；按“学习命令词 -> 注册词”流程验证。 |
| A-27 | releaseRegist.registMode=contLearn | 解包确认模式；按连续学习流程验证直学/自动下一条/结束逻辑。 |
| A-28 | releaseRegist.wakeupRepeatCount | 配不同重复次数；学习唤醒词时检查录入次数和阶段流转。 |
| A-29 | releaseRegist.commandRepeatCount | 配不同重复次数；学习命令词时检查录入次数和最终保存条件。 |
| A-30 | releaseRegist.wakeupRetryCount | 播放错误唤醒注册语料，检查失败重试次数和退出逻辑。 |
| A-31 | releaseRegist.commandRetryCount | 播放错误命令注册语料，检查失败重试次数和退出逻辑。 |
| A-32 | releaseRegist.wakeupRegistMaxLimit | 连续学习唤醒词到模板满，检查模板满提示和阻止继续学习。 |
| A-33 | releaseRegist.commandRegistMaxLimit | 连续学习命令词到模板满，检查模板满提示和阻止继续学习。 |
| A-34 | releaseRegist.wakeupWordsMinLimit | 播放过短唤醒注册词，检查 length error 和不保存。 |
| A-35 | releaseRegist.wakeupWordsMaxLimit | 播放过长唤醒注册词，检查 length error 和不保存。 |
| A-36 | releaseRegist.commandWordsMinLimit | 播放过短命令注册词，检查 length error 和不保存。 |
| A-37 | releaseRegist.commandWordsMaxLimit | 播放过长命令注册词，检查 length error 和不保存。 |
| A-38 | releaseRegistConfig.*.condition | 播放学习、删除、退出、全部删除等入口词，校验流程进入。 |
| A-39 | releaseRegistConfig.*.reply | 触发对应阶段，检查播报提示和阶段流转。 |
| A-40 | releaseRegistConfig.*.delReply | 执行删除注册词，检查删除成功提示和后续回测不再命中。 |
| A-41 | releaseRegist.reply | 进入注册流程后检查通用学习播报。 |
| A-42 | releaseRegist.sndProtocol | 进入/完成学习流程时检查协议发送。 |
| A-43 | releaseRegist.recProtocol | 协议输入后校验注册流程响应或状态变化。 |

## 5. B 类：专项/隔离验证

| 序号 | 参数 | 测试方式 |
| --- | --- | --- |
| B-01 | product | 解析产品树并创建/复用产品；打包成功、固件可下载。无单独设备行为，只作为目标矩阵验证。 |
| B-02 | module | 选择芯片/模组；打包成功后烧录对应设备验证基础链路。 |
| B-03 | language | 打包不同语言；检查词条、播报资源、唤醒/命令词音频是否匹配语言。 |
| B-04 | version | 选择垂类版本；检查功能开关和词表是否符合该垂类。 |
| B-05 | scene | 选择场景；打包成功并核对 release detail。 |
| B-06 | sourceReleaseId | 指定源版本；复制打包后比对配置继承和变更项。 |
| B-07 | comments | 打包后检查版本备注是否写入，用于追踪配置组合。 |
| B-08 | uportUart | 解包确认；需要按物理口重连协议串口验证，容易影响设备通信，建议专项。 |
| B-09 | traceUart | 解包确认；需要按物理口重连日志串口验证，容易造成误判，建议专项。 |
| B-10 | traceBaud | 解包确认；切换日志口波特率后看启动日志，适合串口专项。 |
| B-11 | vcn | 解包确认发音人；触发播报后做听感/音频特征检查，自动精确判定难度较高。 |
| B-12 | speed | 解包确认；触发固定播报，统计播报时长变化。 |
| B-13 | vol | 解包确认；触发固定播报，统计响度变化。 |
| B-14 | compress | 解包确认；触发播报，检查是否异常、卡顿、失真。 |
| B-15 | word | 解包确认欢迎语；启动播报检查 play log/音频内容，部分 TTS 内容需要人工辅助。 |
| B-16 | sensitivity | 解包确认；用固定音频集多轮统计唤醒率、误唤醒率、拒识率。 |
| B-17 | algoViewMode | 解包/接口确认；主要影响算法配置保存路径/展示密度，设备侧无独立行为。 |
| B-18 | multiWkeMode=loop | 解包确认；连续触发切换，验证循环顺序和当前唤醒词。 |
| B-19 | multiWkeMode=protocol | 解包确认；通过协议触发切换，验证当前唤醒词和协议响应。 |
| B-20 | releaseMultiWke.wkelist[*].isDefault | 恢复默认/断电重启后验证默认词是否生效。 |
| B-21 | releaseMultiWke.wkelist[*].isFrozen | 尝试切换冻结词/非冻结词，检查是否允许变更。 |
| B-22 | releaseRegist.wakeupSensitivity | 解包确认；用固定注册语料多轮统计学习成功率。 |
| B-23 | releaseRegist.commandSensitivity | 解包确认；用固定注册语料多轮统计命令词学习成功率。 |
| B-24 | releaseRegist.replyMode | 解包确认；根据主/被动回复模式设计对应触发路径验证。 |
| B-25 | releaseAlgoList[*].children[*].extWord | 解包确认；播放子泛化词，确认归一到父词条。 |
| B-26 | releaseDepthList[*].pinyin | 解包确认；播放对应词，检查拼音修正后识别是否命中。 |
| B-27 | releaseDepthList[*].decEnable | 解包确认；固定音频集统计 DEC 开关对命中率影响。 |
| B-28 | releaseDepthList[*].decThreshold | 解包确认；固定音频集按门限多轮统计。 |
| B-29 | releaseDepthList[*].e2eEnable | 解包确认；固定音频集统计 E2E 开关影响。 |
| B-30 | releaseDepthList[*].e2eThreshold | 解包确认；固定音频集按门限多轮统计。 |
| B-31 | releaseDepthList[*].embeddedEnable | 解包确认；唤醒词固定音频集验证内置唤醒门限链路。 |
| B-32 | releaseDepthList[*].embeddedThreshold | 解包确认；唤醒词固定音频集统计边界变化。 |
| B-33 | releaseDepthList[*].asrFreeEnable | 解包确认；命令词自由说法音频集验证。 |
| B-34 | releaseDepthList[*].asrFreeThreshold | 解包确认；命令词自由说法音频集按门限统计。 |

## 6. C 类：配置可断言，设备侧直接判定弱

| 序号 | 参数 | 测试方式 |
| --- | --- | --- |
| C-01 | again | 配置/解包断言；真机只能间接观察识别稳定性，缺少直接量化证据。 |
| C-02 | dgain | 配置/解包断言；真机只能间接观察识别稳定性，缺少直接量化证据。 |
| C-03 | paConfigEnable | 解包确认；播报时看 PA ON/OFF 日志和播放是否正常，能测开关但不等价于硬件电平完整验证。 |
| C-04 | ctlIoPad | 解包确认；需要硬件引脚接线/示波器或电平采集才能完整验证。 |
| C-05 | ctlIoNum | 解包确认；需要硬件引脚接线/示波器或电平采集才能完整验证。 |
| C-06 | holdTime | 解包确认；需要测 PA 保持时长日志或外部电平采样。 |
| C-07 | paConfigEnableLevel | 解包确认；需要外部电平观测确认高/低有效。 |
| C-08 | protocolConfig | release detail 可见但标准配置页/当前主流程不稳定作为可编辑项；只做配置存在性记录。 |
| C-09 | releaseDepthList[*].type | 主要是展示/分类字段；用来选择验证集，不作为独立行为参数。 |
| C-10 | releaseDepthList[*].category | 主要是唤醒/命令词调优分类；用页签和数据归属确认，不作为独立真机动作。 |

## 7. D 类：UI 通道/前端交互验证

| 序号 | 参数/功能 | 测试方式 |
| --- | --- | --- |
| D-01 | form_item_language 语言下拉 | 用 UI 自动化点击下拉、选择项、保存，检查前端选择和后端结果一致。 |
| D-02 | form_item_type 产品类别下拉 | 用 UI 自动化选择类别/垂类，检查联动产品模板是否正确。 |
| D-03 | 产品模板/版本卡片选择 | UI 点击不同卡片，检查版本、模块、配置项是否联动变化。 |
| D-04 | 导入数据 xlsx 模板导入 | UI 上传合法/非法 xlsx，检查前端校验、错误提示、导入后表格结果。 |
| D-05 | 文件选择控件 | UI 上传空文件、错后缀、字段缺失文件，检查是否被前端阻止。 |
| D-06 | 表单输入边界提示 | UI 输入超范围值，如非法 defaultVol，检查前端提示/按钮禁用。 |
| D-07 | 回复语/协议切换控件 | UI 点击“切换到回复语/协议”，检查字段是否切换并保存正确。 |
| D-08 | 语音注册阶段表格新增/删除行 | UI 新增/删除阶段行，检查前端数据结构和保存结果。 |
| D-09 | 多唤醒候选词表格勾选/默认/冻结 | UI 操作勾选框，检查互斥关系和保存结果。 |
| D-10 | 生成并关闭按钮链路 | UI 从保存到生成关闭全链路，检查按钮状态、生成结果、异常提示。 |

## 8. 当前默认推荐测试矩阵

### 参数取值压缩原则

- UI-only 与 API 打包共用同一套参数覆盖口径，不能因为执行通道不同而改成全枚举或一参一包。
- 列表型参数只取三个代表值：两端边界 + 一个中间值。例如音量 10 级取 `1/5/10`，不逐级打包。
- 布尔型参数覆盖 `true/false` 两个状态，但合入配置向量包，不为单个布尔值单独扩包。
- 字符串参数取符合 UI 校验和业务语义的合法样例；异常字符串只用于 UI 负例专项。
- 普通数字输入参数不机械使用极限边界。语音注册模板数、重试次数、次数上限优先使用 `1/3/5` 或 UI 允许范围内的近似低/中/高代表值；若 UI 最大值小于 5，则只取可选范围内的代表值。
- 依赖型参数必须同包联动，例如 `voiceRegEnable + registMode + 模板数/重试次数 + 学习命令配置`，`multiWkeEnable + multiWkeMode + wakeWordSave + 候选词配置`。

| 包 | 目标 | 典型覆盖 |
| --- | --- | --- |
| 基础中值稳定包 | 稳定基线 | timeout/volLevel/defaultVol 中值、播报文本、volSave=false、基础命令词协议链路 |
| 基础左边界包 | 低边界 | timeout、volLevel、defaultVol、uportBaud、logLevel、TTS 低边界 |
| 基础右边界包 | 高边界 | timeout、volLevel、defaultVol、uportBaud、logLevel、volSave=true、paConfigEnable=true |
| 全功能保持开启包 | 功能耦合 | voiceRegEnable、multiWkeEnable、wakeWordSave=true、指定学习、多唤醒指定切换 |
| 多唤醒保持关闭隔离包 | 状态隔离 | multiWkeEnable、wakeWordSave=false、切换后断电恢复默认 |

## 9. 算法模板覆盖要求

算法导入模板必须支撑配置参数验证，不能只满足“能导入”。当前模板体系按参数能力拆分，完整矩阵见 `references/platform_firmware_template_requirement_matrix.md`，生成清单见 `assets/templates/template_manifest.json`。

| 配置组 | 必须覆盖的数据 | 推荐模板 |
| --- | --- | --- |
| 基础/边界 | 唤醒、业务命令、音量上/下/最大/最小/中等、退出识别、负性词、欢迎/播报/休息/心跳、snd/rec 协议 | `algo_<lang>_base_core.xlsx` |
| 主动/被动协议 | 主动命令发送协议、被动接收协议触发播报、被动命令回复、心跳协议 | `algo_<lang>_protocol_active_passive.xlsx` |
| 多唤醒 | 默认唤醒、两个候选唤醒、切换、查询、恢复、指定切换、协议切换 | `algo_<lang>_multi_wakeup_*.xlsx` |
| 语音注册 | 学习命令词/唤醒词、删除命令词/唤醒词、全部删除、退出学习/删除、可学习宿主动作 | `algo_<lang>_voice_reg_*.xlsx` |
| 深度调优 | 唤醒词、命令词、子泛化词，用于 pinyin/DEC/E2E/embedded/ASRFree 调优 | `algo_<lang>_depth_tuning.xlsx` |
| 全功能耦合 | 基础 + 协议 + 多唤醒 + 语音注册 + 掉电保持冒烟 | `algo_<lang>_full_feature_stateful.xlsx` |

注意：语音注册的字数上下限、重复次数、重试次数、模板上限等边界语料运行时按测试配置合成；模板只负责提供可进入流程的控制词和可回测的宿主动作。多唤醒的 `isDefault`、`isFrozen`、候选词协议等必须通过当前 UI 多唤醒配置表真实配置，不能只靠 xlsx 行数据替代。

## 10. UI-only 配置和打包可行性

可以只通过 UI 操作完成参数配置和打包，但要满足以下前提：

1. 目标参数必须在 UI 页面上真实暴露并允许编辑；后台存在但 UI 未开放的字段不能算 UI-only 可配置。
2. 需要 Playwright/Puppeteer 维护稳定选择器、等待逻辑、弹窗处理、文件选择和生成状态轮询。
3. UI-only 更适合验证 D 类“前端通道”问题，例如下拉联动、文件导入、边界提示、按钮禁用、异常提示。
4. UI-only 可以完成完整打包动作：进入固件打包、新建/选择产品、快速创建、填写基础配置、配置算法/多唤醒/语音注册、继续、生成并关闭、下载产物。
5. UI-only 不替代设备侧验证；固件生成后仍需解包断言、SDK 校验、烧录、日志口/协议口/声卡播报验证。
6. 平台 UI 会持续更新，历史枚举的产品/芯片/语言/SDK 矩阵只能作为参考和排查思路，不能作为后续脚本打包的固定输入源。每次测试必须重新通过 UI 当前页面或当前 UI 同源 options 确认可选项。
7. UI-only 固件打包可复用流程见 `references/ui_firmware_packaging_workflow.md`。本地 fallback 模板在 `assets/templates/`，正式验证如 UI 可下载最新模板，应优先使用 UI 最新模板。

工程建议：平台功能回归采用“API 批量打包 + 产物/真机验证”为主；前端功能回归单独采用 UI-only 覆盖 D 类和用户操作链路。两套证据不要混用。

## 11. UI-only 批量异常分类与处理规则

| 分类 | 触发条件 | 判定 | 处理规则 |
| --- | --- | --- | --- |
| `strict_ui_version_option_missing` | UI 新建产品时语言/品类/芯片已按页面选择，但版本下拉找不到计划中的 `defId/versionLabel` | 严格 UI 创建产品链路失败，不等于配置打包失败 | 保留 UI 失败证据；若组合来自平台实时矩阵，可用同源 API 创建/复用产品壳兜底，后续 release 配置和生成仍必须走 UI |
| `legacy_v1_generate_no_release` | V1.0 老版本可进入配置页和完成页，但点击生成后 release page 无记录，轮询超时 | V1.0 老版本 UI 生成链路未落 release | 不能按成功处理；最终报告列为 legacy/UI 限制，并附产品 id、result.json、完成页截图和 release 列表为空证据 |
| `release_poll_timeout` | UI 点击生成后存在 release 但长时间停留 `pending` 或无构包产物 | 平台构包或轮询超时 | 继续用 release id 二次轮询；若最终成功则覆盖旧结果，若仍 pending 列为构包超时 |
| `ui_runner_error` | 选择器、弹窗、文件导入、表单提示等导致 runner 异常 | 自动化/前端交互问题 | 先看 `error-state.json` 和截图；能通过更稳定 UI 操作修复的沉淀到 runner，不能绕过 UI 直接写配置 |

报告必须同时给出：

- 产品清单：产品名、语言、垂类路径、`defId/versionLabel`。
- 每产品 release 清单：profile、release version/id、创建路径、状态、构包/SDK URL。
- 配置覆盖点：展开 `coveragePoints`，说明单个 release 同时覆盖哪些配置点。
- 失败分类：按上表归类，并说明是否已通过后续 fallback 或重跑闭环。
