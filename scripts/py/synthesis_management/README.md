# 合成管理验证模块

该目录独立承载平台「合成管理」相关自动化验证逻辑，避免继续堆在单一脚本中。

## 文件职责

- `validation.py`：音频合成、播报合成、自定义音频的全功能链路验证。
- `batch_import_negative.py`：播报合成批量导入异常矩阵验证。
- `import_boundary_validation.py`：音频合成导入表、播报合成导入表、试听合成的异常兜底和边界上限验证。
- `import_downstream_validation.py`：对导入阶段被错误放行的异常行继续做音频产物/播报版本下游闭环复核。
- `v405_validation.py`：V4.0.5 播报固件专项，按 UI 可操作口径覆盖音频合成导入、播报控制导入/新增、控制配置新增、音频上传异常和 SDK 发布。
- 主测试结论必须模拟正常 UI 操作：只提交页面可填写、可选择、可导入、可上传产生的数据；UI 不可填写/不可选择参数不得通过 API 强行修改，只能单独作为接口健壮性探测。
- `__init__.py`：模块导出。

## 推荐入口

兼容旧命令：

```bash
python3 scripts/py/listenai_synthesis_validation.py --publish-broadcast
```

模块方式：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.validation --publish-broadcast
PYTHONPATH=scripts/py python3 -m synthesis_management.batch_import_negative
PYTHONPATH=scripts/py python3 -m synthesis_management.import_boundary_validation
PYTHONPATH=scripts/py python3 -m synthesis_management.import_downstream_validation --source-report artifacts/platform-validation/<timestamp>-synthesis-import-boundary/synthesis_import_boundary_result.json
PYTHONPATH=scripts/py python3 -m synthesis_management.v405_validation --publish-broadcast --keep-platform-records --no-persist-token
```

## 结果目录

- 正常链路：`artifacts/synthesis-validation/<timestamp>/`
- 批量导入异常矩阵：`artifacts/synthesis-validation/<timestamp>-broadcast-batch-import-negative/`
- 导入/边界专项：`artifacts/platform-validation/<timestamp>-synthesis-import-boundary/`
- 导入下游闭环：`artifacts/platform-validation/<timestamp>-synthesis-import-downstream/`
- V4.0.5 专项：`artifacts/platform-validation/<timestamp>-v405-full-validation/`
