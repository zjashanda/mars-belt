# 平台固件打包配置参数与算法模板覆盖矩阵

生成来源：`scripts/ui/generate_algo_template_variants.py`。

## 使用原则

- 模板按参数能力和测试类型选择，不按“中文/英文基础三件套”粗略选择。
- 正式 UI 打包优先使用平台 UI 当前下载的最新模板作为底板；本目录生成的模板是 fallback 和测试数据参考。
- UI 页面下拉、芯片、语言、垂类、SDK 版本必须运行时从 UI 当前页面确认，不能从本 manifest 固化。
- 语音注册负例不得使用语音注册控制词，避免误触发真实学习/删除功能。
- 语音注册算法模板的 `词条预处理` 不得导入 `学习命令词/删除命令词/学习唤醒词/删除唤醒词/删除全部命令词/退出学习/退出删除` 等普通协议命令；这些控制词必须由 UI 语音注册配置生成 special 控制词。否则运行时会命中普通协议命令并发送协议帧，无法进入学习态。
- 综合模板词量较大；容量或编译失败时拆分为专项模板复测。

## 模板清单

| 模板 | 语言 | 用途 | 适用测试 | 覆盖能力 | 风险 |
| --- | --- | --- | --- | --- | --- |
| `assets/templates/algo_zh_base_core.xlsx` | zh | 基础功能：默认唤醒、业务命令、音量边界、退出识别、负性词、欢迎/播报/休息/心跳协议。 | 基础中值包；左右边界包；无专项功能的垂类烟测 | 基础唤醒；业务命令；音量调节/边界；主动播报；被动播报；心跳协议；负性词 | 低：推荐作为所有包的基础模板。 |
| `assets/templates/algo_zh_protocol_active_passive.xlsx` | zh | 协议专项：主动识别发送协议、被动接收协议触发播报、被动命令回复、心跳协议。 | 协议主动/被动专项；recProtocol/sndProtocol 回归；replyMode 主/被切换验证 | 主动协议；被动协议；被动播报；被动命令；心跳协议 | 低：协议口波特率必须和包配置一致。 |
| `assets/templates/algo_zh_multi_wakeup_loop.xlsx` | zh | 多唤醒循环切换：默认唤醒词、两个候选唤醒词、切换/查询/恢复公共指令。 | multiWkeMode=loop；wakeWordSave 与循环切换联动 | 多唤醒候选词；循环切换；查询唤醒词；恢复默认；掉电保持联动 | 中：需要产品支持多唤醒且候选词不能与默认唤醒冲突。 |
| `assets/templates/algo_zh_multi_wakeup_specified.xlsx` | zh | 多唤醒指定切换：候选唤醒词、指定切换到候选词、查询和恢复默认。 | multiWkeMode=specified；指定切换正反例；默认/冻结唤醒词验证 | 指定切换；候选唤醒词；默认唤醒词；冻结唤醒词；查询/恢复 | 中：必须通过 UI 当前多唤醒表格配置候选词属性。 |
| `assets/templates/algo_zh_multi_wakeup_protocol.xlsx` | zh | 多唤醒协议切换：协议确认型候选切换、协议触发播报、查询/恢复公共链路。 | multiWkeMode=protocol；协议切换候选唤醒词 | 协议切换唤醒词；多唤醒 snd/recProtocol；被动播报；查询/恢复 | 中：协议口和波特率不一致会造成假失败。 |
| `assets/templates/algo_zh_voice_reg_specific.xlsx` | zh | 语音注册指定学习：基础宿主动作、可学习目标命令；学习/删除/退出控制词由 UI 语音注册配置生成 special 词条。 | voiceRegEnable=true；releaseRegist.registMode=specificLearn；repeat/retry 正反例 | 指定学习；学习命令词；学习唤醒词；删除命令词；删除唤醒词；退出学习/删除；全部删除 | 中：模板内不能重复导入普通协议控制词。 |
| `assets/templates/algo_zh_voice_reg_continuous.xlsx` | zh | 语音注册连续学习：基础宿主动作、可学习目标命令；连续学习入口由 UI 语音注册配置生成 special 词条。 | releaseRegist.registMode=contLearn；连续学习状态机回归 | 连续学习；自动下一条；两步删除命令词；模板满处理；重试耗尽 | 中：学习态需等待提示播报结束和算法重建完成。 |
| `assets/templates/algo_zh_voice_reg_boundary_delete.xlsx` | zh | 语音注册边界/删除：字数上下限、重试次数、模板上限、删除闭环；删除入口由 UI special 词条提供。 | 语音注册边界包；删除闭环包；retryCount/repeatCount/maxLimit 边界 | 字数上下限；重试次数；模板上限；删除闭环；失败后不生效 | 中：字数边界语料运行时合成，模板只提供宿主动作。 |
| `assets/templates/algo_zh_depth_tuning.xlsx` | zh | 深度调优：提供唤醒词、命令词、子泛化词基础数据，用于 pinyin/DEC/E2E/embedded/ASRFree 调优。 | 灵敏度/深度调优专项；releaseDepthList 阈值边界 | 唤醒词调优；命令词调优；子泛化词；pinyin；DEC/E2E/ASRFree/embedded | 中：阈值变化需要固定音频集统计，不宜仅凭一次识别判定。 |
| `assets/templates/algo_zh_full_feature_stateful.xlsx` | zh | 综合状态包：基础功能 + 协议主动/被动 + 多唤醒指定切换 + 语音注册宿主动作。 | 全功能保持开启包；功能耦合冒烟；报告前综合回归 | 基础功能；协议；多唤醒；语音注册；掉电保持 | 高：词量较多；语音注册控制词必须由 UI special 配置生成，3021 上如出现内存超限应拆分专项模板定位。 |
| `assets/templates/algo_en_base_core.xlsx` | en | 基础功能：默认唤醒、业务命令、音量边界、退出识别、负性词、欢迎/播报/休息/心跳协议。 | 基础中值包；左右边界包；无专项功能的垂类烟测 | 基础唤醒；业务命令；音量调节/边界；主动播报；被动播报；心跳协议；负性词 | 低：推荐作为所有包的基础模板。 |
| `assets/templates/algo_en_protocol_active_passive.xlsx` | en | 协议专项：主动识别发送协议、被动接收协议触发播报、被动命令回复、心跳协议。 | 协议主动/被动专项；recProtocol/sndProtocol 回归；replyMode 主/被切换验证 | 主动协议；被动协议；被动播报；被动命令；心跳协议 | 低：协议口波特率必须和包配置一致。 |
| `assets/templates/algo_en_multi_wakeup_loop.xlsx` | en | 多唤醒循环切换：默认唤醒词、两个候选唤醒词、切换/查询/恢复公共指令。 | multiWkeMode=loop；wakeWordSave 与循环切换联动 | 多唤醒候选词；循环切换；查询唤醒词；恢复默认；掉电保持联动 | 中：需要产品支持多唤醒且候选词不能与默认唤醒冲突。 |
| `assets/templates/algo_en_multi_wakeup_specified.xlsx` | en | 多唤醒指定切换：候选唤醒词、指定切换到候选词、查询和恢复默认。 | multiWkeMode=specified；指定切换正反例；默认/冻结唤醒词验证 | 指定切换；候选唤醒词；默认唤醒词；冻结唤醒词；查询/恢复 | 中：必须通过 UI 当前多唤醒表格配置候选词属性。 |
| `assets/templates/algo_en_multi_wakeup_protocol.xlsx` | en | 多唤醒协议切换：协议确认型候选切换、协议触发播报、查询/恢复公共链路。 | multiWkeMode=protocol；协议切换候选唤醒词 | 协议切换唤醒词；多唤醒 snd/recProtocol；被动播报；查询/恢复 | 中：协议口和波特率不一致会造成假失败。 |
| `assets/templates/algo_en_voice_reg_specific.xlsx` | en | 语音注册指定学习：基础宿主动作、可学习目标命令；学习/删除/退出控制词由 UI 语音注册配置生成 special 词条。 | voiceRegEnable=true；releaseRegist.registMode=specificLearn；repeat/retry 正反例 | 指定学习；学习命令词；学习唤醒词；删除命令词；删除唤醒词；退出学习/删除；全部删除 | 中：模板内不能重复导入普通协议控制词。 |
| `assets/templates/algo_en_voice_reg_continuous.xlsx` | en | 语音注册连续学习：基础宿主动作、可学习目标命令；连续学习入口由 UI 语音注册配置生成 special 词条。 | releaseRegist.registMode=contLearn；连续学习状态机回归 | 连续学习；自动下一条；两步删除命令词；模板满处理；重试耗尽 | 中：学习态需等待提示播报结束和算法重建完成。 |
| `assets/templates/algo_en_voice_reg_boundary_delete.xlsx` | en | 语音注册边界/删除：字数上下限、重试次数、模板上限、删除闭环；删除入口由 UI special 词条提供。 | 语音注册边界包；删除闭环包；retryCount/repeatCount/maxLimit 边界 | 字数上下限；重试次数；模板上限；删除闭环；失败后不生效 | 中：字数边界语料运行时合成，模板只提供宿主动作。 |
| `assets/templates/algo_en_depth_tuning.xlsx` | en | 深度调优：提供唤醒词、命令词、子泛化词基础数据，用于 pinyin/DEC/E2E/embedded/ASRFree 调优。 | 灵敏度/深度调优专项；releaseDepthList 阈值边界 | 唤醒词调优；命令词调优；子泛化词；pinyin；DEC/E2E/ASRFree/embedded | 中：阈值变化需要固定音频集统计，不宜仅凭一次识别判定。 |
| `assets/templates/algo_en_full_feature_stateful.xlsx` | en | 综合状态包：基础功能 + 协议主动/被动 + 多唤醒指定切换 + 语音注册宿主动作。 | 全功能保持开启包；功能耦合冒烟；报告前综合回归 | 基础功能；协议；多唤醒；语音注册；掉电保持 | 高：词量较多；语音注册控制词必须由 UI special 配置生成，3021 上如出现内存超限应拆分专项模板定位。 |

## 参数到模板映射

| 配置组 | 参数 | 应使用模板类型 | 数据目的 |
| --- | --- | --- | --- |
| 基础配置 | `timeout`<br>`volLevel`<br>`defaultVol`<br>`volMaxOverflow`<br>`volMinOverflow`<br>`uportBaud`<br>`logLevel`<br>`volSave` | `base_core`<br>`protocol_active_passive` | 需要唤醒、命令词、音量调节、退出识别、主动/被动播报数据支撑设备侧验证。 |
| 算法词条 | `releaseAlgoList[*].word`<br>`releaseAlgoList[*].extWord`<br>`releaseAlgoList[*].children[*].extWord`<br>`releaseAlgoList[*].type`<br>`releaseAlgoList[*].reply`<br>`releaseAlgoList[*].replyMode`<br>`releaseAlgoList[*].sndProtocol`<br>`releaseAlgoList[*].recProtocol` | `base_core`<br>`protocol_active_passive`<br>`depth_tuning` | 模板必须提供不同功能类型、父/子泛化词、回复语、主/被播报和协议字段。 |
| 多唤醒 | `multiWkeEnable`<br>`multiWkeMode`<br>`wakeWordSave`<br>`releaseMultiWke.common[*].condition`<br>`releaseMultiWke.common[*].reply`<br>`releaseMultiWke.wkelist[*].condition`<br>`releaseMultiWke.wkelist[*].reply`<br>`releaseMultiWke.wkelist[*].sndProtocol`<br>`releaseMultiWke.wkelist[*].recProtocol`<br>`releaseMultiWke.wkelist[*].isDefault`<br>`releaseMultiWke.wkelist[*].isFrozen` | `multi_wakeup_loop`<br>`multi_wakeup_specified`<br>`multi_wakeup_protocol` | loop/specified/protocol 三种模式的切换触发方式不同，必须分模板分包验证。 |
| 语音注册 | `voiceRegEnable`<br>`releaseRegist.registMode`<br>`releaseRegist.wakeupRepeatCount`<br>`releaseRegist.commandRepeatCount`<br>`releaseRegist.wakeupRetryCount`<br>`releaseRegist.commandRetryCount`<br>`releaseRegist.wakeupRegistMaxLimit`<br>`releaseRegist.commandRegistMaxLimit`<br>`releaseRegist.wakeupWordsMinLimit`<br>`releaseRegist.wakeupWordsMaxLimit`<br>`releaseRegist.commandWordsMinLimit`<br>`releaseRegist.commandWordsMaxLimit`<br>`releaseRegist.wakeupSensitivity`<br>`releaseRegist.commandSensitivity`<br>`releaseRegist.reply`<br>`releaseRegist.replyMode`<br>`releaseRegist.sndProtocol`<br>`releaseRegist.recProtocol`<br>`releaseRegistConfig.*.triggers.*.stages.*.(condition|reply|delReply)` | `voice_reg_specific`<br>`voice_reg_continuous`<br>`voice_reg_boundary_delete` | specificLearn 与 contLearn 状态机不同；边界、重试、模板上限和删除闭环需要独立数据。 |
| 深度调优 | `sensitivity`<br>`releaseDepthList[*].pinyin`<br>`releaseDepthList[*].decEnable`<br>`releaseDepthList[*].decThreshold`<br>`releaseDepthList[*].e2eEnable`<br>`releaseDepthList[*].e2eThreshold`<br>`releaseDepthList[*].embeddedEnable`<br>`releaseDepthList[*].embeddedThreshold`<br>`releaseDepthList[*].asrFreeEnable`<br>`releaseDepthList[*].asrFreeThreshold` | `depth_tuning` | 阈值、拼音和使能项需要同时存在唤醒词、命令词、子泛化词，便于固定音频集统计。 |
| 综合状态 | `wakeWordSave`<br>`volSave`<br>`multiWkeEnable`<br>`voiceRegEnable` | `full_feature_stateful` | 状态保持和能力耦合需要同包冒烟，但容量异常时必须拆回专项模板定位。 |

## 兼容旧文件名

| 旧模板名 | 等价 profile |
| --- | --- |
| `assets/templates/algo_zh_basic.xlsx` | `zh_base_core` |
| `assets/templates/algo_zh_multi_wakeup.xlsx` | `zh_multi_wakeup_specified` |
| `assets/templates/algo_zh_voice_register.xlsx` | `zh_voice_reg_specific` |
| `assets/templates/algo_en_basic.xlsx` | `en_base_core` |
| `assets/templates/algo_en_multi_wakeup.xlsx` | `en_multi_wakeup_specified` |
| `assets/templates/algo_en_voice_register.xlsx` | `en_voice_reg_specific` |
