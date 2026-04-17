# 打包平台功能点测试方案与用例矩阵

更新时间：2026-04-10

## 1. 当前 skill 的测试入口

- `base`
  - 用于验证当前打包产物的基础能力与当前配置值。
  - 核心用例来源：`CORE-*`、`BASE-*`。
- `changed`
  - 用于验证“改值后是否生效、是否影响基础功能、是否引入系统性异常”。
  - 核心用例来源：`CHG-*`。
- `voice-reg`
  - 用于验证语音注册学习、删除、冲突、恢复等专项。
  - 核心用例来源：`VOICE-001` ~ `VOICE-014`。
- `multi-wke`
  - 用于验证多唤醒切换、查询、恢复默认、掉电保持、非法配置约束。
  - 核心用例来源：`MWK-*`、`MWK-CFG-001`、`MWK-SAVE-001`。

## 2. 基础功能点覆盖矩阵

| 功能点 | 目标值/值域 | 测试逻辑方案 | 主要用例编号 | 主包/专项归属 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| 唤醒时长 | `1~60 秒` | 解包校验 `timeout_config.time`，实机等待唤醒超时退出，确认未提前退出/不退出 | `CORE-TIMEOUT-001`、`CHG-TIMEOUT-001` | `pkg-01/pkg-02/pkg-03` | 已覆盖；当前 5 包矩阵已覆盖中值、左边界、右边界，`iso-core-prompt` 证明 `timeout=1` 不是低边界异常根因 |
| 音量档位数 | `2/3/5/7/10` | 解包校验档位数，实机执行音量增减直到边界，确认档位切换与边界行为一致 | `CORE-VOLUME-001` | `pkg-01/pkg-02/pkg-03` | 已覆盖；当前主包覆盖 `2/5/10`，`iso-core-prompt` 证明 `volLevel=2` 不是低边界异常根因 |
| 初始化默认音量 | 随 `volLevel` 联动 | 解包校验默认档位，实机重启或初始播报后执行音量查询/边界验证，确认默认档位正确 | `CORE-DEFAULTVOL-001`、`CHG-DEFAULTVOL-001` | `pkg-01/pkg-02/pkg-03` + `pkg-03-defaultvol-only` | 已覆盖；`pkg-03-defaultvol-only` 隔离复跑确认 `defaultVol=10` 已入包，但启动后实际初始化到最小档，属真实固件/运行态问题 |
| 最大音量上溢播报语 | 自定义字符串 | 改值后把音量调到最大档，再次执行“最大音量”命令，确认播报命中自定义文案且不影响基础链路 | `CHG-MAXOVERFLOW-001` | `pkg-01` | 已补齐；字符串参数已在 `pkg-01-mid-stable` 入包，`iso-core-prompt` smoke 正常 |
| 最小音量下溢播报语 | 自定义字符串 | 改值后把音量调到最小档，再次执行“最小音量”命令，确认播报命中自定义文案且不影响基础链路 | `CHG-MINOVERFLOW-001` | `pkg-01` | 已补齐；字符串参数已在 `pkg-01-mid-stable` 入包，`iso-core-prompt` smoke 正常 |
| 协议串口 | `0/1` | 解包校验 `uport_uart`，同步本地串口映射后跑基础唤醒/命令词链路，确认通信不丢失 | `CHG-UPORTUART-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-uart-switch` 单独复现系统性异常，最小失败组合为 `uportUart=0 + traceUart=1` |
| 协议串口波特率 | `2400/4800/9600/19200/38400/57600/115200/921600` | 解包校验 `uport_baud`，同步脚本波特率后跑基础链路，确认协议不乱序/不断链 | `CHG-UPORTBAUD-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-baud2400` smoke 正常，`2400` 不是低边界异常根因 |
| 日志串口 | `0/1` | 解包校验 `trace_uart`，同步本地日志串口后确认日志链路和基础功能正常 | `CHG-TRACEUART-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-uart-switch` 单独复现系统性异常，最小失败组合为 `uportUart=0 + traceUart=1` |
| 日志级别 | `0/1/2/3/4/5` | 解包校验 `logLevel`，观察低/中/高日志级别下设备是否仍稳定运行 | `CHG-LOGLEVEL-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-log1` smoke 正常，`logLevel=1` 不是低边界异常根因 |
| 唤醒词掉电保存 | `开/关` | 配置断言 + 多唤醒切词后断电重启，确认重启后保持/恢复行为 | `CHG-WAKEWORDSAVE-001`、`MWK-SAVE-001` | `pkg-04/pkg-05` | 已覆盖；`true/false` 两种取值均可正常打包。`wakeWordSave=false` 时重启后当前词恢复默认词，说明保存开关本身有效；但 `specified` 模式下非当前唤醒词仍可继续唤醒，属多唤醒运行态隔离问题 |
| 音量掉电保存 | `开/关` | 调到非默认音量后断电重启，确认保持当前音量或恢复默认音量 | `CHG-VOLSAVE-001` | `pkg-01/pkg-03` | 已覆盖；`false/true` 两种取值均可正常打包。先前 `code=500` 已排除为脚本布尔归一化问题；但 `pkg-03-defaultvol-only` 证明 `volSave=true` 右边界组合下仍伴随默认音量初始化异常，需按运行态问题跟踪 |
| 合成发音人 `vcn` | 平台下拉发音人 | 解包校验发音人，实机观察欢迎播报是否仍正常；无法自动判音色时至少验证不重启、不死机 | `CHG-VCN-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-tts-low` smoke 正常；`x4_KoKr_Kyung_assist` 在中文产品上构包失败，现归因为语言与产品不匹配 |
| 合成语速 | `1~100` | 解包校验语速，实机观察欢迎播报是否正常；至少确认改值不影响设备运行 | `CHG-SPEED-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-tts-low` smoke 正常 |
| 合成音量 | `1~100` | 解包校验音量，实机观察欢迎播报与基础链路是否正常 | `CHG-VOL-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-tts-low` smoke 正常 |
| 播报音压缩比 | `1/2/3` | 解包校验压缩比，实机至少验证欢迎播报和基础命令链路不异常 | `CHG-COMPRESS-001` | `pkg-01/pkg-02/pkg-03` | 已补齐；`iso-tts-low` smoke 正常 |
| 欢迎语 TTS 文案 | 自定义字符串 | 解包校验文案，实机唤醒后确认欢迎语为改值文案 | `CHG-WORD-001` | 单独问题项 | 当前打包后 `welcome_config.reply` 未随 `word` 改值，已归类为参数未生效问题 |
| 功放配置开关 | `true/false` | 解包校验 `paConfigEnable`，实机确认设备可稳定启动、播报、识别；必要时硬件侧补充确认电平 | `CHG-PACONFIGENABLE-001` | `pkg-03/pkg-04/pkg-05` | 已补齐；`pkg-03` 高边界组合已覆盖 `true`，当前未发现功放开关导致的运行阻塞，`false` 为稳定基线 |

## 3. 补齐的基础直参测试用例定义

| 用例编号 | 目标配置 | 执行步骤摘要 | 关键判定与可发现问题 |
| --- | --- | --- | --- |
| `CHG-MAXOVERFLOW-001` | `volMaxOverflow=<自定义文案>` | 1. 解包确认文案入包 2. 将音量升到最大档 3. 再次执行“最大音量” | 命中自定义上溢播报；若仍播默认文案、播报异常或影响基础命令链路，可发现参数映射或运行态问题 |
| `CHG-MINOVERFLOW-001` | `volMinOverflow=<自定义文案>` | 1. 解包确认文案入包 2. 将音量降到最小档 3. 再次执行“最小音量” | 命中自定义下溢播报；若播报错误、设备异常或影响基础链路，可发现参数映射或运行态问题 |
| `CHG-UPORTUART-001` | `uportUart=0/1` | 1. 解包确认协议串口 2. 同步本地协议串口映射 3. 跑基础唤醒/命令/协议收发 | 协议链路持续可用；若出现失联、短帧、无响应，可发现串口切换类固件问题 |
| `CHG-UPORTBAUD-001` | `uportBaud=2400/.../921600` | 1. 解包确认协议波特率 2. 同步脚本波特率 3. 跑基础命令与协议收发 | 协议不乱序、不断链；若低波特率或高波特率导致异常，可定位到串口波特率支持问题 |
| `CHG-TRACEUART-001` | `traceUart=0/1` | 1. 解包确认日志串口 2. 同步本地日志监听口 3. 观察启动日志和运行日志 | 日志链路与设备功能同时正常；若参数未生效、日志口不切换或切换后异常，可发现运行态实现缺陷 |
| `CHG-LOGLEVEL-001` | `logLevel=0/1/2/3/4/5` | 1. 解包确认日志级别 2. 观察启动日志和运行日志 3. 跑基础功能 | 低日志级别不应导致设备假死，高日志级别不应引入重启或阻塞；能发现日志级别与判活逻辑的耦合问题 |
| `CHG-WAKEWORDSAVE-001` | `wakeWordSave=true/false` | 1. 切换到非默认唤醒词 2. 断电重启 3. 校验重启后当前生效唤醒词 | 能判断掉电保持是否生效；2026-04-10 已确认耦合包触发打包恢复正常，后续只需继续做设备侧断电链路验证 |
| `CHG-VOLSAVE-001` | `volSave=true/false` | 1. 调到非默认音量 2. 断电重启 3. 校验恢复默认还是保持当前音量 | 能判断音量掉电保持是否生效；2026-04-10 已确认 `false/true` 触发打包恢复正常，先前 `500` 属脚本归一化问题 |
| `CHG-VCN-001` | `vcn=<平台发音人>` | 1. 解包确认发音人 2. 唤醒观察欢迎播报 3. 跑基础命令链路 | 无法稳定自动判音色时，至少要求不重启、不死机、不丢基础功能；能发现构包失败和运行态异常 |
| `CHG-SPEED-001` | `speed=1~100` | 1. 解包确认语速 2. 触发欢迎播报 3. 跑基础功能 | 改值后设备仍稳定，播报链路不异常；主要用于发现边界值导致的播报异常或系统不稳 |
| `CHG-VOL-001` | `vol=1~100` | 1. 解包确认合成音量 2. 触发欢迎播报 3. 跑基础功能 | 改值后播报链路和基础功能正常；主要用于发现极低/极高值导致的异常 |
| `CHG-COMPRESS-001` | `compress=1/2/3` | 1. 解包确认压缩比 2. 触发欢迎播报 3. 跑基础功能 | 改值后设备仍可稳定播报和识别；主要用于发现压缩比切换带来的播报链路异常 |
| `CHG-WORD-001` | `word=<自定义欢迎语>` | 1. 解包检查 `welcome_config.reply` 2. 唤醒设备听欢迎语 | 欢迎语应随配置变化；当前已发现“打包成功但未真实入包”的参数映射问题 |
| `CHG-PACONFIGENABLE-001` | `paConfigEnable=true/false` | 1. 解包确认功放开关 2. 启动设备 3. 验证欢迎播报、唤醒、命令执行 | 能发现改值后静音、重启、识别异常或播放链路异常；必要时再补硬件电平观测 |

## 4. 语音注册与多唤醒专项用例定义

| 用例组 | 覆盖目的 | 代表用例 | 执行要点 | 当前结论 |
| --- | --- | --- | --- | --- |
| 语音注册-学习成功/失败重试 | 验证学习次数、失败重试次数和学习生效链路 | `VOICE-001~006` | 分别覆盖命令词/唤醒词的正例、重试恢复、重试耗尽；成功后验证学习语料生效，失败后验证语料不生效 | `VOICE-006` 暴露真实问题：失败耗尽后禁止学习词仍被错误学习成功 |
| 语音注册-冲突拦截 | 验证功能词、默认唤醒词、保留提示词不能被学习 | `VOICE-007~010` | 输入已支持功能词、默认唤醒词、学习/删除/退出类保留词，观察是否被拒绝，并确认原始功能不被篡改 | `VOICE-007/009` 暴露真实问题：冲突拦截不完整 |
| 语音注册-删除学习数据 | 验证删除成功、删除退出、默认词不受影响 | `VOICE-011~014` | 先学习，再执行删除正反例；删除成功后学习词应失效，退出删除后学习词应保留 | `VOICE-011` 暴露真实问题：删除后别名仍可触发，且过程伴随一次重启 |
| 多唤醒-初始态/切换/查询/恢复 | 验证 `specified` 模式下默认词、切换目标词、查询当前词、恢复默认词 | `MWK-001~006` | 先补齐 2 个额外唤醒词，再依次做切换、查询、恢复；同时校验“非当前词必须失效” | `specified` 模式存在真实问题：非当前词仍可继续唤醒 |
| 多唤醒-配置约束 | 验证“非默认词不能被冻结” | `MWK-CFG-001` | 通过页面/接口尝试保存非法配置 | 当前保持人工校验项，不计入固件运行失败 |
| 多唤醒-掉电保持 | 验证切换后的当前词在断电后保持或恢复默认 | `MWK-SAVE-001` | 先切到非默认词，再断电重启校验当前词 | 当前受 `specified` 模式真实缺陷影响，场景已能稳定发现问题 |

## 5. 本轮 5 包最小覆盖配置矩阵（已执行）

- 设计原则：
  - 数值/枚举参数覆盖左边界、右边界、中值
  - 字符串参数只验证一次
  - 布尔参数覆盖 `true/false`
  - 有依赖的参数必须放入同一包验证
  - `vcn` 必须先按产品语言过滤；中文产品不选英文发音人，英文产品只选英文发音人

| 包名 | 配置清单 | 设计目的 |
| --- | --- | --- |
| `PKG-01 基础中值稳定包` | `timeout=30`、`volLevel=5`、`defaultVol=3`、`volMaxOverflow=中值最大音量播报`、`volMinOverflow=中值最小音量播报`、`uportUart=1`、`traceUart=0`、`uportBaud=38400`、`logLevel=3`、`volSave=false`、`vcn=x2_xiaoye`、`speed=50`、`vol=50`、`compress=2`、`word=欢迎使用边界法验证固件`、`paConfigEnable=false` | 作为稳定基线，同时一次性验证所有字符串型参数和布尔 `false` 基线，不把多唤醒/语音注册混进来 |
| `PKG-02 基础左边界包` | `timeout=1`、`volLevel=2`、`defaultVol=1`、`uportUart=0`、`traceUart=1`、`uportBaud=2400`、`logLevel=0`、`vcn=x_nannan`、`speed=1`、`vol=1`、`compress=1`、`paConfigEnable=false` | 覆盖全部左边界；若整包出现重启/无日志/失联，先按控制变量法拆串口组，再拆其他参数 |
| `PKG-03 基础右边界包` | `timeout=60`、`volLevel=10`、`defaultVol=10`、`uportUart=1`、`traceUart=0`、`uportBaud=921600`、`logLevel=5`、`volSave=true`、`vcn=x3_doudou`、`speed=100`、`vol=100`、`compress=3`、`paConfigEnable=true` | 覆盖全部右边界，同时验证 `volSave=true` 与 `paConfigEnable=true` 在高位组合下不引入系统性异常 |
| `PKG-04 全功能保持开启包` | 基于 `PKG-01` 的中值稳定核心配置，叠加 `voiceRegEnable=true`、`releaseRegist.registMode=specificLearn`、`releaseRegist.commandRepeatCount=2`、`releaseRegist.commandRetryCount=2`、`releaseRegist.wakeupRepeatCount=1`、`releaseRegist.wakeupRetryCount=0`、`multiWkeEnable=true`、`multiWkeMode=specified`、新增唤醒词 `暖风精灵/取暖管家`、`wakeWordSave=true` | 在稳定基础配置上验证语音注册 + 多唤醒 + 唤醒词掉电保持开启的完整耦合链路 |
| `PKG-05 多唤醒保持关闭隔离包` | 基于 `PKG-01` 的中值稳定核心配置，叠加 `multiWkeEnable=true`、`multiWkeMode=specified`、新增唤醒词 `暖风精灵/取暖管家`、`wakeWordSave=false` | 不混入语音注册，只隔离验证多唤醒场景下 `wakeWordSave=false` 的断电恢复逻辑 |

## 6. 当前 5 包验证结果总览

| 包 | 覆盖重点 | 最终结果 |
| --- | --- | --- |
| `pkg-01-mid-stable` | 基础中值稳定基线 + 字符串参数一次性验证 + `volSave=false` | 打包成功；`config-only` 为 `24 OK + 1 ConfigFail`，唯一失败为 `CHG-WORD-001`。结论：`word` 欢迎语参数未真实入包，属平台/模板映射问题，不再阻断其余设备验证判断 |
| `pkg-02-left-boundary` | 全部左边界：`timeout=1`、`volLevel=2`、`defaultVol=1`、`uportUart=0`、`traceUart=1`、`uportBaud=2400`、低边界 TTS | 配置断言通过、烧录成功。经控制变量法收敛，真实最小失败组合为 `uportUart=0 + traceUart=1`；不是脚本误判，也不是其余左边界参数导致 |
| `pkg-03-right-boundary` | 全部右边界：`timeout=60`、`volLevel=10`、`defaultVol=10`、`uportBaud=921600`、`logLevel=5`、`volSave=true`、`paConfigEnable=true` | 配置断言通过；已排除旧的 `traceUart=0 -> /dev/ttyACM1` 错误路径。`pkg-03-defaultvol-only` 隔离复跑确认 `defaultVol=10` 已写入，但启动实际初始化到最小档，属真实固件/运行态问题 |
| `pkg-04-full-save-on` | 中值稳定基线 + `voiceRegEnable=true` + `multiWkeEnable=true` + `wakeWordSave=true` | 打包成功；`config-only` 为 `59 OK + 1 Skip`。原 `CORE-WAKE-*` 受旧脚本固定播放默认唤醒词影响，已通过子集补跑修正为 `5/5 PASS`；当前稳定暴露真实问题为语音注册冲突/删除链路缺陷，以及 `specified` 模式多唤醒隔离失效 |
| `pkg-05-multi-save-off` | 中值稳定基线 + 多唤醒 `specified` + `wakeWordSave=false` | 打包成功；`config-only` 为 `36 OK + 1 Skip`。本轮直接使用修正后的唤醒识别脚本，`CORE-WAKE-002/003/004` 已按目标词实测通过；重启后当前词恢复默认词，说明 `wakeWordSave=false` 生效，但 `specified` 模式下非当前唤醒词仍可继续唤醒 |

## 7. 补跑结果与测试侧修复

- 补跑结果
  - `pkg-04-core-wake-only`
    - 使用修正后的唤醒识别脚本补跑 `CORE-WAKE-001~005`，结果 `5/5 PASS`
    - 结论：`pkg-04` 原始 `CORE-WAKE-*` 失败属于测试脚本问题，不是固件问题
  - `pkg-03-defaultvol-only`
    - 隔离复跑 `CORE-DEFAULTVOL-001`，结果 `1/1 ConfigFail`
    - 关键证据：`running_volume=0`、`df_vol=9`、`init_vol=0`、`set_vol=0->0`、`inferred_default=1`
    - 结论：`defaultVol=10` 已入包，但启动初始化异常，属真实固件/运行态问题
- 测试侧修复
  - `scripts/py/listenai_profile_suite.py`
    - 将 `CORE-DEFAULTVOL-001` 前移，避免前序音量调节与 `volSave=true` 污染默认音量校验
  - `scripts/py/voiceTestLite.py`
    - `唤醒识别/唤醒稳定性` 改为按用例目标词播放，不再固定播放默认唤醒词
  - `scripts/py/listenai_voice_test_lite.py`
    - 增强默认音量取证，直接记录 `running_volume / df vol / init vol lev / set vol`

## 8. 当前已确认的问题归因

- 平台/模板映射问题
  - `word`
    - `pkg-01-mid-stable` 证明参数可提交并成功打包，但 `firmware.welcome_config.reply` 未随改值变化
    - 当前归因为欢迎语字段未真实入包，不是设备执行问题
- 配置规则问题
  - `vcn`
    - 中文产品必须选择中文发音人，英文产品必须选择英文发音人
    - `x4_KoKr_Kyung_assist` 在中文产品上的失败归因为语种选择错误，不归类为平台通用故障
- 固件/运行态问题
  - `uportUart=0 + traceUart=1`
    - 已通过控制变量法收敛为最小失败组合，会导致系统性异常
  - `traceUart=0`
    - 在当前设备/固件上运行态未真实切换，日志仍固定出现在 `/dev/ttyACM0`
  - `defaultVol=10 + volSave=true`
    - `pkg-03-defaultvol-only` 已确认启动时实际初始化到最小档
  - 语音注册
    - `VOICE-006`：禁止学习词被错误学习成功
    - `VOICE-007/009`：冲突拦截不完整
    - `VOICE-011`：删除链路异常，且伴随重启恢复
  - 多唤醒 `specified`
    - `wakeWordSave=true/false` 两种取值下，非当前唤醒词均仍可继续唤醒
    - `wakeWordSave=false` 的“重启恢复默认词”本身有效，问题根因在运行态隔离，不在保存开关
- 已排除为根因的项
  - `wakeWordSave=false`
    - 单项和耦合包均可成功打包，不再归因为参数级打包阻塞
  - `volSave=true`
    - 单项和耦合包均可成功打包，不再归因为参数级打包阻塞
  - `timeout=1 + volLevel=2 + defaultVol=1 + 上下溢播报语`
    - `iso-core-prompt` smoke 正常
  - `logLevel=1`
    - `iso-log1` smoke 正常
  - `uportBaud=2400`
    - `iso-baud2400` smoke 正常
  - `vcn=x_nannan + speed=1 + vol=1 + compress=1`
    - `iso-tts-low` smoke 正常
- 已修复的测试/执行问题
  - `listenai_profile_suite.py` 旧顺序会让 `CORE-DEFAULTVOL-001` 被前序音量操作污染
  - `voiceTestLite.py` 旧逻辑固定播放默认唤醒词，导致多唤醒识别结论失真
  - `listenai_voice_test_lite.py` 旧日志证据不足，已补强默认音量诊断
  - `apply_release_overrides()` 曾把 `"0"/"1"` 型开关写成 `"False"/"True"`
  - `package_release_with_algo_unified()` 曾把平台回读值 `"0"/"1"` 与 Python `True/False` 直接比较

## 9. 验收时建议查看

- 主用例生成逻辑：`scripts/py/listenai_profile_suite.py`
- 唤醒词播放与识别逻辑：`scripts/py/voiceTestLite.py`
- 设备侧默认音量与多唤醒执行逻辑：`scripts/py/listenai_voice_test_lite.py`
- 周跑器串口联动逻辑：`scripts/py/listenai_weekly_validation_runner.py`
- 打包稳定化与 release 复用链路：`scripts/py/listenai_auto_package.py`、`scripts/py/listenai_shared_product_flow.py`
