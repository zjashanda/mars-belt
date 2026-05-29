# 平台接口自动化验证说明

## 目标

用于验证平台业务接口中可以安全落地的功能点。当前模块覆盖 P1/P0：发音词典、协议模板、算法/补丁打包及其关联固件配置读取。

## 入口

```bash
python3 scripts/py/listenai_platform_api_validation.py
```

或：

```bash
PYTHONPATH=scripts/py python3 -m platform_api_validation.validation
```

Token 固定从当前 skill 的 `TOOLS.md` 中读取：`LISTENAI_TOKEN=`。

## 覆盖范围

| 模块 | 已验证接口/能力 |
|---|---|
| 我的发音词典 | `/biz/pinyinDict/page`、`template`、`getTerms`、`add`、`detail`、`export`、`import`、`delete` |
| 协议模板 | `/fw/protocol/page`、`add`、`detail`、`configs`、`records`、`refreshConfigs`、`delete` |
| 算法/补丁打包 | `/biz/releaseAlgo/page`、`detail`、`audioConfigTemplate`、`import`、`depthConfig` |
| 固件关联读取 | `/biz/release/detail`、`getAlgoConfig`、条件探测 `download`、`downloadLogs` |

## 安全规则

- 写入动作只创建受控数据：`AUTO_TEST_*` 或 `自动测试*`。
- 默认清理受控记录，避免污染平台账号。
- 不对非受控历史 release 执行写入型配置覆盖接口。
- 报告中隐藏 token。
- 结果统一保存在 `artifacts/platform-validation/<YYYYMMDD-HHMMSS>/`。

## 当前限制

以下接口必须依赖受控 release 或成功打包产物，不能直接对历史共享记录执行：

- `/biz/releaseAlgo/{id}/depthConfigSave`
- `/biz/release/saveAlgoConfig`
- `/biz/release/rewriteAlgoWakeupAndCmdConfigs`
- `/biz/releaseAlgo/delete`
- `/biz/release/download`、`/biz/release/downloadLogs`：只有样本存在真实产物/日志时才能验证下载内容。

## V4.0.5 深定制验证补充

V4.0.5 需求验证时已补充受控 release 方式，不能再只读历史记录后直接跳过写入链路：

- 全词条/算法参数保存打包：使用 `package-custom` 创建受控 3021 release，通过 `algoUnifiedSave` 保存 `sensitivity=中` 并成功打包下载。
- 词条勾选子集打包：复制受控 release 后仅保留“小聆小聆/打开风扇”两个词条，保存后打包产物只包含该子集。
- 深度调优保存打包：`/fw/release/saveDepthData` 可保存命令词阈值修改并触发打包；但下载产物未观察到修改后的阈值，需作为风险单独上报。
- 当前源版本 `free_cmd`、`muti_intent` 为 `Unsupported`，自由说/多意图继承无可验证样本，不能伪造参数强测。

关键报告：

- `artifacts/platform-validation/20260528-v405-completion-audit/v405_completion_audit.md`
- `artifacts/platform-validation/20260528-v405-deep-algo-subset/v405_deep_algo_subset_result.md`
- `artifacts/platform-validation/20260528-v405-deep-depth-tune-command/v405_deep_depth_tune_command_result.md`
