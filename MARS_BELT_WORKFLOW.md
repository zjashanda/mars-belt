# mars-belt 工作流程图

以下流程图描述的是 `mars-belt` 的真实业务工作流程，基于 `SKILL.md` 与 `scripts/mars_belt.py` 梳理。

```mermaid
flowchart TD
    A([用户发起 mars-belt 任务]) --> B[解析意图<br/>package / voice / burn / validate / full]
    B --> C[参数补全<br/>token / product / module / language / version<br/>ctrl_port / port]
    C --> D[执行约束<br/>固定默认串口 不扫描<br/>中间态写入 _runtime<br/>最终产物写入 result]
    D --> E{是否为 package / voice / full?}

    E -- 是 --> F[list-catalog 预检查平台支持矩阵]
    F --> G{配置是否支持?}
    G -- 否 --> X[立即中断并报告用户<br/>不得擅自替换配置]
    G -- 是 --> H{是否命中相同配置历史包?}

    E -- 否 --> I{动作类型}

    H -- 是 --> J[复用 last_package]
    H -- 否 --> K{动作类型}

    K -- package --> L[执行 package-custom]
    K -- voice --> M[执行 package-voice-reg]
    K -- full --> L

    L --> N[保存 last_package]
    M --> N
    J --> O{是否继续 burn / validate?}
    N --> O

    I -- burn --> P[执行 burn]
    I -- validate --> Q{是否已有 last_suite?}

    O -- 仅打包 --> END1([结束])
    O -- 继续全流程 --> P

    P --> P1[进入烧录模式<br/>uut-switch2.off -> uut-switch1.on<br/>uut-switch2.on -> uut-switch1.off]
    P1 --> P2[调用烧录工具写入 fw.bin]
    P2 --> P3[退出烧录模式并恢复上电<br/>设置 loglevel 4]
    P3 --> P4{版本校验通过?}
    P4 -- 否 --> P5[烧录重试<br/>最多 2 次后报错]
    P5 --> P1
    P4 -- 是 --> Q

    Q -- 否 --> R[generate-suite]
    Q -- 是 --> S[执行 validate]
    R --> T[保存 last_suite]
    T --> S

    S --> U[强制预处理<br/>设备上电 -> 等待启动 -> loglevel 4 -> 等待唤醒就绪]
    U --> V{预处理通过?}
    V -- 否 --> W[立即停止测试并报告失败<br/>禁止跳过 pretest]
    V -- 是 --> Y[执行设备验证<br/>唤醒 / 命令词 / 协议 / 播报 / 音量档位 / 超时]

    Y --> Y1{是否出现协议日志异常?}
    Y1 -- 是 --> Y2[协议专项重试<br/>最多 5 次并保留断开协议]
    Y1 -- 否 --> Z[归档验证产物]
    Y2 --> Z

    Z --> Z1[输出 testResult.xlsx / serial_raw.log / test_tool.log]
    Z1 --> Z2[生成 HTML 邮件报告和结果压缩包]
    Z2 --> END2([结束])
```

## 关键规则

- `package`、`voice`、`full` 在执行前必须先做平台支持预检查。
- `burn` 只能使用既定的 `switch` 控制链路，不允许替换控制方式。
- `validate` 的预处理是强制步骤，失败后必须终止，不能使用跳过参数绕过。
- `last_package`、`last_suite` 会被复用，用于减少重复打包和重复生成测试集。
- `scripts/_runtime/` 保存中间态，`scripts/result/` 保存最终交付物。
