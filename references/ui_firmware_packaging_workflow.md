# UI-only 固件打包流程参考

## 数据源原则

- 每次测试和打包必须以平台 UI 当前页面、当前下拉选项和当前联动结果为准。
- 历史枚举结果、CSV、JSON、截图只作为设计测试方案、排查差异和复现问题的参考，不能作为脚本内置死数据。
- 不允许把历史产品/语言/芯片/SDK 版本矩阵硬编码成后续打包输入；脚本必须重新进入 UI，确认目标产品或目标选项当前可见、可选、可继续。
- 固件打包主链路必须 UI-only：产品创建/复用、配置、模板导入、保存和生成都由浏览器 UI 触发。
- 若直接调用 options/API，只能作为 UI 同源只读探测、登录态注入或 release 状态轮询；不得用写接口创建产品、强制配置 UI 不可选项或直接触发打包。

## 登录态注入

前端路由守卫不只检查 token。无头浏览器进入页面前需要补齐：

- `TOKEN`
- `USER_INFO`
- `MENU`
- `DICT_TYPE_TREE_DATA`

数据通过当前 token 请求平台实时接口获取：

- `/auth/b/getLoginUser`
- `/sys/userCenter/loginMenu`
- `/dev/dict/tree`

## 推荐执行链路

1. 打开 `产品管理 -> 固件打包`。
2. 先查询当前 UI 中是否真实存在目标产品：存在则进入 `详情`；平台为空或目标产品不存在时，必须通过 UI `新增` 新建产品。
3. 点击 `快速创建>>>`。
4. 在基础配置页修改目标参数，例如默认音量、日志级别、串口等。
5. 点击 `继续` 进入算法配置。
6. 若算法配置为空，通过 UI 的 `导入数据` 上传当前模板或本地 fallback 模板。
7. 继续到深度调优、完成页。
8. 在版本描述中填写短配置摘要，例如 `默认+指定唤醒`、`左边界+连续学习+循环`、`右边界+协议唤醒`、`关闭隔离`。
9. 点击 `生成并关闭`。
10. 轮询 UI 或同源 release page 接口确认状态从 `pending` 到 `success`，再做产物/SDK/真机验证。

## 配置取值压缩策略

- 取值策略仅作为 UI-only 打包主链路的配置压缩方法；历史 API 打包不得再作为平台固件打包测试入口。
- 列表型参数不全枚举。音量 `[1..10]` 这类只取最小、中间、最大三个代表值；非数字列表取首项、典型项、末项。
- 布尔参数必须覆盖 `true/false`，但应合并到不同配置向量包中，不为单个布尔值单独打包。
- 字符串参数取一个合法样例即可，除非目标是验证 UI 异常提示。
- 普通数字输入参数使用代表值，不机械打极限边界。语音注册模板数、重试次数、次数上限优先用 `1/3/5`；若 UI 范围不足，则取范围内低/中/高近似值，例如只允许 `1/2` 时覆盖 `1/2`。
- 一个 release 应尽量同时覆盖基础配置、协议/日志、掉电保存、算法模板和专项能力参数，避免 one-param-one-package。

## 3021 全量 UI 打包执行口径

- 同产品多固件版本：一个产品组合只创建/复用一个产品，`base_*`、`multi_*`、`voice_*` 作为同产品下多个 release 生成，不再为每个配置创建新产品。
- 如果平台当前没有任何产品，或目标产品不存在，必须通过 UI 新建一个产品；新建成功后同一产品下连续生成多个配置 release。
- release 版本描述必须简短表达配置差异，不超过一行，不写冗余产品信息；推荐格式为 `边界/默认 + 专项模式 + 协议/保存摘要`。
- `base_mid/base_left/base_right` 只是配置向量 profile 名称，不代表只改一个字段。基础包必须同时覆盖基础参数、串口/日志、掉电保存、播报文本、基础算法模板、主被动协议数据。
- 多唤醒包必须在基础配置向量上叠加 `multiWkeEnable + multiWkeMode + 候选唤醒词表 + 默认/冻结/协议字段`，分别覆盖 `loop/specified/protocol/off_negative`。
- 语音注册包必须在基础配置向量上叠加 `voiceRegEnable + registMode + 模板数/重试次数代表值 + 学习命令子表格`，分别覆盖 `specific/continuous/boundary/off_negative`。
- 批量结果必须展开 `coveragePoints`，不能只在报告里写 profile 名称，否则无法证明单包覆盖了多项配置。

## UI 创建产品异常处理

- 严格 UI-only 任务中，产品创建也必须走 UI；平台为空时不允许复用历史产品，也不允许用旧 API 参数创建隐藏产品壳。
- 新建产品必须使用页面实时联动：语言 -> 产品大类 -> 产品小类/垂类 -> 芯片模组卡片 -> SDK/垂类版本。每一步以后都要等待下一步控件变为可选。
- 芯片模组不是稳定的 `.ant-card` 结构时，脚本应按最新弹窗内可见文本定位卡片，例如包含 `CSK3021-CHIP` 且不包含其他模组文本，再点击卡片中心；不能假设历史 DOM class 固定。
- 若版本下拉未启用，优先检查是否真实选中了芯片模组卡片；只有模组选择生效后，版本下拉才会出现当前 UI 可选 SDK/垂类版本。
- API/options 接口只能作为只读排查或和 UI 当前展示交叉确认，不能用于强制创建、强制选择 UI 不暴露的垂类/版本，不能混入严格 UI 主结论。
- 若 UI 新建产品仍失败，记录为 `strict_ui_create_product_failed`，附失败截图、当前弹窗 body 文本、下拉/卡片可见状态和已尝试修正规则。
- 历史 API 产品壳兜底只允许作为非严格 UI 兼容性附录，报告必须单独标记，不得计入 UI-only 通过结果。
- V1.0 老版本若出现 UI/API 辅助产品可进入配置页、完成页提示成功但 release 列表为空，应记录为 `legacy_v1_generate_no_release`，不能按成功或未测处理。

## 产品存在/不存在分支

| 场景 | 处理方式 | 证据要求 |
| --- | --- | --- |
| 目标产品已存在 | 通过 UI 查询产品名，进入该产品 `详情`，在同一产品下连续创建多个 release。 | 产品列表命中截图/响应、产品 ID、每个 release ID。 |
| 平台为空或产品不存在 | 点击 `新增`，完全通过 UI 新建一个产品；保存成功后再进入详情创建 release。 | 新建产品弹窗截图、UI 选项、保存后的产品记录。 |
| 产品创建成功但 release 配置失败 | 不重新建产品；继续复用同产品定位 release 配置页失败点。 | release 配置步骤、失败页截图、接口只读状态。 |
| 产品创建失败 | 不使用旧 API 参数兜底；先修复 UI 选择链路或列为 UI 阻塞。 | 失败分类、当前 UI 可见信息、已尝试动作。 |

## 版本描述规则

- 必填，且保持简短；用于平台版本列表直接区分同一产品下不同配置。
- 推荐示例：`默认+指定唤醒`、`左边界+循环唤醒`、`右边界+协议唤醒`、`默认+指定学习+指定唤醒`、`左边界+连续学习+循环`、`关闭隔离`。
- 若 UI 页面当前不暴露版本描述输入，记录为 `version_description_ui_not_exposed`，不得用 API 补写后计入 UI-only 成功证据。

## 结果汇总与编码

3021 UI 打包批量结果使用以下脚本汇总，生成 JSON/Markdown/CSV 三份证据；CSV 使用 `utf-8-sig`，便于 Excel 打开不乱码。

```bash
python3 scripts/ui/summarize_3021_packaging_results.py \
  --root artifacts/tasks/3021-ui-packaging-20260611-121007 \
  --plan artifacts/tasks/3021-ui-packaging-20260611-121007/ui_3021_packaging_plan_latest.json
```

输出文件：

- `ui_3021_packaging_result_summary.json`
- `ui_3021_packaging_result_summary.md`
- `ui_3021_packaging_result_summary.csv`

## 可复用脚本

脚本：`scripts/ui/ui_firmware_packaging.js`

安装依赖：

```bash
npm install --prefix scripts/ui --no-audit --no-fund
```

示例命令：

```bash
node scripts/ui/ui_firmware_packaging.js \
  --product-name '测试窗帘垂类' \
  --default-vol 3 \
  --log-level WARN \
  --algo-template 'assets/templates/聆思科技_命令词播报词协议配置表V1.0_中文模板.xlsx'
```

注意：`--product-name` 和 `--algo-template` 必须显式传入，避免脚本静默使用历史默认值。若平台 UI 可下载最新模板，优先下载最新模板后再作为 `--algo-template` 输入。

## 本地模板

当前仓库保留基础模板和按测试类型生成的 fallback 模板，只用于离线参考或平台模板不可下载时临时导入验证：

- 中文：`assets/templates/聆思科技_命令词播报词协议配置表V1.0_中文模板.xlsx`
- 英文：`assets/templates/聆思科技_算法配置英文模板.xlsx`
- 参数驱动模板清单：`assets/templates/template_manifest.json`
- 模板覆盖矩阵：`assets/templates/template_requirement_matrix.md`

这两份模板不能代表平台永远最新格式；正式验证前如 UI 提供模板下载，应以 UI 下载版本为准。

## 模板选择规则

测试哪类功能，就导入同类功能模板，不再使用“中文/英文各三份”的粗略策略：

| 测试类型 | 中文模板 | 英文模板 |
| --- | --- | --- |
| 基础功能/边界/普通垂类 | `assets/templates/algo_zh_base_core.xlsx` | `assets/templates/algo_en_base_core.xlsx` |
| 主动/被动协议 | `assets/templates/algo_zh_protocol_active_passive.xlsx` | `assets/templates/algo_en_protocol_active_passive.xlsx` |
| 多唤醒循环切换 | `assets/templates/algo_zh_multi_wakeup_loop.xlsx` | `assets/templates/algo_en_multi_wakeup_loop.xlsx` |
| 多唤醒指定切换 | `assets/templates/algo_zh_multi_wakeup_specified.xlsx` | `assets/templates/algo_en_multi_wakeup_specified.xlsx` |
| 多唤醒协议切换 | `assets/templates/algo_zh_multi_wakeup_protocol.xlsx` | `assets/templates/algo_en_multi_wakeup_protocol.xlsx` |
| 语音注册指定学习 | `assets/templates/algo_zh_voice_reg_specific.xlsx` | `assets/templates/algo_en_voice_reg_specific.xlsx` |
| 语音注册连续学习 | `assets/templates/algo_zh_voice_reg_continuous.xlsx` | `assets/templates/algo_en_voice_reg_continuous.xlsx` |
| 语音注册边界/删除 | `assets/templates/algo_zh_voice_reg_boundary_delete.xlsx` | `assets/templates/algo_en_voice_reg_boundary_delete.xlsx` |
| 深度调优 | `assets/templates/algo_zh_depth_tuning.xlsx` | `assets/templates/algo_en_depth_tuning.xlsx` |
| 全功能耦合冒烟 | `assets/templates/algo_zh_full_feature_stateful.xlsx` | `assets/templates/algo_en_full_feature_stateful.xlsx` |

旧文件名仍保留兼容：`algo_zh_basic.xlsx`、`algo_zh_multi_wakeup.xlsx`、`algo_zh_voice_register.xlsx` 及英文同名等价模板；新测试计划应优先使用上表的 profile 文件名。
