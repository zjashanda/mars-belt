# 深定制算法打包验证方案与复用思路

## 1. 适用范围

本方案适用于 MarsPlatform「产品管理 -> 语言模型 -> 算法打包 -> 深定制搜索」流程，重点验证：

- 深定制版本能否通过 UI 建立产品并生成多个 release；
- 内置推荐词、自定义词、泛化子词是否进入算法配置；
- 唤醒词 DEC/E2E/mainE2E 与命令词 DEC/E2E/ASRFree 的开关、门限和组合关系；
- `web_config.json` 与 `stage1_install/docs/` 四个词表是否一致；
- 语音注册、多唤醒、深度调优与算法词条变化的联动；
- 平台拒绝、UI 能力缺失、自动化缺陷和真实构建失败的区别。

本方案只覆盖打包和静态产物检查。烧录、串口、电源、声卡和真机运行属于后续独立阶段，不能因为 ZIP 生成成功就推导运行态通过。

## 2. 当前验证结论摘要

当前产品：`奋腾达风扇深定制全参数验证0916A`，productId `2100142930366947329`。

深定制版本：`奋腾达风扇深定制-V2.0.0_F0.0.0_A1.7.5.0_20260831`。

已尝试 44 个配置：

| 分类 | 数量 | 口径 |
| --- | ---: | --- |
| PASS | 37 | 成功生成 ZIP，静态断言通过 |
| EXPECTED | 4 | 平台按已知资源约束拒绝，结果符合预期 |
| BLOCKED | 3 | UI 没有目标开关，提交前停止，无有效构建 |
| FAIL | 0 | 没有未分类的真实失败 |

主要结论：

1. 唤醒词至少保留一个 `mainE2E=true` 时，已验证的 32 个“唤醒有效状态 × 命令词 8 状态”组合全部可以构建。
2. 唤醒词 `mainE2E=false` 的 000、010、100、110 四种状态均在 stage1 被拒绝，错误为主唤醒索引集合不一致。
3. 内置词有完整的 DEC/E2E/mainE2E 或 DEC/E2E/ASRFree 控件；自定义词不是所有专用通路都开放。
4. 自定义父词/子词的 E2E 独立配置已经成功验证；自定义命令词 ASRFree、自定义唤醒词 mainE2E 当前 UI 未暴露。

## 3. 端到端验证流程

```text
实时 UI 能力扫描
    -> 固定一个产品
    -> 读取/选择深定制版本
    -> 只读盘点算法页和深度调优页
    -> 生成配置矩阵与唯一 submission key
    -> 未提交 smoke（验证词源、父子关系、可见控件）
    -> 同一产品串行创建多个 release
    -> 轮询 release/task/pipeline 终态
    -> 下载 ZIP
    -> CRC/必需文件/web_config/四词表断言
    -> 汇总 PASS/EXPECTED/BLOCKED/FAIL
```

### 3.1 前置检查

1. 认证：读取本机 token 或持久浏览器 Profile；只用 `/getLoginUser` 校验，不在日志和报告中输出 token/Cookie。
2. 浏览器：确认 Chrome/Chromium 可用，UI-only 打包不能用历史写接口替代。
3. 深定制版本：在 UI 实时搜索，不能仅凭历史缓存判断版本存在。
4. 产品：目标产品不存在时通过 UI 新建一次；后续所有参数组合复用同一个 productId。
5. 能力：记录内置推荐入口、导入/导出入口、算法表可编辑列、深度调优页签和每行控件。
6. 依赖：检查 `scripts/ui/node_modules`、下载目录和验证脚本；正式运行前先跑一个未提交 smoke。

### 3.2 词条准备

推荐固定少量可追踪词条，不要每个组合更换词集：

| 标识 | 示例 | 来源 | 用途 |
| --- | --- | --- | --- |
| W | 小西小西 | 内置唤醒词 | 唤醒三路真值表 |
| C | 打开风扇摆动 | 内置命令词 | 命令词三路真值表 |
| K | 关闭风扇摆动 | 内置命令词 | 保持不变的保护词 |
| U | 启动专属风扇 | 手工自定义命令词 | 自定义父词 |
| V | 开启专属风扇 | U 的泛化子词 | 父子词独立性 |
| X | 风扇管家 | 手工自定义唤醒词 | 自定义唤醒能力边界 |

提交前必须断言：词条来源、类型、拼音唯一、父子关系、算法行数量和目标控件存在性。

### 3.3 开关和门限模型

唤醒词按 `DEC/E2E/mainE2E` 三位表示，命令词按 `DEC/E2E/ASRFree` 三位表示。

| 路由 | 配置字段 | 主要产物位置 |
| --- | --- | --- |
| 唤醒 DEC | `dec_enable`, `dec_threshold` | `keywords_main.txt` |
| 命令 DEC | `dec_enable`, `dec_threshold` | `keywords_mlc.txt` |
| 唤醒 mainE2E | `main_e2e_enable`, `main_e2e_threshold` | `maplist_main.txt` 的 `main_keyword_lable` |
| 唤醒 E2E | `e2e_enable`, `e2e_threshold` | 以首次成功包差分锁定实际分区 |
| 命令 E2E | `e2e_enable`, `e2e_threshold` | `maplist_mlc.txt` 的 `text_match_lable` |
| 命令 ASRFree | `free_enable`, `free_threshold` | `maplist_mlc.txt` 的 `mlc_freetalk_lable` |

门限测试必须使用真实键盘输入、失焦和保存请求，不能只改 DOM 属性；深度调优通常以扩展词子行作为包内数据源，父行和子行必须同步修改。

## 4. 推荐测试矩阵

### 4.1 最小可复用矩阵

资源和时间有限时，至少执行以下 8 类：

1. 内置推荐基线：W=001、C=010，确认默认词集能进入包。
2. 唤醒 mainE2E 边界：W=000 与 W=001，确认平台是否要求主唤醒。
3. 唤醒 DEC 对照：W=101，确认 DEC 与 mainE2E 可并存。
4. 命令词三路全关：C=000。
5. 命令词三路全开：C=111。
6. 命令词 E2E+ASRFree：C=011，确认两个通路不互斥。
7. 一个门限变化包：分别改变唤醒 mainE2E 或命令 E2E，并检查词表数值。
8. 一个父子词包：父词和子词使用不同 E2E 门限，确认独立落地。

### 4.2 完整矩阵

完整验证分两层：

- 唤醒自身 8 状态：`000/001/010/011/100/101/110/111`；本版本 000、010、100、110 已证明会因 mainE2E 关闭被拒绝。
- 命令自身 8 状态：全部已验证成功。
- 交叉矩阵：只对唤醒 `001/011/101/111` 与命令 8 状态做 32 个有效组合，避免重复提交已知拒绝状态。

每个组合只创建一个 release，版本描述使用短向量，例如 `W111-C111`；不得用产品名或长版本号替代配置摘要。

## 5. 逐包验证和判定

### 5.1 UI 配置断言

- productId 与计划一致；不允许在同一轮创建第二产品。
- 内置推荐词和自定义词数量、类型、来源、拼音符合计划。
- 算法表不能为空；父词/子词不能重复或错绑。
- 目标深度控件存在时，逐组设置使能和门限；不存在时记录具体行、组、控件名称。
- `saveDepthData` 请求和响应都存在，业务码为 200。
- 提交前保存 UI 截图、状态 dump 和网络证据。

### 5.2 平台构建断言

- release 状态必须到 `success`，并且同时存在 taskId、pipelineId、pkgUrl。
- `failed` 必须保留 pkgLogs，区分 stage1/stage2/下载/runner。
- 没有 task/pipeline/pkgUrl 的“配置中”记录不能算构建失败或成功。

### 5.3 ZIP 静态断言

每个成功 ZIP 至少检查：

1. ZIP CRC 通过；
2. `Standard_product/web_config.json` 可解析；
3. `Standard_product/deep_respak_output/deep_respak.bin` 存在；
4. 四个词表存在且文本结构可解析：`keywords_main.txt`、`keywords_mlc.txt`、`maplist_main.txt`、`maplist_mlc.txt`；
5. `web_config.json` 中的词条开关和门限与计划一致；
6. 使能路由在预期词表出现，禁用路由不出现；
7. 与当前包无关的保护词 K/U 行保持不变；
8. 记录 ZIP SHA-256、大小和验证 JSON。

## 6. 状态分类规则

| 状态 | 适用条件 | 是否计入固件成功率 |
| --- | --- | --- |
| PASS | ZIP 成功，全部静态断言通过 | 是 |
| EXPECTED | 已提交但平台按已知业务/资源约束拒绝 | 否，单独统计 |
| BLOCKED | 控件、认证、依赖或环境前置不满足，未进入有效构建 | 否，不能叫平台失败 |
| FAIL | 进入有效构建或验证流程，结果错误且非预期 | 是，必须闭环 |
| SCRIPT_LIMITATION | 自动化定位失败或脚本缺陷 | 不计入产品成功率，先修脚本 |

`rawStatus` 和 `reviewedStatus` 必须同时保留。报告不能把 `EXPECTED`、`BLOCKED` 或草稿混入成功率。

## 7. 失败定位决策树

```text
没有 release/task/pipeline
  -> 看 UI 是否存在目标控件
     -> 不存在：BLOCKED / 平台能力边界
     -> 存在但脚本找不到：SCRIPT_LIMITATION

有 release，stage1 failed
  -> 读取 pkgLogs
     -> 主唤醒索引不一致：检查 mainE2E 是否全部关闭
     -> 词表/拼音/资源断言：保留最小控制变量包

release success 但 ZIP 断言失败
  -> 先检查验证器映射和词表分区
  -> 再核对 web_config 与 saveDepthData
  -> 确认父行/子行是否同步
  -> 不能把脚本口径错误直接判为平台失败
```

## 8. 本次已知问题和升级建议

### 平台侧

1. 明确产品契约：若业务允许唤醒词全部关闭，stage1 必须修复主唤醒索引集合生成；若不允许，应在 UI 提交前给出明确校验而不是构建器断言。
2. 在自定义唤醒词行暴露 mainE2E 控件，或明确标注自定义唤醒词不支持主唤醒通路。
3. 在自定义命令词和子词行暴露 ASRFree 控件，或在能力目录中返回 `Unsupported`，让测试计划提前裁剪。
4. 为 `saveDepthData` 返回结构化字段级错误，避免只能依赖页面缺控件或 stage1 日志定位。
5. UI 表头要明确区分“内置唤醒词/mainE2E”和普通 E2E，防止测试人员误以为所有唤醒词都有相同能力。

### 自动化和验证侧

1. 每次平台升级先执行未提交 smoke，确认新增意图、推荐弹窗、父子行和深度页签定位器。
2. 使用动态 submission namespace 和 ledger，禁止重复提交旧向量。
3. 首次出现新词表格式时先做路由差分，再固化断言映射。
4. 只在控制变量包中追加新 release；不要为了试错重复创建产品。
5. 下载后立即断言，失败包保留日志和 release 信息，不删除历史证据。

## 9. 结果目录约定

```text
runtime/<task>_<timestamp>/
├── task_config.json
├── task.log
├── task_events.jsonl
├── errors.log
├── results.csv
├── summary_final.json
├── summary_final.md
├── <case>/result.json
├── artifacts/*.zip
└── artifacts/validations/*.json
```

报告必须给出相对证据路径；token、Cookie、完整授权头和浏览器 Profile 不得进入报告、JSON、CSV、ZIP 或 git。

## 10. 复测入口

- UI runner：`scripts/ui/ui_3021_packaging_runner.js`
- 计划生成：`scripts/ui/build_deep_custom_depth_runner_plan.js`
- 交叉矩阵生成：`scripts/ui/build_deep_custom_cross_matrix.js`
- ZIP 下载：`scripts/ui/download_matrix_artifacts.js`
- ZIP 断言：`scripts/ui/validate_deep_custom_depth_artifact.js`
- 结果报告：`scripts/ui/generate_deep_custom_validation_report.js`

复测前必须重新扫描 UI 当前能力；不能把本文件中的历史成功/拒绝结论当作新平台版本的先验事实。
