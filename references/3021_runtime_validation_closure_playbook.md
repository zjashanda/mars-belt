# 3021 运行态验证闭环 Playbook

## 适用场景

用于 3021 固件包烧录后出现“无日志、无唤醒、命令缺失、语音注册失败、多唤醒异常、边界包误判”等问题时快速收敛。目标不是重跑更多包，而是按固定顺序把问题落到明确归因：配置构造、测试用例、状态清理、声学/台架、设备链路、固件产物或平台缺陷。

## 固定闭环顺序

1. **静态门禁先于烧录**：解析 `web_config.json`，检查 special 控制词是否被普通协议词遮蔽；命中则直接 `CONFIG_FAIL`，不烧录、不播放、不写成平台缺陷。
2. **烧录前预清状态**：允许用当前已运行固件执行一次 `clear.configall` 清历史 `wkword/regSave/reg_cmd_count`。
3. **目标包启动后读 Running Config**：记录 `volume/voice/wkword/regSaveFlag/regSaveSize/reg_cmd_count/reg_cmd_status`，作为后续归因证据。
4. **语音注册包禁止后置 clear**：目标语音注册包启动后禁止再执行 `clear.configall`，否则会清掉目标包的学习配置。
5. **按配置选择用例集**：左边界包只跑 wake+timeout；协议多唤醒包先发协议切换帧；语音注册包按学习模式执行。
6. **命令缺失用单词专项**：单个命令缺失时，不直接判固件失败，先用 `--command-probe-word` 重复验证并输出 `commandProbeCounts`。
7. **冒烟固件隔离环境**：若基础唤醒/协议/日志整体异常，烧录已知可用冒烟固件验证台架和声学链路。

## 静态配置拦截规则

- 多唤醒语音切换控制词：`切换唤醒词`、`恢复默认唤醒词`、`查询唤醒词`。
- 语音注册控制词：`学习命令词`、`删除命令词`、`学习唤醒词`、`删除唤醒词`、`删除全部命令词`、`退出学习`、`退出删除`。
- 这些词如果同时存在 `special_type` 和普通 `snd_protocol/rec_protocol`，普通协议词会遮蔽 special 状态机，应标记 `CONFIG_FAIL`。
- `CONFIG_FAIL` 表示测试配置构造不合理，不表示平台打包失败、固件运行失败或设备失败。

## `wkword=255` 判定

- 基础包、普通多唤醒语音切换包出现 `wkword=255`：先按状态污染处理，预清/重烧/重启恢复；仍异常再标记运行态状态异常。
- 协议切换多唤醒包出现 `wkword=255`：不能粗暴拦截。先发送默认唤醒词协议切换帧，再观察是否出现 `cur wk id ... != ...`、`not waked!` 或命令被唤醒态拒绝。
- `wkword=255` 单独出现不能作为 FAIL；必须结合包类型和后续日志判定。

## 语音注册执行规则

- 指定学习多目标：`小聆小聆 -> 学习命令词 -> 目标命令 -> 重复目标命令本身`。
- 指定学习只从 UI `study_config.reg_commands` 中选择目标；优先选择当前业务 preferred，否则按 UI 顺序选择。
- 连续学习：进入学习态后直接说合法自然别名样本，不再额外重复目标命令；自然别名不能是内置命令词本身。
- 删除链路必须闭环：先确认学习词可用，再删除，再确认学习词不可用。
- 成功 marker 必须是强信号：`wIvwRegistArbitrate success`、`save new voice.bin`、`reg cmd over success`、`reg auto next`、`reg over!`、`reg status: 3`。
- `voice regging over` 只是流程结束，不是成功；若伴随 `reg length error`、`wreg write failed`、`wIvwRegistWrite fail`、`reg failed`、`error cnt >`，必须继续归因。

## 左边界包规则

`wakeTimeout<=1` 的包只验证：

- 烧录和启动。
- `loglevel 4` 可设置。
- 协议 RX/播报链路可观测。
- 默认唤醒词能触发 Wakeup，并能观察到 `Wk timeout`。

不执行完整命令词、多唤醒长链路和语音注册正向学习。报告必须写明完整功能由默认包或右边界/协议包覆盖。

## 命令级识别专项

当同包唤醒、协议、多唤醒或语音注册已通过，仅某个业务命令缺失时，执行：

```bash
python3 scripts/py/run_3021_firmware_batch_verify.py \
  --download-summary artifacts/tasks/<task>/download/download_summary.json \
  --indices <index> \
  --out-dir artifacts/tasks/<task>/runtime/command_probe_<word> \
  --summary artifacts/tasks/<task>/runtime/command_probe_<word>/summary.json \
  --preclean-state \
  --platform-audio \
  --command-probe-word <命令词> \
  --command-probe-repeat 3
```

判定：

- `commandProbeCounts` 有稳定计数：主批次缺失归为声学/状态偶发，不写成固件或平台配置失败。
- 仍无识别，但其他命令稳定：归为命令级声学/阈值/词条匹配问题。
- 冒烟固件也无唤醒：归为台架声学或播放/采集链路问题，先修环境。

## 归因口径

报告和计划中使用下面的归因，不使用“有风险”这种含糊描述：

- `配置构造问题`：静态门禁拦截、功能开关与模板不匹配、special 重名。
- `测试用例问题`：左边界包跑了长链路、指定学习样本不符合状态机、未等待提示播报结束。
- `状态清理问题`：历史 `regSave/wkword/reg_cmd_count` 污染，或目标包启动后错误 clear。
- `声学/状态偶发`：主批次单个命令缺失，但单词专项 PASS。
- `台架/设备链路问题`：冒烟固件也不能完成启动、协议或唤醒。
- `固件产物问题`：冒烟固件正常，目标包启动/配置/协议整体异常且排除配置构造。
- `平台缺陷`：UI 合法配置生成的产物缺失、构建失败、配置未入包或平台前端未拦截明确非法配置。

## 本轮 21 包闭环样例

- 最终批次：21 包，PASS 8，CONFIG_FAIL 12，FAIL 1，烧录失败 0。
- 12 个 `CONFIG_FAIL`：多唤醒 special 控制词与普通协议词重名，属于配置构造问题。
- 唯一主批次 FAIL：取暖桌协议多唤醒+指定学习包核心命令缺失；`打开取暖桌`、`关闭取暖桌` 单词专项均 PASS，归因为声学/状态偶发。
- 语音注册 idx5/idx9/idx16 专项均 PASS，说明语音注册功能可用；关键修正是禁止后置 clear、指定学习重复目标命令本身。

## 必留证据字段

runner summary 至少保留：

- `staticFindings/staticBlocked`
- `precheckConfig`
- `stateDirtyReason/stateRecoveryAttempted/stateRecoveryOk`
- `invalidWakeStateMarkers`
- `protocolWakeSwitchSent/protocolWakeSwitchFrame`
- `boundaryMode/boundaryTimeoutObserved`
- `commandProbeWords/commandProbeCounts`
- `voiceRegWords/voiceRegFailureMarkers`
- `coreMissing/multiMissing/gates/verdict`

