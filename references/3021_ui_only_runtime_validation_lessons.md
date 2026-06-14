# 3021 UI-only 打包 / 固件 / SDK 真机验证经验

本文沉淀 2026-06-14 3021 全支持垂类 UI-only 重新打包、固件真机验证、SDK 编译产物验证和语音注册问题闭环的可复用规则。

## 适用场景

- 平台“产品管理 -> 固件打包”要求严格 UI-only：产品创建、基础配置、算法配置、生成 release 都通过 UI 触发。
- 3021 支持垂类的批量回归：每个垂类固定一个代表品类，同一个产品下生成多个配置 release。
- 需要下载固件包和 SDK 包，并用本地 3021 设备完成烧录、日志、协议、声卡识别、SDK 编译产物运行验证。

## 本轮参考结论

- 主配置 release：21/21 UI 构建成功。
- 连续学习纠偏 release：3/3 UI 构建成功。
- 主配置真机：18 个正向功能包 PASS；3 个 `wakeTimeout=1 + 连续学习 + retry=1` 左边界包按边界行为闭环，不作为语音注册正向学习成功判据。
- 连续学习正向：3 个正常超时纠偏包 PASS。
- SDK：6/6 下载、解压、readme 确认、`build.sh -r all` 编译 PASS；6/6 编译 `app.bin` 烧录运行 PASS。

## UI-only 打包执行规则

1. 每次执行前实时读取 UI 当前下拉、卡片、版本和联动结果；历史 catalog、CSV、JSON 只做设计参考，不能作为死数据。
2. 平台为空或目标产品不存在时，必须通过 UI 新建产品；同一垂类同一代表产品下连续生成多个 release。
3. release 版本描述要短，写配置向量，例如 `默认+指定唤醒`、`左边界+连续学习+循环`、`右边界+协议唤醒`。
4. 生成 release 后不要逐包等待；先提交所有配置，再统一轮询后台构建状态，最后统一下载固件/SDK。
5. 长时间不操作平台时，每 1 小时内做一次轻量交互，避免 token 2 小时无交互失效。

## 最小配置矩阵经验

- 不要一参一包。一个 release 应组合多个配置点，覆盖等价类和依赖链路。
- 无语音注册、支持多唤醒：通常 3 包覆盖默认指定切换、左边界循环切换、右边界协议切换。
- 同时支持语音注册和多唤醒：通常 4 个主包覆盖默认指定学习+指定唤醒、左边界连续学习+循环、右边界指定学习+协议、关闭隔离。
- 如果某个组合因为边界语义无法承担正向功能验证，新增纠偏包；纠偏包必须只调整必要参数，例如把 `wakeTimeout=1` 改回正常超时，其他保持最小依赖。

## 运行态验证 gate

固件或 SDK 编译产物烧录成功不等于测试通过。每包至少校验：

- 烧录工具成功，并恢复到运行态上电。
- 启动日志存在，例如 `APP version`、`Running Config`、`Engine info`、`ai create success`。
- 日志等级能设置到 `loglevel 4`。
- 协议口按 `web_config.json` 波特率收发，日志出现 `[RX]` 或对应协议帧。
- 声卡播报默认唤醒词、代表业务命令、音量命令；日志能看到对应 intent/pinyin 和 `[TX]`。
- 多唤醒按当前模式验证：指定切换看切换控制词 + 候选唤醒词；循环切换看候选唤醒词识别；协议切换看协议链路。
- 语音注册按当前模式验证成功 marker，不能只看有活动日志。

## 语音注册收敛规则

1. 测试前优先执行 `clear.configall` 并重新上电，清掉 `wkword/regSave/reg_cmd_count` 等历史状态；历史学习数据会导致假失败。
2. 注册目标必须来自 `web_config.json` 的 `firmware.study_config.reg_commands`，不能用 preferred business command 覆盖。
3. 语音注册控制词不能作为普通协议命令导入算法模板；学习、删除、退出等控制词只能由 UI 语音注册 special 配置生成。
4. 进入学习态后等待提示音结束和算法重建完成，再播下一句；优先观察 `play stop`、`reg status: 1`、`Reg info`、`cmdlist get`。
5. 成功判定使用明确 marker：`reg cmd over success`、`save new voice.bin`、`reg auto next`、`reg over!`、`voice regging over`、`reg status: 3`。
6. 连续学习不要把内置命令词本身当注册样本；这会触发冲突/错误。应使用合法非内置别名样本，例如为 `打开风扇` 注册 `我要吹风`。
7. `wakeTimeout=1 + 连续学习 + retry=1` 只能作为左边界/超时观察包。该组合会持续 `Wk timeout`，不适合作为语音注册正向成功用例；正向连续学习要用正常超时纠偏包验证。

## SDK 验证闭环

每个垂类至少选择 1 个 SDK 包：

1. 下载 SDK zip，确认外层包含 `MarsSDK_product/mars-sdk.zip`。
2. 解压后进入 `MarsSDK_product/mars-sdk/mars-sdk`。
3. 阅读 readme，Linux 优先执行 `build.sh -r all`；Windows 才使用 `build.bat`。
4. 若系统 PEP668 阻止 pip 安装依赖，使用隔离 `PYTHONPATH` 或本地 pydeps 解决，不修改系统 Python。
5. 确认生成 `build/bin/app.bin`，记录 size 和 sha256。
6. 烧录该 `app.bin`，再复用对应固件包 `web_config.json` 做运行态验证。

## 问题归因模板

失败后不要停在 `FAIL`，按以下顺序闭环：

1. 设备链路：用已知可用 3021 冒烟固件复测，排除供电、boot、协议门控、声卡、串口映射。
2. 配置状态：执行 `clear.configall` 后重新上电，排除历史学习/唤醒状态。
3. 包配置语义：确认当前包是不是边界包、关闭隔离包、协议包，不能拿边界包验证正向功能。
4. 固件产物：解包检查 `web_config.json` 和固件文件是否符合配置预期。
5. SDK 产物：区分 SDK 可编译、`app.bin` 可烧录、运行态可用三件事。
6. 测试执行：复核 TTS 文本、时序、提示音等待、成功 marker，不把执行器问题写成平台问题。

## 证据与报告要求

- 结构化报告格式按 `references/platform_test_report_writing_standard.md` 执行：标题下先写测试结论，再写测试目的、测试方案、测试用例和结果、测试问题与分析。
- 报告必须列出：垂类/代表产品/配置包清单、固件验证结果、SDK 编译结果、SDK app.bin 烧录结果、失败项归因。
- 对裁剪或不作为正例的配置必须说明原因，例如 `wakeTimeout=1` 左边界包只验证超时/协议/循环唤醒。
- 报告中不要只给总数；异常项必须写清测试意图、执行到哪一步、观察到什么日志、最终归因是什么。
- 附件至少包含主计划、release poll summary、download summary、firmware summary、sdk build summary、sdk app runtime summary。
