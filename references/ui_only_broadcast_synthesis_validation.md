# UI-only 播报合成全链路验证参考

本参考用于后续验证「合成管理 -> 播报合成」。结论必须来自浏览器 UI 触发的创建、配置、导入、构建和下载动作；API 只能用于登录态、只读 options、只读轮询和证据补充。

## 推荐脚本

```bash
NODE_PATH="$PWD/scripts/ui/node_modules" \
node scripts/ui/ui_broadcast_synthesis_fullchain.js \
  --out-dir artifacts/tasks/ui-only-broadcast-synthesis-$(date +%Y%m%d-%H%M%S)
```

调试单项时使用：

```bash
node scripts/ui/ui_broadcast_synthesis_fullchain.js --out-dir <out> --case-filter '^publish_sdk_positive$'
node scripts/ui/ui_broadcast_synthesis_fullchain.js --out-dir <out> --case-filter '^invalid_audio_'
```

## 当前 UI 规则

批量导入弹窗当前页面声明：

- 音频格式：MP3
- 大小：不大于 500KB
- 采样率：不大于 48K
- 采样深度：16bit
- 声道：单通道
- MP3 码率：不大于 32kbps
- 映射文件：xlsx，包含 `播报内容`、`音频描述`、`接收协议`

历史资料里的 `<=20KB/16K` 不能直接覆盖当前 UI；每轮执行以页面实时文案为准。

## 自动化注意点

1. 批量导入使用 `webkitdirectory`，Puppeteer 必须对 file input 传入目录路径；不要把目录展开成单文件列表，否则前端不会生成预览。
2. 异常音频专项要用中文文件名和中文 `音频描述`，避免被“回复文本仅支持中文...”遮挡，导致测到的是文本校验而不是音频规格校验。
3. 正例必须从导入预览继续点击保存，再保存播报版本；只看到预览不算导入闭环。
4. 发布下载必须点击 UI 的 `构建`，轮询到 `status=success` 后再点击页面 `下载 -> SDK/固件`；只创建 `status=init` 版本不算通过。
5. 下载菜单需要等待 `.ant-dropdown-menu-item` 可见后再点 `SDK` 或 `固件`，不能只判断页面文本包含 SDK，因为页面说明中也包含 sdk 字样。
6. 3021 真机验证使用 UI 下载固件 zip 的 `Standard_product/fw.bin`，按固定 `scripts/burn/app.bin` 暂存烧录；协议口发送配置中的接收协议帧，日志至少校验 `[RX]` 和 `mini-player play id`。
7. 需要验证 `PA_MGR` 时必须打开功放配置生成专项包；普通合成播报包可能只输出 `[RX]` 和 `mini-player play id`。功放配置专项包应同时校验 `PA_MGR` 刷新 ON/OFF、`[RX]` 和 `mini-player play id`。

## 2026-06-17 实测经验

- 正向链路通过：新建播报产品、手填被动协议播报、主动播报、批量导入 MP3、批量导入文本、50/200 行导入、SDK/固件发布下载、3021 烧录和协议播报。
- 默认音量输入 `999` 被 UI 钳制为合法最大值后保存，按 `PASS_UI_SANITIZED` 处理。
- UI 放行的问题项：非法协议被清洗成仍不合法的 `AA C` 后可保存；脚本/emoji/超长合成文本可保存；缺音频、重复协议、缺接收协议列可导入并保存。
- 异常音频使用中文文件名复测后，超大 MP3、WAV 后缀、双声道 MP3、64K 采样率 MP3、64kbps MP3、损坏 MP3 均可通过 UI 导入并保存为播报版本，属于前端/导入校验不足。
- UI 已拦截：空表、csv 后缀；这些不能写成平台缺陷。
- 2026-06-17 r11 功放配置专项包验证通过：`paConfigEnable=true`，3021 日志同时出现 `[RX]`、`PA_MGR Refresh PA to ON/OFF` 和 `mini-player play id`，说明播报配置进入固件并可由协议口触发播放。

## 异常项判定口径

1. 先确认输入来自真实 UI：手填、文件选择或 `webkitdirectory` 目录上传；直接写接口不能计入 UI 主结论。
2. 再确认是否“钳制回合法值”：异常值被 UI 修正到合法范围并保存时，按防护通过处理，不计平台问题。典型例子：默认音量 `999` 保存为合法 `defaultVol/volLevel`。
3. 如果异常值仍触发 `/biz/audiofile/batchImportItems` 或 `/biz/broadcastrelease/add` 成功，并且保存 payload 没有回归合法值，标记 `ISSUE_UI_ACCEPTED_INVALID`。
4. 不能只写“有风险”：必须写成明确问题，列出实际保存内容或导入返回内容。
5. 对音频规格异常，要保存本地 `file`/`ffprobe` 或等价元数据证据，说明文件确实违反当前 UI 页面声明。

## 研发定位报告口径

给研发看的报告必须包含以下列：

| 列 | 内容 |
|---|---|
| ID | 稳定问题编号，例如 `P1-01` |
| 问题类型 | 协议格式、播报文本、xlsx 映射、音频规格等 |
| 填了什么/上传了什么 | 精确到输入值、文件名、文件规格、xlsx 行内容 |
| 为什么应拦截 | 对应 UI 页面规则或业务约束 |
| 实际通过情况 | 是否触发 `batchImportItems`、`broadcastrelease/add`，返回和保存 payload 摘要 |
| 是否钳制回合法值 | 明确写“未回归合法值”或“已钳制，不计问题” |
| 影响 | 对构建、触发、播放、资源或固件解析的潜在影响 |
| 证据路径 | `result.json`、截图、日志、fixtures 路径 |

本轮确认的 11 个播报合成 UI 应拦截问题，后续回归至少覆盖：

| ID | 问题 | 输入/文件 | 期望 | 已观察实际 |
|---|---|---|---|---|
| P1-01 | 非法协议 | 手填 `AA ZZ <script>` | 拦截或清洗成合法 hex 协议 | 保存为仍非法的 `AA C` |
| P1-02 | 异常播报文本 | `<script>alert(1)</script>😀` 重复超长 | 拦截、清洗或截断到合法文本 | 原样进入 `word` 保存 |
| P1-03 | 音频名不匹配 | xlsx `音频描述=missing_audio`，目录无对应音频 | 导入失败 | 导入并保存成功 |
| P1-04 | 重复接收协议 | 两行同为 `A5 FA 00 81 0B 00 8B FB` | 提示冲突或拒绝保存 | 两条相同协议保存成功 |
| P1-05 | 缺接收协议 | xlsx 缺列/协议为空 | 导入失败 | `recProtocol:""` 保存成功 |
| P1-06 | 超大 MP3 | `超大音频.mp3`，532480 bytes，超过 500KB | 拦截 | 导入并保存成功 |
| P1-07 | WAV 后缀 | `波形音频.wav`，RIFF/WAVE PCM | 拦截 | 导入并保存成功 |
| P1-08 | 双声道 MP3 | MP3 16k Joint Stereo | 拦截 | 导入并保存成功 |
| P1-09 | 64K 采样率 | 实际 64000 Hz 音频 | 拦截 | 导入并保存成功 |
| P1-10 | 64kbps MP3 | MP3 64kbps | 拦截 | 导入并保存成功 |
| P1-11 | 损坏 MP3 | `file` 识别为 data，非有效 MP3 | 拦截 | 导入并保存成功 |

不计入平台问题但要在报告中说明：默认音量 `999` 被钳制回合法值；空表和 csv 后缀已拦截；200 行导入当前无明确上限，只记录边界现状。

## 报告要求

报告按 `references/platform_test_report_writing_standard.md` 输出，结构固定为：

1. 测试结论
2. 测试目的
3. 测试方案
4. 用例和结果
5. 测试问题与分析
6. 证据索引

异常项必须写清：测试意图、UI 操作路径、是否触发 `/biz/audiofile/batchImportItems` 或 `/biz/broadcastrelease/add`、实际保存内容和影响。

邮件版报告必须使用 inline table border，不依赖 `<style>` 或外部 CSS，避免复制到邮件后表格线条丢失。
