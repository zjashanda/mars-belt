# artifacts 历史证据轻量知识索引

## 定位

`artifacts/` 是本机历史测试证据库，保存平台打包、SDK 下载、烧录、串口日志、UI 截图、报告中间版和调试数据。它不是 skill 可迁移运行依赖，也不进入 git。其他电脑使用 `mars-belt` 时，应依赖 `SKILL.md`、`references/`、`scripts/`、`assets/` 中沉淀后的规则和资产；只有需要追溯本机某次历史现场时才回看 `artifacts/`。

本索引只沉淀“历史任务 -> 可复用知识 -> 当前承载文件”的映射，不迁移 3.8G 原始数据。

## 当前本机规模快照

统计时间：2026-06-18。

| 目录 | 规模 | 文件数 | 角色 |
| --- | ---: | ---: | --- |
| `artifacts/tasks/` | 约 3.3G | 28556 | 大任务级结果：固件/SDK 下载包、验证日志、报告、复测数据。 |
| `artifacts/platform-validation/` | 约 218M | 7169 | 平台功能、V4.0.5/V4.5.0、合成管理、UI-only 验证证据。 |
| `artifacts/runtime/` | 约 122M | 2680 | 真机运行态、串口、协议、声卡、设备 baseline 证据。 |
| `artifacts/cucumber-debug/` | 约 74M | 609 | Cucumber/Gherkin 化探索调试数据。 |
| `artifacts/platform-scan/` | 约 20M | 577 | 平台菜单、接口、产品/垂类/options 扫描结果。 |
| `artifacts/burn/` | 约 11M | 752 | 烧录工具执行、烧录日志和临时烧录证据。 |
| `artifacts/synthesis-validation/` | 约 6.6M | 117 | 音频合成、播报合成、异常导入、SDK 发布下载验证。 |
| `artifacts/synthesis-discovery/` | 约 5.0M | 27 | 合成管理接口和页面能力早期摸索。 |
| `artifacts/device-check/` | 约 3.2M | 70 | 设备基础链路、串口、声卡、协议、上下电检查。 |
| `artifacts/plan_archive/` | 约 960K | 1 | 旧 `plan.md` 全量历史归档。 |
| `artifacts/tmp*` / `state` | 小体积 | 少量 | 临时探测、批量导入 probe、运行状态。 |

## 历史任务族索引

| 任务族 | 代表路径 | 原始证据内容 | 已沉淀知识 | 当前承载文件 |
| --- | --- | --- | --- | --- |
| 3122 语音注册全链路 | `artifacts/tasks/3122-取暖器-通用垂类-*`、`artifacts/runtime/3122-*` | 3122 固件包、语音注册指定/连续学习日志、删除命令词后重新学习证据。 | 命令词满时先删除；进入学习后直接说注册词；算法重启和设备重启分开判；注册成功必须看强 marker。 | `语音注册专项测试说明.md`、`references/语音注册命令词学习手工验证模板.md`、`references/3021_runtime_state_and_special_control_validation.md` |
| V4.0.5/V4.5.0 平台需求验证 | `artifacts/tasks/V405-平台需求验证`、`artifacts/tasks/当前平台4.5.0需求验证结果`、`artifacts/platform-validation/20260528-*` | 需求扫描、播报控制、控制配置、深度调优、固件产物、报告版本。 | 报告结构；UI-only 主结论；深度调优产物需检查是否入包；需求项要按方案/用例/结果/问题闭环展示。 | `references/platform_test_report_writing_standard.md`、`references/ui_invalid_input_validation_strategy.md`、`SKILL.md` |
| 3021 设备 bring-up 与串口/电源定位 | `artifacts/runtime/device-baseline/*`、`artifacts/platform-validation/manual-port-capture-*`、`artifacts/device-check/*` | ACM0/ACM1/ACM2 对照、协议口拔插、switch1/2/3/4 实验、重复烧录稳定性。 | 当前 3021 口径：ACM0 日志/烧录、ACM2 协议、ACM4 控制；switch1 电源、switch2 协议门控、switch3 boot；正常上电先断协议口。 | `references/3021_known_good_smoke_firmware.md`、`SKILL.md` 严格规则 17/18 |
| 3021 已知可用冒烟固件 | `artifacts/device-check/3021-ui-burn-*`、`artifacts/device-check/3021-repeat-burn-*` | 风扇 UI 包连续烧录和基础唤醒/协议验证证据。 | 冒烟固件作为设备/线序/声学/协议链路对照，不依赖历史 artifacts 复用。 | `assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip`、`references/3021_known_good_smoke_firmware.md` |
| UI-only 打包流程收敛 | `artifacts/tasks/ui-only-a-config-*`、`artifacts/tasks/3021-ui-packaging-*`、`artifacts/tasks/3021-all-verticals-ui-once-*` | UI 创建产品、options 扫描、release 创建、旧 API 参数误用排查。 | 固件打包强制 UI-only；产品为空时 UI 新建；同产品多 release；UI 当前下拉选项实时读取；禁止使用老 API 隐藏参数。 | `references/ui_firmware_packaging_workflow.md`、`SKILL.md` 严格规则 22/24/25 |
| 最小规则打包矩阵 | `artifacts/tasks/3021-fixed-product-min-rule-*`、`artifacts/tasks/3021-all-verticals-*` | 多垂类产品、多个 release、配置向量、打包状态。 | 固定代表品类；3/4 包最小矩阵；数值取边界+中间，布尔全覆盖，功能选择全覆盖；版本描述短配置向量。 | `references/platform_firmware_minimal_packaging_strategy.md`、`references/platform_firmware_template_requirement_matrix.md` |
| 五垂类运行态与 SDK 闭环 | `artifacts/tasks/3021-all-verticals-firmware-sdk-runtime-*`、`artifacts/tasks/3021-full-repack-ui-runtime-*` | 固件下载、SDK 下载、readme/build、app.bin、烧录运行态、报告。 | 每垂类 SDK 至少验证一个；SDK 必须读 readme、build、烧录 app.bin；失败要用冒烟固件和控制变量闭环。 | `references/platform_test_report_writing_standard.md`、`references/3021_runtime_validation_closure_playbook.md` |
| 茶吧机/窗帘专项问题 | `artifacts/tasks/3021-茶吧机-窗帘-*`、`artifacts/tasks/3021-复测包-茶吧机-窗帘-*` | 窗帘重启循环、茶吧机命令识别、复测包、手动验证对照。 | 重启循环优先判固件/配置异常；命令识别问题必须证明配置、音频、状态和冒烟对照；不要把未闭环问题写成风险。 | `references/known_issue_diagnosis_matrix.md`、`references/3021_runtime_validation_closure_playbook.md` |
| 中文全垂类平台音频验证 | `artifacts/tasks/3021-zh-platform-audio-runtime-*`、`artifacts/tasks/3021-zh-all-vertical-platform-audio-runtime-*` | 平台音频合成产物、声卡播放、中文多垂类识别结果。 | 固件识别音频优先使用平台正式音频合成；中文必须中文发音人；已合成可复用音频放 `assets/audio/platform_synthesis/`。 | `references/platform_audio_synthesis_test_assets.md`、`assets/audio/platform_synthesis/` |
| 英文版本验证 | `artifacts/tasks/3021-english-firmware-audio-*`、`artifacts/tasks/3021-en-all-vertical-*` | 英文支持矩阵、英文发音人、英文音频合成和声卡识别证据。 | 英文不能从中文垂类外推；当前 UI 仅开放什么就测什么；英文必须选择英文发音人。 | `references/3021_english_ui_minrule_validation_lessons.md`、`references/english_platform_audio_synthesis_runtime_workflow.md` |
| 播报合成 UI-only | `artifacts/tasks/ui-only-broadcast-synthesis-*`、`artifacts/synthesis-validation/*` | 播报产品、手填/协议/主动播报、批量导入、SDK/固件发布下载、异常导入、3021 协议播报。 | 播报合成必须 UI-only；批量导入是目录上传；异常项要区分 UI 拦截、钳制回合法值和异常放行；研发报告要写清输入和影响。 | `references/ui_only_broadcast_synthesis_validation.md`、`SYNTHESIS_MANAGEMENT_VALIDATION.md` |
| 合成管理接口与导入边界 | `artifacts/platform-validation/20260521-*synthesis*`、`artifacts/synthesis-discovery/*` | 音频合成、播报合成、导入表边界、后端接口放行探测。 | UI 主结论与后端健壮性探测分层；表格导入异常要覆盖缺列/空值/重复/长度/文件损坏；下游能否继续创建也要验证。 | `SYNTHESIS_MANAGEMENT_VALIDATION.md`、`references/ui_invalid_input_validation_strategy.md` |
| token/profile 登录态探索 | `artifacts/tasks/profile-token-refresh-*`、`artifacts/tasks/listenai-token-tool-*` | Profile 7、企业微信 QR、token-tool 桌面扫码、手动操作记录。 | token 失效优先 token-tool/profile；只有 token 文件生成且 `/getLoginUser` 校验通过才写回；不能宣称完全无人值守。 | `references/platform_token_tool_login_workflow.md`、`references/platform_profile_login_token_refresh.md` |
| 报告样式迭代 | `artifacts/tasks/*report*`、`artifacts/tasks/*报告*`、`artifacts/platform-validation/*summary*` | 多版 HTML/MD 报告、邮件复制格式、表格边框、科技感样式。 | 报告标题下先给结论；量化覆盖；表格为主体；HTML 邮件内联样式；问题详情可给研发定位。 | `references/platform_test_report_writing_standard.md` |
| Cucumber/Gherkin 探索 | `artifacts/cucumber-debug/` | Cucumber 化验证调试数据。 | 可落地为表达层/用例组织，不适合替代所有平台配置、烧录、UI 交互和真机控制逻辑。 | `orion.skilltest.json`、`SKILL.md` 中能力索引规则 |

## 什么时候需要回看 artifacts

只在以下场景回看本机 `artifacts/`：

1. 用户要求追溯某次历史任务的原始日志、截图或报告中间版。
2. 当前沉淀规则无法解释新问题，需要找历史相同现象的原始证据。
3. 需要复核某个历史固件/SDK 包的实际下载内容，但该包未沉淀为 `assets/` 正式资产。
4. 需要比较报告样式、邮件正文复制效果或历史测试统计口径。

不应回看的场景：

- 新电脑首次执行平台打包/烧录/合成验证。
- 常规 3021 设备问题定位。
- 编写新报告。
- 运行最小规则打包矩阵。
- token 失效处理。

这些场景应直接读取 `SKILL.md` 和对应 `references/`。

## 后续维护规则

每完成一轮大型任务，按下面规则处理：

1. 原始证据继续写入 `artifacts/tasks/<task>/` 或对应验证目录。
2. 若产生可复用经验，必须补充到 `references/`，不要只留在报告或日志里。
3. 若产生稳定资产，如冒烟固件、平台合成音频、模板，应迁移到 `assets/` 并同步 git。
4. 若只是单次任务日志、截图、下载 SDK、临时固件，不同步 git。
5. 若新增问题类型，更新 `references/known_issue_diagnosis_matrix.md`。
6. 若新增历史任务族或关键证据源，更新本文。
7. 更新后执行文本 UTF-8 校验和必要 JSON 校验，再构建可迁移发布副本。
