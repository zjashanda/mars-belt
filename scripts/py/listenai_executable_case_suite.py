import argparse
import csv
import json
import os
import platform
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from listenai_task_support import RUNTIME_ROOT, TASKS_ROOT, load_global_audio_card_config, load_global_tts_config


DEFAULT_TEST_CATALOG = str(RUNTIME_ROOT / "catalog" / "listenai_test_case_catalog.json")
DEFAULT_OUT_DIR = str(TASKS_ROOT / "listenai_executable_suite")
DEFAULT_LOG_PORT = "/dev/ttyACM0" if platform.system() == "Linux" else "COM14"
DEFAULT_CTRL_PORT = "/dev/ttyACM4" if platform.system() == "Linux" else "COM15"


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


CSV_HEADERS = [
    "用例编号",
    "功能模块",
    "测试类型",
    "命令词",
    "功能类型",
    "期望协议",
    "期望播报",
    "播报模式",
    "优先级",
    "测试方法",
    "前置条件",
    "测试步骤",
    "预期结果",
    "执行器",
    "打包参数",
    "配置断言",
    "运行断言",
    "原始参数",
    "输入值",
    "联动说明",
    "voiceTestLite适配",
]


MANUAL_PARAMS = {
    "again",
    "dgain",
    "uportUart",
    "uportBaud",
    "traceUart",
    "traceBaud",
    "logLevel",
    "paConfigEnable",
    "ctlIoPad",
    "ctlIoNum",
    "holdTime",
    "paConfigEnableLevel",
    "algoViewMode",
    "protocolConfig",
}


VOICE_PARTIAL_PARAMS = {
    "timeout",
    "volLevel",
    "defaultVol",
    "volMaxOverflow",
    "volMinOverflow",
    "wakeWordSave",
    "volSave",
    "vcn",
    "speed",
    "vol",
    "compress",
    "word",
    "sensitivity",
    "voiceRegEnable",
    "multiWkeEnable",
    "multiWkeMode",
}


DIRECT_ASSERT_PATHS = {
    "firmwareVersion": [{"path": "firmware.general_config.version", "op": "eq"}],
    "timeout": [{"path": "firmware.timeout_config.time", "op": "eq"}],
    "volLevel": [{"path": "firmware.volume_config.level", "op": "len_eq"}],
    "defaultVol": [{"path": "firmware.volume_config.default", "op": "eq"}],
    "volMaxOverflow": [{"path": "firmware.volume_config.adj_max_reply", "op": "eq"}],
    "volMinOverflow": [{"path": "firmware.volume_config.adj_min_reply", "op": "eq"}],
    "again": [{"path": "firmware.general_config.mic.again", "op": "eq"}],
    "dgain": [{"path": "firmware.general_config.mic.dgain", "op": "eq"}],
    "uportUart": [{"path": "firmware.uart_config.uport_uart", "op": "eq"}],
    "uportBaud": [{"path": "firmware.uart_config.uport_baud", "op": "eq"}],
    "traceUart": [{"path": "firmware.uart_config.trace_uart", "op": "eq"}],
    "traceBaud": [{"path": "firmware.uart_config.trace_baud", "op": "eq"}],
    "logLevel": [{"path": "firmware.general_config.log_level", "op": "eq"}],
    "wakeWordSave": [{"path": "firmware.general_config.persisted.wakeup", "op": "eq"}],
    "volSave": [{"path": "firmware.general_config.persisted.volume", "op": "eq"}],
    "vcn": [{"path": "firmware.custom_voice.speaker.vcn", "op": "eq"}],
    "speed": [{"path": "firmware.custom_voice.speaker.speed", "op": "eq"}],
    "vol": [{"path": "firmware.custom_voice.speaker.volume", "op": "eq"}],
    "compress": [{"path": "firmware.custom_voice.speaker.compre_ratio", "op": "eq"}],
    "word": [{"path": "firmware.welcome_config.reply", "op": "eq"}],
    "paConfigEnable": [{"path": "firmware.pa_config.enable", "op": "eq"}],
    "ctlIoPad": [{"path": "firmware.pa_config.ctl_io_pad", "op": "eq"}],
    "ctlIoNum": [{"path": "firmware.pa_config.ctl_io_num", "op": "eq"}],
    "holdTime": [{"path": "firmware.pa_config.hold_time", "op": "eq"}],
    "paConfigEnableLevel": [{"path": "firmware.pa_config.enable_level", "op": "eq"}],
    "voiceRegEnable": [{"path": "firmware.study_config.enable", "op": "eq"}],
    "multiWkeEnable": [{"path": "firmware.multi_wakeup.enable", "op": "eq"}],
    "multiWkeMode": [{"path": "firmware.multi_wakeup.mode", "op": "eq", "optional": True}],
}


STRUCT_ASSERT_PATHS = {
    "releaseAlgoList[*].word": [
        {"path": "_ver_list[0].asr_cmds[*].intent", "op": "contains"},
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].keyword", "op": "contains", "optional": True},
    ],
    "releaseAlgoList[*].extWord": [{"path": "_ver_list[0].asr_cmds[*].expand_words[*].keyword", "op": "contains"}],
    "releaseAlgoList[*].type": [{"path": "_ver_list[0].asr_cmds[*].type", "op": "contains"}],
    "releaseAlgoList[*].reply": [{"path": "_ver_list[0].asr_cmds[*].reply", "op": "contains"}],
    "releaseAlgoList[*].replyMode": [{"path": "_ver_list[0].asr_cmds[*].reply_mode", "op": "contains"}],
    "releaseAlgoList[*].sndProtocol": [{"path": "_ver_list[0].asr_cmds[*].snd_protocol", "op": "contains"}],
    "releaseAlgoList[*].recProtocol": [{"path": "_ver_list[0].asr_cmds[*].rec_protocol", "op": "contains"}],
    "releaseAlgoList[*].children[*].extWord": [{"path": "_ver_list[0].asr_cmds[*].expand_words[*].keyword", "op": "contains"}],
    "releaseMultiWke.wkelist[*].condition": [{"path": "firmware.multi_wakeup.switch_list[*].word", "op": "contains", "optional": True}],
    "releaseMultiWke.wkelist[*].reply": [{"path": "firmware.multi_wakeup.switch_list[*].reply", "op": "contains", "optional": True}],
    "releaseMultiWke.wkelist[*].sndProtocol": [{"path": "firmware.multi_wakeup.switch_list[*].snd_protocol", "op": "contains", "optional": True}],
    "releaseMultiWke.wkelist[*].recProtocol": [{"path": "firmware.multi_wakeup.switch_list[*].rec_protocol", "op": "contains", "optional": True}],
    "releaseMultiWke.wkelist[*].isFrozen": [{"path": "firmware.multi_wakeup.switch_list[*].frozen", "op": "contains", "optional": True}],
    "releaseDepthList[*].pinyin": [
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].keyword_pinyin", "op": "contains"},
        {"path": "_ver_list[0].asr_wakeup[*].expand_words[*].keyword_pinyin", "op": "contains", "optional": True},
    ],
    "releaseDepthList[*].decEnable": [
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].dec_enable", "op": "contains"},
        {"path": "_ver_list[0].asr_wakeup[*].expand_words[*].dec_enable", "op": "contains", "optional": True},
    ],
    "releaseDepthList[*].decThreshold": [
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].dec_threshold", "op": "contains"},
        {"path": "_ver_list[0].asr_wakeup[*].expand_words[*].dec_threshold", "op": "contains", "optional": True},
    ],
    "releaseDepthList[*].e2eEnable": [
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].e2e_enable", "op": "contains"},
        {"path": "_ver_list[0].asr_wakeup[*].expand_words[*].e2e_enable", "op": "contains", "optional": True},
    ],
    "releaseDepthList[*].e2eThreshold": [
        {"path": "_ver_list[0].asr_cmds[*].expand_words[*].e2e_threshold", "op": "contains"},
        {"path": "_ver_list[0].asr_wakeup[*].expand_words[*].e2e_threshold", "op": "contains", "optional": True},
    ],
    "releaseDepthList[*].embeddedEnable": [{"path": "_ver_list[0].asr_wakeup[*].expand_words[*].main_e2e_enable", "op": "contains", "optional": True}],
    "releaseDepthList[*].embeddedThreshold": [{"path": "_ver_list[0].asr_wakeup[*].expand_words[*].main_e2e_threshold", "op": "contains", "optional": True}],
    "releaseDepthList[*].asrFreeEnable": [{"path": "_ver_list[0].asr_cmds[*].expand_words[*].free_enable", "op": "contains", "optional": True}],
    "releaseDepthList[*].asrFreeThreshold": [{"path": "_ver_list[0].asr_cmds[*].expand_words[*].free_threshold", "op": "contains", "optional": True}],
    "releaseDepthList[*].type": [{"path": "_ver_list[0].asr_cmds[*].type", "op": "contains"}],
    "releaseDepthList[*].category": [{"path": "_ver_list[0].asr_cmds[*].type", "op": "contains"}],
}


def display_multi_wke_mode(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "loop": "循环切换",
        "specified": "指定切换",
        "protocol": "协议切换",
    }
    return mapping.get(text, text)


def parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(value)


def multi_wke_control_path(type_name: str, field_name: str) -> str:
    mapping = {
        "query": "query_info",
        "restore": "restore_info",
        "switch": "switch_info",
    }
    key = mapping.get(str(type_name or "").strip())
    if not key:
        return ""
    return f"firmware.multi_wakeup.switch_control.{key}.{field_name}"


def normalize_multi_wke_common_type(item: Dict[str, Any]) -> str:
    text = str(item.get("type") or "").strip()
    mapping = {
        "query": "query",
        "查询": "query",
        "restore": "restore",
        "恢复": "restore",
        "switch": "switch",
        "切换": "switch",
    }
    return mapping.get(text, text)


def build_multi_wke_special_assertions(parameter: str, overrides: Dict[str, Any], target_value: Any) -> List[Dict[str, Any]]:
    assertions: List[Dict[str, Any]] = []
    release_multi = overrides.get("releaseMultiWke")

    if parameter in {"releaseMultiWke.common[*].condition", "releaseMultiWke.common[*].reply"}:
        if not isinstance(release_multi, dict):
            return assertions
        field_name = "word" if parameter.endswith(".condition") else "reply"
        value_key = "condition" if field_name == "word" else "reply"
        for item in release_multi.get("common") or []:
            if not isinstance(item, dict):
                continue
            path = multi_wke_control_path(normalize_multi_wke_common_type(item), field_name)
            expected = item.get(value_key)
            if path and expected is not None:
                assertions.append({"path": path, "op": "eq", "expected": expected})
        return assertions

    if parameter == "releaseMultiWke.wkelist[*].isDefault":
        bool_value = parse_boolish(target_value)
        if bool_value:
            return [{"path": "firmware.multi_wakeup.switch_list[*].special_type", "op": "contains", "expected": "默认唤醒词", "optional": True}]
        return assertions

    return assertions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export ListenAI parameter cases into an executable suite.")
    parser.add_argument("--test-catalog-json", default=DEFAULT_TEST_CATALOG, help="listenai_test_case_catalog json")
    parser.add_argument("--package-zip", default="", help="已打包固件 zip，优先读取其中的 web_config.json 作为运行上下文")
    parser.add_argument("--web-config", default="", help="直接指定 web_config.json")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="输出目录")
    parser.add_argument("--port", default=DEFAULT_LOG_PORT, help="写入 deviceInfo_generated.json 的默认日志串口")
    parser.add_argument("--ctrl-port", default=DEFAULT_CTRL_PORT, help="写入 deviceInfo_generated.json 的默认上电控制串口")
    return parser


def read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def ensure_dir(path: str) -> Path:
    out_dir = Path(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def load_web_config(package_zip: str, web_config_path: str) -> Optional[Dict[str, Any]]:
    if web_config_path:
        return read_json(web_config_path)

    if not package_zip:
        return None

    with zipfile.ZipFile(package_zip) as zf:
        for name in zf.namelist():
            if name.endswith("web_config.json"):
                return json.loads(zf.read(name).decode("utf-8"))
    raise RuntimeError(f"在 {package_zip} 中没有找到 web_config.json")


def first_version(web_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not web_config:
        return {}
    versions = web_config.get("_ver_list") or []
    if not versions:
        return {}
    return versions[0]


def split_choices(text: str) -> List[str]:
    if not text:
        return []
    parts: List[str] = []
    for raw in str(text).replace("|", "/").split("/"):
        item = raw.strip()
        if item:
            parts.append(item)
    return parts


def build_voice_context(web_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "wakeup_word": "小聆小聆",
        "sample_command": "打开风扇",
        "sample_protocol": "",
        "sample_reply": "",
        "timeout_reply": "",
        "welcome_reply": "",
        "reply_mode": "主",
        "commands_by_type": {},
        "negative_wakeup_words": [],
        "negative_command_words": [],
        "device_info": {},
    }

    if not web_config:
        return context

    ver = first_version(web_config)
    commands = ver.get("asr_cmds") or []
    wake_entries = [item for item in commands if item.get("type") == "唤醒词"]
    if wake_entries:
        wake_entry = wake_entries[0]
        context["wakeup_word"] = wake_entry.get("intent") or context["wakeup_word"]
        context["reply_mode"] = wake_entry.get("reply_mode") or context["reply_mode"]

    command_entries = [item for item in commands if item.get("type") == "命令词" and item.get("intent")]
    if command_entries:
        sample = command_entries[0]
        context["sample_command"] = sample.get("intent") or context["sample_command"]
        context["sample_protocol"] = sample.get("snd_protocol") or ""
        reply_choices = split_choices(sample.get("reply") or "")
        context["sample_reply"] = reply_choices[0] if reply_choices else (sample.get("reply") or "")
        context["reply_mode"] = sample.get("reply_mode") or context["reply_mode"]

    firmware = ver.get("firmware") or {}
    timeout_cfg = firmware.get("timeout_config") or {}
    welcome_cfg = firmware.get("welcome_config") or {}
    context["timeout_reply"] = timeout_cfg.get("reply") or ""
    context["welcome_reply"] = welcome_cfg.get("reply") or ""

    commands_by_type: Dict[str, Dict[str, Any]] = {}
    for item in commands:
        item_type = item.get("type")
        if item_type and item_type not in commands_by_type:
            commands_by_type[item_type] = item
    context["commands_by_type"] = commands_by_type

    wake_negative: List[str] = []
    cmd_negative: List[str] = []
    for item in commands:
        entry_type = item.get("type")
        for expand in item.get("expand_words") or []:
            keyword = expand.get("keyword")
            if not keyword:
                continue
            if entry_type == "唤醒词负性词":
                wake_negative.append(keyword)
            if entry_type == "命令词负性词":
                cmd_negative.append(keyword)
    context["negative_wakeup_words"] = wake_negative
    context["negative_command_words"] = cmd_negative
    context["device_info"] = build_device_info_template(web_config, context)
    return context


def build_spell_map_from_web_config(ver: Dict[str, Any]) -> Dict[str, str]:
    spell_map: Dict[str, str] = {}
    for section in ["asr_cmds", "asr_wakeup"]:
        for item in ver.get(section) or []:
            pinyin = item.get("pinyin")
            intent = item.get("intent")
            if pinyin and intent:
                spell_map[pinyin] = intent
            for expand in item.get("expand_words") or []:
                keyword_pinyin = expand.get("keyword_pinyin")
                keyword = expand.get("keyword")
                if keyword_pinyin and keyword:
                    spell_map[keyword_pinyin] = keyword
    return spell_map


def build_device_info_template(web_config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    ver = first_version(web_config)
    firmware = ver.get("firmware") or {}
    commands = ver.get("asr_cmds") or []
    wakeup_word = context.get("wakeup_word") or "小聆小聆"

    word_list: List[str] = [wakeup_word]
    kw2protocol: Dict[str, str] = {}
    protocol_groups: Dict[str, List[str]] = defaultdict(list)
    for item in commands:
        intent = item.get("intent")
        if intent and intent not in word_list:
            word_list.append(intent)
        snd_protocol = item.get("snd_protocol")
        if intent and snd_protocol:
            kw2protocol[intent] = snd_protocol
            protocol_groups[snd_protocol].append(intent)

    absorb: Dict[str, List[str]] = {}
    for intents in protocol_groups.values():
        if len(intents) < 2:
            continue
        for intent in intents:
            absorb[intent] = [item for item in intents if item != intent]

    speaker = ((firmware.get("custom_voice") or {}).get("speaker") or {})
    uart_cfg = firmware.get("uart_config") or {}
    trace_baud = uart_cfg.get("trace_baud", 115200)
    uport_baud = uart_cfg.get("uport_baud", 9600)
    global_tts = load_global_tts_config()
    global_audio = load_global_audio_card_config()

    return {
        "wakeupWord": wakeup_word,
        "wordList": word_list,
        "kw2protocol": kw2protocol,
        "spell2zh": build_spell_map_from_web_config(ver),
        "absorb": absorb,
        "projectInfo": "ListenAI参数验证_取暖器_3021_中文_通用垂类",
        "commandRandom": 0,
        "protocolPort": "",
        "protocolBaud": int(uport_baud or 9600),
        "uartConfig": {
            "uportUart": uart_cfg.get("uport_uart"),
            "uportBaud": int(uport_baud or 9600),
            "traceUart": uart_cfg.get("trace_uart"),
            "traceBaud": int(trace_baud or 115200),
        },
        "ttsConfig": {
            "app_id": str(global_tts.get("app_id") or ""),
            "api_key": str(global_tts.get("api_key") or ""),
            "vcn": str(global_tts.get("vcn") or speaker.get("vcn", "x2_xiaoye")),
            "speed": str(global_tts.get("speed") or speaker.get("speed", 50)),
            "pitch": str(global_tts.get("pitch") or "50"),
            "volume": str(global_tts.get("volume") or speaker.get("volume", 100)),
        },
        "deviceListInfo": {
            "cskApLog": {
                "port": "",
                "baudRate": trace_baud,
                "regex": {
                    "rebootReason": ".*Boot Reason: (.*)",
                    "endLine": "",
                    "wakeKw": "",
                    "asrKw": "",
                    "sendMsg": "",
                    "recvMsg": "",
                    "playId": "",
                    "volume": "",
                },
            },
            "audioCard": {
                "deviceKey": str(global_audio.get("deviceKey") or ""),
                "useDefault": as_bool(global_audio.get("useDefault"), False),
                "fallbackToDefault": as_bool(global_audio.get("fallbackToDefault"), True),
                "name": str(global_audio.get("name") or ""),
                "backendTarget": str(global_audio.get("backendTarget") or ""),
                "lastError": str(global_audio.get("lastError") or ""),
            },
        },
        "pretestConfig": {
            "enabled": False,
            "ctrlPort": "",
            "ctrlBaudRate": 115200,
            "powerOnCmds": ["uut-switch2.off", "uut-switch1.off", "uut-switch1.on"],
            "cmdDelay": 0.3,
            "bootWait": 5,
        },
    }


def apply_runtime_ports(device_info: Dict[str, Any], log_port: str = "", ctrl_port: str = "") -> Dict[str, Any]:
    device_list = device_info.setdefault("deviceListInfo", {})
    csk = device_list.setdefault("cskApLog", {})
    csk["port"] = str(log_port or csk.get("port") or DEFAULT_LOG_PORT)
    csk["baudRate"] = int(csk.get("baudRate") or 115200)
    regex = csk.setdefault("regex", {})
    for key, value in {
        "rebootReason": r".*Boot Reason: (.*)",
        "endLine": "",
        "wakeKw": "",
        "asrKw": "",
        "sendMsg": "",
        "recvMsg": "",
        "playId": "",
        "volume": "",
    }.items():
        regex.setdefault(key, value)

    audio_defaults = load_global_audio_card_config()
    audio_card = device_list.setdefault("audioCard", {})
    audio_card["deviceKey"] = str(audio_card.get("deviceKey") or audio_defaults.get("deviceKey") or "")
    audio_card["useDefault"] = as_bool(
        audio_card.get("useDefault"),
        as_bool(audio_defaults.get("useDefault"), False),
    )
    audio_card["fallbackToDefault"] = as_bool(
        audio_card.get("fallbackToDefault"),
        as_bool(audio_defaults.get("fallbackToDefault"), True),
    )
    audio_card["name"] = str(audio_card.get("name") or audio_defaults.get("name") or "")
    audio_card["backendTarget"] = str(audio_card.get("backendTarget") or audio_defaults.get("backendTarget") or "")
    audio_card["lastError"] = str(audio_card.get("lastError") or audio_defaults.get("lastError") or "")

    pretest = device_info.setdefault("pretestConfig", {})
    selected_ctrl_port = str(ctrl_port or pretest.get("ctrlPort") or DEFAULT_CTRL_PORT)
    pretest["enabled"] = bool(selected_ctrl_port)
    pretest["ctrlPort"] = selected_ctrl_port
    pretest["ctrlBaudRate"] = int(pretest.get("ctrlBaudRate") or 115200)
    pretest.setdefault("powerOnCmds", ["uut-switch2.off", "uut-switch1.off", "uut-switch1.on"])
    pretest.setdefault("cmdDelay", 0.3)
    pretest.setdefault("bootWait", 5)
    return device_info


def base_priority(case: Dict[str, Any], parameter: str) -> str:
    if case.get("case_type") == "negative":
        return "P0"
    if parameter in {"timeout", "volLevel", "defaultVol", "uportBaud", "traceBaud", "wakeWordSave", "volSave"}:
        return "P0"
    if case.get("scope") == "dependency":
        return "P0"
    if case.get("scope") == "direct":
        return "P1"
    return "P2"


def config_assertions_for_case(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    parameter = case.get("parameter") or ""
    overrides = case.get("config_change") or {}
    target_value = case.get("input_value")
    if parameter in overrides and (case.get("scope") == "dependency" or isinstance(target_value, (dict, list))):
        target_value = overrides.get(parameter)
    if parameter == "volLevel":
        target_value = int(case.get("input_value"))
    if parameter == "multiWkeMode":
        target_value = display_multi_wke_mode(target_value)

    special_asserts = build_multi_wke_special_assertions(parameter, overrides, target_value)
    if special_asserts:
        return special_asserts

    mappings = DIRECT_ASSERT_PATHS.get(parameter) or STRUCT_ASSERT_PATHS.get(parameter) or []
    assertions: List[Dict[str, Any]] = []
    if mappings:
        for mapping in mappings:
            assertion = dict(mapping)
            assertion["expected"] = target_value
            assertions.append(assertion)
    elif overrides and case.get("scope") == "dependency":
        for key, value in overrides.items():
            direct = DIRECT_ASSERT_PATHS.get(key)
            if not direct:
                continue
            if key == "multiWkeMode":
                value = display_multi_wke_mode(value)
            for mapping in direct:
                assertion = dict(mapping)
                assertion["expected"] = value
                assertion["source_key"] = key
                assertions.append(assertion)
    return assertions


def pick_runtime_command(parameter: str, context: Dict[str, Any]) -> Tuple[str, str, str, str]:
    wake_word = context.get("wakeup_word", "")
    sample_command = context.get("sample_command", "")
    sample_protocol = context.get("sample_protocol", "")
    sample_reply = context.get("sample_reply", "")
    reply_mode = context.get("reply_mode", "主")
    commands_by_type = context.get("commands_by_type") or {}

    def command_info(command_type: str) -> Tuple[str, str, str, str]:
        info = commands_by_type.get(command_type) or {}
        intent = info.get("intent") or sample_command
        protocol = info.get("snd_protocol") or sample_protocol
        reply = split_choices(info.get("reply") or "")
        reply_text = reply[0] if reply else (info.get("reply") or sample_reply)
        return intent, protocol, reply_text, info.get("reply_mode") or reply_mode

    if parameter == "timeout":
        return wake_word, "", context.get("timeout_reply", ""), "主"
    if parameter in {"volLevel", "defaultVol", "volMaxOverflow"}:
        return command_info("最大音量")
    if parameter == "volMinOverflow":
        return command_info("最小音量")
    if parameter in {"vol", "volSave"}:
        return command_info("增大音量")
    if parameter in {"wakeWordSave", "multiWkeEnable", "multiWkeMode"}:
        return wake_word, "", "", "主"
    if parameter in {"vcn", "speed", "compress", "word"}:
        return wake_word, "", context.get("welcome_reply", ""), "主"
    if parameter.startswith("releaseAlgoList"):
        return sample_command, sample_protocol, sample_reply, reply_mode
    if parameter.startswith("releaseDepthList"):
        return sample_command, sample_protocol, sample_reply, reply_mode
    if parameter.startswith("releaseRegist"):
        return wake_word, "", "", "主"
    if parameter.startswith("releaseMultiWke"):
        return wake_word, "", "", "主"
    return sample_command, sample_protocol, sample_reply, reply_mode


def automation_level(case: Dict[str, Any], parameter: str, assertions: Sequence[Dict[str, Any]]) -> Tuple[str, str, str]:
    if case.get("case_type") == "negative":
        return "页面/接口校验", "自动-规则校验", "否"
    if parameter in MANUAL_PARAMS:
        return "人工-设备/仪器", "人工", "否"
    if parameter in VOICE_PARTIAL_PARAMS:
        return "半自动-设备", "自动+人工", "部分"
    if parameter.startswith("releaseAlgoList"):
        return "设备自动化", "自动", "是"
    if parameter.startswith("releaseDepthList"):
        return "半自动-设备", "自动+人工", "部分"
    if parameter.startswith("releaseRegist") or parameter.startswith("releaseMultiWke"):
        return "半自动-设备", "自动+人工", "部分"
    if assertions:
        return "自动-解包配置", "自动", "否"
    return "人工分析", "人工", "否"


def classify_test_type(parameter: str, case: Dict[str, Any]) -> str:
    if case.get("case_type") == "negative":
        return "配置约束校验"
    if parameter == "timeout":
        return "超时退出"
    if parameter in {"volLevel", "defaultVol", "volMaxOverflow", "volMinOverflow", "volSave"}:
        return "功能验证"
    if parameter in {"uportUart", "uportBaud", "traceUart", "traceBaud", "logLevel"}:
        return "串口参数验证"
    if parameter in {"wakeWordSave"}:
        return "重启恢复"
    if parameter in {"vcn", "speed", "vol", "compress", "word"}:
        return "播报验证"
    if parameter.startswith("releaseAlgoList"):
        return "命令词识别"
    if parameter.startswith("releaseDepthList"):
        return "重复稳定性"
    if parameter.startswith("releaseRegist"):
        return "功能验证"
    if parameter.startswith("releaseMultiWke"):
        return "功能验证"
    return "功能验证"


def module_name(case: Dict[str, Any]) -> str:
    mapping = {
        "基础配置": "基础参数",
        "音频输入配置": "音频输入",
        "串口配置": "串口配置",
        "掉电配置": "掉电保持",
        "播报配置": "播报配置",
        "功放配置": "功放配置",
        "算法配置": "算法配置",
        "算法词条": "词条配置",
        "自学习/声纹": "自学习配置",
        "自学习阶段配置": "自学习阶段",
        "多唤醒": "多唤醒",
        "深度调优": "深度调优",
        "跨参数联动": "跨参数联动",
    }
    return mapping.get(case.get("group") or "", case.get("group") or "参数验证")


def package_overrides_text(case: Dict[str, Any]) -> str:
    return render_json(case.get("config_change") or {})


def assertion_text(assertions: Sequence[Dict[str, Any]]) -> str:
    if not assertions:
        return ""
    parts = []
    for item in assertions:
        source_key = f" <- {item['source_key']}" if item.get("source_key") else ""
        optional = " (可选路径)" if item.get("optional") else ""
        parts.append(f"{item['path']} {item['op']} {render_json(item['expected'])}{source_key}{optional}")
    return "；".join(parts)


def runtime_expectation_text(case: Dict[str, Any], parameter: str, context: Dict[str, Any]) -> str:
    command, protocol, reply, _reply_mode = pick_runtime_command(parameter, context)
    parts: List[str] = []
    if command:
        parts.append(f"语音操作={command}")
    if protocol:
        parts.append(f"协议观察={protocol}")
    if reply:
        parts.append(f"播报观察={reply}")
    if case.get("runtime_check"):
        parts.append(case["runtime_check"])
    return "；".join(parts)


def precondition_text(case: Dict[str, Any], engine: str) -> str:
    common = [
        "已按用例中的打包参数生成目标固件",
        "已将目标固件刷入设备，且设备可正常启动",
    ]
    if engine == "自动-解包配置":
        common = ["已完成打包，且可获取 zip 中的 web_config.json"]
    elif engine == "页面/接口校验":
        common = ["无需实际打包，直接在平台页面或接口提交配置"]
    elif engine == "人工-设备/仪器":
        common.append("串口工具/示波器/万用表已连接")
    return "；".join(common)


def build_step_lines(case: Dict[str, Any], parameter: str, context: Dict[str, Any], engine: str) -> List[str]:
    overrides = package_overrides_text(case)
    command, _protocol, _reply, _reply_mode = pick_runtime_command(parameter, context)
    steps: List[str] = []
    if engine == "页面/接口校验":
        steps.append(f"1. 在平台提交打包参数 {overrides}")
        steps.append("2. 观察平台保存/校验结果")
        steps.append("3. 记录是否被拒绝或自动修正")
        return steps

    steps.append(f"1. 按打包参数 {overrides} 生成测试固件")
    if engine == "自动-解包配置":
        steps.append("2. 解包产物并提取 web_config.json")
        steps.append(f"3. 按配置断言检查 `{case['parameter']}` 对应字段")
        return steps

    steps.append("2. 刷机并重启设备")
    if command:
        steps.append(f"3. 执行语音操作：{command}")
    else:
        steps.append("3. 按用例要求执行对应设备操作")
    steps.append("4. 结合串口日志/播报/配置文件完成验证")
    return steps


def expected_result_text(case: Dict[str, Any], assertions: Sequence[Dict[str, Any]]) -> str:
    parts = []
    if assertions:
        parts.append("解包配置符合期望")
    if case.get("expected_result"):
        parts.append(case["expected_result"])
    return "；".join(dict.fromkeys(parts))


def build_csv_row(case: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
    parameter = case.get("parameter") or ""
    assertions = config_assertions_for_case(case)
    engine, method, compat = automation_level(case, parameter, assertions)
    command, protocol, reply, reply_mode = pick_runtime_command(parameter, context)

    return {
        "用例编号": case.get("id", ""),
        "功能模块": module_name(case),
        "测试类型": classify_test_type(parameter, case),
        "命令词": command,
        "功能类型": case.get("scope", ""),
        "期望协议": protocol,
        "期望播报": reply,
        "播报模式": reply_mode,
        "优先级": base_priority(case, parameter),
        "测试方法": method,
        "前置条件": precondition_text(case, engine),
        "测试步骤": " ".join(build_step_lines(case, parameter, context, engine)),
        "预期结果": expected_result_text(case, assertions),
        "执行器": engine,
        "打包参数": package_overrides_text(case),
        "配置断言": assertion_text(assertions),
        "运行断言": runtime_expectation_text(case, parameter, context),
        "原始参数": parameter,
        "输入值": render_json(case.get("input_value")),
        "联动说明": "; ".join(case.get("constraints") or []),
        "voiceTestLite适配": compat,
    }


def build_suite_payload(
    catalog: Dict[str, Any],
    web_config: Optional[Dict[str, Any]],
    csv_rows: Sequence[Dict[str, str]],
    voice_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counters = Counter(row["执行器"] for row in csv_rows)
    compat_counters = Counter(row["voiceTestLite适配"] for row in csv_rows)
    method_counters = Counter(row["测试方法"] for row in csv_rows)
    voice_context = voice_context or build_voice_context(web_config)
    return {
        "generatedAt": catalog.get("generatedAt"),
        "input": catalog.get("input"),
        "selected": catalog.get("selected"),
        "sourceTestCatalog": catalog,
        "hasWebConfigContext": web_config is not None,
        "voiceContext": voice_context,
        "statistics": {
            "caseCount": len(csv_rows),
            "executorCounters": dict(counters),
            "methodCounters": dict(method_counters),
            "voiceTestLiteCompatCounters": dict(compat_counters),
        },
        "rows": list(csv_rows),
    }


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_HEADERS})


def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    rows = payload["rows"]
    stats = payload["statistics"]
    selected = payload["selected"] or {}
    voice_context = payload["voiceContext"] or {}
    lines = [
        "# ListenAI 可执行参数测试套件",
        "",
        f"- 目标：`{selected.get('productPath')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`",
        f"- 用例总数：`{stats['caseCount']}`",
        f"- 执行器分布：`{json.dumps(stats['executorCounters'], ensure_ascii=False)}`",
        f"- 测试方法分布：`{json.dumps(stats['methodCounters'], ensure_ascii=False)}`",
        f"- voiceTestLite 适配：`{json.dumps(stats['voiceTestLiteCompatCounters'], ensure_ascii=False)}`",
        "",
        "## 运行上下文",
        "",
        f"- 唤醒词：`{voice_context.get('wakeup_word') or ''}`",
        f"- 代表命令词：`{voice_context.get('sample_command') or ''}`",
        f"- 超时播报：`{voice_context.get('timeout_reply') or ''}`",
        f"- 欢迎语：`{voice_context.get('welcome_reply') or ''}`",
        "",
        "## 代表用例",
        "",
        "| 用例编号 | 原始参数 | 执行器 | 打包参数 | 配置断言 | 运行断言 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:30]:
        lines.append(
            f"| `{row['用例编号']}` | `{row['原始参数']}` | {row['执行器']} | {row['打包参数']} | {row['配置断言'] or '-'} | {row['运行断言'] or '-'} |"
        )
    lines.extend(
        [
            "",
            "## 文件说明",
            "",
            "- `testCases.csv`：按 `openclaw` 风格整理后的参数验证用例表，保留了原始 13 列，并增加执行器/打包参数/配置断言等扩展列。",
            "- `deviceInfo_generated.json`：基于当前 `web_config.json` 生成的 `voiceTestLite.py` 配置模板，后续补串口/TTS 后即可继续扩展设备侧测试。",
            "- `executable_cases.json`：完整结构化数据，适合后续脚本化执行。",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    catalog = read_json(args.test_catalog_json)
    web_config = load_web_config(args.package_zip, args.web_config)
    voice_context = build_voice_context(web_config)
    if voice_context.get("device_info"):
        apply_runtime_ports(voice_context["device_info"], args.port, args.ctrl_port)
    csv_rows = [build_csv_row(case, voice_context) for case in catalog.get("testCases") or []]

    out_dir = ensure_dir(args.out_dir)
    suite_payload = build_suite_payload(catalog, web_config, csv_rows, voice_context=voice_context)

    write_csv(out_dir / "testCases.csv", csv_rows)
    (out_dir / "executable_cases.json").write_text(json.dumps(suite_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if voice_context.get("device_info"):
        (out_dir / "deviceInfo_generated.json").write_text(
            json.dumps(voice_context["device_info"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    write_markdown(out_dir / "README.md", suite_payload)

    print(f"out_dir       : {out_dir.resolve()}")
    print(f"testCases.csv : {(out_dir / 'testCases.csv').resolve()}")
    print(f"cases         : {len(csv_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
