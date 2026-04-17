# 3021 中文取暖器垂类产品范围与当前验证状态

更新时间：2026-04-10

## 1. 产品识别依据

- 产品：`小家电 / 取暖器`
- 模组：`CSK3021-CHIP`
- 语言：`中文`
- 版本：`取暖器垂类-V2.0.1_F2.0.3_A1.4.1.0`

当前判断依据：

- 参数目录：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021中文取暖器垂类，音量9档0331192003310919_2/catalog/parameter_catalog.json`
- 本地样本汇总：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021中文取暖器垂类，音量9档0331192003310919_2/summary.json`

feature gate 结论：

- `voice_regist=Unsupported`
- `multi_wakeup=Optional`

这意味着 3021 中文取暖器垂类当前不能按“全平台通用功能”一刀切测试，必须按产品能力裁剪：

- 语音注册相关功能不应进入当前产品测试范围
- 多唤醒相关功能应进入当前产品测试范围

## 2. 当前产品应测功能点

### 基础配置

- 功能点：`timeout`
  - 唤醒时长
- 功能点：`volLevel`
  - 音量档位数
- 功能点：`defaultVol`
  - 初始化默认音量
- 功能点：`volMaxOverflow`
  - 最大音量上溢播报语
- 功能点：`volMinOverflow`
  - 最小音量下溢播报语

### 串口配置

- 功能点：`uportUart`
  - 协议串口
- 功能点：`uportBaud`
  - 协议串口波特率
- 功能点：`traceUart`
  - 日志串口
- 功能点：`logLevel`
  - 日志级别

### 掉电配置

- 功能点：`wakeWordSave`
  - 唤醒词掉电保存
  - 仅在多唤醒链路中联动验证
- 功能点：`volSave`
  - 音量掉电保存

### 播报配置

- 功能点：`vcn`
  - 合成发音人
  - 中文产品必须选中文发音人
- 功能点：`speed`
  - 合成语速
- 功能点：`vol`
  - 合成音量
- 功能点：`compress`
  - 播报音压缩比
- 功能点：`word`
  - 欢迎语 TTS 文案

### 功放配置

- 功能点：`paConfigEnable`
  - 功放配置开关

### 算法配置

- 功能点：`multiWkeEnable`
  - 多唤醒切换开关
- 功能点：`multiWkeMode`
  - 多唤醒切换模式
- 功能点：`releaseMultiWke.common[*].condition`
  - 多唤醒基础触发指令
- 功能点：`releaseMultiWke.common[*].reply`
  - 多唤醒基础回复语
- 功能点：`releaseMultiWke.wkelist[*].condition`
  - 唤醒词候选
- 功能点：`releaseMultiWke.wkelist[*].reply`
  - 切换回复语
- 功能点：`releaseMultiWke.wkelist[*].sndProtocol`
  - 查询协议
- 功能点：`releaseMultiWke.wkelist[*].recProtocol`
  - 确认协议
- 功能点：`releaseMultiWke.wkelist[*].isDefault`
  - 默认唤醒词
- 功能点：`releaseMultiWke.wkelist[*].isFrozen`
  - 冻结唤醒词
  - 需同时验证“非默认词不可冻结”约束

## 3. 当前产品不应测试的功能点

这些功能在当前产品 feature gate 下为不支持，不能再继续写入本产品专项包，也不能把执行失败归因到固件。

- 功能点：`voiceRegEnable`
  - 语音注册（自学习）开关
- 功能点：`releaseRegist.*`
  - 语音注册主配置矩阵
- 功能点：`releaseRegistConfig.*`
  - 语音注册阶段触发词/播报矩阵

补充说明：

- `voice_regist=Unsupported`
  - 当前产品不应生成语音注册专项包
- `muti_intent=Unsupported`
  - 当前产品不应追加多意图专项
- `denoise=Unsupported`
  - 当前产品不应追加降噪专项
- `free_cmd=Unsupported`
  - 当前产品不应追加 free 命令专项
- `echo_cancellation=Unsupported`
  - 当前产品不应追加回声消除专项

## 4. 当前产品只读观察项

这些字段在当前产品目录里存在，但不是当前前端可直接编辑项。它们可以作为“是否影响设备正常运行”的观察项，不应当写成主打包参数断言。

- 功能点：`traceBaud`
  - 日志串口波特率
- 功能点：`ctlIoPad`
  - 控制引脚组
- 功能点：`ctlIoNum`
  - 引脚号
- 功能点：`holdTime`
  - 保持时长
- 功能点：`paConfigEnableLevel`
  - 使能电平
- 功能点：`protocolConfig`
  - 协议配置对象

## 5. 已落地的产品裁剪逻辑

已更新：

- `scripts/py/listenai_weekly_validation_runner.py`

裁剪规则：

- 当 `voice_regist=Optional` 且 `multi_wakeup=Optional` 时
  - 生成基础包 + 全功能包 + 多唤醒隔离包
- 当 `voice_regist=Unsupported` 且 `multi_wakeup=Optional` 时
  - 生成基础包 + 多唤醒开启包 + 多唤醒关闭包
- 当 `voice_regist=Optional` 且 `multi_wakeup=Unsupported` 时
  - 生成基础包 + 语音注册专项包
- 当两者都不支持时
  - 只生成基础包

## 6. 在 3021 中文取暖器垂类上的本地逻辑验证

使用当前产品的 `parameter_catalog.json` 本地验证 `build_variant_specs()` 后，产出的包清单为：

- `pkg-01-mid-stable`
- `pkg-02-left-boundary`
- `pkg-03-right-boundary`
- `pkg-04-multi-save-on`
- `pkg-05-multi-save-off`

验证结论：

- 已正确裁掉语音注册专项包
- 已保留多唤醒开启/关闭两种联动包
- 已保留基础边界值/中值 3 包

静态校验结果：

- `python3 -m py_compile scripts/py/listenai_weekly_validation_runner.py`
  - 通过

## 7. 当前可复用的历史本地结果

下列结果来自本机已有的 3021 中文取暖器垂类历史任务，可作为当前产品基础能力的本地证据；但它们不是本轮实时平台重打包结果。

- 基础模式：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021取暖器基础模式0331112003311124`
  - `12` 条中 `9 OK + 3 ConfigFail`
- 指定参数模式：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021取暖器指定参数模式0331113503311135`
  - `12` 条中 `9 OK + 3 ConfigFail`
- 组合中值重跑：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021取暖器组合中值重跑0331122303311223`
  - `12` 条中 `9 OK + 3 ConfigFail`
- 组合高边界：`scripts/_runtime/3021-取暖器-取暖器垂类-0330/3021取暖器组合高边界0331121203311212`
  - `12` 条中 `8 OK + 3 ConfigFail + 1 WakeupFail`

这些历史结果只说明：

- 3021 中文取暖器垂类已有基础能力实测痕迹
- 当前本地有可复用的产品样本、参数目录和结果文件

这些历史结果不能替代本轮“按新裁剪逻辑重跑后的实时平台结果”。

## 8. 当前外部阻塞

- 缺少有效 `LISTENAI_TOKEN`
  - 已尝试环境变量、本机 history、浏览器本地存储中的候选 token
  - 当前能提取到的候选 token 已全部返回 `401 token 无效`
- 因此当前无法继续对平台执行：
  - 实时拉取当前产品目录
  - 新建 release
  - 新打包 3021 中文取暖器垂类
  - 生成本轮完整实时固件验证结果

结论：

- 产品范围裁剪逻辑已经完成，并已在 3021 中文取暖器垂类样本上验证通过
- 实时平台重跑目前被无效 token 阻塞，阻塞解除后即可按当前逻辑继续执行
