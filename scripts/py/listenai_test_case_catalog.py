import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from listenai_parameter_catalog import build_catalog_payload
from listenai_parameter_catalog import build_parser as build_catalog_parser
from listenai_parameter_catalog import group_entries
from listenai_parameter_catalog import render_json_value
from listenai_task_support import RUNTIME_ROOT


DEFAULT_MD_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_test_case_catalog.md")
DEFAULT_JSON_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_test_case_catalog.json")


def build_parser():
    parser = build_catalog_parser()
    parser.set_defaults(md_out=DEFAULT_MD_OUT, json_out=DEFAULT_JSON_OUT)
    return parser


def slugify(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    return slug or "case"


def unique_preserve(items: Sequence[Any]) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if not isinstance(item, (str, int, float, bool, type(None))) else repr(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def label_for_value(value: Any, enum_values: Sequence[Dict[str, Any]]) -> str:
    for option in enum_values:
        if option.get("value") == value:
            label = option.get("label")
            if label is not None and str(label) != str(value):
                return f"{render_json_value(value)}({label})"
            return render_json_value(value)
    return render_json_value(value)


def current_max_ref(item: Dict[str, Any], defaults: Dict[str, Any]) -> Optional[int]:
    value_range = item.get("range") or {}
    max_ref = value_range.get("max_ref")
    if not max_ref:
        return None
    ref_value = defaults.get(str(max_ref))
    if isinstance(ref_value, int) and not isinstance(ref_value, bool):
        return ref_value
    return None


def bounded_values(min_value: int, max_value: int) -> List[int]:
    return list(range(min_value, max_value + 1))


def numeric_ref_options(
    ref_key: str,
    ref_index: Dict[str, Dict[str, Any]],
    defaults: Dict[str, Any],
) -> List[Tuple[int, Any]]:
    options: List[Tuple[int, Any]] = []
    ref_item = ref_index.get(ref_key) or {}
    for option in ref_item.get("enum_values") or []:
        raw_value = option.get("value")
        numeric_value = as_int(raw_value)
        if numeric_value is not None:
            options.append((numeric_value, raw_value))

    value_range = ref_item.get("range") or {}
    min_value = value_range.get("min")
    max_value = value_range.get("max")
    if isinstance(min_value, int) and isinstance(max_value, int) and (max_value - min_value) <= 100:
        options.extend((value, value) for value in range(min_value, max_value + 1))

    default_value = defaults.get(ref_key)
    numeric_default = as_int(default_value)
    if numeric_default is not None:
        options.append((numeric_default, default_value))

    deduped: List[Tuple[int, Any]] = []
    seen = set()
    for numeric_value, raw_value in sorted(options, key=lambda item: item[0]):
        if numeric_value in seen:
            continue
        seen.add(numeric_value)
        deduped.append((numeric_value, raw_value))
    return deduped


def boundary_values(
    item: Dict[str, Any],
    defaults: Dict[str, Any],
    ref_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[Any], bool, str]:
    key = item["key"]
    value_range = item.get("range") or {}
    default = item.get("default")
    min_value = value_range.get("min")
    max_value = value_range.get("max")
    max_ref_value = current_max_ref(item, defaults)

    if key == "timeout" and isinstance(min_value, int) and isinstance(max_value, int):
        return bounded_values(min_value, max_value), True, "完整可选值为 1..60 秒；测试用例按每个值逐个覆盖。"
    if key in {"speed", "vol"} and isinstance(min_value, int) and isinstance(max_value, int):
        return bounded_values(min_value, max_value), True, f"完整可选值为 {min_value}..{max_value}；测试用例按每个值逐个覆盖。"
    if key == "defaultVol" and isinstance(min_value, int):
        ref_options = numeric_ref_options("volLevel", ref_index, defaults)
        if ref_options:
            max_allowed = ref_options[-1][0]
            allowed_text = "/".join(str(value) for value, _ in ref_options)
            mapping = "；".join(f"volLevel={value} -> defaultVol=1..{value}" for value, _ in ref_options)
            return (
                bounded_values(min_value, max_allowed),
                False,
                f"依赖 volLevel 动态变化：可选 volLevel 为 {allowed_text}；{mapping}。",
            )
        if isinstance(max_ref_value, int):
            return bounded_values(min_value, max_ref_value), False, f"依赖 volLevel 动态变化：当前默认 volLevel={max_ref_value}，可选 defaultVol 为 {min_value}..{max_ref_value}。"

    if isinstance(min_value, int) and isinstance(max_value, int) and (max_value - min_value) <= 100:
        return bounded_values(min_value, max_value), True, f"完整可选值为 {min_value}..{max_value}。"

    values: List[Any] = []
    if isinstance(min_value, int):
        values.append(min_value)
    recommended_min = value_range.get("recommended_min")
    recommended_max = value_range.get("recommended_max")
    if isinstance(recommended_min, int):
        values.append(recommended_min)
    if item.get("default_known"):
        values.append(default)
    if isinstance(recommended_max, int):
        values.append(recommended_max)
    if isinstance(max_value, int):
        values.append(max_value)
    if max_ref_value is not None:
        values.append(max_ref_value)
    if not values and item.get("default_known"):
        values.append(default)
    return unique_preserve(values), False, "取值空间过大或上界未知，测试用例按边界值/推荐值/默认值覆盖。"


def text_values(item: Dict[str, Any]) -> Tuple[List[Any], bool, str]:
    key = item["key"]
    default = item.get("default")
    if key in {"volMaxOverflow", "volMinOverflow"}:
        return unique_preserve([default, "测试播报文案", "最长播报文案边界测试"]), False, "文本可自由编辑，测试用例使用默认/自定义/较长文案覆盖。"
    if key == "word":
        return unique_preserve([default, "欢迎使用测试固件", "欢迎使用3021取暖器测试固件请开始体验"]), False, "文本可自由编辑，测试用例使用默认/自定义/较长文案覆盖。"
    if key.endswith("sndProtocol") or key.endswith("recProtocol"):
        return ["AA BB CC", "55 AA 01 02 03 04"], False, "协议类字段按合法十六进制字节串做正向覆盖。"
    if "reply" in key:
        return ["好的", "好的/已执行", "请说出要学习的新命令"], False, "回复语字段按短回复/多回复/流程回复覆盖。"
    if key.endswith(".word"):
        return ["小聆小聆", "打开取暖器", "切换到学习模式"], False, "词条字段按唤醒词/命令词代表值覆盖。"
    if key.endswith(".extWord"):
        return ["开机", "打开一下", "切到二号唤醒词"], False, "扩展词字段按常见别名覆盖。"
    if key.endswith("pinyin"):
        return ["xiao3-ling2-xiao3-ling2", "da3-kai1-qu3-nuan3-qi4"], False, "拼音字段按普通词和多音字修正词覆盖。"
    if key == "ctlIoPad":
        return [default] if item.get("default_known") else ["PB"], False, "当前合法全集未知，仅先覆盖已知默认值。"
    if key == "releaseMultiWke.common[*].condition":
        return ["切换唤醒词", "切换到默认唤醒词", "恢复默认唤醒词"], False, "多唤醒基础触发语为开放文本，测试用例按切换/恢复类代表指令覆盖。"
    if key == "releaseMultiWke.wkelist[*].condition":
        return ["小聆小聆", "暖风精灵", "取暖管家"], False, "多唤醒候选词为开放文本，测试用例按不同风格的唤醒词样本覆盖。"
    if key == "releaseDepthList[*].type":
        return ["唤醒词", "命令词"], False, "展示字段，用观测型用例覆盖唤醒词/命令词两类代表值。"
    return unique_preserve([default] if item.get("default_known") else []), False, "当前没有完整枚举来源，仅先覆盖默认值。"


def parameter_values(
    item: Dict[str, Any],
    defaults: Dict[str, Any],
    ref_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    enum_values = item.get("enum_values") or []
    if enum_values:
        values = [option.get("value") for option in enum_values]
        return {
            "selectable_complete": True,
            "selectable_values": values,
            "test_values": values,
            "strategy_note": "枚举型参数，测试用例按所有可选值逐个覆盖。",
        }

    value_range = item.get("range") or {}
    if value_range:
        values, complete, note = boundary_values(item, defaults, ref_index)
        return {
            "selectable_complete": complete,
            "selectable_values": values if complete else [],
            "selectable_desc": note if not complete else "",
            "test_values": values,
            "strategy_note": note,
        }

    values, complete, note = text_values(item)
    return {
        "selectable_complete": complete,
        "selectable_values": values if complete else [],
        "selectable_desc": note if not complete else "",
        "test_values": values,
        "strategy_note": note,
    }


def verification_template(item: Dict[str, Any], value: Any) -> Dict[str, str]:
    key = item["key"]
    value_text = render_json_value(value)

    if key == "timeout":
        return {
            "config_check": f"解包后核对 `web_config.json` 中 `timeout={value_text}`。",
            "runtime_check": f"唤醒设备后不说命令，计时确认约 {value_text} 秒退出唤醒态。",
        }
    if key in {"volLevel", "defaultVol"}:
        return {
            "config_check": f"核对配置中 `{key}={value_text}`。",
            "runtime_check": "上电后查询默认音量，并连续执行增减音量命令，确认档位总数和当前初始档位符合设置。",
        }
    if key in {"volMaxOverflow", "volMinOverflow"}:
        return {
            "config_check": f"核对配置中文案字段为 `{value_text}`。",
            "runtime_check": "连续调节音量直到溢出，确认设备播报文案与配置一致。",
        }
    if key in {"again", "dgain"}:
        return {
            "config_check": f"核对配置中 `{key}={value_text}`。",
            "runtime_check": "在安静环境和轻噪声环境分别测试唤醒与命令识别，确认无明显失真、底噪异常或失效。",
        }
    if key in {"uportUart", "uportBaud"}:
        return {
            "config_check": f"核对业务串口配置为 `{key}={value_text}`。",
            "runtime_check": "用串口工具连接业务串口，发送/接收协议，确认串口通信正常无乱码。",
        }
    if key in {"traceUart", "traceBaud", "logLevel"}:
        return {
            "config_check": f"核对日志串口相关字段包含 `{key}={value_text}`。",
            "runtime_check": "用串口工具连接日志串口，确认日志波特率和输出级别符合设置。",
        }
    if key in {"wakeWordSave", "volSave"}:
        return {
            "config_check": f"核对持久化字段 `{key}={value_text}`。",
            "runtime_check": "修改对应状态后断电重启，验证设置是否按开关要求保留。",
        }
    if key in {"vcn", "speed", "vol", "compress", "word"}:
        return {
            "config_check": f"核对播报配置字段 `{key}={value_text}`。",
            "runtime_check": "播放欢迎语或回复语，确认音色/语速/音量/压缩质量/文案内容符合设置。",
        }
    if key in {"paConfigEnable", "ctlIoPad", "ctlIoNum", "holdTime", "paConfigEnableLevel"}:
        return {
            "config_check": f"核对功放配置字段 `{key}={value_text}`。",
            "runtime_check": "通过示波器、万用表或功放板观察引脚电平、保持时长和开关行为是否符合设置。",
        }
    if key in {"sensitivity", "voiceRegEnable", "multiWkeEnable", "multiWkeMode", "algoViewMode", "protocolConfig"}:
        if key == "sensitivity":
            runtime = "在安静/噪声环境分别重复唤醒和命令识别，比较命中率、误唤醒率和响应稳定性。"
        elif key == "voiceRegEnable":
            runtime = "检查设备侧是否可进入语音注册/自学习流程。"
        elif key == "multiWkeEnable":
            runtime = "检查设备侧是否支持多唤醒切换。"
        elif key == "multiWkeMode":
            runtime = "按 loop / specified / protocol 不同模式执行切换流程，确认交互逻辑符合预期。"
        elif key == "algoViewMode":
            runtime = "该字段主要影响配置视图，运行时只需确认配置可保存且不影响固件行为。"
        else:
            runtime = "核对配置保存成功，并确认协议相关运行行为不异常。"
        return {
            "config_check": f"核对算法配置字段 `{key}={value_text}`。",
            "runtime_check": runtime,
        }

    if key.startswith("releaseAlgoList"):
        if key.endswith(".type"):
            runtime = "分别测试代表唤醒词、命令词、播报语、负性词等是否按功能类型工作。"
        elif key.endswith(".reply") or key.endswith(".replyMode"):
            runtime = "触发对应语义，确认回复内容与主/被动播报模式正确。"
        elif key.endswith(".sndProtocol") or key.endswith(".recProtocol"):
            runtime = "抓业务串口协议，确认发送/接收十六进制协议与配置一致。"
        else:
            runtime = "用代表词条和扩展词发音测试识别，确认映射关系正确。"
        return {
            "config_check": f"核对算法词条配置中 `{key}` 包含测试值 `{value_text}`。",
            "runtime_check": runtime,
        }

    if key.startswith("releaseRegist") or key.startswith("releaseRegistConfig"):
        return {
            "config_check": f"核对自学习配置中 `{key}` 包含测试值 `{value_text}`。",
            "runtime_check": "执行自学习流程，验证学习次数、字数限制、重试次数、回复语和删除流程是否符合设置。",
        }

    if key.startswith("releaseMultiWke"):
        return {
            "config_check": f"核对多唤醒配置中 `{key}` 包含测试值 `{value_text}`。",
            "runtime_check": "执行多唤醒切换、查询、恢复默认和协议切换流程，确认行为与配置一致。",
        }

    if key.startswith("releaseDepthList"):
        return {
            "config_check": f"核对深度调优配置中 `{key}` 包含测试值 `{value_text}`。",
            "runtime_check": "对代表唤醒词/命令词重复测试，比较门限、拼音修正和开关状态对识别效果的影响。",
        }

    return {
        "config_check": f"核对配置中 `{key}={value_text}`。",
        "runtime_check": "验证设备行为与配置保持一致。",
    }


def build_cases_for_item(
    item: Dict[str, Any],
    defaults: Dict[str, Any],
    ref_index: Dict[str, Dict[str, Any]],
    scope: str,
    counter_start: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], int]:
    value_plan = parameter_values(item, defaults, ref_index)
    enum_values = item.get("enum_values") or []
    cases: List[Dict[str, Any]] = []
    counter = counter_start

    for value in value_plan.get("test_values") or []:
        counter += 1
        value_label = label_for_value(value, enum_values) if enum_values else render_json_value(value)
        verify = verification_template(item, value)
        config_change = {item["key"]: value}
        title = f"{item['label']} = {value_label}"
        notes = item.get("notes") or ""

        if item["key"] == "defaultVol":
            current_vol_level = as_int(defaults.get("volLevel"))
            requested_default = as_int(value)
            level_options = numeric_ref_options("volLevel", ref_index, defaults)
            if requested_default is not None and (current_vol_level is None or requested_default > current_vol_level):
                paired_level = next((option for option in level_options if option[0] >= requested_default), None)
                if paired_level:
                    paired_level_numeric, paired_level_raw = paired_level
                    config_change = {"volLevel": paired_level_raw, item["key"]: value}
                    title = f"{item['label']} = {value_label}（联动 volLevel={paired_level_numeric}）"
                    verify = {
                        "config_check": f"核对配置中 `volLevel={paired_level_numeric}` 且 `{item['key']}={render_json_value(value)}`。",
                        "runtime_check": "上电后查询默认音量，并连续执行增减音量命令，确认联动后的档位总数和初始档位符合设置。",
                    }
                    notes = "为覆盖更高默认音量值，自动补充最小合法 volLevel。"

        case = {
            "id": f"TC-{counter:04d}",
            "scope": scope,
            "group": item["group"],
            "parameter": item["key"],
            "title": title,
            "case_type": "positive",
            "strategy": "control_variable",
            "input_value": value,
            "input_value_label": value_label,
            "config_change": config_change,
            "directly_editable": item.get("directly_editable", True),
            "config_check": verify["config_check"],
            "runtime_check": verify["runtime_check"],
            "expected_result": "配置保存成功，打包成功后设备行为与设置一致。",
            "notes": notes,
        }
        if item.get("constraints"):
            case["constraints"] = item["constraints"]
        if item.get("feature_gate"):
            case["feature_gate"] = item["feature_gate"]
        cases.append(case)

    inventory_item = {
        "key": item["key"],
        "label": item["label"],
        "group": item["group"],
        "type": item["type"],
        "default": item.get("default"),
        "default_known": item.get("default_known"),
        "directly_editable": item.get("directly_editable", True),
        "selectable_complete": value_plan.get("selectable_complete", False),
        "selectable_values": value_plan.get("selectable_values", []),
        "selectable_desc": value_plan.get("selectable_desc", ""),
        "test_values": value_plan.get("test_values", []),
        "strategy_note": value_plan.get("strategy_note", ""),
        "linked_case_ids": [case["id"] for case in cases],
    }
    return cases, inventory_item, counter


def build_dependency_cases(defaults: Dict[str, Any], counter_start: int) -> Tuple[List[Dict[str, Any]], int]:
    cases: List[Dict[str, Any]] = []
    counter = counter_start

    dependency_specs = [
        {
            "title": "volLevel 与 defaultVol 联动上限验证",
            "changes": {"volLevel": 10, "defaultVol": 10},
            "config_check": "核对 `volLevel=10` 且 `defaultVol=10`，并确认不存在越界。",
            "runtime_check": "上电后默认音量应为最大档，连续减音量再加音量确认 10 档行为正确。",
            "expected_result": "配置可保存、可打包，设备档位逻辑正确。",
        },
        {
            "title": "volLevel 与 defaultVol 非法越界校验",
            "changes": {"volLevel": 5, "defaultVol": 6},
            "config_check": "前端或接口应拒绝保存该配置。",
            "runtime_check": "无需下设备；该用例属于配置校验。",
            "expected_result": "保存失败或被自动纠正，不允许 defaultVol 大于 volLevel。",
            "case_type": "negative",
        },
        {
            "title": "paConfigEnable 打开后功放字段联动验证",
            "changes": {
                "paConfigEnable": True,
                "ctlIoPad": defaults.get("ctlIoPad", "PB"),
                "ctlIoNum": defaults.get("ctlIoNum", 11),
                "holdTime": defaults.get("holdTime", 20000),
                "paConfigEnableLevel": defaults.get("paConfigEnableLevel", "high"),
            },
            "config_check": "核对功放相关 4 个字段在开关打开时全部入配置。",
            "runtime_check": "示波器观察 PA 使能电平与持续时间，确认联动正确。",
            "expected_result": "打开功放配置后，其余字段完整生效。",
        },
        {
            "title": "voiceRegEnable 与自学习配置联动验证",
            "changes": {"voiceRegEnable": True},
            "config_check": "核对开启 `voiceRegEnable` 后，自学习结构配置同时存在。",
            "runtime_check": "设备进入自学习流程，验证学习次数、删除流程与回复语链路。",
            "expected_result": "开启语音注册后，自学习功能可正常使用。",
        },
        {
            "title": "multiWkeEnable 与 multiWkeMode 联动验证",
            "changes": {"multiWkeEnable": True, "multiWkeMode": "loop"},
            "config_check": "核对开启多唤醒后，多唤醒模式与唤醒词列表一起写入。",
            "runtime_check": "执行循环切换流程，验证切换、查询、恢复默认等功能。",
            "expected_result": "多唤醒功能可正常启用，模式行为符合预期。",
        },
        {
            "title": "wakeWordSave 与多唤醒切换持久化联动验证",
            "changes": {"multiWkeEnable": True, "wakeWordSave": "1"},
            "config_check": "核对多唤醒开关与唤醒词掉电保存同时生效。",
            "runtime_check": "切换唤醒词后断电重启，确认当前唤醒词被保留。",
            "expected_result": "唤醒词持久化逻辑正确。",
        },
        {
            "title": "sensitivity 与深度调优阈值联动验证",
            "changes": {"sensitivity": "high_sensitivity"},
            "config_check": "切换灵敏度后，深度调优阈值应随对应档位刷新。",
            "runtime_check": "在统一测试语料下重复测试，确认高灵敏度命中率变化与档位预期一致。",
            "expected_result": "灵敏度档位与门限映射一致。",
        },
        {
            "title": "协议串口与日志串口冲突校验",
            "changes": {"uportUart": "0", "traceUart": "0"},
            "config_check": "前端或接口应拒绝将协议串口与日志串口配置为同一个 UART。",
            "runtime_check": "无需下设备；该用例属于配置校验。",
            "expected_result": "保存失败，提示串口配置冲突。",
            "case_type": "negative",
        },
        {
            "title": "非默认唤醒词禁止冻结校验",
            "changes": {"releaseMultiWke.wkelist[*].isDefault": False, "releaseMultiWke.wkelist[*].isFrozen": True},
            "config_check": "前端应限制只有默认唤醒词才允许勾选冻结。",
            "runtime_check": "无需下设备；该用例属于配置校验。",
            "expected_result": "保存失败或自动取消冻结，不允许非默认唤醒词冻结。",
            "case_type": "negative",
        },
        {
            "title": "被动播报模式缺少确认协议校验",
            "changes": {
                "releaseAlgoList[*].type": "命令词",
                "releaseAlgoList[*].replyMode": "被",
                "releaseAlgoList[*].recProtocol": "",
            },
            "config_check": "当 replyMode=被 且类型不是心跳协议时，应要求填写确认协议。",
            "runtime_check": "无需下设备；该用例属于配置校验。",
            "expected_result": "保存失败，提示需要补充确认协议。",
            "case_type": "negative",
        },
        {
            "title": "协议类字段格式非法校验",
            "changes": {"releaseAlgoList[*].sndProtocol": "AA ZZ"},
            "config_check": "前端或接口应拒绝非十六进制协议字符串。",
            "runtime_check": "无需下设备；该用例属于配置校验。",
            "expected_result": "保存失败，提示协议格式错误。",
            "case_type": "negative",
        },
    ]

    for spec in dependency_specs:
        counter += 1
        cases.append(
            {
                "id": f"TC-{counter:04d}",
                "scope": "dependency",
                "group": "跨参数联动",
                "parameter": "+".join(spec["changes"].keys()),
                "title": spec["title"],
                "case_type": spec.get("case_type", "positive"),
                "strategy": "dependency",
                "input_value": spec["changes"],
                "input_value_label": json.dumps(spec["changes"], ensure_ascii=False),
                "config_change": spec["changes"],
                "directly_editable": True,
                "config_check": spec["config_check"],
                "runtime_check": spec["runtime_check"],
                "expected_result": spec["expected_result"],
                "notes": "",
            }
        )

    return cases, counter


def build_test_catalog(catalog: Dict[str, Any]) -> Dict[str, Any]:
    defaults = catalog.get("releaseDefaults") or {}
    ref_index = {item["key"]: item for item in (catalog.get("parameters") or []) + (catalog.get("editableStructures") or [])}
    direct_cases: List[Dict[str, Any]] = []
    structure_cases: List[Dict[str, Any]] = []
    parameter_inventory: List[Dict[str, Any]] = []
    structure_inventory: List[Dict[str, Any]] = []
    counter = 0

    for item in catalog.get("parameters") or []:
        cases, inventory_item, counter = build_cases_for_item(item, defaults, ref_index, "direct", counter)
        direct_cases.extend(cases)
        parameter_inventory.append(inventory_item)

    for item in catalog.get("editableStructures") or []:
        cases, inventory_item, counter = build_cases_for_item(item, defaults, ref_index, "structure", counter)
        structure_cases.extend(cases)
        structure_inventory.append(inventory_item)

    dependency_cases, counter = build_dependency_cases(defaults, counter)

    return {
        "generatedAt": catalog["generatedAt"],
        "input": catalog["input"],
        "selected": catalog["selected"],
        "versionFamily": catalog["versionFamily"],
        "sourceReleaseId": catalog["sourceReleaseId"],
        "coverageMethod": {
            "principle": "按控制变量法设计，不做全笛卡尔积；枚举项做逐值覆盖，连续大范围数值做边界值/代表值覆盖，强关联参数补联动用例。",
            "notes": [
                "可枚举且可穷举的字段，直接列出全部可选值并逐个生成测试用例。",
                "大范围数值字段（例如 0..2000 的门限）按最小/默认/推荐边界/最大覆盖，而不是逐值穷举。",
                "当前未知合法全集的字段（如 ctlIoPad、again、dgain）只输出已知默认值用例和待探索说明。",
            ],
        },
        "parameterInventory": parameter_inventory,
        "structureInventory": structure_inventory,
        "testCases": direct_cases + structure_cases + dependency_cases,
        "statistics": {
            "directCaseCount": len(direct_cases),
            "structureCaseCount": len(structure_cases),
            "dependencyCaseCount": len(dependency_cases),
            "totalCaseCount": len(direct_cases) + len(structure_cases) + len(dependency_cases),
            "directParameterCount": len(parameter_inventory),
            "structureParameterCount": len(structure_inventory),
        },
        "warnings": catalog.get("warnings") or [],
        "dataSources": catalog.get("dataSources") or [],
    }


def markdown_inventory_table(items: Sequence[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for group, entries in group_entries(items):
        lines.append(f"### {group}")
        lines.append("")
        lines.append("| 参数 | 可选数据 | 测试值覆盖策略 | 用例数 |")
        lines.append("| --- | --- | --- | --- |")
        for item in entries:
            if item.get("selectable_complete"):
                values = item.get("selectable_values") or []
                display = "；".join(render_json_value(v) for v in values[:12])
                if len(values) > 12:
                    display += f"；... 共 {len(values)} 项"
            else:
                values = item.get("test_values") or []
                display = item.get("selectable_desc") or "未知"
                if values:
                    display += "<br>测试值：" + "；".join(render_json_value(v) for v in values[:12])
                    if len(values) > 12:
                        display += f"；... 共 {len(values)} 项"
            lines.append(
                f"| `{item['key']}` | {display} | {item.get('strategy_note') or '-'} | {len(item.get('linked_case_ids') or [])} |"
            )
        lines.append("")
    return lines


def markdown_cases(cases: Sequence[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    by_scope: Dict[str, List[Dict[str, Any]]] = {"direct": [], "structure": [], "dependency": []}
    for case in cases:
        by_scope.setdefault(case["scope"], []).append(case)

    scope_titles = {
        "direct": "直接参数用例",
        "structure": "复杂结构用例",
        "dependency": "联动与负向用例",
    }
    for scope in ["direct", "structure", "dependency"]:
        lines.append(f"## {scope_titles[scope]}")
        lines.append("")
        lines.append("| 用例ID | 参数 | 输入值 | 类型 | 校验重点 | 预期结果 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for case in by_scope.get(scope, []):
            check = f"配置：{case['config_check']}<br>运行：{case['runtime_check']}"
            lines.append(
                f"| `{case['id']}` | `{case['parameter']}` | {case['input_value_label']} | {case['case_type']} | {check} | {case['expected_result']} |"
            )
        lines.append("")
    return lines


def markdown_test_catalog(payload: Dict[str, Any]) -> str:
    selected = payload["selected"]
    lines: List[str] = []
    lines.append("# ListenAI 参数测试用例目录")
    lines.append("")
    lines.append(f"- 生成时间：`{payload['generatedAt']}`")
    lines.append(
        f"- 目标：`{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`"
    )
    lines.append(f"- defId：`{selected.get('defId')}`")
    lines.append(f"- 版本家族：`{payload.get('versionFamily')}`")
    lines.append(f"- 参考模板 sourceReleaseId：`{payload.get('sourceReleaseId') or '未提供'}`")
    lines.append("")
    lines.append("## 覆盖策略")
    lines.append("")
    lines.append(f"- 原则：{payload['coverageMethod']['principle']}")
    for note in payload["coverageMethod"]["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 直接参数可选数据")
    lines.append("")
    lines.extend(markdown_inventory_table(payload.get("parameterInventory") or []))
    lines.append("## 复杂结构字段可选数据")
    lines.append("")
    lines.extend(markdown_inventory_table(payload.get("structureInventory") or []))
    lines.extend(markdown_cases(payload.get("testCases") or []))
    lines.append("## 统计")
    lines.append("")
    stats = payload.get("statistics") or {}
    for key in ["directCaseCount", "structureCaseCount", "dependencyCaseCount", "totalCaseCount"]:
        lines.append(f"- `{key}` = `{stats.get(key)}`")
    lines.append("")
    lines.append("## 当前已知风险与缺口")
    lines.append("")
    for warning in payload.get("warnings") or []:
        lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = build_parser().parse_args()
    catalog = build_catalog_payload(args)
    test_catalog = build_test_catalog(catalog)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(test_catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md_out).write_text(markdown_test_catalog(test_catalog), encoding="utf-8")
    print(f"selected defId: {test_catalog['selected']['defId']}")
    print(f"markdown      : {os.path.abspath(args.md_out)}")
    print(f"json          : {os.path.abspath(args.json_out)}")
    print(f"test cases    : {test_catalog['statistics']['totalCaseCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
