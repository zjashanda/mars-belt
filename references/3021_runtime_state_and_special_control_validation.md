# 3021 运行态状态清理与 special 控制词校验规则

## 适用范围

适用于 3021 中文/英文固件包真机运行态验证，尤其是全垂类最小规则包、多唤醒包、语音注册包和左边界包复测。

如果需要完整闭环决策树、失败归因口径和本轮 21 包样例，先读取 `3021_runtime_validation_closure_playbook.md`。本文只保留状态清理、special 门禁、左边界和命令专项的执行规则。

## Runner 前置状态检查

1. 烧录目标包前，可以使用当前已运行固件执行一次 `clear.configall` 作为预清理，目的是清掉上一包遗留的 `wkword/regSave/reg_cmd_count`。
2. 预清理必须发生在目标包烧录之前；目标包烧录启动后，runner 先读取 `Running Config`，记录：
   - `volume`
   - `voice`
   - `wkword`
   - `regSaveFlag`
   - `regSaveSize`
   - `reg_cmd_count`
   - `reg_cmd_status`
3. `wkword=255` 不能只看数值粗暴判定：
   - 普通语音切换/基础包出现 `wkword=255`，优先尝试清理并重新上电；仍不可恢复时标记 `ENV_STATE_DIRTY`。
   - 协议多唤醒切换包可能合法显示 `wkword=255`。声学验证前先发送默认唤醒词协议切换帧（优先取 `multi_wakeup.switch_list[0].snd_protocol`），再发送业务协议探针；只有后续出现 `cur wk id: ... != ...`、`not waked!` 或命令被唤醒态拒绝，才标记无效状态。
   - 语音注册包目标启动后不允许通过后置 `clear.configall` 修复，因为会破坏目标包学习配置。
4. Runner summary 必须输出 `precheckConfig`、`stateDirtyReason`、`stateRecoveryAttempted`、`stateRecoveryOk` 和 `invalidWakeStateMarkers`，用于后续归因。

## 语音注册包清理策略

- 禁止目标语音注册包启动后执行 `clear.configall`。
- 允许烧录前对上一包执行预清理，目标包烧录后由目标包自己的 `study_config` 初始化 `reg_cmd_count`。
- 若目标语音注册包启动后出现历史模板已满、删除态残留或 `regSaveSize` 异常，先烧录已知可用冒烟固件或非语音包预清，再重新烧录目标包复测。
- 连续学习进入学习态后直接说合法注册样本，不再重复目标命令；注册样本不能使用内置命令词本身。
- 指定学习要根据 `study_config.reg_commands` 数量自适应：
  - 只有一个目标或 `学习命令词` 后直接出现 `Reg info/cmdlist get/reg status:1`，进入学习态后优先重复目标命令本身。
  - 多个可学习目标时执行 `学习命令词 -> 目标命令 -> 重复目标命令本身`；目标优先选择 UI 当前 `reg_commands` 中的业务 preferred，没有时按 UI `reg_commands` 顺序选择第一个目标。不要在多目标指定学习中替换成自然别名样本，否则容易触发相似度/写入错误或未进入目标学习态。
- 语音注册成功必须用强 marker 判定：`wIvwRegistArbitrate success`、`save new voice.bin`、`reg cmd over success`、`reg auto next`、`reg over!` 或 `reg status: 3`。单独的 `voice regging over` 只是流程结束，不代表写入成功。
- 出现 `reg length error`、`wreg write failed`、`wIvwRegistWrite fail`、`reg failed` 或 `error cnt >` 时，即使后面有退出态日志，也必须按失败继续复测和归因。

## special 控制词静态门禁

运行态验证前必须解析 `web_config.json`，检查 `asr_cmds` 和扩展词：

- 语音注册控制词不得与普通协议词重名：`学习命令词`、`删除命令词`、`学习唤醒词`、`删除唤醒词`、`删除全部命令词`、`退出学习`、`退出删除` 等。
- 多唤醒语音切换控制词不得与普通协议词重名：`切换唤醒词`、`恢复默认唤醒词`、`查询唤醒词`。
- 若同名词同时存在 `special_type` 和普通协议 `snd_protocol/rec_protocol`，普通协议词会遮蔽 special 状态机，应静态标记 `CONFIG_FAIL`，不能继续把运行态失败归因到设备或音频。
- 当前脚本入口：

```bash
python3 scripts/py/validate_3021_firmware_static.py \
  --download-summary artifacts/tasks/<task>/download/download_summary.json \
  --out-json artifacts/tasks/<task>/static_special_shadow.json
```

## 左边界包用例集

`wakeTimeout<=1` 的包只验证边界行为：

1. 烧录启动成功。
2. 日志口可设置 `loglevel 4`。
3. 协议口至少一次 RX/协议链路可观测。
4. 声卡重复播放默认唤醒词，期望看到 Wakeup 与 `Wk timeout`。

该类包不执行长链路命令词、多唤醒完整切换、语音注册学习/删除正向流程。完整功能由默认包或右边界/协议包覆盖。

## 命令级专项复测

若同包默认唤醒和其他命令可识别，只有某个业务命令缺失，不能直接归因到平台/固件整体失败。应使用命令专项模式重复播放目标词：

```bash
python3 scripts/py/run_3021_firmware_batch_verify.py \
  --download-summary artifacts/tasks/<task>/download/download_summary.json \
  --indices <index> \
  --out-dir artifacts/tasks/<task>/runtime/command_probe_<word> \
  --summary artifacts/tasks/<task>/runtime/command_probe_<word>/summary.json \
  --preclean-state \
  --allow-special-shadow \
  --platform-audio \
  --command-probe-word <命令词> \
  --command-probe-repeat 3
```

专项结果必须输出每个词的 `commandProbeCounts`。若重复 3 次仍无 intent，但同包其他命令稳定，则归为命令级声学/阈值/词条匹配问题；若连已知可用冒烟固件也无唤醒，则先归为台架声学链路异常。

## 已知可用冒烟对照

运行态声学异常时，先烧录 `assets/firmware/3021-smoke/3021_fan_ui_smoke_verified_20260612.zip`。若冒烟固件也无唤醒/命令识别，而启动日志、协议 RX、声卡 probe 均正常，则当前阻塞在声学播放/采集链路，不应继续把 21 包全部跑成固件失败。
