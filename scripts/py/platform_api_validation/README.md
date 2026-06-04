# 平台接口验证模块

该目录承载平台业务接口的受控自动化验证，当前覆盖：

- 我的发音词典：分页、模板下载、分词、受控新增、详情、导出、TXT 导入、清理。
- 协议模板：分页、受控新增、详情、配置查询、受控刷新协议字段、记录查询、清理。
- 算法/补丁打包：算法词条分页/详情、音频配置模板下载、模板导入解析、深度配置读取、关联固件详情/算法配置读取。

标准入口：

```bash
python3 scripts/py/listenai_platform_api_validation.py
```

模块入口：

```bash
PYTHONPATH=scripts/py python3 -m platform_api_validation.validation
```

结果目录：`artifacts/platform-validation/<YYYYMMDD-HHMMSS>/`。

安全规则：

- 写入数据必须使用 `AUTO_TEST_*` 或 `自动测试*` 受控前缀。
- 默认清理平台临时记录；只有人工页面复核时才使用 `--keep-platform-records`。
- 不对非受控历史发布执行 `depthConfigSave`、`saveAlgoConfig`、`rewriteAlgoWakeupAndCmdConfigs`、`delete` 等破坏性接口。
- 报告中不得输出 token 明文。
