# 合成管理自动化验证说明

## 范围

用于验证平台「合成管理」能力，覆盖：

- 音频合成：模板下载、试听音频、项目创建/查询/详情/编辑/删除、Excel 导入、草稿保存、产物合成、产物详情、备注编辑、zip 下载、产物清理。
- 播报合成：芯片/版本选项、播报产品创建/查询/详情/编辑/删除、播报版本手填创建、协议行创建、详情、编辑、复制、SDK 发布、SDK zip 下载、版本清理。
- 自定义音频：小体积 WAV 上传、查询、备注编辑、音频下载、Excel+目录批量导入、删除闭环。

## 固定入口

- 独立模块：`scripts/py/synthesis_management/`
- 全功能实现：`scripts/py/synthesis_management/validation.py`
- 批量导入异常矩阵：`scripts/py/synthesis_management/batch_import_negative.py`
- 导入/边界专项：`scripts/py/synthesis_management/import_boundary_validation.py`
- 导入下游闭环：`scripts/py/synthesis_management/import_downstream_validation.py`
- 兼容入口：`scripts/py/listenai_synthesis_validation.py`
- Token：默认读取当前 skill 根目录 `TOOLS.md` 中的 `LISTENAI_TOKEN=`。
- 结果目录：`artifacts/synthesis-validation/<YYYYMMDD-HHMMSS>/`
- 关键报告：
  - `synthesis_validation_result.md`
  - `synthesis_validation_result.json`
  - `语音合成文本导入模板.xlsx`

## 标准执行

```bash
python3 scripts/py/listenai_synthesis_validation.py --publish-broadcast
```

等价模块命令：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.validation --publish-broadcast
```

该命令会执行完整闭环：

1. 读取菜单，确认 `合成管理/音频合成/播报合成` 存在。
2. 读取字典，确认发音人和压缩比可用。
3. 下载音频合成 Excel 模板。
4. 调用 `generateAudio` 生成试听 mp3，并校验返回内容是音频。
5. 生成临时 WAV，执行自定义音频上传、查询、备注编辑、下载、Excel+目录批量导入，再删除。
6. 创建 `AUTO_TEST_SYNTH_*` 音频合成项目，验证项目查询、详情、编辑、Excel 导入、草稿保存、产物详情、备注编辑、草稿转合成、手填产物合成、zip 下载，再删除产物和项目。
7. 创建 `AUTO_TEST_BROADCAST_*` 播报产品，验证产品查询、详情、编辑，创建自动播报版本，发布 SDK 并轮询成功，下载 SDK zip，再创建协议播报版本，验证编辑、复制、删除，最后删除产品。
8. 最终复核平台无 `AUTO_TEST_*` 临时数据残留。

## 导入表和边界专项

正常全链路只能证明平台可用，不能证明异常兜底正确。涉及“从文件导入表、异常数据、合成上限、条数上限、单条字符上限”的需求，必须额外执行：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.import_boundary_validation
```

数据来源规则：

- 表格内容可以按模板自动构造，包括正常、缺列、空字段、重复、超长、非法协议等异常数据。
- UI 页面元素不能自造；发音人、压缩比、芯片、版本、下拉枚举、已有产品等必须来自 `/dev/dict/tree`、`/biz/broadcast/options`、页面查询或实际 UI 可选项。
- 对 UI 不可能选择但 API 可直接传入的值，例如非法发音人，只能归类为“接口健壮性探测”，不能等同 UI 可执行路径。
- 报告必须区分：真实 UI 触发、UI 等价 API、前端规则建模、接口健壮性探测。
- V4.0.5 起主结论必须按“正常人在 UI 上能操作”的口径执行：严格 UI 结论必须由浏览器 UI/人工 UI 触发；只调用 UI 同款接口但没有经过前端组件的，最多作为 UI 等价 API 辅助证据。UI 会拦截的负例只记录为前端校验，不得绕过 UI 强行提交后端并混入主结论；UI 不可选/不可填的伪造字段只作为单独 API 探测。
- 禁止通过直接改 API 请求体强行设置 UI 页面不可填写、不可选择、不可提交的参数；若确需验证后端健壮性，必须显式标注为非 UI 路径，并从功能验收结论中隔离。

证据分级：

- `browser_ui`：Playwright/Selenium 或人工在页面上选择文件、点击导入/保存并抓取页面提示/网络请求；可进入 UI 主结论。
- `ui_equivalent_api`：脚本构造的数据等价于 UI 校验通过后的表单或导入结果，但直接调用接口；只能证明后端处理和流程联通。
- `frontend_model`：根据前端源码或页面规则判断会被拦截，未提交后端；只能证明规则模型，需在报告中写明未触发服务端。
- `api_probe`：直接调用接口、旧接口或 UI 不可达参数；只能作为后端健壮性/风险探测。

覆盖范围：

- 音频合成导入表：空表、缺 `序号/音频名称/播报文本` 列、字段为空或仅空格、序号非数字、重复音频名/序号、非法文件名字符、音频名长度、文本长度、导入行数、损坏 xlsx、csv 后缀。
- 播报合成导入表：合法 1/10 行、50/100/200/500 行、播报内容长度、音频描述长度、仅空格、重复音频描述、重复接收协议。
- 试听合成：文本为空/长文本、语速和音量边界/越界、非法发音人。

判定规则：

- 预期拒绝但返回 `code=200`：后端校验放行风险。
- 预期拒绝且接口失败但仅返回“服务器异常”或空信息：错误提示不合格。
- 边界探测项返回通过时，需要在报告中写明“当前未观察到上限”，不能误写成已经证明无上限。
- 播报音频文件格式类异常仍执行 `synthesis_management.batch_import_negative`，覆盖 `>20KB`、`32000/48000/64000Hz`、多通道、码率超限、损坏/空文件、伪后缀、缺 xlsx、缺音频等。
- `batch_import_negative.py` 和 `v405_validation.py` 中直接调用 `/biz/audiofile/validate`、`/biz/audiofile/batchImport`、`/biz/audiofile/batchImportItems` 的结果默认是 API/等价 API 证据；MP3/WAV 上传限制（码率、bit depth、采样率、声道、后缀）必须通过真实 UI 上传组件复核后，才能写成 UI 功能缺陷。
- 如果导入阶段出现“预期拒绝但放行”，继续执行下游闭环，验证异常行是否还能创建音频合成产物或播报版本：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.import_downstream_validation --source-report artifacts/platform-validation/<timestamp>-synthesis-import-boundary/synthesis_import_boundary_result.json
```

## 人工复核模式

如果需要在平台账号里直接看到本次生成的音频合成/播报合成记录，执行：

```bash
python3 scripts/py/listenai_synthesis_validation.py --publish-broadcast --keep-platform-records
```

该模式会跳过最终清理，并保留 `AUTO_TEST_*` 记录供页面复核。复核完成后必须再执行标准命令，或调用脚本的初始清理逻辑，清掉这些临时数据。

## 播报 SDK 设备侧复核

- 设备侧复核前先静态检查 SDK zip：必须确认 `Standard_product/fw.bin`、`out/stage2_output/cfg.json`、`ring_cfg.json` 都存在，并核对 `uart_config.trace_uart/trace_baud`、`uport_uart/uport_baud`、`command.recv_pro_buffer`、`play_id` 与音频资源是否一致。
- 当前本地 3021 台架默认串口：日志 `/dev/ttyACM0@115200`、协议 `/dev/ttyACM2@9600`、控制 `/dev/ttyACM4@115200`、烧录 `/dev/ttyACM0@460800`；`/dev/ttyACM0` 暂不作为运行日志口或烧录口使用。
- 不能只因为 SDK zip 可下载/可解压就判定设备侧可运行；必须烧录后看到日志串口、协议口或可观测播报中的至少一种证据。
- 烧录仍遵循固定流程：清空 `scripts/burn/app.bin`，把待烧录固件复制或从 zip 提取成 `scripts/burn/app.bin`，再调用 `Uart_Burn_Tool -f app.bin`；不得把外部 bin/img 路径直接喂给烧录工具。
- 如果播报 SDK 的 `fw.bin/fw.img` 写入成功但设备无启动日志、协议无响应，应记录为“设备侧运行证据不足/当前烧录路径不适配”，不能把平台播报功能判为通过；验证后要恢复已知可用固件，避免设备停留在不可观测状态。

## V4.0.5 播报固件专项

执行：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.v405_validation --publish-broadcast --keep-platform-records --no-persist-token
```

覆盖：

- 音频合成：叶子（情感）默认发音人、默认试听文案、试听文本长度、Excel 导入合法/缺列/空字段/后缀/长度边界。
- 播报控制：导入和新增，覆盖文本合成、本地 MP3、本地 WAV、混合导入；空字段、非法协议、找不到文件等按 UI 校验判定。
- 播报音频上传：只使用 UI 页面能选择的文件；大小、采样率、声道、bit depth、码率、损坏文件、空文件必须由真实 UI 选择文件并触发表单校验，不能只用 `/biz/audiofile/validate` 直连接口替代。
- 控制配置：欢迎语、增大/减小/最大/最小/中等音量新增正例，以及缺类型、缺音频、缺协议、重复功能类型的 UI 校验。
- SDK 发布：有效配置发布、轮询 success、SDK zip 下载解压；需要设备闭环时提取 `Standard_product/fw.bin` 并按固定 `app.bin` 流程烧录。
- 完成审计：最终要输出逐需求审计报告，明确 PASS/PARTIAL/RISK/FAIL/SKIP；示例报告见 `artifacts/platform-validation/20260528-v405-completion-audit/v405_completion_audit.md`。

本轮已确认的 V4.0.5 风险项：

- 菜单仍显示“播报合成”，配置页仍存在“播报控制”，开关文案仍是“主动播报”。
- 音频上传直连接口放行 WAV 8bit、WAV 24bit、MP3 64kbps；该结论当前只能算后端/API 探测风险，需真实 UI 上传组件复核后才能升级为 UI 缺陷。
- 深度调优参数可保存并打包，但产物 `web_config.json` 未体现命令词阈值修改。
- 3021 播报 SDK 固件可烧录成功，但运行态串口/协议未拿到证据，不能判设备播报运行态通过。

## 安全约束

- 所有写入数据必须使用 `AUTO_TEST_*` 前缀。
- 脚本必须在 `finally` 中清理临时产物；即使中间失败，也要尽量删除已创建记录。
- 报告中不得输出 token；认证失败时也必须脱敏。
- 默认不复用用户已有项目做写入验证，避免污染真实数据。

## 主要接口

### 音频合成

- `GET /fw/voice/page`
- `POST /fw/voice/add`
- `GET /fw/voice/detail`
- `POST /fw/voice/edit`
- `POST /fw/voice/delete`
- `POST /fw/common/importRows`
- `GET /fw/voice/output/page`
- `GET /fw/voice/output/detail`
- `POST /fw/voice/output/comments`
- `POST /fw/voice/output/edit`
- `POST /fw/voice/output/add`
- `POST /fw/voice/output/delete`
- `POST /fw/common/generateAudio`
- `GET /fw/common/download/template`
- `GET /dev/file/download`

### 播报合成

- `GET /biz/broadcast/options`
- `GET /biz/broadcast/page`
- `GET /biz/broadcast/detail`
- `POST /biz/broadcast/add`
- `POST /biz/broadcast/edit`
- `POST /biz/broadcast/delete`
- `GET /biz/broadcastrelease/page`
- `GET /biz/broadcastrelease/detail`
- `POST /biz/broadcastrelease/add`
- `POST /biz/broadcastrelease/edit`
- `POST /biz/broadcastrelease/duplicate`
- `GET /biz/broadcastrelease/publish`
- `POST /biz/broadcastrelease/delete`
- `GET /biz/release/download`

### 自定义音频

- `GET /biz/audiofile/options`
- `GET /biz/audiofile/page`
- `POST /biz/audiofile/batchUpload`
- `POST /biz/audiofile/edit`
- `POST /biz/audiofile/batchImport`
- `GET downloadPath`
- `POST /biz/audiofile/delete`

## 播报批量导入要求

- 入口：播报合成版本配置中的“批量导入”以真实 UI 行为为准；当前前端选择目录后读取 `.xlsx`，只保留 `.mp3/.wav/.xlsx`，表格校验通过后调用 `/biz/audiofile/batchImportItems`。`/biz/audiofile/batchImport` 属于旧接口/后端探测口径，不能直接当作 UI 路径。
- 音频文件：仅使用 `.mp3`；要求大小 `<=20KB`、`16K` 单通道、`16bit`、码率 `<=32kbps`。
- 映射文件：`.xlsx`，列名必须包含 `播报内容`、`音频描述`、`接收协议`。
- 关联规则：`音频描述` 必须与 mp3 文件名去掉扩展名后的名称一致，例如 `AUTO_TEST_A.mp3` 对应表格 `音频描述=AUTO_TEST_A`。
- 导入结果：接口返回 `reply/comments/recProtocol` 行数据，前端会把这些行追加到播报版本的 `playConfig`；导入本身不一定在自定义音频列表持久化记录。

异常矩阵执行：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.batch_import_negative
```

导入表/边界专项执行：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.import_boundary_validation
```

导入异常下游闭环：

```bash
PYTHONPATH=scripts/py python3 -m synthesis_management.import_downstream_validation --source-report artifacts/platform-validation/<timestamp>-synthesis-import-boundary/synthesis_import_boundary_result.json
```

### 异常验证现状

- API 探测已正确拒绝：文件 `>20KB`、采样率 `32000/48000`、双声道、码率 `40/64kbps`、损坏 mp3、0 字节 mp3、映射文件非 `.xlsx`、缺少映射文件。
- API 探测曾观察到放行：`.wav` 音频、mp3 内容改成 `.txt/.aac` 后缀、`64000Hz WAV`、xlsx 中 `音频描述` 与音频文件名不匹配、缺少/为空的 `播报内容/音频描述/接收协议`、非法 `接收协议`、只上传 xlsx 不上传音频；这些不能直接写成 UI 缺陷，需真实 UI 复核。
- 下游复核：上述被 API 放行的异常导入行继续调用 `/biz/broadcastrelease/add` 时也会创建成功，说明后端没有二次拦截；但 UI 是否能产生这些异常行，需要单独通过浏览器 UI 证明。
- 最近报告：`artifacts/synthesis-validation/20260519-broadcast-batch-import-negative/negative_batch_import_result.md`。

## 判定标准

- 全部步骤 `PASS`：合成管理链路可用。
- 任一步骤 `FAIL`：必须查看 `synthesis_validation_result.json`，优先确认 token、平台接口返回、临时数据是否已清理。
- SDK 发布必须轮询到 `status=success` 才算通过；只创建播报版本且状态为 `init` 不能算完整播报合成通过。
- 报告必须体现功能点数量和覆盖范围；如果只跑只读或 smoke，必须在结论中明确“不等同于全功能验证”。
