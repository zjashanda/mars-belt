from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from listenai_auto_package import parse_override_args
from listenai_executable_case_suite import (
    CSV_HEADERS,
    DEFAULT_CTRL_PORT,
    DEFAULT_LOG_PORT,
    DIRECT_ASSERT_PATHS,
    apply_runtime_ports,
    build_device_info_template,
    build_voice_context,
    first_version,
    load_web_config,
    split_choices,
)
from listenai_task_support import infer_profile_name, load_validation_metadata


def render_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_hex_protocol(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(part.upper() for part in text.replace(",", " ").split() if part)


def assertion_text_from_direct(parameter: str, expected: Any) -> str:
    assertions = []
    for item in DIRECT_ASSERT_PATHS.get(parameter) or []:
        optional = " (可选路径)" if item.get("optional") else ""
        assertions.append(f"{item['path']} {item['op']} {render_json(expected)}{optional}")
    return "；".join(assertions)


def suite_row(
    *,
    case_id: str,
    module_name: str,
    test_type: str,
    command: str = "",
    func_type: str = "profile",
    expect_proto: str = "",
    expect_reply: str = "",
    reply_mode: str = "",
    priority: str = "P0",
    method: str = "自动",
    precondition: str = "已烧录当前固件并完成串口/TTS配置",
    steps: str = "",
    expected: str = "",
    executor: str = "device",
    pack_args: Optional[Dict[str, Any]] = None,
    config_assert: str = "",
    runtime_assert: str = "",
    raw_param: str = "",
    input_value: Any = "",
    linkage: str = "",
    compat: str = "yes",
) -> Dict[str, str]:
    payload = {key: "" for key in CSV_HEADERS}
    payload.update(
        {
            "用例编号": case_id,
            "功能模块": module_name,
            "测试类型": test_type,
            "命令词": command,
            "功能类型": func_type,
            "期望协议": expect_proto,
            "期望播报": expect_reply,
            "播报模式": reply_mode,
            "优先级": priority,
            "测试方法": method,
            "前置条件": precondition,
            "测试步骤": steps,
            "预期结果": expected,
            "执行器": executor,
            "打包参数": json.dumps(pack_args or {}, ensure_ascii=False),
            "配置断言": config_assert,
            "运行断言": runtime_assert,
            "原始参数": raw_param,
            "输入值": render_json(input_value),
            "联动说明": linkage,
            "voiceTestLite适配": compat,
        }
    )
    return payload


BASE_COVERAGE_SCALAR_PARAMS = {
    "firmwareVersion": "固件版本",
    "timeout": "唤醒超时",
    "volLevel": "音量档位",
    "defaultVol": "默认音量",
}

BASE_COVERAGE_ALGO_ASPECTS = {
    "releaseAlgoList[*].word": "识别结果/协议/响应播报",
    "releaseAlgoList[*].sndProtocol": "发送协议",
    "releaseAlgoList[*].recProtocol": "回传协议",
    "releaseAlgoList[*].replyMode": "播报模式与 playId",
    "releaseAlgoList[*].reply": "响应播报",
}


def append_note_text(existing: Any, text: Any, sep: str = "；") -> str:
    left = str(existing or "").strip()
    right = str(text or "").strip()
    if not right:
        return left
    if not left:
        return right
    if right == left or right in left.split(sep):
        return left
    return f"{left}{sep}{right}"


def attach_result_note(row: Dict[str, Any], note: str) -> None:
    row["结果备注"] = append_note_text(row.get("结果备注", ""), note)


def attach_linkage_note(row: Dict[str, str], note: str) -> None:
    row["联动说明"] = append_note_text(row.get("联动说明", ""), note)


def mark_base_covered(row: Dict[str, Any], parameter: str, value: Any, detail: str) -> None:
    note = (
        f"指定打包参数 {parameter}={render_json(value)} 已生效；"
        f"{detail} 已由基础必测覆盖验证，不再重复生成独立用例"
    )
    attach_result_note(row, note)
    attach_linkage_note(row, f"基础覆盖: {parameter}")


def find_rows_by_command(rows: Sequence[Dict[str, Any]], command: str) -> List[Dict[str, Any]]:
    target = str(command or "").strip()
    if not target:
        return []
    return [row for row in rows if str(row.get("命令词") or "").strip() == target]


def normalize_item_protocol(value: Any) -> str:
    return normalize_hex_protocol(value)


def voice_item_text(item: Dict[str, Any]) -> str:
    return str(item.get("intent") or item.get("condition") or item.get("word") or "").strip()


def unique_intents(items: Iterable[Dict[str, Any]]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        intent = voice_item_text(item)
        if not intent or intent in seen:
            continue
        result.append(intent)
        seen.add(intent)
    return result


def matched_algo_entries(parameter: str, value: Any, web_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    expected_text = str(value or "").strip()
    expected_proto = normalize_item_protocol(value)
    matched: List[Dict[str, Any]] = []
    for item in iter_voice_entries(web_config):
        item_type = voice_entry_type(item)
        intent = voice_item_text(item)
        if parameter == "releaseAlgoList[*].word":
            if intent and intent == expected_text:
                matched.append(item)
            continue
        if item_type == "wakeup":
            continue
        if parameter == "releaseAlgoList[*].sndProtocol":
            if normalize_item_protocol(item.get("snd_protocol") or item.get("sndProtocol")) == expected_proto:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].recProtocol":
            if normalize_item_protocol(item.get("rec_protocol") or item.get("recProtocol")) == expected_proto:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].replyMode":
            if str(item.get("reply_mode") or item.get("replyMode") or "").strip() == expected_text:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].reply":
            if pick_reply(item) == expected_text:
                matched.append(item)
    return matched


def matched_intents_from_release_algo(applied: Dict[str, Any], parameter: str, value: Any) -> List[str]:
    release_algo = applied.get("releaseAlgoList")
    if not isinstance(release_algo, list):
        return []
    expected_text = str(value or "").strip()
    expected_proto = normalize_item_protocol(value)
    matched: List[Dict[str, Any]] = []
    for item in release_algo:
        if not isinstance(item, dict):
            continue
        if parameter == "releaseAlgoList[*].word":
            intent = voice_item_text(item)
            if intent and intent == expected_text:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].sndProtocol":
            if normalize_item_protocol(item.get("snd_protocol") or item.get("sndProtocol")) == expected_proto:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].recProtocol":
            if normalize_item_protocol(item.get("rec_protocol") or item.get("recProtocol")) == expected_proto:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].replyMode":
            if str(item.get("reply_mode") or item.get("replyMode") or "").strip() == expected_text:
                matched.append(item)
        elif parameter == "releaseAlgoList[*].reply":
            if pick_reply(item) == expected_text:
                matched.append(item)
    return unique_intents(matched)


def resolve_algo_override_command(parameter: str, value: Any, applied: Dict[str, Any], web_config: Dict[str, Any]) -> str:
    direct_word = str(applied.get("releaseAlgoList[*].word") or "").strip()
    if direct_word:
        return direct_word

    intents = matched_intents_from_release_algo(applied, parameter, value)
    if len(intents) == 1:
        return intents[0]

    matched = unique_intents(matched_algo_entries(parameter, value, web_config))
    if len(matched) == 1:
        return matched[0]
    return ""


def annotate_base_covered_rows(rows: Sequence[Dict[str, Any]], web_config: Dict[str, Any], metadata: Dict[str, Any]) -> set[str]:
    applied = dict(metadata.get("appliedOverrides") or {})
    covered: set[str] = set()

    for key, label in BASE_COVERAGE_SCALAR_PARAMS.items():
        if key not in applied:
            continue
        matched_rows = [row for row in rows if str(row.get("原始参数") or "").strip() == key]
        if not matched_rows:
            continue
        for row in matched_rows:
            mark_base_covered(row, key, applied[key], label)
        covered.add(key)

    if "releaseAlgoList[*].word" in applied:
        command = str(applied["releaseAlgoList[*].word"] or "").strip()
        matched_rows = find_rows_by_command(rows, command)
        if matched_rows:
            for row in matched_rows:
                mark_base_covered(row, "releaseAlgoList[*].word", applied["releaseAlgoList[*].word"], "该词条识别/协议/响应播报")
            covered.add("releaseAlgoList[*].word")

    for key, aspect in BASE_COVERAGE_ALGO_ASPECTS.items():
        if key == "releaseAlgoList[*].word" or key not in applied:
            continue
        command = resolve_algo_override_command(key, applied[key], applied, web_config)
        matched_rows = find_rows_by_command(rows, command)
        if not matched_rows:
            continue
        for row in matched_rows:
            mark_base_covered(row, key, applied[key], f"命令词 {command} 的{aspect}")
        covered.add(key)

    return covered


def selected_from_inputs(selected_meta: Optional[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    if selected_meta:
        return dict(selected_meta)
    scalars = metadata.get("scalars") or {}
    return {
        "productLabel": scalars.get("product", ""),
        "moduleBoard": scalars.get("module", ""),
        "language": scalars.get("language", ""),
        "versionLabel": scalars.get("version", ""),
        "defId": scalars.get("defId", ""),
        "productPath": scalars.get("product", ""),
        "sceneLabel": scalars.get("scene", ""),
    }


def project_name(selected: Dict[str, Any], profile: str) -> str:
    parts = [
        "ListenAI",
        str(selected.get("productLabel") or "product"),
        str(selected.get("moduleBoard") or "module"),
        str(selected.get("language") or "lang"),
        str(selected.get("versionLabel") or "version"),
        profile,
    ]
    return "_".join(part.replace("/", "-").replace("\\", "-") for part in parts if str(part).strip())


def ensure_device_info(
    web_config: Dict[str, Any],
    selected: Dict[str, Any],
    profile: str,
    log_port: str = "",
    ctrl_port: str = "",
) -> Dict[str, Any]:
    context = build_voice_context(web_config)
    device_info = deepcopy(context.get("device_info") or build_device_info_template(web_config, context))
    device_info["projectInfo"] = project_name(selected, profile)
    return apply_runtime_ports(device_info, log_port, ctrl_port)


def _multi_wke_control_words(web_config: Dict[str, Any]) -> set:
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    multi_cfg = dict(ver.get("multi_wakeup") or firmware.get("multi_wakeup") or {})
    words = set()

    for item in multi_cfg.get("common") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("condition") or item.get("word") or "").strip()
        if word:
            words.add(word)

    switch_control = dict(multi_cfg.get("switch_control") or multi_cfg.get("switchControl") or {})
    for key in ("switch_info", "restore_info", "query_info", "switchInfo", "restoreInfo", "queryInfo"):
        info = switch_control.get(key) or {}
        if not isinstance(info, dict):
            continue
        word = str(info.get("word") or info.get("condition") or "").strip()
        if word:
            words.add(word)
    return words


def _voice_reg_control_words(web_config: Dict[str, Any]) -> set:
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    study_cfg = dict(firmware.get("study_config") or {})
    words = set()

    for item in study_cfg.get("reg_wakewords") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("intent") or "").strip()
        if word:
            words.add(word)

    for item in ver.get("asr_cmds") or []:
        if not isinstance(item, dict):
            continue
        special_type = str(item.get("special_type") or item.get("specialType") or "").strip()
        if "语音注册控制相关" not in special_type and "虚拟语音注册唤醒意图" not in special_type:
            continue
        word = str(item.get("intent") or item.get("condition") or "").strip()
        if word:
            words.add(word)
    return words


def iter_voice_entries(web_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    ver = first_version(web_config)
    result: List[Dict[str, Any]] = []
    seen = set()
    excluded_intents = _multi_wke_control_words(web_config) | _voice_reg_control_words(web_config)
    for section in ["asr_wakeup", "asr_cmds"]:
        for item in ver.get(section) or []:
            if not isinstance(item, dict):
                continue
            intent = str(item.get("intent") or item.get("condition") or "").strip()
            item_type = str(item.get("type") or "").strip()
            if not intent or "负性词" in item_type:
                continue
            # 专项控制词/内部虚拟意图只应在专项用例中验证，不应落入通用 CORE。
            if intent in excluded_intents:
                continue
            key = (item_type, intent)
            if key in seen:
                continue
            seen.add(key)
            copied = dict(item)
            copied["_section"] = section
            result.append(copied)
    return result


def voice_entry_type(item: Dict[str, Any]) -> str:
    item_type = str(item.get("type") or "").strip()
    if item_type == "唤醒词" or item.get("_section") == "asr_wakeup":
        return "wakeup"
    return "command"


def make_timeout_row(case_id: str, timeout_value: int) -> Dict[str, str]:
    pack_args = {"timeout": int(timeout_value)}
    return suite_row(
        case_id=case_id,
        module_name="超时验证",
        test_type="超时退出",
        command="",
        expect_reply="",
        priority="P0",
        method="自动",
        steps="1. 唤醒设备 2. 不说命令 3. 观察退出唤醒态耗时",
        expected=f"设备在 {timeout_value} 秒附近退出唤醒态",
        executor="device",
        pack_args=pack_args,
        config_assert=assertion_text_from_direct("timeout", int(timeout_value)),
        runtime_assert=f"timeout={int(timeout_value)}",
        raw_param="timeout",
        input_value=int(timeout_value),
        linkage="只验证超时配置",
    )


def current_firmware_version_text(web_config: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> str:
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    general_cfg = firmware.get("general_config") or {}
    version_text = str(general_cfg.get("version") or "").strip()
    if version_text:
        return version_text
    final_release = (metadata or {}).get("finalRelease") or {}
    return str(final_release.get("version") or "").strip()


def make_version_row(case_id: str, version_text: str) -> Dict[str, str]:
    pack_args = {"firmwareVersion": version_text}
    return suite_row(
        case_id=case_id,
        module_name="固件版本验证",
        test_type="固件版本校验",
        command="",
        priority="P0",
        steps="1. 触发设备上电/重启 2. 读取启动日志中的 config version 3. 与当前固件版本比对",
        expected=f"设备启动日志中的 config version 与 {version_text} 匹配",
        executor="device",
        pack_args=pack_args,
        config_assert=assertion_text_from_direct("firmwareVersion", version_text),
        runtime_assert=f"firmwareVersion={version_text}",
        raw_param="firmwareVersion",
        input_value=version_text,
        linkage="固件版本为正式必测项",
    )


def make_volume_row(case_id: str, vol_level: int) -> Dict[str, str]:
    pack_args = {"volLevel": int(vol_level)}
    return suite_row(
        case_id=case_id,
        module_name="音量档位验证",
        test_type="功能验证",
        command="",
        priority="P0",
        steps="1. 查询初始音量 2. 连续执行加减音量 3. 统计档位总数",
        expected=f"设备总音量档位为 {vol_level}",
        executor="device",
        pack_args=pack_args,
        config_assert=assertion_text_from_direct("volLevel", int(vol_level)),
        runtime_assert=f"volLevel={int(vol_level)}",
        raw_param="volLevel",
        input_value=int(vol_level),
        linkage="只验证音量档位配置",
    )


def make_default_volume_row(case_id: str, default_vol: int) -> Dict[str, str]:
    pack_args = {"defaultVol": int(default_vol)}
    return suite_row(
        case_id=case_id,
        module_name="默认音量验证",
        test_type="功能验证",
        command="",
        priority="P0",
        steps="1. 断电重启设备 2. 唤醒后执行一次音量调节 3. 观察当前默认档位",
        expected=f"设备默认音量为第 {default_vol} 档",
        executor="device",
        pack_args=pack_args,
        config_assert=assertion_text_from_direct("defaultVol", int(default_vol)),
        runtime_assert=f"defaultVol={int(default_vol)}",
        raw_param="defaultVol",
        input_value=int(default_vol),
        linkage="只验证默认音量配置",
    )


def make_bool_row(case_id: str, parameter: str, title: str, value: bool, expected: str) -> Dict[str, str]:
    pack_args = {parameter: bool(value)}
    return suite_row(
        case_id=case_id,
        module_name=title,
        test_type="功能验证",
        priority="P1",
        steps=f"1. 验证配置 {parameter} 2. 执行关联设备动作",
        expected=expected,
        executor="自动-解包配置",
        pack_args=pack_args,
        config_assert=assertion_text_from_direct(parameter, bool(value)),
        runtime_assert=f"{parameter}={bool(value)}",
        raw_param=parameter,
        input_value=bool(value),
        linkage="当前自动化以配置断言为主",
        compat="config-only",
    )


def normalize_boolish(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def command_context_info(context: Dict[str, Any], command_type: str) -> Dict[str, str]:
    commands_by_type = context.get("commands_by_type") or {}
    info = commands_by_type.get(command_type) or {}
    reply_choices = split_choices(info.get("reply") or "")
    return {
        "command": str(info.get("intent") or "").strip(),
        "protocol": normalize_hex_protocol(info.get("snd_protocol") or info.get("sndProtocol")),
        "reply": str((reply_choices[0] if reply_choices else (info.get("reply") or "")) or "").strip(),
        "reply_mode": str(info.get("reply_mode") or info.get("replyMode") or context.get("reply_mode") or "主").strip() or "主",
    }


def make_direct_assert_row(
    case_id: str,
    parameter: str,
    value: Any,
    module_name: str,
    expected: str,
    *,
    test_type: str = "功能验证",
    command: str = "",
    expect_proto: str = "",
    expect_reply: str = "",
    reply_mode: str = "",
    steps: str = "",
    executor: str = "device",
    method: str = "自动+人工",
    linkage: str = "",
    compat: str = "部分",
    priority: str = "P1",
) -> Dict[str, str]:
    pack_args = {parameter: value}
    return suite_row(
        case_id=case_id,
        module_name=module_name,
        test_type=test_type,
        command=command,
        expect_proto=expect_proto,
        expect_reply=expect_reply,
        reply_mode=reply_mode,
        priority=priority,
        method=method,
        steps=steps,
        expected=expected,
        executor=executor,
        pack_args=pack_args,
        config_assert=assertion_text_from_direct(parameter, value),
        runtime_assert=runtime_assert_text({parameter: value}),
        raw_param=parameter,
        input_value=value,
        linkage=linkage,
        compat=compat,
    )


VOICE_REG_DEFAULT_LEARNED_COMMAND = "一小时关机"
VOICE_REG_DEFAULT_LEARNED_WAKEWORD = "工藤新一"
VOICE_REG_VIRTUAL_WAKE_INTENT = "虚拟语音注册唤醒意图"
VOICE_REG_COMMAND_SUCCESS_ALIAS = "笑逐颜开"
VOICE_REG_COMMAND_RECOVER_ALIAS = "心想事成"
VOICE_REG_COMMAND_FAIL_ALIAS = "万事大吉"
VOICE_REG_COMMAND_INVALID_LENGTH_PHRASES = [
    "春夏秋冬东西南北平安喜乐",
    "东南西北春夏秋冬平安顺遂",
    "日月星辰山川湖海平安喜乐",
]
VOICE_REG_COMMAND_DELETE_ALIAS = "一帆风顺"
VOICE_REG_COMMAND_DELETE_KEEP_ALIAS = "一生平安"
VOICE_REG_WAKE_SUCCESS_ALIAS = "晴空万里"
VOICE_REG_WAKE_RECOVER_ALIAS = "小树小树"
VOICE_REG_WAKE_FAIL_ALIAS = "小熊维尼"
VOICE_REG_WAKE_INVALID_LENGTH_PHRASES = [
    "春夏秋冬平安喜乐",
    "东南西北天天开心",
    "日月星辰平安顺遂",
]
VOICE_REG_WAKE_DELETE_ALIAS = "你好在吗"
VOICE_REG_WAKE_DELETE_KEEP_ALIAS = "小猫队长"
VOICE_REG_FORBIDDEN_COMMAND = "增大音量"
VOICE_REG_FORBIDDEN_WAKEWORD = "小聆小聆"
VOICE_REG_COMMAND_RESERVED_PHRASES = ["学习唤醒词", "删除命令词", "退出删除"]
VOICE_REG_WAKE_RESERVED_PHRASES = ["学习命令词", "删除唤醒词", "全部删除"]
MULTI_WKE_SWITCH_COMMAND = "切换唤醒词"
MULTI_WKE_RESTORE_COMMAND = "恢复默认唤醒词"
MULTI_WKE_QUERY_COMMAND = "查询唤醒词"
MULTI_WKE_SWITCH_REPLY = "请说您想切换的唤醒词"
MULTI_WKE_DEFAULT_WAKEWORD = "小聆小聆"
MULTI_WKE_CANDIDATE_WAKEWORDS = ["暖风精灵", "取暖管家"]
MULTI_WKE_INVALID_WAKEWORD = "小狗管家"
MULTI_WKE_INVALID_PROTOCOL = "A5 FA 00 82 7F 00 A0 FB"


def runtime_assert_text(values: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, value in values.items():
        if value is None or value == "":
            continue
        if isinstance(value, str):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    return ";".join(parts)


def _voice_reg_default_wakeup_word(web_config: Dict[str, Any]) -> str:
    ver = first_version(web_config)
    for item in ver.get("asr_wakeup") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("intent") or item.get("condition") or item.get("word") or "").strip()
        if word:
            return word
    return VOICE_REG_FORBIDDEN_WAKEWORD


def _voice_reg_supported_command_intents(web_config: Dict[str, Any], *, include_control: bool) -> List[str]:
    ver = first_version(web_config)
    result: List[str] = []
    for item in ver.get("asr_cmds") or []:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or item.get("condition") or "").strip()
        if not intent:
            continue
        item_type = str(item.get("type") or "").strip()
        if "负性词" in item_type:
            continue
        special_type = str(item.get("special_type") or item.get("specialType") or "").strip()
        is_control = "语音注册控制相关" in special_type or "虚拟语音注册唤醒意图" in special_type
        if not include_control and is_control:
            continue
        if intent not in result:
            result.append(intent)
    return result


def _voice_reg_supported_control_intents(web_config: Dict[str, Any]) -> List[str]:
    ver = first_version(web_config)
    result: List[str] = []
    for item in ver.get("asr_cmds") or []:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or item.get("condition") or "").strip()
        if not intent:
            continue
        special_type = str(item.get("special_type") or item.get("specialType") or "").strip()
        if "语音注册控制相关" not in special_type and "虚拟语音注册唤醒意图" not in special_type:
            continue
        if intent not in result:
            result.append(intent)
    return result


def _voice_reg_pick_supported_words(
    preferred: Sequence[str],
    supported: Sequence[str],
    *,
    limit: int,
) -> List[str]:
    result: List[str] = []
    seen = set()
    supported_list = [str(item or "").strip() for item in supported if str(item or "").strip()]
    for word in preferred:
        text = str(word or "").strip()
        if text and text in supported_list and text not in seen:
            result.append(text)
            seen.add(text)
        if len(result) >= limit:
            return result
    for word in supported_list:
        if word not in seen:
            result.append(word)
            seen.add(word)
        if len(result) >= limit:
            break
    return result


def _voice_reg_pick_supported_word(
    preferred: Sequence[str],
    supported: Sequence[str],
    *,
    fallback: str,
) -> str:
    items = _voice_reg_pick_supported_words(preferred, supported, limit=1)
    return items[0] if items else fallback


def _voice_reg_case_pack_args(metadata: Dict[str, Any], web_config: Dict[str, Any]) -> Dict[str, Any]:
    learn_words = list(metadata.get("learnWords") or [])
    applied = dict(metadata.get("appliedOverrides") or {})
    release_regist = dict(applied.get("releaseRegist") or {})
    ver = first_version(web_config)
    firmware = dict(ver.get("firmware") or {})
    study_config = dict(firmware.get("study_config") or {})
    user_cfg = dict(study_config.get("user_cfg") or {})
    reg_commands = [item for item in (study_config.get("reg_commands") or []) if isinstance(item, dict)]
    reg_wakewords = [item for item in (study_config.get("reg_wakewords") or []) if isinstance(item, dict)]

    if not learn_words:
        learn_words = [str(item.get("word") or "").strip() for item in reg_commands if str(item.get("word") or "").strip()]

    command_repeat_count = int(release_regist.get("commandRepeatCount") or user_cfg.get("asr_study_repeat_count") or 2)
    command_retry_count = int(release_regist.get("commandRetryCount") or user_cfg.get("asr_study_retry_count") or 2)
    wakeup_repeat_count = int(release_regist.get("wakeupRepeatCount") or user_cfg.get("wakeup_study_repeat_count") or 1)
    wakeup_retry_count = int(release_regist.get("wakeupRetryCount") or user_cfg.get("wakeup_study_retry_count") or 0)
    regist_mode = str(release_regist.get("registMode") or "specificLearn").strip() or "specificLearn"
    selected_command = str(learn_words[0] if learn_words else "").strip()
    learned_wake_word = str(reg_wakewords[0].get("word") if reg_wakewords else VOICE_REG_VIRTUAL_WAKE_INTENT).strip()
    default_wake_word = _voice_reg_default_wakeup_word(web_config)
    supported_commands = _voice_reg_supported_command_intents(web_config, include_control=False)
    supported_control_words = _voice_reg_supported_control_intents(web_config)
    forbidden_command = ""
    for word in [VOICE_REG_FORBIDDEN_COMMAND, *supported_commands]:
        text = str(word or "").strip()
        if text and text != selected_command:
            forbidden_command = text
            break
    if not forbidden_command:
        forbidden_command = selected_command
    command_reserved_phrases = _voice_reg_pick_supported_words(
        VOICE_REG_COMMAND_RESERVED_PHRASES,
        supported_control_words,
        limit=len(VOICE_REG_COMMAND_RESERVED_PHRASES),
    ) or list(VOICE_REG_COMMAND_RESERVED_PHRASES)
    wake_reserved_phrases = _voice_reg_pick_supported_words(
        VOICE_REG_WAKE_RESERVED_PHRASES,
        supported_control_words,
        limit=len(VOICE_REG_WAKE_RESERVED_PHRASES),
    ) or list(VOICE_REG_WAKE_RESERVED_PHRASES)

    pack_args: Dict[str, Any] = {
        "voiceRegEnable": True,
        "voiceRegCommandRepeatCount": command_repeat_count,
        "voiceRegCommandRetryCount": command_retry_count,
        "voiceRegWakeupRepeatCount": wakeup_repeat_count,
        "voiceRegWakeupRetryCount": wakeup_retry_count,
        "voiceRegRegistMode": regist_mode,
        "voiceRegLearnCommandAlias": VOICE_REG_DEFAULT_LEARNED_COMMAND,
        "voiceRegLearnWakeWord": VOICE_REG_DEFAULT_LEARNED_WAKEWORD,
        "voiceRegVirtualWakeIntent": learned_wake_word or VOICE_REG_VIRTUAL_WAKE_INTENT,
        "voiceRegForbiddenCommand": forbidden_command,
        "voiceRegDefaultWakeWord": default_wake_word,
        "voiceRegCommandReservedPhrases": command_reserved_phrases,
        "voiceRegWakeReservedPhrases": wake_reserved_phrases,
        "voiceRegLearnCommandEntry": _voice_reg_pick_supported_word(
            ["学习命令词"],
            supported_control_words,
            fallback="学习命令词",
        ),
        "voiceRegLearnWakeEntry": _voice_reg_pick_supported_word(
            ["学习唤醒词"],
            supported_control_words,
            fallback="学习唤醒词",
        ),
        "voiceRegDeleteCommandEntry": _voice_reg_pick_supported_word(
            ["删除命令词"],
            supported_control_words,
            fallback="删除命令词",
        ),
        "voiceRegDeleteWakeEntry": _voice_reg_pick_supported_word(
            ["删除唤醒词"],
            supported_control_words,
            fallback="删除唤醒词",
        ),
        "voiceRegDeleteAllCommandEntry": _voice_reg_pick_supported_word(
            ["删除全部命令词", "全部删除"],
            supported_control_words,
            fallback="删除全部命令词",
        ),
        "voiceRegExitLearnEntry": _voice_reg_pick_supported_word(
            ["退出学习"],
            supported_control_words,
            fallback="退出学习",
        ),
        "voiceRegExitDeleteEntry": _voice_reg_pick_supported_word(
            ["退出删除"],
            supported_control_words,
            fallback="退出删除",
        ),
    }
    if learn_words:
        pack_args["studyRegCommands"] = learn_words
    if selected_command:
        pack_args["voiceRegSelectedCommand"] = selected_command
    return pack_args


def make_voice_reg_rows(metadata: Dict[str, Any], web_config: Dict[str, Any]) -> List[Dict[str, str]]:
    pack_args = _voice_reg_case_pack_args(metadata, web_config)
    selected_command = str(pack_args.get("voiceRegSelectedCommand") or "").strip()
    virtual_wake = str(pack_args.get("voiceRegVirtualWakeIntent") or VOICE_REG_VIRTUAL_WAKE_INTENT).strip()
    command_repeat_count = int(pack_args.get("voiceRegCommandRepeatCount", 2) or 2)
    command_retry_count = int(pack_args.get("voiceRegCommandRetryCount", 2) or 2)
    wakeup_repeat_count = int(pack_args.get("voiceRegWakeupRepeatCount", 1) or 1)
    wakeup_retry_count = int(pack_args.get("voiceRegWakeupRetryCount", 0) or 0)
    forbidden_command = str(pack_args.get("voiceRegForbiddenCommand") or VOICE_REG_FORBIDDEN_COMMAND).strip()
    default_wake_word = str(pack_args.get("voiceRegDefaultWakeWord") or VOICE_REG_FORBIDDEN_WAKEWORD).strip()
    command_reserved_phrases = [
        str(item or "").strip()
        for item in (pack_args.get("voiceRegCommandReservedPhrases") or VOICE_REG_COMMAND_RESERVED_PHRASES)
        if str(item or "").strip()
    ]
    wake_reserved_phrases = [
        str(item or "").strip()
        for item in (pack_args.get("voiceRegWakeReservedPhrases") or VOICE_REG_WAKE_RESERVED_PHRASES)
        if str(item or "").strip()
    ]
    regist_mode = str(pack_args.get("voiceRegRegistMode") or "specificLearn").strip() or "specificLearn"
    delete_command_phrase = str(pack_args.get("voiceRegDeleteCommandEntry") or "删除命令词").strip() or "删除命令词"
    delete_wake_phrase = str(pack_args.get("voiceRegDeleteWakeEntry") or "删除唤醒词").strip() or "删除唤醒词"

    config_base = ["firmware.study_config.enable eq true"]
    if selected_command:
        config_base.append(f"firmware.study_config.reg_commands[*].word contains {render_json(selected_command)}")
    if virtual_wake:
        config_base.append(f"firmware.study_config.reg_wakewords[*].word contains {render_json(virtual_wake)}")

    if not selected_command:
        return [
            suite_row(
                case_id="VOICE-001",
                module_name="语音注册-命令词学习流程",
                test_type="功能验证",
                command="学习命令词",
                priority="P0",
                steps="1. 进入学习命令词流程 2. 校验当前固件存在可学习命令词 3. 验证设备不重启",
                expected="当前固件存在可学习命令词，且学习流程可继续",
                executor="device",
                pack_args=pack_args,
                config_assert="；".join(config_base),
                runtime_assert=runtime_assert_text({"voiceRegScenario": "flow_only"}),
                raw_param="voiceRegScenario",
                input_value="flow_only",
                linkage="未解析到可学习命令词时退化为流程可进入校验",
            )
        ]

    def merged_pack_args(**overrides: Any) -> Dict[str, Any]:
        merged = dict(pack_args)
        merged.update(overrides)
        return merged

    def cycle_phrases(candidates: Sequence[str], count: int) -> List[str]:
        items = [str(item or "").strip() for item in candidates if str(item or "").strip()]
        if not items or count <= 0:
            return []
        return [items[idx % len(items)] for idx in range(count)]

    command_retry_supported_mismatches = cycle_phrases([forbidden_command], max(command_retry_count, 1))
    if not command_retry_supported_mismatches:
        command_retry_supported_mismatches = [forbidden_command or VOICE_REG_FORBIDDEN_COMMAND]
    command_retry_recover_mismatch = command_retry_supported_mismatches[0]

    wake_retry_supported_mismatches = cycle_phrases([forbidden_command], max(wakeup_retry_count, 1))
    if not wake_retry_supported_mismatches:
        wake_retry_supported_mismatches = [forbidden_command or VOICE_REG_FORBIDDEN_COMMAND]
    wake_retry_recover_mismatch = wake_retry_supported_mismatches[0]

    def command_config_assert(*extra: str) -> str:
        return "；".join(
            config_base
            + [
                f"firmware.study_config.user_cfg.asr_study_repeat_count eq {command_repeat_count}",
                f"firmware.study_config.user_cfg.asr_study_retry_count eq {command_retry_count}",
                "_ver_list[0].asr_cmds[*].intent contains 学习命令词",
            ]
            + list(extra)
        )

    def wake_config_assert(*extra: str) -> str:
        return "；".join(
            config_base
            + [
                f"firmware.study_config.user_cfg.wakeup_study_repeat_count eq {wakeup_repeat_count}",
                f"firmware.study_config.user_cfg.wakeup_study_retry_count eq {wakeup_retry_count}",
                "_ver_list[0].asr_cmds[*].intent contains 学习唤醒词",
            ]
            + list(extra)
        )

    def voice_case(
        *,
        case_id: str,
        module_name: str,
        command: str,
        steps: str,
        expected: str,
        config_assert: str,
        runtime_values: Dict[str, Any],
        input_value: Any,
        linkage: str,
        pack_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        return suite_row(
            case_id=case_id,
            module_name=module_name,
            test_type="功能验证",
            command=command,
            priority="P0",
            steps=steps,
            expected=expected,
            executor="device",
            pack_args=merged_pack_args(**(pack_overrides or {})),
            config_assert=config_assert,
            runtime_assert=runtime_assert_text(runtime_values),
            raw_param="voiceRegScenario",
            input_value=input_value,
            linkage=linkage,
        )

    command_retry_single_recover_seed = command_retry_recover_mismatch
    wake_retry_single_recover_seed = wake_retry_recover_mismatch

    command_retry_recover_case_name = "语音注册-命令词失败重试恢复成功"
    command_retry_recover_scenario = "command_retry_recover"
    command_retry_recover_verify_should_work = True
    if command_retry_count <= 1:
        command_retry_recover_case_name = "语音注册-命令词失败一次即退出"
        command_retry_recover_scenario = "command_retry_single_fail"
        command_retry_recover_verify_should_work = False
        if command_repeat_count <= 1:
            command_retry_recover_steps = (
                f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
                f"2. 录入普通功能词 {command_retry_single_recover_seed} 触发首次失配 "
                f"3. 因失败重试次数=1，当前学习流程应直接退出 "
                f"4. 再说 {VOICE_REG_COMMAND_RECOVER_ALIAS} 不应触发 {selected_command}，默认命令 {selected_command} 仍应正常"
            )
            command_retry_recover_phrases = [command_retry_single_recover_seed]
        else:
            command_retry_recover_steps = (
                f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
                f"2. 前 {command_repeat_count - 1} 遍录入 {VOICE_REG_COMMAND_RECOVER_ALIAS} "
                f"3. 第 {command_repeat_count} 遍故意录入普通功能词 {command_retry_single_recover_seed} "
                f"4. 因失败重试次数=1，当前学习流程应直接退出，不进入恢复重试 "
                f"5. 再说 {VOICE_REG_COMMAND_RECOVER_ALIAS} 不应触发 {selected_command}，默认命令 {selected_command} 仍应正常"
            )
            command_retry_recover_phrases = [VOICE_REG_COMMAND_RECOVER_ALIAS] * max(command_repeat_count - 1, 0) + [
                command_retry_single_recover_seed
            ]
        command_retry_recover_expected = "命令词失败重试次数=1 时，首次失配即终止当前学习流程，学习语料不生效，默认命令保持正常"
        command_retry_exhaust_steps = (
            f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
            f"2. 连续录入删除类命令 {cycle_phrases([delete_command_phrase], max(command_retry_count + 1, 1))} "
            f"直到耗尽失败重试次数 "
            f"3. 学习失败后确认默认命令 {selected_command} 仍正常，且未产生新的学习映射"
        )
        command_retry_exhaust_expected = "单遍学习模式下，连续输入删除类命令耗尽失败计数后学习失败，默认命令保持正常且不会残留新的学习映射"
        command_retry_exhaust_phrases = cycle_phrases(
            [delete_command_phrase],
            max(command_retry_count + 1, 1),
        )
        command_retry_exhaust_verify_phrase = ""
    elif command_repeat_count <= 1:
        command_retry_recover_steps = (
            f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
            f"2. 先录入普通功能词 {command_retry_single_recover_seed} 触发失败计数但保持学习态 "
            f"3. 在重试窗口内录入 {VOICE_REG_COMMAND_RECOVER_ALIAS} "
            f"4. 学习成功后验证 {VOICE_REG_COMMAND_RECOVER_ALIAS} 可触发 {selected_command}"
        )
        command_retry_recover_expected = "单遍学习模式下，普通功能词先触发失败计数但未直接终止学习，随后在重试窗口补录成功且学习语料生效"
        command_retry_recover_phrases = [
            command_retry_single_recover_seed,
            VOICE_REG_COMMAND_RECOVER_ALIAS,
        ]
        command_retry_exhaust_steps = (
            f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
            f"2. 连续录入删除类命令 {cycle_phrases([delete_command_phrase], max(command_retry_count + 1, 1))} "
            f"直到耗尽失败重试次数 "
            f"3. 学习失败后确认默认命令 {selected_command} 仍正常，且未产生新的学习映射"
        )
        command_retry_exhaust_expected = "单遍学习模式下，连续输入删除类命令耗尽失败计数后学习失败，默认命令保持正常且不会残留新的学习映射"
        command_retry_exhaust_phrases = cycle_phrases(
            [delete_command_phrase],
            max(command_retry_count + 1, 1),
        )
        command_retry_exhaust_verify_phrase = ""
    else:
        command_retry_recover_steps = (
            f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
            f"2. 第一遍录入 {VOICE_REG_COMMAND_RECOVER_ALIAS} "
            f"3. 第二遍故意录入普通功能词 {command_retry_recover_mismatch} 后触发重试 "
            f"4. 在重试窗口内重新录入 {VOICE_REG_COMMAND_RECOVER_ALIAS} "
            f"5. 学习成功后验证 {VOICE_REG_COMMAND_RECOVER_ALIAS} 可触发 {selected_command}"
        )
        command_retry_recover_expected = "失败重试次数生效，在重试窗口内恢复一致后学习成功，学习语料生效"
        command_retry_recover_phrases = [
            VOICE_REG_COMMAND_RECOVER_ALIAS,
            command_retry_recover_mismatch,
            VOICE_REG_COMMAND_RECOVER_ALIAS,
        ]
        command_retry_exhaust_steps = (
            f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
            f"2. 第一遍录入 {VOICE_REG_COMMAND_FAIL_ALIAS} "
            f"3. 后续连续录入删除类命令 {[delete_command_phrase] * max(command_retry_count, 1)} 直到耗尽失败重试次数 "
            f"4. 学习失败后再说 {VOICE_REG_COMMAND_FAIL_ALIAS} "
            f"5. 该学习语料不应生效"
        )
        command_retry_exhaust_expected = "失败重试次数耗尽后学习失败，失败语料不会生效"
        command_retry_exhaust_phrases = [VOICE_REG_COMMAND_FAIL_ALIAS] + [delete_command_phrase] * max(command_retry_count, 1)
        command_retry_exhaust_verify_phrase = VOICE_REG_COMMAND_FAIL_ALIAS

    wake_retry_recover_case_name = "语音注册-唤醒词失败重试恢复成功"
    wake_retry_recover_scenario = "wakeup_retry_recover"
    wake_retry_recover_verify_should_work = True
    if wakeup_retry_count <= 1:
        wake_retry_recover_case_name = "语音注册-唤醒词失败一次即退出"
        wake_retry_recover_scenario = "wakeup_retry_single_fail"
        wake_retry_recover_verify_should_work = False
        if wakeup_repeat_count <= 1:
            wake_retry_recover_steps = (
                f"1. 进入学习唤醒词 "
                f"2. 录入普通功能词 {wake_retry_single_recover_seed} 触发首次失配 "
                f"3. 因失败重试次数=1，当前学习流程应直接退出 "
                f"4. 再说 {VOICE_REG_WAKE_RECOVER_ALIAS} 不应唤醒设备，默认唤醒词仍应正常"
            )
            wake_retry_recover_phrases = [wake_retry_single_recover_seed]
        else:
            wake_retry_recover_steps = (
                f"1. 进入学习唤醒词 "
                f"2. 前 {wakeup_repeat_count - 1} 遍录入 {VOICE_REG_WAKE_RECOVER_ALIAS} "
                f"3. 第 {wakeup_repeat_count} 遍故意录入普通功能词 {wake_retry_single_recover_seed} "
                f"4. 因失败重试次数=1，当前学习流程应直接退出，不进入恢复重试 "
                f"5. 再说 {VOICE_REG_WAKE_RECOVER_ALIAS} 不应唤醒设备，默认唤醒词仍应正常"
            )
            wake_retry_recover_phrases = [VOICE_REG_WAKE_RECOVER_ALIAS] * max(wakeup_repeat_count - 1, 0) + [
                wake_retry_single_recover_seed
            ]
        wake_retry_recover_expected = "唤醒词失败重试次数=1 时，首次失配即终止当前学习流程，学习唤醒词不生效，默认唤醒保持正常"
        wake_retry_exhaust_steps = (
            f"1. 进入学习唤醒词 "
            f"2. 连续录入删除类命令 {cycle_phrases([delete_wake_phrase], max(wakeup_retry_count + 1, 1))} "
            f"直到耗尽失败重试次数 "
            f"3. 学习失败后确认默认唤醒仍正常，且不会残留新的学习唤醒词"
        )
        wake_retry_exhaust_expected = "单遍学习模式下，连续输入删除类命令耗尽失败计数后学习失败，默认唤醒保持正常且不会残留新的学习唤醒词"
        wake_retry_exhaust_phrases = cycle_phrases(
            [delete_wake_phrase],
            max(wakeup_retry_count + 1, 1),
        )
        wake_retry_exhaust_verify_phrase = ""
    elif wakeup_repeat_count <= 1:
        wake_retry_recover_steps = (
            f"1. 进入学习唤醒词 "
            f"2. 先录入普通功能词 {wake_retry_single_recover_seed} 触发失败计数但保持学习态 "
            f"3. 在重试窗口内录入 {VOICE_REG_WAKE_RECOVER_ALIAS} "
            f"4. 学习成功后验证 {VOICE_REG_WAKE_RECOVER_ALIAS} 可正常唤醒"
        )
        wake_retry_recover_expected = "单遍学习模式下，普通功能词先触发失败计数但未直接终止学习，随后在重试窗口补录成功且学习唤醒词生效"
        wake_retry_recover_phrases = [
            wake_retry_single_recover_seed,
            VOICE_REG_WAKE_RECOVER_ALIAS,
        ]
        wake_retry_exhaust_steps = (
            f"1. 进入学习唤醒词 "
            f"2. 连续录入删除类命令 {cycle_phrases([delete_wake_phrase], max(wakeup_retry_count + 1, 1))} "
            f"直到耗尽失败重试次数 "
            f"3. 学习失败后确认默认唤醒仍正常，且不会残留新的学习唤醒词"
        )
        wake_retry_exhaust_expected = "单遍学习模式下，连续输入删除类命令耗尽失败计数后学习失败，默认唤醒保持正常且不会残留新的学习唤醒词"
        wake_retry_exhaust_phrases = cycle_phrases(
            [delete_wake_phrase],
            max(wakeup_retry_count + 1, 1),
        )
        wake_retry_exhaust_verify_phrase = ""
    else:
        wake_retry_recover_steps = (
            f"1. 进入学习唤醒词 "
            f"2. 第一遍录入 {VOICE_REG_WAKE_RECOVER_ALIAS} "
            f"3. 第二遍故意录入普通功能词 {wake_retry_recover_mismatch} 后触发重试 "
            f"4. 在重试窗口内重新录入 {VOICE_REG_WAKE_RECOVER_ALIAS} "
            f"5. 学习成功后验证 {VOICE_REG_WAKE_RECOVER_ALIAS} 可正常唤醒"
        )
        wake_retry_recover_expected = "唤醒词失败重试次数生效，在重试窗口内恢复一致后学习成功，学习唤醒词生效"
        wake_retry_recover_phrases = [
            VOICE_REG_WAKE_RECOVER_ALIAS,
            wake_retry_recover_mismatch,
            VOICE_REG_WAKE_RECOVER_ALIAS,
        ]
        wake_retry_exhaust_steps = (
            f"1. 进入学习唤醒词 "
            f"2. 第一遍录入 {VOICE_REG_WAKE_FAIL_ALIAS} "
            f"3. 后续连续录入删除类命令 {[delete_wake_phrase] * max(wakeup_retry_count, 1)} 直到耗尽失败重试次数 "
            f"4. 学习失败后再使用 {VOICE_REG_WAKE_FAIL_ALIAS} 唤醒 "
            f"5. 该学习唤醒词不应生效"
        )
        wake_retry_exhaust_expected = "失败重试次数耗尽后学习失败，失败唤醒语料不会生效"
        wake_retry_exhaust_phrases = [VOICE_REG_WAKE_FAIL_ALIAS] + [delete_wake_phrase] * max(wakeup_retry_count, 1)
        wake_retry_exhaust_verify_phrase = VOICE_REG_WAKE_FAIL_ALIAS

    rows = [
        voice_case(
            case_id="VOICE-001",
            module_name="语音注册-命令词学习次数正例",
            command="学习命令词",
            steps=(
                f"1. 确认命令词学习次数={command_repeat_count}、失败重试次数={command_retry_count} "
                f"2. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
                f"3. 连续录入 {VOICE_REG_COMMAND_SUCCESS_ALIAS} 共 {command_repeat_count} 次 "
                f"4. 学习成功后，再说 {VOICE_REG_COMMAND_SUCCESS_ALIAS} "
                f"5. 应触发 {selected_command} 的原始命令逻辑"
            ),
            expected="命令词学习次数与配置一致，学习成功后自定义学习语料真实生效",
            config_assert=command_config_assert(),
            runtime_values={
                "voiceRegScenario": "command_learn_success",
                "selectedCommand": selected_command,
                "learnedCommand": VOICE_REG_COMMAND_SUCCESS_ALIAS,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "registMode": regist_mode,
                "learningPhrases": [VOICE_REG_COMMAND_SUCCESS_ALIAS] * command_repeat_count,
                "verifyPhrase": VOICE_REG_COMMAND_SUCCESS_ALIAS,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": True,
            },
            input_value="command_learn_success",
            linkage="参考 Excel: 语音注册测试_119",
            pack_overrides={"voiceRegLearnCommandAlias": VOICE_REG_COMMAND_SUCCESS_ALIAS},
        ),
        voice_case(
            case_id="VOICE-002",
            module_name="语音注册-唤醒词学习次数正例",
            command="学习唤醒词",
            steps=(
                f"1. 确认唤醒词学习次数={wakeup_repeat_count}、失败重试次数={wakeup_retry_count} "
                f"2. 进入学习唤醒词 "
                f"3. 连续录入 {VOICE_REG_WAKE_SUCCESS_ALIAS} 共 {wakeup_repeat_count} 次 "
                f"4. 学习成功后使用 {VOICE_REG_WAKE_SUCCESS_ALIAS} 唤醒设备"
            ),
            expected="唤醒词学习次数与配置一致，学习成功后自定义唤醒词真实生效",
            config_assert=wake_config_assert(),
            runtime_values={
                "voiceRegScenario": "wakeup_learn_success",
                "learnedWakeWord": VOICE_REG_WAKE_SUCCESS_ALIAS,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "registMode": regist_mode,
                "learningPhrases": [VOICE_REG_WAKE_SUCCESS_ALIAS] * wakeup_repeat_count,
                "verifyPhrase": VOICE_REG_WAKE_SUCCESS_ALIAS,
                "verifyExpectedResults": [VOICE_REG_WAKE_SUCCESS_ALIAS, virtual_wake],
                "verifyShouldWork": True,
            },
            input_value="wakeup_learn_success",
            linkage="参考 Excel: 语音注册测试_8 / 语音注册测试_12",
            pack_overrides={"voiceRegLearnWakeWord": VOICE_REG_WAKE_SUCCESS_ALIAS},
        ),
        voice_case(
            case_id="VOICE-003",
            module_name=command_retry_recover_case_name,
            command="学习命令词",
            steps=command_retry_recover_steps,
            expected=command_retry_recover_expected,
            config_assert=command_config_assert(),
            runtime_values={
                "voiceRegScenario": command_retry_recover_scenario,
                "selectedCommand": selected_command,
                "learnedCommand": VOICE_REG_COMMAND_RECOVER_ALIAS,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "registMode": regist_mode,
                "learningPhrases": command_retry_recover_phrases,
                "verifyPhrase": VOICE_REG_COMMAND_RECOVER_ALIAS,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": command_retry_recover_verify_should_work,
            },
            input_value=command_retry_recover_scenario,
            linkage="参考 Excel: 语音注册测试_121",
            pack_overrides={"voiceRegLearnCommandAlias": VOICE_REG_COMMAND_RECOVER_ALIAS},
        ),
        voice_case(
            case_id="VOICE-004",
            module_name=wake_retry_recover_case_name,
            command="学习唤醒词",
            steps=wake_retry_recover_steps,
            expected=wake_retry_recover_expected,
            config_assert=wake_config_assert(),
            runtime_values={
                "voiceRegScenario": wake_retry_recover_scenario,
                "learnedWakeWord": VOICE_REG_WAKE_RECOVER_ALIAS,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "registMode": regist_mode,
                "learningPhrases": wake_retry_recover_phrases,
                "verifyPhrase": VOICE_REG_WAKE_RECOVER_ALIAS,
                "verifyExpectedResults": [VOICE_REG_WAKE_RECOVER_ALIAS, virtual_wake],
                "verifyShouldWork": wake_retry_recover_verify_should_work,
            },
            input_value=wake_retry_recover_scenario,
            linkage="参考 Excel: 语音注册测试_11",
            pack_overrides={"voiceRegLearnWakeWord": VOICE_REG_WAKE_RECOVER_ALIAS},
        ),
        voice_case(
            case_id="VOICE-005",
            module_name="语音注册-命令词失败重试耗尽",
            command="学习命令词",
            steps=command_retry_exhaust_steps,
            expected=command_retry_exhaust_expected,
            config_assert=command_config_assert(),
            runtime_values={
                "voiceRegScenario": "command_retry_exhaust",
                "selectedCommand": selected_command,
                "learnedCommand": VOICE_REG_COMMAND_FAIL_ALIAS,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "registMode": regist_mode,
                "learningPhrases": command_retry_exhaust_phrases,
                "verifyPhrase": command_retry_exhaust_verify_phrase,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": False,
            },
            input_value="command_retry_exhaust",
            linkage="参考 Excel: 语音注册测试_120",
            pack_overrides={"voiceRegLearnCommandAlias": VOICE_REG_COMMAND_FAIL_ALIAS},
        ),
        voice_case(
            case_id="VOICE-006",
            module_name="语音注册-唤醒词失败重试耗尽",
            command="学习唤醒词",
            steps=wake_retry_exhaust_steps,
            expected=wake_retry_exhaust_expected,
            config_assert=wake_config_assert(),
            runtime_values={
                "voiceRegScenario": "wakeup_retry_exhaust",
                "learnedWakeWord": VOICE_REG_WAKE_FAIL_ALIAS,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "registMode": regist_mode,
                "learningPhrases": wake_retry_exhaust_phrases,
                "verifyPhrase": wake_retry_exhaust_verify_phrase,
                "verifyExpectedResults": [VOICE_REG_WAKE_FAIL_ALIAS, virtual_wake],
                "verifyShouldWork": False,
            },
            input_value="wakeup_retry_exhaust",
            linkage="参考 Excel: 语音注册测试_9",
            pack_overrides={"voiceRegLearnWakeWord": VOICE_REG_WAKE_FAIL_ALIAS},
        ),
        voice_case(
            case_id="VOICE-007",
            module_name="语音注册-命令词功能词冲突",
            command="学习命令词",
            steps=(
                f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
                f"2. 在学习阶段连续录入已支持功能命令词 {forbidden_command} "
                f"3. 观察设备拒绝学习并退出 "
                f"4. 再说 {forbidden_command}，应仍执行其原始功能而不是映射到 {selected_command}"
            ),
            expected="已支持功能命令词不能作为学习语料，学习失败且原始命令逻辑不被篡改",
            config_assert=command_config_assert(),
            runtime_values={
                "voiceRegScenario": "command_supported_conflict",
                "selectedCommand": selected_command,
                "forbiddenCommand": forbidden_command,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "learningPhrases": [forbidden_command] * max(command_retry_count + 1, 1),
                "verifyPhrase": forbidden_command,
                "verifyTargetCommand": forbidden_command,
                "verifyShouldWork": True,
            },
            input_value="command_supported_conflict",
            linkage="参考 Excel: 语音注册测试_126 / 语音注册测试_134",
        ),
        voice_case(
            case_id="VOICE-008",
            module_name="语音注册-唤醒词默认唤醒词冲突",
            command="学习唤醒词",
            steps=(
                f"1. 进入学习唤醒词 "
                f"2. 在学习阶段连续录入默认唤醒词 {default_wake_word} "
                f"3. 观察设备拒绝学习并退出 "
                f"4. 默认唤醒词 {default_wake_word} 仍只按原始唤醒逻辑生效"
            ),
            expected="默认唤醒词不能作为学习语料，学习失败且默认唤醒逻辑保持正常",
            config_assert=wake_config_assert(),
            runtime_values={
                "voiceRegScenario": "wakeup_default_conflict",
                "defaultWakeWord": default_wake_word,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "learningPhrases": [default_wake_word] * max(wakeup_retry_count + 1, 1),
                "verifyPhrase": default_wake_word,
                "verifyExpectedResults": [default_wake_word],
                "verifyShouldWork": True,
            },
            input_value="wakeup_default_conflict",
            linkage="参考 Excel: 语音注册测试_20",
        ),
        voice_case(
            case_id="VOICE-009",
            module_name="语音注册-命令词保留提示词冲突",
            command="学习命令词",
            steps=(
                f"1. 进入学习命令词；若当前固件要求选目标命令，则选择 {selected_command} "
                f"2. 在学习阶段依次录入保留控制词 {command_reserved_phrases} "
                f"3. 观察设备提示该指令不支持学习/冲突并最终退出 "
                f"4. 默认命令词 {selected_command} 仍保持正常"
            ),
            expected="学习/删除/退出类保留词不能作为命令词学习语料，学习失败且默认功能保持正常",
            config_assert=command_config_assert(),
            runtime_values={
                "voiceRegScenario": "command_reserved_conflict",
                "selectedCommand": selected_command,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "learningPhrases": command_reserved_phrases,
                "verifyPhrase": selected_command,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": True,
            },
            input_value="command_reserved_conflict",
            linkage="参考 Excel: 语音注册测试_130 / 语音注册测试_155",
        ),
        voice_case(
            case_id="VOICE-010",
            module_name="语音注册-唤醒词保留提示词冲突",
            command="学习唤醒词",
            steps=(
                "1. 进入学习唤醒词 "
                f"2. 在学习阶段依次录入保留控制词 {wake_reserved_phrases} "
                f"3. 观察设备提示冲突并最终退出 "
                f"4. 默认唤醒词 {default_wake_word} 仍保持正常"
            ),
            expected="学习/删除/退出类保留词不能作为唤醒词学习语料，学习失败且默认唤醒保持正常",
            config_assert=wake_config_assert(),
            runtime_values={
                "voiceRegScenario": "wakeup_reserved_conflict",
                "defaultWakeWord": default_wake_word,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "learningPhrases": wake_reserved_phrases,
                "verifyPhrase": default_wake_word,
                "verifyExpectedResults": [default_wake_word],
                "verifyShouldWork": True,
            },
            input_value="wakeup_reserved_conflict",
            linkage="参考 Excel: 语音注册测试_30",
        ),
        voice_case(
            case_id="VOICE-011",
            module_name="语音注册-删除命令词正例",
            command="删除命令词",
            steps=(
                f"1. 先学习自定义命令词 {VOICE_REG_COMMAND_DELETE_ALIAS} -> {selected_command} "
                f"2. 进入删除命令词流程并删除该学习数据 "
                f"3. 删除后再说 {VOICE_REG_COMMAND_DELETE_ALIAS} 不应触发命令 "
                f"4. 默认命令词 {selected_command} 仍可正常生效"
            ),
            expected="删除命令词成功后仅移除学习映射，不影响默认命令词",
            config_assert=command_config_assert("_ver_list[0].asr_cmds[*].intent contains 删除命令词"),
            runtime_values={
                "voiceRegScenario": "command_delete_positive",
                "selectedCommand": selected_command,
                "learnedCommand": VOICE_REG_COMMAND_DELETE_ALIAS,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "verifyPhrase": VOICE_REG_COMMAND_DELETE_ALIAS,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": False,
            },
            input_value="command_delete_positive",
            linkage="参考 Excel: 语音注册测试_103 / 语音注册测试_153",
            pack_overrides={"voiceRegLearnCommandAlias": VOICE_REG_COMMAND_DELETE_ALIAS},
        ),
        voice_case(
            case_id="VOICE-012",
            module_name="语音注册-删除命令词反例",
            command="删除命令词",
            steps=(
                f"1. 先学习自定义命令词 {VOICE_REG_COMMAND_DELETE_KEEP_ALIAS} -> {selected_command} "
                f"2. 进入删除命令词流程后退出删除 "
                f"3. 再说 {VOICE_REG_COMMAND_DELETE_KEEP_ALIAS} 仍应触发 {selected_command}"
            ),
            expected="删除未完成或主动退出时，学习命令词不应被删除",
            config_assert=command_config_assert("_ver_list[0].asr_cmds[*].intent contains 删除命令词"),
            runtime_values={
                "voiceRegScenario": "command_delete_negative",
                "selectedCommand": selected_command,
                "learnedCommand": VOICE_REG_COMMAND_DELETE_KEEP_ALIAS,
                "commandRepeatCount": command_repeat_count,
                "commandRetryCount": command_retry_count,
                "verifyPhrase": VOICE_REG_COMMAND_DELETE_KEEP_ALIAS,
                "verifyTargetCommand": selected_command,
                "verifyShouldWork": True,
            },
            input_value="command_delete_negative",
            linkage="参考 Excel: 语音注册测试_104 / 语音注册测试_106",
            pack_overrides={"voiceRegLearnCommandAlias": VOICE_REG_COMMAND_DELETE_KEEP_ALIAS},
        ),
        voice_case(
            case_id="VOICE-013",
            module_name="语音注册-删除唤醒词正例",
            command="删除唤醒词",
            steps=(
                f"1. 先学习自定义唤醒词 {VOICE_REG_WAKE_DELETE_ALIAS} "
                f"2. 进入删除唤醒词流程并完成删除 "
                f"3. 删除后再使用 {VOICE_REG_WAKE_DELETE_ALIAS} 应无法唤醒 "
                f"4. 默认唤醒词 {default_wake_word} 仍可正常唤醒"
            ),
            expected="删除唤醒词成功后仅移除学习唤醒词，不影响默认唤醒",
            config_assert=wake_config_assert("_ver_list[0].asr_cmds[*].intent contains 删除唤醒词"),
            runtime_values={
                "voiceRegScenario": "wakeup_delete_positive",
                "learnedWakeWord": VOICE_REG_WAKE_DELETE_ALIAS,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "verifyPhrase": VOICE_REG_WAKE_DELETE_ALIAS,
                "verifyExpectedResults": [VOICE_REG_WAKE_DELETE_ALIAS, virtual_wake],
                "verifyShouldWork": False,
            },
            input_value="wakeup_delete_positive",
            linkage="参考 Excel: 语音注册测试_101",
            pack_overrides={"voiceRegLearnWakeWord": VOICE_REG_WAKE_DELETE_ALIAS},
        ),
        voice_case(
            case_id="VOICE-014",
            module_name="语音注册-删除唤醒词反例",
            command="删除唤醒词",
            steps=(
                f"1. 先学习自定义唤醒词 {VOICE_REG_WAKE_DELETE_KEEP_ALIAS} "
                "2. 进入删除唤醒词流程后退出删除 "
                f"3. 再使用 {VOICE_REG_WAKE_DELETE_KEEP_ALIAS} 仍可唤醒设备"
            ),
            expected="删除未完成或主动退出时，学习唤醒词不应被删除",
            config_assert=wake_config_assert("_ver_list[0].asr_cmds[*].intent contains 删除唤醒词"),
            runtime_values={
                "voiceRegScenario": "wakeup_delete_negative",
                "learnedWakeWord": VOICE_REG_WAKE_DELETE_KEEP_ALIAS,
                "virtualWakeIntent": virtual_wake,
                "wakeupRepeatCount": wakeup_repeat_count,
                "wakeupRetryCount": wakeup_retry_count,
                "verifyPhrase": VOICE_REG_WAKE_DELETE_KEEP_ALIAS,
                "verifyExpectedResults": [VOICE_REG_WAKE_DELETE_KEEP_ALIAS, virtual_wake],
                "verifyShouldWork": True,
            },
            input_value="wakeup_delete_negative",
            linkage="参考 Excel: 语音注册测试_102 / 语音注册测试_106",
            pack_overrides={"voiceRegLearnWakeWord": VOICE_REG_WAKE_DELETE_KEEP_ALIAS},
        ),
    ]
    return rows


def _multi_wke_protocol(code: int, *, recv: bool = False) -> str:
    command = "82" if recv else "81"
    tail = (code + (0x21 if recv else 0x20)) & 0xFF
    return f"A5 FA 00 {command} {code:02X} 00 {tail:02X} FB"


def _normalize_multi_wke_mode(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "loop": "loop",
        "循环切换": "loop",
        "specified": "specified",
        "指定切换": "specified",
        "protocol": "protocol",
        "协议切换": "protocol",
    }
    return mapping.get(text, text)


def _display_multi_wke_mode(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "loop": "循环切换",
        "specified": "指定切换",
        "protocol": "协议切换",
    }
    return mapping.get(text, text)


def build_default_specified_multi_wke_payload(
    *,
    default_wakeword: str = MULTI_WKE_DEFAULT_WAKEWORD,
    extra_wakewords: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    words = [str(default_wakeword or "").strip()]
    for raw in extra_wakewords or MULTI_WKE_CANDIDATE_WAKEWORDS:
        text = str(raw or "").strip()
        if text and text not in words:
            words.append(text)

    wkelist: List[Dict[str, Any]] = []
    for index, word in enumerate(words):
        code = 0x31 + index
        wkelist.append(
            {
                "condition": word,
                "reply": f"已切换到{word}",
                "sndProtocol": _multi_wke_protocol(code, recv=False),
                "recProtocol": _multi_wke_protocol(code, recv=True),
                "isDefault": index == 0,
                "isFrozen": False,
            }
        )

    return {
        "multiWkeEnable": True,
        "multiWkeMode": "specified",
        "releaseMultiWke": {
            "common": [
                {"type": "query", "condition": MULTI_WKE_QUERY_COMMAND, "reply": ""},
                {"type": "restore", "condition": MULTI_WKE_RESTORE_COMMAND, "reply": ""},
                {"type": "switch", "condition": MULTI_WKE_SWITCH_COMMAND, "reply": MULTI_WKE_SWITCH_REPLY},
            ],
            "wkelist": wkelist,
        },
    }


def _multi_wke_word(item: Dict[str, Any]) -> str:
    return str(item.get("condition") or item.get("word") or "").strip()


def _multi_wke_send_protocol(item: Dict[str, Any]) -> str:
    return normalize_hex_protocol(item.get("sndProtocol") or item.get("snd_protocol") or "")


def _multi_wke_recv_protocol(item: Dict[str, Any]) -> str:
    return normalize_hex_protocol(item.get("recProtocol") or item.get("rec_protocol") or "")


def _multi_wke_explicit_query_protocol(*items: Any) -> str:
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in (
            "queryExpectedProtocol",
            "query_expected_protocol",
            "queryProtocol",
            "query_protocol",
            "queryRespProtocol",
            "query_resp_protocol",
            "queryRecvProtocol",
            "query_recv_protocol",
            "queryRecProtocol",
            "query_rec_protocol",
        ):
            protocol = normalize_hex_protocol(item.get(key) or "")
            if protocol:
                return protocol
    return ""


def _multi_wke_default_item(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    for item in items:
        if item.get("isDefault") is True:
            return item
    return dict(items[0]) if items else {}


def _multi_wke_find_item(items: Sequence[Dict[str, Any]], word: str) -> Dict[str, Any]:
    target = str(word or "").strip()
    for item in items:
        if _multi_wke_word(item) == target:
            return item
    return {}


def _multi_wke_negative_config_row(pack_args: Dict[str, Any]) -> Dict[str, str]:
    negative_pack_args = dict(pack_args)
    negative_pack_args.update(
        {
            "releaseMultiWke.wkelist[*].isDefault": False,
            "releaseMultiWke.wkelist[*].isFrozen": True,
        }
    )
    return suite_row(
        case_id="MWK-CFG-001",
        module_name="多唤醒-非默认词冻结约束",
        test_type="配置约束校验",
        command="",
        priority="P0",
        steps="1. 尝试将非默认唤醒词勾选为冻结唤醒词 2. 观察平台是否拒绝保存或自动纠正",
        expected="只有默认唤醒词允许被冻结，非默认唤醒词冻结应被拒绝",
        executor="页面/接口校验",
        pack_args=negative_pack_args,
        config_assert="",
        runtime_assert="",
        raw_param="releaseMultiWke.wkelist[*].isFrozen",
        input_value=True,
        linkage="多唤醒冻结词配置反例",
    )


def _multi_wke_case_pack_args(metadata: Dict[str, Any], web_config: Dict[str, Any]) -> Dict[str, Any]:
    applied = dict(metadata.get("appliedOverrides") or {})
    ver = first_version(web_config)
    firmware = dict(ver.get("firmware") or {})
    multi_cfg = dict(ver.get("multi_wakeup") or firmware.get("multi_wakeup") or {})
    general_cfg = dict(firmware.get("general_config") or {})
    persisted_cfg = dict(general_cfg.get("persisted") or {})

    pack_args: Dict[str, Any] = {
        "multiWkeEnable": bool(applied.get("multiWkeEnable", multi_cfg.get("enable"))),
        "multiWkeMode": _normalize_multi_wke_mode(applied.get("multiWkeMode") or multi_cfg.get("mode")),
    }
    if "wakeWordSave" in applied:
        pack_args["wakeWordSave"] = applied.get("wakeWordSave")
    elif "wakeup" in persisted_cfg:
        pack_args["wakeWordSave"] = persisted_cfg.get("wakeup")

    release_multi = applied.get("releaseMultiWke")
    if isinstance(release_multi, dict):
        pack_args["releaseMultiWke"] = deepcopy(release_multi)
    elif multi_cfg.get("common") or multi_cfg.get("wkelist"):
        pack_args["releaseMultiWke"] = {
            "common": deepcopy(multi_cfg.get("common") or []),
            "wkelist": deepcopy(multi_cfg.get("wkelist") or []),
        }
    elif multi_cfg.get("switch_control") or multi_cfg.get("switch_list"):
        switch_control = dict(multi_cfg.get("switch_control") or {})
        common: List[Dict[str, Any]] = []
        for type_name, key_name in [("query", "query_info"), ("restore", "restore_info"), ("switch", "switch_info")]:
            info = dict(switch_control.get(key_name) or {})
            condition = str(info.get("word") or "").strip()
            if not condition:
                continue
            common.append(
                {
                    "type": type_name,
                    "condition": condition,
                    "reply": str(info.get("reply") or ""),
                }
            )

        wkelist: List[Dict[str, Any]] = []
        for item in multi_cfg.get("switch_list") or []:
            if not isinstance(item, dict):
                continue
            condition = str(item.get("word") or "").strip()
            if not condition:
                continue
            special_type = str(item.get("special_type") or item.get("specialType") or "").strip()
            wkelist.append(
                {
                    "condition": condition,
                    "reply": str(item.get("reply") or ""),
                    "sndProtocol": str(item.get("snd_protocol") or item.get("sndProtocol") or ""),
                    "recProtocol": str(item.get("rec_protocol") or item.get("recProtocol") or ""),
                    "isDefault": "默认唤醒词" in special_type or bool(item.get("isDefault")),
                    "isFrozen": bool(item.get("frozen") if item.get("frozen") is not None else item.get("isFrozen")),
                }
            )
        pack_args["releaseMultiWke"] = {
            "common": common,
            "wkelist": wkelist,
        }
    return pack_args


def make_multi_wke_rows(metadata: Dict[str, Any], web_config: Dict[str, Any]) -> List[Dict[str, str]]:
    pack_args = _multi_wke_case_pack_args(metadata, web_config)
    ver = first_version(web_config)
    firmware = dict(ver.get("firmware") or {})
    multi_cfg = dict(ver.get("multi_wakeup") or firmware.get("multi_wakeup") or {})
    if not pack_args.get("multiWkeEnable"):
        return []

    release_multi = dict(pack_args.get("releaseMultiWke") or {})
    wkelist = [item for item in (release_multi.get("wkelist") or []) if isinstance(item, dict)]
    common_list = [item for item in (release_multi.get("common") or []) if isinstance(item, dict)]
    if len(wkelist) < 3:
        return [_multi_wke_negative_config_row(pack_args)]

    wakewords = [_multi_wke_word(item) for item in wkelist if _multi_wke_word(item)]
    if len(wakewords) < 3:
        return [_multi_wke_negative_config_row(pack_args)]

    default_item = _multi_wke_default_item(wkelist)
    default_wake = _multi_wke_word(default_item) or wakewords[0]
    default_frozen = bool(default_item.get("isFrozen"))
    extra_items = [item for item in wkelist if _multi_wke_word(item) != default_wake]
    extra_wakes = [_multi_wke_word(item) for item in extra_items if _multi_wke_word(item)]
    if len(extra_items) < 2:
        return [_multi_wke_negative_config_row(pack_args)]
    first_item, second_item = extra_items[:2]
    first_target = _multi_wke_word(first_item)
    second_target = _multi_wke_word(second_item)
    first_recv_proto = _multi_wke_recv_protocol(first_item)
    second_recv_proto = _multi_wke_recv_protocol(second_item)

    common_by_type: Dict[str, Dict[str, Any]] = {}
    for item in common_list:
        type_name = str(item.get("type") or "").strip()
        if type_name and type_name not in common_by_type:
            common_by_type[type_name] = item

    switch_control = dict(multi_cfg.get("switch_control") or multi_cfg.get("switchControl") or {})
    switch_info = dict(switch_control.get("switch_info") or switch_control.get("switchInfo") or {})
    restore_info = dict(switch_control.get("restore_info") or switch_control.get("restoreInfo") or {})
    query_info = dict(switch_control.get("query_info") or switch_control.get("queryInfo") or {})

    switch_command = str(switch_info.get("word") or "").strip()
    restore_command = str(restore_info.get("word") or "").strip()
    query_command = str(query_info.get("word") or "").strip()
    query_expected_protocol = _multi_wke_explicit_query_protocol(query_info, common_by_type.get("query") or {})
    mode = str(pack_args.get("multiWkeMode") or "specified").strip() or "specified"
    config_mode = str(multi_cfg.get("mode") or "").strip() or _display_multi_wke_mode(mode)

    wake_asserts = [f"firmware.multi_wakeup.switch_list[*].word contains {render_json(word)}" for word in wakewords]
    algo_wake_asserts = [f"_ver_list[0].asr_wakeup[*].intent contains {render_json(word)}" for word in wakewords]
    common_asserts = []
    if switch_command:
        common_asserts.append(f"firmware.multi_wakeup.switch_control.switch_info.word eq {render_json(switch_command)}")
    if restore_command:
        common_asserts.append(f"firmware.multi_wakeup.switch_control.restore_info.word eq {render_json(restore_command)}")
    if query_command:
        common_asserts.append(f"firmware.multi_wakeup.switch_control.query_info.word eq {render_json(query_command)}")
    config_base = [
        "firmware.multi_wakeup.enable eq true",
        f"firmware.multi_wakeup.mode eq {render_json(config_mode)}",
        *common_asserts,
        *wake_asserts,
        *algo_wake_asserts,
    ]

    def merged_pack_args(**overrides: Any) -> Dict[str, Any]:
        merged = dict(pack_args)
        merged.update(overrides)
        return merged

    def multi_case(
        *,
        case_id: str,
        module_name: str,
        command: str,
        steps: str,
        expected: str,
        runtime_values: Dict[str, Any],
        input_value: Any,
        linkage: str,
    ) -> Dict[str, str]:
        return suite_row(
            case_id=case_id,
            module_name=module_name,
            test_type="功能验证",
            command=command,
            priority="P0",
            steps=steps,
            expected=expected,
            executor="device",
            pack_args=merged_pack_args(),
            config_assert="；".join(config_base),
            runtime_assert=runtime_assert_text(runtime_values),
            raw_param="multiWkeScenario",
            input_value=input_value,
            linkage=linkage,
        )

    rows: List[Dict[str, str]] = [
        multi_case(
            case_id="MWK-001",
            module_name="多唤醒-默认唤醒词初始态",
            command=default_wake,
            steps=(
                f"1. 确认多唤醒模式={mode}，默认唤醒词为 {default_wake} "
                f"2. 上电后直接验证 {default_wake} 可唤醒 "
                f"3. 验证 {first_target} / {second_target} 默认不生效"
            ),
            expected="默认唤醒词在初始态可用，非当前候选唤醒词默认不生效",
            runtime_values={
                "multiWkeScenario": "default_active",
                "mode": mode,
                "defaultWakeWord": default_wake,
                "expectedActiveWakeWord": default_wake,
                "expectedInactiveWakeWords": [first_target, second_target],
            },
            input_value="default_active",
            linkage="多唤醒默认词正反例",
        )
    ]

    if mode == "loop":
        if switch_command:
            rows.extend(
                [
                multi_case(
                    case_id="MWK-002",
                    module_name="多唤醒-循环切换首个候选词",
                    command=switch_command,
                    steps=(
                        f"1. 确认多唤醒模式={mode}，默认唤醒词为 {default_wake} "
                        f"2. 说 {switch_command} 切换到下一个唤醒词 "
                        f"3. 验证 {first_target} 可唤醒，{default_wake} 不再生效"
                    ),
                    expected="循环切换后，首个候选唤醒词生效，原默认唤醒词失效",
                    runtime_values={
                        "multiWkeScenario": "loop_switch",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "switchCommand": switch_command,
                        "switchCount": 1,
                        "expectedActiveWakeWord": first_target,
                        "expectedInactiveWakeWords": [default_wake, second_target],
                    },
                    input_value=f"loop_switch:{first_target}",
                    linkage="多唤醒循环切换正反例",
                ),
                multi_case(
                    case_id="MWK-004",
                    module_name="多唤醒-循环切换第二个候选词",
                    command=switch_command,
                    steps=(
                        f"1. 先切换到 {first_target} "
                        f"2. 再说 {switch_command} 继续循环切换 "
                        f"3. 验证 {second_target} 可唤醒，{first_target} 不再生效"
                    ),
                    expected="二次循环切换后，第二个候选唤醒词生效，前一个候选词失效",
                    runtime_values={
                        "multiWkeScenario": "loop_switch",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": first_target,
                        "switchCommand": switch_command,
                        "switchCount": 1,
                        "expectedActiveWakeWord": second_target,
                        "expectedInactiveWakeWords": [default_wake, first_target],
                    },
                    input_value=f"loop_switch:{second_target}",
                    linkage="多唤醒循环切换正反例",
                ),
                ]
            )
        if switch_command and query_command:
            rows.append(
                multi_case(
                    case_id="MWK-003",
                    module_name="多唤醒-循环切换后查询当前词",
                    command=query_command,
                    steps=(
                        f"1. 先循环切换到 {first_target} "
                        f"2. 说 {query_command} 查询当前唤醒词 "
                        f"3. 验证查询命令识别成功，且 {first_target} 仍保持生效"
                    ),
                    expected="查询命令应返回当前循环切换后的唤醒词信息，且不改变当前词",
                    runtime_values={
                        "multiWkeScenario": "query_current",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": first_target,
                        "switchCommand": switch_command,
                        "queryCommand": query_command,
                        "queryExpectedProtocol": query_expected_protocol,
                        "expectedActiveWakeWord": first_target,
                        "expectedInactiveWakeWords": [default_wake, second_target],
                    },
                    input_value="query_current",
                    linkage="多唤醒查询当前词正反例",
                )
            )
        if switch_command and restore_command:
            rows.append(
                multi_case(
                    case_id="MWK-005",
                    module_name="多唤醒-恢复默认唤醒词",
                    command=restore_command,
                    steps=(
                        f"1. 先切换到 {second_target} "
                        f"2. 说 {restore_command} 恢复默认唤醒词 "
                        f"3. 验证 {default_wake} 可唤醒，{second_target} 不再生效"
                    ),
                    expected="恢复默认后，默认唤醒词生效，当前候选唤醒词失效",
                    runtime_values={
                        "multiWkeScenario": "restore_default",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": second_target,
                        "switchCommand": switch_command,
                        "restoreCommand": restore_command,
                        "expectedActiveWakeWord": default_wake,
                        "expectedInactiveWakeWords": [first_target, second_target],
                    },
                    input_value="restore_default",
                    linkage="多唤醒恢复默认正反例",
                )
            )
    elif mode == "protocol":
        rows.append(
            multi_case(
                case_id="MWK-002",
                module_name="多唤醒-协议切换到候选词一",
                command=first_target,
                steps=(
                    f"1. 确认多唤醒模式={mode}，默认唤醒词为 {default_wake} "
                    f"2. 向协议串口发送 {first_target} 的确认协议 "
                    f"3. 验证 {first_target} 可唤醒，{default_wake} / {second_target} 不生效"
                ),
                expected="协议切换成功后，目标唤醒词生效，其余候选词不生效",
                runtime_values={
                    "multiWkeScenario": "protocol_switch",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "protocolTargetWakeWord": first_target,
                    "protocolBytes": first_recv_proto,
                    "expectedActiveWakeWord": first_target,
                    "expectedInactiveWakeWords": [default_wake, second_target],
                },
                input_value=f"protocol_switch:{first_target}",
                linkage="多唤醒协议切换正例",
            )
        )
        if query_command:
            rows.append(
                multi_case(
                    case_id="MWK-003",
                    module_name="多唤醒-协议切换后查询当前词",
                    command=query_command,
                    steps=(
                        f"1. 先通过协议切换到 {first_target} "
                        f"2. 说 {query_command} 查询当前唤醒词 "
                        f"3. 验证查询命令识别成功，且当前词不改变"
                    ),
                    expected="协议切换后，查询命令应返回当前词信息，且当前词保持不变",
                    runtime_values={
                        "multiWkeScenario": "query_current",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": first_target,
                        "setupTargetProtocol": first_recv_proto,
                        "queryCommand": query_command,
                        "queryExpectedProtocol": query_expected_protocol,
                        "expectedActiveWakeWord": first_target,
                        "expectedInactiveWakeWords": [default_wake, second_target],
                    },
                    input_value="query_current",
                    linkage="多唤醒协议查询正反例",
                )
            )
        rows.append(
            multi_case(
                case_id="MWK-004",
                module_name="多唤醒-协议切换无效协议保持当前词",
                command="无效协议",
                steps=(
                    f"1. 先通过协议切换到 {first_target} "
                    f"2. 向协议串口发送无效协议 {MULTI_WKE_INVALID_PROTOCOL} "
                    f"3. 验证当前唤醒词 {first_target} 保持生效，其余候选词不生效"
                ),
                expected="协议切换收到无效协议时，应保持当前唤醒词不变",
                runtime_values={
                    "multiWkeScenario": "protocol_invalid_target",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "setupTargetWakeWord": first_target,
                    "setupTargetProtocol": first_recv_proto,
                    "protocolBytes": MULTI_WKE_INVALID_PROTOCOL,
                    "expectedActiveWakeWord": first_target,
                    "expectedInactiveWakeWords": [default_wake, second_target],
                },
                input_value="protocol_invalid_target",
                linkage="多唤醒协议切换反例",
            )
        )
        if restore_command:
            rows.append(
                multi_case(
                    case_id="MWK-005",
                    module_name="多唤醒-协议切换后恢复默认唤醒词",
                    command=restore_command,
                    steps=(
                        f"1. 先通过协议切换到 {first_target} "
                        f"2. 说 {restore_command} 恢复默认唤醒词 "
                        f"3. 验证 {default_wake} 可唤醒，{first_target} / {second_target} 不生效"
                    ),
                    expected="协议模式下恢复默认后，默认唤醒词重新生效",
                    runtime_values={
                        "multiWkeScenario": "restore_default",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": first_target,
                        "setupTargetProtocol": first_recv_proto,
                        "restoreCommand": restore_command,
                        "expectedActiveWakeWord": default_wake,
                        "expectedInactiveWakeWords": [first_target, second_target],
                    },
                    input_value="restore_default",
                    linkage="多唤醒协议恢复默认正反例",
                )
            )
    else:
        if switch_command:
            rows.extend(
                [
            multi_case(
                case_id="MWK-002",
                module_name="多唤醒-指定切换到候选词一",
                command=switch_command,
                steps=(
                    f"1. 确认多唤醒模式={mode}，默认唤醒词为 {default_wake} "
                    f"2. 说 {switch_command} 后说 {first_target} "
                    f"3. 验证 {first_target} 可唤醒，{default_wake} 与 {second_target} 不生效"
                ),
                expected="指定切换后，目标唤醒词生效，其余候选词不生效",
                runtime_values={
                    "multiWkeScenario": "specified_switch",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "switchCommand": switch_command,
                    "switchTargetWakeWord": first_target,
                    "expectedActiveWakeWord": first_target,
                    "expectedInactiveWakeWords": [default_wake, second_target],
                },
                input_value=f"specified_switch:{first_target}",
                linkage="多唤醒指定切换正反例",
            ),
            multi_case(
                case_id="MWK-004",
                module_name="多唤醒-指定切换到候选词二",
                command=switch_command,
                steps=(
                    f"1. 先切换到 {first_target} "
                    f"2. 再说 {switch_command} 后说 {second_target} "
                    f"3. 验证 {second_target} 可唤醒，{default_wake} 与 {first_target} 不生效"
                ),
                expected="再次指定切换后，新的目标唤醒词生效，旧目标失效",
                runtime_values={
                    "multiWkeScenario": "specified_switch",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "setupTargetWakeWord": first_target,
                    "switchCommand": switch_command,
                    "switchTargetWakeWord": second_target,
                    "expectedActiveWakeWord": second_target,
                    "expectedInactiveWakeWords": [default_wake, first_target],
                },
                input_value=f"specified_switch:{second_target}",
                linkage="多唤醒指定切换正反例",
            ),
            multi_case(
                case_id="MWK-005",
                module_name="多唤醒-指定切换无效词保持当前唤醒词",
                command=switch_command,
                steps=(
                    f"1. 先切换到 {second_target} "
                    f"2. 再说 {switch_command} 后故意说无效唤醒词 {MULTI_WKE_INVALID_WAKEWORD} "
                    f"3. 验证当前唤醒词 {second_target} 保持生效，{MULTI_WKE_INVALID_WAKEWORD} 不生效"
                ),
                expected="指定切换输入无效目标时，应保持当前唤醒词不变",
                runtime_values={
                    "multiWkeScenario": "specified_invalid_target",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "setupTargetWakeWord": second_target,
                    "switchCommand": switch_command,
                    "invalidTargetWakeWord": MULTI_WKE_INVALID_WAKEWORD,
                    "expectedActiveWakeWord": second_target,
                    "expectedInactiveWakeWords": [default_wake, first_target, MULTI_WKE_INVALID_WAKEWORD],
                },
                input_value="specified_invalid_target",
                linkage="多唤醒指定切换反例",
            ),
                ]
            )
        if switch_command and query_command:
            rows.append(
                multi_case(
                    case_id="MWK-003",
                    module_name="多唤醒-指定切换后查询当前词",
                    command=query_command,
                    steps=(
                        f"1. 先切换到 {first_target} "
                        f"2. 说 {query_command} 查询当前唤醒词 "
                        f"3. 验证查询命令识别成功，且 {first_target} 仍保持生效"
                    ),
                    expected="指定切换后，查询命令应返回当前词信息，且当前词不改变",
                    runtime_values={
                        "multiWkeScenario": "query_current",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": first_target,
                        "switchCommand": switch_command,
                        "queryCommand": query_command,
                        "queryExpectedProtocol": query_expected_protocol,
                        "expectedActiveWakeWord": first_target,
                        "expectedInactiveWakeWords": [default_wake, second_target],
                    },
                    input_value="query_current",
                    linkage="多唤醒查询当前词正反例",
                )
            )
        if switch_command and restore_command:
            rows.append(
                multi_case(
                    case_id="MWK-006",
                    module_name="多唤醒-恢复默认唤醒词",
                    command=restore_command,
                    steps=(
                        f"1. 先切换到 {second_target} "
                        f"2. 说 {restore_command} 恢复默认唤醒词 "
                        f"3. 验证 {default_wake} 可唤醒，{first_target} 与 {second_target} 不生效"
                    ),
                    expected="恢复默认后，默认唤醒词生效，其他候选唤醒词不生效",
                    runtime_values={
                        "multiWkeScenario": "restore_default",
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "setupTargetWakeWord": second_target,
                        "switchCommand": switch_command,
                        "restoreCommand": restore_command,
                        "expectedActiveWakeWord": default_wake,
                        "expectedInactiveWakeWords": [first_target, second_target],
                    },
                    input_value="restore_default",
                    linkage="多唤醒恢复默认正反例",
                )
            )

    if default_frozen:
        rows.append(
            multi_case(
                case_id=f"MWK-{len(rows) + 1:03d}",
                module_name="多唤醒-默认唤醒词冻结保护",
                command=switch_command if mode != "protocol" else "冻结词保护",
                steps=(
                    f"1. 当前配置中默认唤醒词 {default_wake} 已勾选冻结 "
                    f"2. 尝试切换到 {first_target} "
                    f"3. 验证 {default_wake} 仍保持生效，{first_target} / {second_target} 不生效"
                ),
                expected="冻结默认唤醒词后，切换操作不应把当前生效词切离默认词",
                runtime_values={
                    "multiWkeScenario": "frozen_default",
                    "mode": mode,
                    "defaultWakeWord": default_wake,
                    "switchCommand": switch_command,
                    "switchTargetWakeWord": first_target,
                    "protocolBytes": first_recv_proto,
                    "expectedActiveWakeWord": default_wake,
                    "expectedInactiveWakeWords": [first_target, second_target],
                },
                input_value="frozen_default",
                linkage="多唤醒冻结默认词正反例",
            )
        )

    if pack_args.get("wakeWordSave") is not None:
        wake_word_save = bool(pack_args.get("wakeWordSave"))
        expected_active_after_reboot = first_target if wake_word_save else default_wake
        expected_inactive_after_reboot = [default_wake, second_target] if wake_word_save else [first_target, second_target]
        persist_asserts = [item for item in config_base if item]
        persist_assert = assertion_text_from_direct("wakeWordSave", wake_word_save)
        if persist_assert:
            persist_asserts.append(persist_assert)
        rows.append(
            suite_row(
                case_id="MWK-SAVE-001",
                module_name="多唤醒-唤醒词掉电保持",
                test_type="功能验证",
                command=switch_command if mode != "protocol" else "协议切换后断电重启",
                priority="P0",
                steps=(
                    f"1. 当前多唤醒模式={mode}，先切换到 {first_target} "
                    f"2. 对设备执行断电重启 "
                    f"3. 验证重启后 {expected_active_after_reboot} 仍为当前生效唤醒词"
                ),
                expected=(
                    f"wakeWordSave={wake_word_save} 时，断电重启后"
                    f"{'保持切换后的当前唤醒词' if wake_word_save else '恢复默认唤醒词'}"
                ),
                executor="device",
                pack_args=merged_pack_args(),
                config_assert="；".join(persist_asserts),
                runtime_assert=runtime_assert_text(
                    {
                        "mode": mode,
                        "defaultWakeWord": default_wake,
                        "switchCommand": switch_command,
                        "restoreCommand": restore_command,
                        "wakeWordPersistTarget": first_target,
                        "protocolBytes": first_recv_proto,
                        "expectedActiveWakeWord": expected_active_after_reboot,
                        "expectedInactiveWakeWords": expected_inactive_after_reboot,
                    }
                ),
                raw_param="wakeWordSave",
                input_value=wake_word_save,
                linkage="多唤醒切换后掉电保持正反例",
            )
        )

    rows.append(_multi_wke_negative_config_row(pack_args))
    return rows


def make_wakeup_row(case_id: str, intent: str) -> Dict[str, str]:
    return suite_row(
        case_id=case_id,
        module_name="唤醒词识别",
        test_type="唤醒识别",
        command=intent,
        priority="P0",
        steps=f"1. 播放唤醒词 {intent} 2. 观察唤醒识别结果与唤醒耗时",
        expected="唤醒识别成功，且设备进入唤醒态",
        executor="device",
        pack_args={},
        config_assert=(
            f"_ver_list[0].asr_wakeup[*].intent contains {render_json(intent)}；"
            f"_ver_list[0].asr_cmds[*].intent contains {render_json(intent)} (可选路径)"
        ),
        runtime_assert=f"wake={intent}",
        raw_param="wakeupWord",
        input_value=intent,
        linkage="基础唤醒词全量验证",
    )


def pick_reply(item: Dict[str, Any]) -> str:
    reply_choices = split_choices(item.get("reply") or "")
    if reply_choices:
        return reply_choices[0]
    return str(item.get("reply") or "").strip()


def make_command_row(case_id: str, item: Dict[str, Any], raw_param: str = "releaseAlgoList[*].word") -> Dict[str, str]:
    intent = voice_item_text(item)
    snd_protocol = normalize_hex_protocol(item.get("snd_protocol") or item.get("sndProtocol"))
    rec_protocol = normalize_hex_protocol(item.get("rec_protocol") or item.get("recProtocol"))
    reply = pick_reply(item)
    reply_mode = str(item.get("reply_mode") or item.get("replyMode") or "").strip()
    config_parts = [f"_ver_list[0].asr_cmds[*].intent contains {render_json(intent)}"]
    if snd_protocol:
        config_parts.append(f"_ver_list[0].asr_cmds[*].snd_protocol contains {render_json(snd_protocol)}")
    if rec_protocol:
        config_parts.append(f"_ver_list[0].asr_cmds[*].rec_protocol contains {render_json(rec_protocol)}")
    if reply_mode:
        config_parts.append(f"_ver_list[0].asr_cmds[*].reply_mode contains {render_json(reply_mode)}")
    runtime_parts = [f"word={intent}"]
    if snd_protocol:
        runtime_parts.append(f"snd={snd_protocol}")
    if rec_protocol:
        runtime_parts.append(f"rec={rec_protocol}")
    if reply:
        runtime_parts.append(f"reply={reply}")
    return suite_row(
        case_id=case_id,
        module_name="命令词识别",
        test_type="功能验证",
        command=intent,
        expect_proto=snd_protocol,
        expect_reply=reply,
        reply_mode=reply_mode,
        priority="P0",
        steps=f"1. 唤醒设备 2. 播放命令词 {intent} 3. 校验识别、协议、播报ID与响应耗时",
        expected="命令词识别正确，协议与播报模式符合配置",
        executor="device",
        pack_args={},
        config_assert="；".join(config_parts),
        runtime_assert="；".join(runtime_parts),
        raw_param=raw_param,
        input_value=intent,
        linkage="基础命令词全量验证",
    )


def make_release_algo_field_row(case_id: str, parameter: str, value: Any) -> Dict[str, str]:
    normalized_proto = normalize_hex_protocol(value)
    field_meta = {
        "releaseAlgoList[*].sndProtocol": {
            "module": "指定发送协议验证",
            "config_field": "snd_protocol",
            "expected": "命令词发送协议与指定配置一致",
            "runtime_assert": f"snd={normalized_proto}",
            "expect_proto": normalized_proto,
            "reply_mode": "",
            "expect_reply": "",
        },
        "releaseAlgoList[*].recProtocol": {
            "module": "指定回传协议验证",
            "config_field": "rec_protocol",
            "expected": "命令词回传协议与指定配置一致",
            "runtime_assert": f"rec={normalized_proto}",
            "expect_proto": "",
            "reply_mode": "",
            "expect_reply": "",
        },
        "releaseAlgoList[*].replyMode": {
            "module": "播报模式验证",
            "config_field": "reply_mode",
            "expected": "播报模式与 playId 行为一致",
            "runtime_assert": f"replyMode={render_json(value)}",
            "expect_proto": "",
            "reply_mode": str(value or "").strip(),
            "expect_reply": "",
        },
        "releaseAlgoList[*].reply": {
            "module": "播报内容验证",
            "config_field": "reply",
            "expected": "设备返回预期播报内容，必要时人工核对播报文本",
            "runtime_assert": f"reply={render_json(value)}",
            "expect_proto": "",
            "reply_mode": "",
            "expect_reply": str(value or "").strip(),
        },
        "releaseAlgoList[*].type": {
            "module": "词条类型验证",
            "config_field": "type",
            "expected": "设备执行的词条类型与指定配置一致",
            "runtime_assert": f"type={render_json(value)}",
            "expect_proto": "",
            "reply_mode": "",
            "expect_reply": "",
        },
    }
    meta = field_meta[parameter]
    return suite_row(
        case_id=case_id,
        module_name=meta["module"],
        test_type="功能验证",
        command="",
        expect_proto=meta["expect_proto"],
        expect_reply=meta["expect_reply"],
        reply_mode=meta["reply_mode"],
        priority="P0",
        steps=f"1. 唤醒设备 2. 选择匹配 {parameter} 的命令词 3. 校验动态配置效果",
        expected=meta["expected"],
        executor="device",
        pack_args={parameter: value},
        config_assert=f"_ver_list[0].asr_cmds[*].{meta['config_field']} contains {render_json(normalized_proto or value)}",
        runtime_assert=meta["runtime_assert"],
        raw_param=parameter,
        input_value=normalized_proto or value,
        linkage="通过结构化词条改动触发的局部验证",
    )


def find_entry_by_intent(web_config: Dict[str, Any], intent: str) -> Optional[Dict[str, Any]]:
    for item in iter_voice_entries(web_config):
        if voice_item_text(item) == str(intent).strip():
            return item
    return None


def rows_from_release_algo_override(value: Any, web_config: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                continue
            entry_type = str(item.get("type") or "").strip()
            if "负性词" in entry_type:
                continue
            if entry_type == "唤醒词":
                intent = voice_item_text(item)
                if intent:
                    rows.append(make_wakeup_row(f"CHG-WAKE-{index:03d}", intent))
            else:
                if voice_item_text(item):
                    rows.append(make_command_row(f"CHG-CMD-{index:03d}", item))
    elif isinstance(value, str) and value.strip():
        matched = find_entry_by_intent(web_config, value.strip())
        if matched is not None:
            rows.append(make_command_row("CHG-CMD-001", matched))
        else:
            rows.append(
                suite_row(
                    case_id="CHG-CMD-001",
                    module_name="指定词条验证",
                    test_type="功能验证",
                    command=value.strip(),
                    priority="P0",
                    steps=f"1. 唤醒设备 2. 播放命令词 {value.strip()} 3. 观察识别结果",
                    expected="修改后的词条可被识别",
                    executor="device",
                    pack_args={"releaseAlgoList[*].word": value.strip()},
                    runtime_assert=f"word={value.strip()}",
                    raw_param="releaseAlgoList[*].word",
                    input_value=value.strip(),
                    linkage="通过结构化词条改动触发的局部验证",
                )
            )
    return rows


def build_core_rows(web_config: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None, case_prefix: str = "CORE") -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    timeout_cfg = firmware.get("timeout_config") or {}
    volume_cfg = firmware.get("volume_config") or {}
    version_text = current_firmware_version_text(web_config, metadata)
    timeout_value = int(timeout_cfg.get("time") or 0)
    volume_levels = volume_cfg.get("level") or []
    default_vol = int(volume_cfg.get("default") or 0)

    if version_text:
        rows.append(make_version_row(f"{case_prefix}-VERSION-001", version_text))
    if timeout_value > 0:
        rows.append(make_timeout_row(f"{case_prefix}-TIMEOUT-001", timeout_value))
    if default_vol > 0:
        rows.append(make_default_volume_row(f"{case_prefix}-DEFAULTVOL-001", default_vol))
    if volume_levels:
        # 默认音量必须在任何音量调节用例之前校验，避免被 volSave=true 的保持行为污染。
        rows.append(make_volume_row(f"{case_prefix}-VOLUME-001", len(volume_levels)))

    wake_index = 1
    cmd_index = 1
    for item in iter_voice_entries(web_config):
        if voice_entry_type(item) == "wakeup":
            rows.append(make_wakeup_row(f"{case_prefix}-WAKE-{wake_index:03d}", str(item.get("intent") or item.get("condition") or "").strip()))
            wake_index += 1
        else:
            rows.append(make_command_row(f"{case_prefix}-CMD-{cmd_index:03d}", item))
            cmd_index += 1
    return rows


def build_base_direct_rows(web_config: Dict[str, Any], case_prefix: str = "BASE") -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    context = build_voice_context(web_config)
    volume_cfg = firmware.get("volume_config") or {}
    general_cfg = firmware.get("general_config") or {}
    persisted_cfg = general_cfg.get("persisted") or {}
    welcome_cfg = firmware.get("welcome_config") or {}
    welcome_reply = str(welcome_cfg.get("reply") or context.get("welcome_reply") or "").strip()
    welcome_reply_mode = str(welcome_cfg.get("reply_mode") or "主").strip() or "主"
    speaker_cfg = ((firmware.get("custom_voice") or {}).get("speaker") or {})
    uart_cfg = firmware.get("uart_config") or {}
    mic_cfg = general_cfg.get("mic") or {}
    pa_cfg = firmware.get("pa_config") or {}
    max_volume = command_context_info(context, "最大音量")
    min_volume = command_context_info(context, "最小音量")

    max_overflow_reply = str(volume_cfg.get("adj_max_reply") or "").strip()
    if max_overflow_reply:
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-MAXOVERFLOW-001",
                "volMaxOverflow",
                max_overflow_reply,
                "音量边界播报",
                f"达到最大音量边界时播报 {max_overflow_reply}",
                command=max_volume["command"],
                expect_proto=max_volume["protocol"],
                expect_reply=max_overflow_reply,
                reply_mode=max_volume["reply_mode"],
                steps="1. 将设备音量调到最大档 2. 再次执行最大音量命令 3. 观察边界播报与配置是否一致",
                linkage="基础配置当前值验证：最大音量上溢播报",
            )
        )

    min_overflow_reply = str(volume_cfg.get("adj_min_reply") or "").strip()
    if min_overflow_reply:
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-MINOVERFLOW-001",
                "volMinOverflow",
                min_overflow_reply,
                "音量边界播报",
                f"达到最小音量边界时播报 {min_overflow_reply}",
                command=min_volume["command"],
                expect_proto=min_volume["protocol"],
                expect_reply=min_overflow_reply,
                reply_mode=min_volume["reply_mode"],
                steps="1. 将设备音量调到最小档 2. 再次执行最小音量命令 3. 观察边界播报与配置是否一致",
                linkage="基础配置当前值验证：最小音量下溢播报",
            )
        )

    volume_save = normalize_boolish(persisted_cfg.get("volume"))
    if volume_save is not None:
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-VOLSAVE-001",
                "volSave",
                volume_save,
                "音量掉电保持",
                "断电重启后保持当前音量" if volume_save else "断电重启后恢复默认音量",
                steps="1. 调整设备到非默认音量档 2. 断电重启设备 3. 观察重启后的音量是否与配置一致",
                linkage="基础配置当前值验证：音量掉电保持",
            )
        )

    wake_save = normalize_boolish(persisted_cfg.get("wakeup"))
    if wake_save is not None:
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-WAKESAVE-001",
                "wakeWordSave",
                wake_save,
                "唤醒词掉电保持",
                (
                    "wakeWordSave 当前值为开启；设备侧保持能力需在多唤醒专项中验证"
                    if wake_save
                    else "wakeWordSave 当前值为关闭；设备侧保持能力需在多唤醒专项中验证"
                ),
                test_type="重启恢复",
                steps="1. 解包产物并检查 wakeWordSave 配置 2. 需要设备侧验证时转入多唤醒专项执行",
                executor="自动-解包配置",
                method="自动",
                linkage="基础套件先做配置断言；设备侧保持能力依赖多唤醒场景",
                compat="config-only",
            )
        )

    broadcast_steps = "1. 唤醒设备 2. 观察欢迎播报 3. 结合配置断言确认当前播报参数或文案"
    for parameter, value, expected in [
        ("vcn", speaker_cfg.get("vcn"), f"欢迎播报使用当前发音人配置 {speaker_cfg.get('vcn')}；自动化仅验证配置生效与设备稳定性"),
        ("speed", speaker_cfg.get("speed"), f"欢迎播报使用当前语速配置 {speaker_cfg.get('speed')}"),
        ("vol", speaker_cfg.get("volume"), f"欢迎播报使用当前音量配置 {speaker_cfg.get('volume')}"),
        ("compress", speaker_cfg.get("compre_ratio"), f"欢迎播报使用当前压缩比配置 {speaker_cfg.get('compre_ratio')}"),
    ]:
        if value in (None, ""):
            continue
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "播报配置",
                expected,
                test_type="播报验证",
                expect_reply=welcome_reply,
                reply_mode=welcome_reply_mode,
                steps=broadcast_steps,
                linkage="基础配置当前值验证：欢迎播报参数",
            )
        )

    for parameter, value, title in [
        ("uportBaud", uart_cfg.get("uport_baud"), "协议串口波特率"),
        ("logLevel", general_cfg.get("log_level"), "日志级别"),
    ]:
        if value in (None, ""):
            continue
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "串口配置",
                f"{title} 当前值为 {value}，设备侧连通性与日志详细度需人工串口工具验证",
                test_type="串口参数验证",
                steps="1. 解包产物确认串口参数 2. 按对应端口和波特率连接串口工具 3. 观察日志/协议连通性",
                linkage="基础配置当前值验证：串口与日志参数",
            )
        )

    for parameter, value, title in [
        ("again", mic_cfg.get("again"), "MIC 模拟增益"),
        ("dgain", mic_cfg.get("dgain"), "MIC 数字增益"),
    ]:
        if value in (None, ""):
            continue
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "音频输入",
                f"{title} 当前值为 {value}；设备侧采集效果需在真实噪声场景人工确认",
                steps="1. 解包产物并核对当前音频输入参数 2. 需要设备侧验证时在真实噪声场景补充确认",
                executor="自动-解包配置",
                method="自动",
                linkage="基础套件先校验当前值；采集效果仍需场景化人工验证",
                compat="config-only",
            )
        )

    pa_enable = normalize_boolish(pa_cfg.get("enable"))
    pa_specs: List[tuple[str, Any, str, str]] = [
        ("paConfigEnable", pa_enable, "功放配置开关", "功放开关当前值为 {value}；设备侧电平行为需人工/仪器确认"),
        ("ctlIoPad", pa_cfg.get("ctl_io_pad"), "功放控制引脚组", "功放控制引脚组当前值为 {value}；设备侧引脚行为需人工/仪器确认"),
        ("ctlIoNum", pa_cfg.get("ctl_io_num"), "功放控制引脚号", "功放控制引脚号当前值为 {value}；设备侧引脚行为需人工/仪器确认"),
        ("holdTime", pa_cfg.get("hold_time"), "功放保持时长", "功放保持时长当前值为 {value}；设备侧保持时序需人工/仪器确认"),
        ("paConfigEnableLevel", pa_cfg.get("enable_level"), "功放使能电平", "功放使能电平当前值为 {value}；设备侧电平行为需人工/仪器确认"),
    ]
    for parameter, value, title, template in pa_specs:
        if value in (None, ""):
            continue
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "功放配置",
                template.format(value=value),
                steps="1. 解包产物并核对功放配置 2. 需要设备侧验证时结合外部硬件或仪器确认电平与时序",
                executor="自动-解包配置",
                method="自动",
                linkage="基础套件先校验当前值；硬件相关行为需人工/仪器补充验证",
                compat="config-only",
            )
        )

    return rows


def build_changed_direct_rows(web_config: Dict[str, Any], metadata: Dict[str, Any], case_prefix: str = "CHG") -> List[Dict[str, str]]:
    applied = dict(metadata.get("appliedOverrides") or {})
    if not applied:
        return []

    rows: List[Dict[str, str]] = []
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    context = build_voice_context(web_config)
    volume_cfg = firmware.get("volume_config") or {}
    general_cfg = firmware.get("general_config") or {}
    persisted_cfg = general_cfg.get("persisted") or {}
    welcome_cfg = firmware.get("welcome_config") or {}
    welcome_reply = str(welcome_cfg.get("reply") or context.get("welcome_reply") or "").strip()
    welcome_reply_mode = str(welcome_cfg.get("reply_mode") or "主").strip() or "主"
    max_volume = command_context_info(context, "最大音量")
    min_volume = command_context_info(context, "最小音量")

    def raw_value(key: str) -> Any:
        return applied.get(key)

    def boolish_value(key: str) -> Optional[bool]:
        return normalize_boolish(applied.get(key))

    def has_value(key: str) -> bool:
        value = raw_value(key)
        return value not in (None, "")

    if has_value("volMaxOverflow"):
        value = str(raw_value("volMaxOverflow"))
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-MAXOVERFLOW-001",
                "volMaxOverflow",
                value,
                "音量边界播报",
                f"达到最大音量边界时播报 {value}",
                command=max_volume["command"],
                expect_proto=max_volume["protocol"],
                expect_reply=value,
                reply_mode=max_volume["reply_mode"],
                steps="1. 将设备音量调到最大档 2. 再次执行最大音量命令 3. 观察边界播报与改值是否一致",
                linkage="改值验证：最大音量上溢播报；同时结合基础命令词用例观察是否引入副作用",
            )
        )

    if has_value("volMinOverflow"):
        value = str(raw_value("volMinOverflow"))
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-MINOVERFLOW-001",
                "volMinOverflow",
                value,
                "音量边界播报",
                f"达到最小音量边界时播报 {value}",
                command=min_volume["command"],
                expect_proto=min_volume["protocol"],
                expect_reply=value,
                reply_mode=min_volume["reply_mode"],
                steps="1. 将设备音量调到最小档 2. 再次执行最小音量命令 3. 观察边界播报与改值是否一致",
                linkage="改值验证：最小音量下溢播报；同时结合基础命令词用例观察是否引入副作用",
            )
        )

    for parameter, title, expected_true, expected_false, steps, linkage in [
        (
            "volSave",
            "音量掉电保持",
            "断电重启后保持当前音量",
            "断电重启后恢复默认音量",
            "1. 调整设备到非默认音量档 2. 断电重启设备 3. 观察重启后的音量是否符合改值",
            "改值验证：音量掉电保持",
        ),
        (
            "wakeWordSave",
            "唤醒词掉电保持",
            "断电重启后保持当前唤醒词；如未启用多唤醒则至少完成配置断言和基础功能稳定性验证",
            "断电重启后恢复默认唤醒词；如未启用多唤醒则至少完成配置断言和基础功能稳定性验证",
            "1. 如固件启用多唤醒则先切换到非默认唤醒词 2. 断电重启设备 3. 观察重启后当前生效唤醒词是否符合改值",
            "改值验证：唤醒词掉电保持；真正的运行态保持能力需结合多唤醒场景",
        ),
    ]:
        value = boolish_value(parameter)
        if value is None:
            continue
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                title,
                expected_true if value else expected_false,
                test_type="重启恢复",
                steps=steps,
                linkage=linkage,
            )
        )

    for parameter, title, expected in [
        ("vcn", "合成发音人", "欢迎播报使用当前发音人配置 {value}；自动化仅验证配置生效与设备稳定性"),
        ("speed", "合成语速", "欢迎播报使用当前语速配置 {value}"),
        ("vol", "合成音量", "欢迎播报使用当前音量配置 {value}"),
        ("compress", "播报音压缩比", "欢迎播报使用当前压缩比配置 {value}"),
    ]:
        if not has_value(parameter):
            continue
        value = raw_value(parameter)
        expect_reply = str(value) if parameter == "word" else welcome_reply
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "播报配置",
                expected.format(value=value),
                test_type="播报验证",
                expect_reply=expect_reply,
                reply_mode=welcome_reply_mode,
                steps="1. 唤醒设备 2. 观察欢迎播报 3. 结合配置断言和基础功能用例确认改值未引入异常",
                linkage=f"改值验证：{title}；无法稳定自动判音质时，至少验证配置生效且不影响设备正常运行",
            )
        )

    for parameter, title in [
        ("uportBaud", "协议串口波特率"),
        ("logLevel", "日志级别"),
    ]:
        if not has_value(parameter):
            continue
        value = raw_value(parameter)
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "串口配置",
                f"{title} 改为 {value}；需要同步本地串口/波特率后，确认基础唤醒与命令识别链路仍可运行",
                test_type="串口参数验证",
                steps="1. 解包产物确认串口参数 2. 按当前固件配置同步本地串口与波特率 3. 结合基础唤醒/命令词用例观察通信与识别链路是否正常",
                linkage=f"改值验证：{title}；重点观察是否导致通信断链、日志异常或基础功能异常",
            )
        )

    for parameter, title in [
        ("again", "MIC 模拟增益"),
        ("dgain", "MIC 数字增益"),
    ]:
        if not has_value(parameter):
            continue
        value = raw_value(parameter)
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "音频输入",
                f"{title} 改为 {value}；需结合真实场景确认识别效果且不能影响设备正常运行",
                steps="1. 解包产物确认音频输入参数 2. 结合基础唤醒/命令识别用例观察是否引入明显识别异常",
                executor="自动-解包配置",
                method="自动",
                linkage=f"改值验证：{title}；自动化先校验配置和基础功能稳定性，采集质量仍需场景化补充验证",
                compat="config-only",
            )
        )

    for parameter, title, template in [
        ("paConfigEnable", "功放配置开关", "功放配置开关当前值为 {value}；重点观察改值后设备播报链路和基础功能是否仍稳定"),
        ("ctlIoPad", "功放控制引脚组", "功放控制引脚组当前值为 {value}；需要外部硬件或仪器确认引脚行为"),
        ("ctlIoNum", "功放控制引脚号", "功放控制引脚号当前值为 {value}；需要外部硬件或仪器确认引脚行为"),
        ("holdTime", "功放保持时长", "功放保持时长当前值为 {value}；需要外部硬件或仪器确认时序"),
        ("paConfigEnableLevel", "功放使能电平", "功放使能电平当前值为 {value}；需要外部硬件或仪器确认电平"),
    ]:
        if not has_value(parameter):
            continue
        value = raw_value(parameter)
        rows.append(
            make_direct_assert_row(
                f"{case_prefix}-{parameter.upper()}-001",
                parameter,
                value,
                "功放配置",
                template.format(value=value),
                steps="1. 解包产物确认功放配置 2. 结合基础功能用例观察改值后设备是否仍能稳定启动与响应 3. 必要时配合硬件或仪器补充确认",
                executor="自动-解包配置",
                method="自动",
                linkage=f"改值验证：{title}；先验证配置生效与基础功能稳定性，再做硬件级观测",
                compat="config-only",
            )
        )

    return rows


def build_base_rows(web_config: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    return build_core_rows(web_config, metadata, case_prefix="BASE") + build_base_direct_rows(web_config, case_prefix="BASE")


def build_changed_rows(web_config: Dict[str, Any], metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    applied = dict(metadata.get("appliedOverrides") or {})
    rows: List[Dict[str, str]] = build_core_rows(web_config, metadata, case_prefix="CORE")
    base_covered = annotate_base_covered_rows(rows, web_config, metadata)
    firmware = (first_version(web_config).get("firmware") or {})
    voice_enabled = bool(((firmware.get("study_config") or {}).get("enable")))
    multi_enabled = bool(((firmware.get("multi_wakeup") or {}).get("enable")))

    if "defaultVol" in applied and "defaultVol" not in base_covered:
        rows.append(make_default_volume_row("CHG-DEFAULTVOL-001", int(applied["defaultVol"])))
    rows.extend(build_changed_direct_rows(web_config, metadata, case_prefix="CHG"))
    if applied.get("voiceRegEnable") or metadata.get("learnWords") or voice_enabled:
        rows.extend(make_voice_reg_rows(metadata, web_config))
    if applied.get("multiWkeEnable") or applied.get("releaseMultiWke") or applied.get("wakeWordSave") or multi_enabled:
        rows.extend(make_multi_wke_rows(metadata, web_config))

    if "releaseAlgoList" in applied:
        rows.extend(rows_from_release_algo_override(applied["releaseAlgoList"], web_config))

    for key in [
        "releaseAlgoList[*].word",
        "releaseAlgoList[*].extWord",
        "releaseAlgoList[*].children[*].extWord",
    ]:
        if key in applied and key not in base_covered:
            rows.extend(rows_from_release_algo_override(applied[key], web_config))

    dynamic_case_ids = {
        "releaseAlgoList[*].sndProtocol": "CHG-SNDPROTO-001",
        "releaseAlgoList[*].replyMode": "CHG-REPLYMODE-001",
        "releaseAlgoList[*].reply": "CHG-REPLY-001",
        "releaseAlgoList[*].recProtocol": "CHG-RECPROTO-001",
        "releaseAlgoList[*].type": "CHG-TYPE-001",
    }
    for key, case_id in dynamic_case_ids.items():
        if key in applied and key not in base_covered:
            rows.append(make_release_algo_field_row(case_id, key, applied[key]))

    if "multiWkeEnable" in applied:
        rows.append(make_bool_row("CHG-MULTIWKE-001", "multiWkeEnable", "多唤醒开关", bool(applied["multiWkeEnable"]), "multiWkeEnable 配置生效"))

    return rows


def dedupe_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        key = (row.get("测试类型"), row.get("命令词"), row.get("原始参数"), row.get("输入值"))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(row))
    return result


def build_profile_rows(profile: str, web_config: Dict[str, Any], metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    if profile == "voice-reg":
        rows = build_core_rows(web_config, metadata, case_prefix="CORE") + make_voice_reg_rows(metadata, web_config)
    elif profile == "multi-wke":
        rows = build_core_rows(web_config, metadata, case_prefix="CORE") + make_multi_wke_rows(metadata, web_config)
    elif profile == "changed":
        rows = build_changed_rows(web_config, metadata)
    else:
        rows = build_base_rows(web_config, metadata)

    if profile != "changed" and metadata.get("appliedOverrides"):
        annotate_base_covered_rows(rows, web_config, metadata)
    return dedupe_rows(rows)


def build_profile_payload(
    *,
    web_config: Dict[str, Any],
    profile: str,
    metadata: Dict[str, Any],
    selected_meta: Optional[Dict[str, Any]] = None,
    log_port: str = "",
    ctrl_port: str = "",
) -> Dict[str, Any]:
    selected = selected_from_inputs(selected_meta, metadata)
    rows = build_profile_rows(profile, web_config, metadata)
    device_info = ensure_device_info(web_config, selected, profile, log_port, ctrl_port)
    counters = Counter(row["测试类型"] for row in rows)
    return {
        "generatedAt": metadata.get("scalars", {}).get("generatedAt", ""),
        "profile": profile,
        "selected": selected,
        "metadata": metadata,
        "voiceContext": build_voice_context(web_config),
        "deviceInfo": device_info,
        "rows": rows,
        "statistics": {
            "caseCount": len(rows),
            "testTypeCounters": dict(counters),
        },
    }


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_HEADERS})


def write_readme(path: Path, payload: Dict[str, Any]) -> None:
    selected = payload.get("selected") or {}
    stats = payload.get("statistics") or {}
    lines = [
        "# ListenAI Profile Suite",
        "",
        f"- profile: `{payload.get('profile')}`",
        f"- target: `{selected.get('productLabel', '')}` | `{selected.get('moduleBoard', '')}` | `{selected.get('language', '')}` | `{selected.get('versionLabel', '')}`",
        f"- cases: `{stats.get('caseCount', 0)}`",
        f"- testTypes: `{json.dumps(stats.get('testTypeCounters') or {}, ensure_ascii=False)}`",
        "",
        "| 用例编号 | 测试类型 | 命令词 | 原始参数 | 运行断言 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in payload.get("rows") or []:
        lines.append(
            f"| `{row.get('用例编号', '')}` | {row.get('测试类型', '')} | {row.get('命令词', '')} | `{row.get('原始参数', '')}` | {row.get('运行断言', '')} |"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_catalog_markdown(path: Path, payload: Dict[str, Any]) -> None:
    selected = payload.get("selected") or {}
    lines = [
        "# ListenAI 用例目录",
        "",
        f"- profile: `{payload.get('profile')}`",
        f"- target: `{selected.get('productLabel', '')}` | `{selected.get('moduleBoard', '')}` | `{selected.get('language', '')}` | `{selected.get('versionLabel', '')}`",
        "",
        "| 用例编号 | 功能模块 | 测试类型 | 命令词 | 打包参数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in payload.get("rows") or []:
        lines.append(
            f"| `{row.get('用例编号', '')}` | {row.get('功能模块', '')} | {row.get('测试类型', '')} | {row.get('命令词', '')} | {row.get('打包参数', '')} |"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def export_suite(out_dir: Path, payload: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "testCases.csv", payload.get("rows") or [])
    (out_dir / "deviceInfo_generated.json").write_text(json.dumps(payload.get("deviceInfo") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "executable_cases.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_readme(out_dir / "README.md", payload)


def export_catalog(json_out: Path, md_out: Path, payload: Dict[str, Any]) -> None:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_catalog_markdown(md_out, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build profile-based ListenAI case catalogs and suites from one package.")
    parser.add_argument("--package-zip", default="", help="package zip with web_config.json and optional validation_params.txt")
    parser.add_argument("--web-config", default="", help="direct web_config.json path")
    parser.add_argument("--profile", choices=["auto", "base", "changed", "voice-reg", "multi-wke"], default="auto")
    parser.add_argument("--catalog-only", action="store_true", help="only write catalog json/md")
    parser.add_argument("--out-dir", default="", help="suite output dir")
    parser.add_argument("--json-out", default="", help="catalog json output")
    parser.add_argument("--md-out", default="", help="catalog markdown output")
    parser.add_argument("--override", action="append", default=[], help="extra overrides in KEY=VALUE form; merged over validation_params.txt")
    parser.add_argument("--port", default=DEFAULT_LOG_PORT, help="写入 deviceInfo_generated.json 的默认日志串口")
    parser.add_argument("--ctrl-port", default=DEFAULT_CTRL_PORT, help="写入 deviceInfo_generated.json 的默认上电控制串口")
    return parser


def merged_metadata(package_zip: str, overrides: Sequence[str]) -> Dict[str, Any]:
    metadata = load_validation_metadata(package_zip) if package_zip else {
        "scalars": {},
        "sections": {},
        "appliedOverrides": {},
        "learnWords": [],
        "finalRelease": {},
        "variantId": "",
        "variantTitle": "",
        "comments": "",
    }
    if overrides:
        merged = dict(metadata.get("appliedOverrides") or {})
        merged.update(parse_override_args(list(overrides)))
        metadata["appliedOverrides"] = merged
    return metadata


def main() -> int:
    args = build_parser().parse_args()
    web_config = load_web_config(args.package_zip, args.web_config)
    metadata = merged_metadata(args.package_zip, args.override)
    profile = args.profile
    if profile == "auto":
        profile = infer_profile_name(metadata.get("appliedOverrides") or {}, metadata.get("learnWords") or [])
    payload = build_profile_payload(
        web_config=web_config,
        profile=profile,
        metadata=metadata,
        log_port=args.port,
        ctrl_port=args.ctrl_port,
    )

    if args.catalog_only:
        json_out = Path(args.json_out or "result/listenai_profile_case_catalog.json")
        md_out = Path(args.md_out or "result/listenai_profile_case_catalog.md")
        export_catalog(json_out, md_out, payload)
        print(f"json_out : {json_out.resolve()}")
        print(f"md_out   : {md_out.resolve()}")
        print(f"profile  : {profile}")
        print(f"cases    : {payload['statistics']['caseCount']}")
        return 0

    out_dir = Path(args.out_dir or "result/listenai_profile_suite")
    export_suite(out_dir, payload)
    print(f"out_dir  : {out_dir.resolve()}")
    print(f"profile  : {profile}")
    print(f"cases    : {payload['statistics']['caseCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
