import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
import urllib3

from listenai_resolve_and_package import choose_source_release_id
from listenai_resolve_and_package import ensure_matrix_data
from listenai_resolve_and_package import family_of_version
from listenai_resolve_and_package import normalize_scene
from listenai_resolve_and_package import resolve_rows
from listenai_task_support import RUNTIME_ROOT, resolve_listenai_token


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"
DEFAULT_MD_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_parameter_catalog.md")
DEFAULT_JSON_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_parameter_catalog.json")

DEFAULT_DICT_TREE_IN = str(RUNTIME_ROOT / "reference_cache" / "dict_tree.json")
DEFAULT_CONFIG_DETAILS_IN = str(RUNTIME_ROOT / "reference_cache" / "config_details.json")
DEFAULT_CONFIG_SENSITIVITY_IN = str(RUNTIME_ROOT / "reference_cache" / "config_sensitivity.json")
DEFAULT_CONFIG_RECOMMENDED_IN = str(RUNTIME_ROOT / "reference_cache" / "config_recommended.json")
DEFAULT_RELEASE_DETAIL_IN = str(RUNTIME_ROOT / "reference_cache" / "listenai_parameter_report_heater_3021_zh_generic.json")

GROUP_ORDER = [
    "基础配置",
    "音频输入配置",
    "串口配置",
    "掉电配置",
    "播报配置",
    "功放配置",
    "算法配置",
    "算法词条",
    "自学习/声纹",
    "自学习阶段配置",
    "多唤醒",
    "深度调优",
]

FRONTEND = {
    "basic": "tmp/listenai_assets/basicConfig-DF1JK6u-.js",
    "algo_config": "tmp/listenai_assets/algoConfig-DVe787uE.js",
    "algo_main": "tmp/listenai_assets/algoMain-Bp8Wrcv8.js",
    "algo_voice_reg": "tmp/listenai_assets/algoVoiceReg-JXTmwzp8.js",
    "algo_multi_wke": "tmp/listenai_assets/algoMultiWke-BXZ6Tfd-.js",
    "depth_config": "tmp/listenai_assets/depthConfig-DNV1vBox.js",
    "depth_table": "tmp/listenai_assets/depthTable-BpksCzsX.js",
    "validators": "tmp/listenai_assets/validators-B7-9XiQ7.js",
}

LEVEL_LABELS = {
    "low_sensitivity": "低",
    "mid_sensitivity": "中",
    "high_sensitivity": "高",
}

CATEGORY_LABELS = {
    "wakeup": "唤醒等待状态",
    "command": "唤醒后命令词",
}

METRIC_LABELS = {
    "dec": "DEC 门限",
    "e2e": "E2E 门限",
    "embedded_e2e": "内置唤醒词门限",
    "free": "ASRFree 门限",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a structured adjustable-parameter catalog for a selected ListenAI firmware target."
    )
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI 登录 token")
    parser.add_argument("--refresh-live", action="store_true", help="刷新实时产品矩阵")
    parser.add_argument("--product", required=True, help="产品名或完整产品路径，例如 取暖器")
    parser.add_argument("--module", required=True, help="模组，例如 3021")
    parser.add_argument("--language", required=True, help="语言，例如 中文")
    parser.add_argument("--version", required=True, help="版本关键字，例如 通用垂类")
    parser.add_argument("--scene", default="纯离线", help="场景，默认 纯离线")
    parser.add_argument("--source-release-id", default="", help="可选，指定参考模板 releaseId")
    parser.add_argument("--md-out", default=DEFAULT_MD_OUT, help="Markdown 输出路径")
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT, help="JSON 输出路径")

    parser.add_argument("--dict-tree-in", default=DEFAULT_DICT_TREE_IN, help="dict_tree.json 缓存")
    parser.add_argument("--config-details-in", default=DEFAULT_CONFIG_DETAILS_IN, help="config_details.json 缓存")
    parser.add_argument(
        "--config-sensitivity-in",
        default=DEFAULT_CONFIG_SENSITIVITY_IN,
        help="config_sensitivity.json 缓存",
    )
    parser.add_argument(
        "--config-recommended-in",
        default=DEFAULT_CONFIG_RECOMMENDED_IN,
        help="config_recommended.json 缓存",
    )
    parser.add_argument(
        "--release-detail-in",
        default=DEFAULT_RELEASE_DETAIL_IN,
        help="release detail 或旧参数报告 json，用于离线回填默认值",
    )

    parser.add_argument("--json-out-catalog", default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options.json"))
    parser.add_argument("--products-csv-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_product_catalog.csv"))
    parser.add_argument("--modules-csv-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_module_catalog.csv"))
    parser.add_argument("--matrix-csv-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.csv"))
    parser.add_argument("--duplicates-csv-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_version_defid_duplicates.csv"))
    parser.add_argument("--matrix-md-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.md"))
    parser.add_argument("--resolution-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_resolved_product.json"))
    parser.add_argument("--summary-out", default=str(RUNTIME_ROOT / "catalog" / "listenai_selected_package_summary.json"))
    return parser


class ListenAIParameterClient:
    def __init__(self, token: str, timeout: int = 60) -> None:
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"token": token})
        self.timeout = timeout

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(BASE_URL + path, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 200:
            raise RuntimeError(f"{path} failed: code={payload.get('code')} msg={payload.get('msg')}")
        return payload


def read_json(path: str) -> Optional[Any]:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


def unwrap_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("json"), dict):
        return payload["json"]
    return payload


def extract_data(payload: Any, label: str) -> Any:
    if payload is None:
        return None
    body = unwrap_payload(payload)
    if isinstance(body, dict) and "code" in body:
        if body.get("code") != 200:
            raise RuntimeError(f"{label} failed: code={body.get('code')} msg={body.get('msg')}")
        return body.get("data")
    return body


def load_release_defaults(path: str) -> Dict[str, Any]:
    payload = read_json(path)
    if payload is None:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("releaseDefaults"), dict):
        return dict(payload["releaseDefaults"])
    data = extract_data(payload, "release detail")
    if isinstance(data, dict):
        return data
    if isinstance(payload, dict) and ("timeout" in payload or "version" in payload):
        return payload
    return {}


def resolve_target(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    args.scene = normalize_scene(args.scene)
    rows = ensure_matrix_data(args, args.scene)
    resolution = resolve_rows(rows, args)
    selected = resolution.get("selected")
    if not selected:
        raise RuntimeError("没有唯一定位到目标产品，请细化输入条件。")
    return selected, resolution


def dedupe_strings(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def write_json(path: str, payload: Dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str, content: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def default_pair(defaults: Dict[str, Any], key: str) -> Tuple[Any, bool]:
    if key in defaults:
        return defaults[key], True
    return None, False


def dict_options(dict_tree: Sequence[Dict[str, Any]], dict_value: str) -> List[Dict[str, Any]]:
    for item in dict_tree:
        if str(item.get("dictValue")) != dict_value:
            continue
        result: List[Dict[str, Any]] = []
        for child in item.get("children") or []:
            value = child.get("dictValue")
            label = child.get("dictLabel") or child.get("name") or value
            if value is None:
                continue
            result.append({"value": str(value), "label": str(label)})
        return result
    return []


def static_options(items: Sequence[Tuple[Any, str]]) -> List[Dict[str, Any]]:
    return [{"value": value, "label": label} for value, label in items]


def make_param(
    *,
    key: str,
    label: str,
    group: str,
    param_type: str,
    default: Any,
    default_known: bool,
    enum_values: Optional[List[Dict[str, Any]]] = None,
    value_range: Optional[Dict[str, Any]] = None,
    constraints: Optional[List[str]] = None,
    source: Optional[List[str]] = None,
    confidence: str = "high",
    directly_editable: bool = True,
    notes: str = "",
    feature_gate: str = "",
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "key": key,
        "label": label,
        "group": group,
        "type": param_type,
        "default": default,
        "default_known": default_known,
        "enum_values": enum_values or [],
        "range": value_range,
        "constraints": constraints or [],
        "source": source or [],
        "confidence": confidence,
        "directly_editable": directly_editable,
        "notes": notes,
    }
    if feature_gate:
        item["feature_gate"] = feature_gate
    return item


def recommended_range(metric: str) -> Optional[Dict[str, Any]]:
    if metric == "dec":
        return {"min": 550, "max": 850, "step": 50}
    if metric == "e2e":
        return {"min": 76, "max": 230, "step": 25}
    if metric in {"embedded_e2e", "free"}:
        return {"min": -234, "max": -57, "step": 35}
    return None


def build_sensitivity_profiles(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    warnings: List[str] = []
    order = {"low_sensitivity": 1, "mid_sensitivity": 2, "high_sensitivity": 3}
    for row in rows:
        category = str(row.get("category") or "")
        metric = str(row.get("type") or "")
        if not category or not metric:
            continue
        key = (category, metric)
        item = grouped.setdefault(
            key,
            {
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, category),
                "metric": metric,
                "metric_label": METRIC_LABELS.get(metric, metric),
                "levels": [],
                "source": ["/fw/config/sensitivity", FRONTEND["depth_table"]],
            },
        )
        min_value = row.get("minValue")
        max_value = row.get("maxValue")
        default_value = row.get("defaultValue")
        if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)) and isinstance(default_value, (int, float)):
            if not (min_value <= default_value <= max_value):
                warnings.append(
                    f"灵敏度接口存在异常默认值：{category}/{metric}/{row.get('level')} 默认值 {default_value} 不在 {min_value}..{max_value} 内。"
                )
        item["levels"].append(
            {
                "level": row.get("level"),
                "level_label": LEVEL_LABELS.get(str(row.get("level") or ""), str(row.get("level") or "")),
                "min": min_value,
                "default": default_value,
                "max": max_value,
                "recommended": recommended_range(metric),
            }
        )
    profiles = list(grouped.values())
    for item in profiles:
        item["levels"].sort(key=lambda x: order.get(str(x.get("level") or ""), 99))
    profiles.sort(key=lambda x: (x.get("category") != "wakeup", str(x.get("metric"))))
    return profiles, dedupe_strings(warnings)


def build_direct_parameters(
    dict_tree: Sequence[Dict[str, Any]],
    defaults: Dict[str, Any],
    feature_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    voice_level = dict_options(dict_tree, "voice_level") or static_options([("2", "2"), ("3", "3"), ("5", "5"), ("7", "7"), ("10", "10")])
    voice = dict_options(dict_tree, "voice")
    compress = dict_options(dict_tree, "compress")
    log_level = dict_options(dict_tree, "log_level")
    baud = dict_options(dict_tree, "baud")
    uart = static_options([("0", "UART0"), ("1", "UART1")])
    switch = static_options([("1", "开"), ("0", "关")])
    boolean = static_options([(True, "开"), (False, "关")])
    pa_level = static_options([("high", "高电平"), ("low", "低电平")])
    sensitivity = static_options([("low_sensitivity", "低"), ("mid_sensitivity", "中"), ("high_sensitivity", "高")])
    algo_view = static_options([("simple", "简洁模式"), ("full", "完整模式")])
    multi_mode = static_options([("loop", "循环切换"), ("specified", "指定切换"), ("protocol", "协议切换")])

    result: List[Dict[str, Any]] = []
    append = result.append

    value, known = default_pair(defaults, "timeout")
    append(make_param(key="timeout", label="唤醒时长", group="基础配置", param_type="integer", default=value, default_known=known, value_range={"min": 1, "max": 60, "unit": "s"}, constraints=["超过 30 秒时，前端会提示误识别概率上升。"], source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前表单为滑杆配置。"))
    value, known = default_pair(defaults, "volLevel")
    append(make_param(key="volLevel", label="音量档位数", group="基础配置", param_type="enum", default=value, default_known=known, enum_values=voice_level, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"], notes="不是连续 1..10，而是固定档位集合。"))
    value, known = default_pair(defaults, "defaultVol")
    append(make_param(key="defaultVol", label="初始化默认音量", group="基础配置", param_type="integer", default=value, default_known=known, value_range={"min": 1, "max_ref": "volLevel"}, constraints=["不得大于 volLevel。"], source=[FRONTEND["basic"], "/fw/release/detail"]))
    value, known = default_pair(defaults, "volMaxOverflow")
    append(make_param(key="volMaxOverflow", label="最大音量上溢播报语", group="基础配置", param_type="text", default=value, default_known=known, source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前前端支持从候选播报语选择，也可直接改文案。"))
    value, known = default_pair(defaults, "volMinOverflow")
    append(make_param(key="volMinOverflow", label="最小音量下溢播报语", group="基础配置", param_type="text", default=value, default_known=known, source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前前端支持从候选播报语选择，也可直接改文案。"))

    value, known = default_pair(defaults, "again")
    append(make_param(key="again", label="mic 模拟增益", group="音频输入配置", param_type="integer", default=value, default_known=known, constraints=["当前前端未暴露合法范围。"], source=[FRONTEND["basic"], "/fw/release/detail"], confidence="low", directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))
    value, known = default_pair(defaults, "dgain")
    append(make_param(key="dgain", label="mic 数字增益", group="音频输入配置", param_type="integer", default=value, default_known=known, constraints=["当前前端未暴露合法范围。"], source=[FRONTEND["basic"], "/fw/release/detail"], confidence="low", directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))

    value, known = default_pair(defaults, "uportUart")
    append(make_param(key="uportUart", label="协议串口", group="串口配置", param_type="enum", default=value, default_known=known, enum_values=uart, constraints=["协议串口与日志串口不能选同一个 UART。"], source=[FRONTEND["basic"], "/fw/release/detail"], notes="切换其中一个串口时，前端会自动把另一个切到剩余 UART。"))
    value, known = default_pair(defaults, "uportBaud")
    append(make_param(key="uportBaud", label="协议串口波特率", group="串口配置", param_type="enum", default=value, default_known=known, enum_values=baud, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"]))
    value, known = default_pair(defaults, "traceUart")
    append(make_param(key="traceUart", label="日志串口", group="串口配置", param_type="enum", default=value, default_known=known, enum_values=uart, constraints=["日志串口与协议串口不能选同一个 UART。"], source=[FRONTEND["basic"], "/fw/release/detail"], notes="切换其中一个串口时，前端会自动把另一个切到剩余 UART。"))
    value, known = default_pair(defaults, "traceBaud")
    append(make_param(key="traceBaud", label="日志串口波特率", group="串口配置", param_type="enum", default=value, default_known=known, enum_values=baud, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"], directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))
    value, known = default_pair(defaults, "logLevel")
    append(make_param(key="logLevel", label="日志级别", group="串口配置", param_type="enum", default=value, default_known=known, enum_values=log_level, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"]))

    value, known = default_pair(defaults, "wakeWordSave")
    append(make_param(key="wakeWordSave", label="唤醒词掉电保存", group="掉电配置", param_type="switch", default=value, default_known=known, enum_values=switch, source=[FRONTEND["basic"], "/fw/release/detail"], notes="打开多唤醒切换时通常会结合此项使用。"))
    value, known = default_pair(defaults, "volSave")
    append(make_param(key="volSave", label="音量掉电保存", group="掉电配置", param_type="switch", default=value, default_known=known, enum_values=switch, source=[FRONTEND["basic"], "/fw/release/detail"]))

    value, known = default_pair(defaults, "vcn")
    append(make_param(key="vcn", label="合成发音人", group="播报配置", param_type="enum", default=value, default_known=known, enum_values=voice, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"], notes="字典树是全平台发音人全集，不保证每个产品都逐一试听验证。"))
    value, known = default_pair(defaults, "speed")
    append(make_param(key="speed", label="合成语速", group="播报配置", param_type="integer", default=value, default_known=known, value_range={"min": 1, "max": 100}, source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前表单为滑杆配置。"))
    value, known = default_pair(defaults, "vol")
    append(make_param(key="vol", label="合成音量", group="播报配置", param_type="integer", default=value, default_known=known, value_range={"min": 1, "max": 100}, source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前表单为滑杆配置。"))
    value, known = default_pair(defaults, "compress")
    append(make_param(key="compress", label="播报音压缩比", group="播报配置", param_type="enum", default=value, default_known=known, enum_values=compress, source=[FRONTEND["basic"], "/dev/dict/tree", "/fw/release/detail"]))
    value, known = default_pair(defaults, "word")
    append(make_param(key="word", label="欢迎语 TTS 文案", group="播报配置", param_type="text", default=value, default_known=known, source=[FRONTEND["basic"], "/fw/release/detail"], notes="当前前端支持直接试听生成音频。"))

    value, known = default_pair(defaults, "paConfigEnable")
    append(make_param(key="paConfigEnable", label="功放配置开关", group="功放配置", param_type="boolean", default=value, default_known=known, enum_values=boolean, source=[FRONTEND["basic"], "/fw/release/detail"], notes="打开后才会展示后续 PA 字段。"))
    value, known = default_pair(defaults, "ctlIoPad")
    append(make_param(key="ctlIoPad", label="控制引脚组", group="功放配置", param_type="text", default=value, default_known=known, constraints=["合法引脚组全集尚未拿到。"], source=[FRONTEND["basic"], "/fw/release/detail"], confidence="low", directly_editable=False, notes="当前 basicConfig 页面里为只读展示文本框。"))
    value, known = default_pair(defaults, "ctlIoNum")
    append(make_param(key="ctlIoNum", label="引脚号", group="功放配置", param_type="integer", default=value, default_known=known, value_range={"min": 0}, source=[FRONTEND["basic"], "/fw/release/detail"], directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))
    value, known = default_pair(defaults, "holdTime")
    append(make_param(key="holdTime", label="保持时长", group="功放配置", param_type="integer", default=value, default_known=known, value_range={"min": 0, "unit": "ms"}, source=[FRONTEND["basic"], "/fw/release/detail"], directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))
    value, known = default_pair(defaults, "paConfigEnableLevel")
    append(make_param(key="paConfigEnableLevel", label="使能电平", group="功放配置", param_type="enum", default=value, default_known=known, enum_values=pa_level, source=[FRONTEND["basic"], "/fw/release/detail"], directly_editable=False, notes="当前 basicConfig 页面里为只读展示。"))

    value, known = default_pair(defaults, "sensitivity")
    append(make_param(key="sensitivity", label="灵敏度档位", group="算法配置", param_type="enum", default=value, default_known=known, enum_values=sensitivity, constraints=["切换灵敏度会重新生成深度调优配置。"], source=[FRONTEND["algo_config"], "/fw/config/sensitivity", "/fw/release/detail"]))
    voice_gate = feature_map.get("voice_regist") or "Unknown"
    value, known = default_pair(defaults, "voiceRegEnable")
    append(make_param(key="voiceRegEnable", label="语音注册（自学习）开关", group="算法配置", param_type="boolean", default=value, default_known=known, enum_values=boolean, source=[FRONTEND["algo_main"], "/fw/config/details", "/fw/release/detail"], directly_editable=voice_gate == "Optional", notes="打开后会显示自学习配置卡片。", feature_gate=f"voice_regist={voice_gate}"))
    multi_gate = feature_map.get("multi_wakeup") or "Unknown"
    value, known = default_pair(defaults, "multiWkeEnable")
    append(make_param(key="multiWkeEnable", label="多唤醒切换开关", group="算法配置", param_type="boolean", default=value, default_known=known, enum_values=boolean, source=[FRONTEND["algo_main"], "/fw/config/details", "/fw/release/detail"], directly_editable=multi_gate == "Optional", notes="打开后会显示多唤醒配置卡片。", feature_gate=f"multi_wakeup={multi_gate}"))
    value, known = default_pair(defaults, "multiWkeMode")
    append(make_param(key="multiWkeMode", label="多唤醒切换模式", group="算法配置", param_type="enum", default=value, default_known=known, enum_values=multi_mode, source=[FRONTEND["algo_multi_wke"], "/fw/release/detail"], directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    value, known = default_pair(defaults, "algoViewMode")
    append(make_param(key="algoViewMode", label="算法视图模式", group="算法配置", param_type="enum", default=value, default_known=known, enum_values=algo_view, source=[FRONTEND["algo_config"], "/fw/release/detail"], notes="只影响前端展示密度，不改变 defId。"))
    value, known = default_pair(defaults, "protocolConfig")
    append(make_param(key="protocolConfig", label="协议配置对象", group="算法配置", param_type="object|null", default=value, default_known=known, source=["/fw/release/detail"], confidence="medium", directly_editable=False, notes="release detail 可见该字段，但当前标准页未发现直接编辑控件。"))
    return result


def build_structure_parameters(dict_tree: Sequence[Dict[str, Any]], feature_map: Dict[str, str]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    append = result.append
    word_type = dict_options(dict_tree, "wordType")
    reply_mode = static_options([("主", "主"), ("被", "被")])
    reg_mode = static_options([("contLearn", "连续学习"), ("specificLearn", "指定学习")])
    cn_sensitivity = static_options([("高", "高"), ("中", "中"), ("低", "低")])
    boolean = static_options([(True, "开"), (False, "关")])
    protocol_rule = "十六进制字节串，形如 `AA BB CC`。"

    algo_sources = [FRONTEND["algo_config"], FRONTEND["validators"], "/dev/dict/tree"]
    reg_sources = [FRONTEND["algo_voice_reg"], FRONTEND["validators"]]
    multi_sources = [FRONTEND["algo_multi_wke"], FRONTEND["validators"]]
    depth_sources = [FRONTEND["depth_config"], FRONTEND["depth_table"], "/fw/config/sensitivity"]

    append(make_param(key="releaseAlgoList[*].word", label="主词条", group="算法词条", param_type="text", default=None, default_known=False, source=algo_sources, notes="用于唤醒词/命令词等主语义词条；播报语等纯播报型词条不使用该字段。"))
    append(make_param(key="releaseAlgoList[*].extWord", label="功能泛化词/别名", group="算法词条", param_type="text", default=None, default_known=False, source=algo_sources, notes="前端支持父词条 extWord，也支持 children 子泛化词。"))
    append(make_param(key="releaseAlgoList[*].type", label="功能类型", group="算法词条", param_type="enum", default=None, default_known=False, enum_values=word_type, source=algo_sources, notes="内置推荐词条的功能类型通常不可改。"))
    append(make_param(key="releaseAlgoList[*].reply", label="播报内容", group="算法词条", param_type="text", default=None, default_known=False, source=algo_sources, notes="负性词、心跳协议、子泛化词等类型不一定使用该字段。"))
    append(make_param(key="releaseAlgoList[*].replyMode", label="播报模式", group="算法词条", param_type="enum", default=None, default_known=False, enum_values=reply_mode, source=algo_sources, notes="播报语类型在前端会固定成“被”。"))
    append(make_param(key="releaseAlgoList[*].sndProtocol", label="发送协议", group="算法词条", param_type="text", default=None, default_known=False, constraints=[protocol_rule], source=algo_sources))
    append(make_param(key="releaseAlgoList[*].recProtocol", label="接收协议", group="算法词条", param_type="text", default=None, default_known=False, constraints=[protocol_rule, "当 replyMode=被 且类型不是心跳协议时会触发额外校验。"], source=algo_sources))
    append(make_param(key="releaseAlgoList[*].children[*].extWord", label="子泛化词", group="算法词条", param_type="text", default=None, default_known=False, source=algo_sources, notes="子词条必须依附父语义存在。"))

    voice_gate = feature_map.get("voice_regist") or "Unknown"
    append(make_param(key="releaseRegist.registMode", label="学习模式", group="自学习/声纹", param_type="enum", default=None, default_known=False, enum_values=reg_mode, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupRepeatCount", label="唤醒词每词重复次数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 1, "max": 2}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupWordsMaxLimit", label="唤醒词字数上限", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 5, "max": 6}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupWordsMinLimit", label="唤醒词字数下限", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 3, "max": 4}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupSensitivity", label="唤醒词学习灵敏度", group="自学习/声纹", param_type="enum", default=None, default_known=False, enum_values=cn_sensitivity, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupRegistMaxLimit", label="唤醒词模板数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 1, "max": 3}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.wakeupRetryCount", label="唤醒词学习失败重试次数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 0, "max": 3}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandRepeatCount", label="命令词每词重复次数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 1, "max": 2}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandWordsMaxLimit", label="命令词字数上限", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 6, "max": 10}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandWordsMinLimit", label="命令词字数下限", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 2, "max": 4}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandSensitivity", label="命令词学习灵敏度", group="自学习/声纹", param_type="enum", default=None, default_known=False, enum_values=cn_sensitivity, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandRegistMaxLimit", label="命令词模板数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 1, "max": 20}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.commandRetryCount", label="命令词学习失败重试次数", group="自学习/声纹", param_type="integer", default=None, default_known=False, value_range={"min": 1, "max": 3}, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.reply", label="学习流程播报语", group="自学习/声纹", param_type="text", default=None, default_known=False, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.replyMode", label="学习流程播报模式", group="自学习/声纹", param_type="enum", default=None, default_known=False, enum_values=reply_mode, source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.sndProtocol", label="学习流程发送协议", group="自学习/声纹", param_type="text", default=None, default_known=False, constraints=[protocol_rule], source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegist.recProtocol", label="学习流程接收协议", group="自学习/声纹", param_type="text", default=None, default_known=False, constraints=[protocol_rule], source=reg_sources, directly_editable=voice_gate == "Optional", feature_gate=f"voice_regist={voice_gate}"))
    append(make_param(key="releaseRegistConfig.*.triggers.*.stages.*.(condition|reply|delReply)", label="自学习阶段触发词/回复语矩阵", group="自学习阶段配置", param_type="nested-structure", default=None, default_known=False, source=reg_sources, directly_editable=voice_gate == "Optional", notes="连续学习和指定学习模式下都存在多层级 stages 配置，字段随模式动态展开。", feature_gate=f"voice_regist={voice_gate}"))

    multi_gate = feature_map.get("multi_wakeup") or "Unknown"
    append(make_param(key="releaseMultiWke.common[*].condition", label="基础多唤醒触发指令", group="多唤醒", param_type="text", default=None, default_known=False, source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.common[*].reply", label="基础多唤醒回复语", group="多唤醒", param_type="text", default=None, default_known=False, source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].condition", label="唤醒词候选", group="多唤醒", param_type="text", default=None, default_known=False, source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].reply", label="切换提示音/回复语", group="多唤醒", param_type="text", default=None, default_known=False, source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].sndProtocol", label="查询协议", group="多唤醒", param_type="text", default=None, default_known=False, constraints=[protocol_rule], source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].recProtocol", label="确认协议", group="多唤醒", param_type="text", default=None, default_known=False, constraints=[protocol_rule], source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].isDefault", label="默认唤醒词", group="多唤醒", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=multi_sources, directly_editable=multi_gate == "Optional", feature_gate=f"multi_wakeup={multi_gate}"))
    append(make_param(key="releaseMultiWke.wkelist[*].isFrozen", label="冻结唤醒词", group="多唤醒", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=multi_sources, directly_editable=multi_gate == "Optional", notes="只有默认唤醒词才允许勾选冻结。", feature_gate=f"multi_wakeup={multi_gate}"))

    append(make_param(key="releaseDepthList[*].pinyin", label="拼音转换结果", group="深度调优", param_type="text", default=None, default_known=False, constraints=["拼音之间使用 - 连接；声调用 1/2/3/4 表示。"], source=depth_sources, notes="主要用于处理多音字或系统自动转写不准的情况。"))
    append(make_param(key="releaseDepthList[*].decEnable", label="DEC 使能", group="深度调优", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=depth_sources, notes="前端说明里提示资源占用较高，通常不推荐默认开启。"))
    append(make_param(key="releaseDepthList[*].decThreshold", label="DEC 门限", group="深度调优", param_type="integer", default=None, default_known=False, value_range={"min": 0, "max": 2000, "recommended_min": 550, "recommended_max": 850, "recommended_step": 50}, source=depth_sources))
    append(make_param(key="releaseDepthList[*].e2eEnable", label="E2E 使能", group="深度调优", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=depth_sources, notes="前端说明里更推荐开启 E2E。"))
    append(make_param(key="releaseDepthList[*].e2eThreshold", label="E2E 门限", group="深度调优", param_type="integer", default=None, default_known=False, value_range={"min": 0, "max": 256, "recommended_min": 76, "recommended_max": 230, "recommended_step": 25}, source=depth_sources))
    append(make_param(key="releaseDepthList[*].embeddedEnable", label="内置唤醒词使能", group="深度调优", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=depth_sources, notes="只在唤醒词相关行且存在 embeddedHit 时可编辑。"))
    append(make_param(key="releaseDepthList[*].embeddedThreshold", label="内置唤醒词门限", group="深度调优", param_type="integer", default=None, default_known=False, value_range={"min": -512, "max": 0, "recommended_min": -234, "recommended_max": -57, "recommended_step": 35}, source=depth_sources, notes="/fw/config/sensitivity 里对应的是 embedded_e2e。"))
    append(make_param(key="releaseDepthList[*].asrFreeEnable", label="ASRFree 使能", group="深度调优", param_type="boolean", default=None, default_known=False, enum_values=boolean, source=depth_sources, notes="只在命令词相关行且存在 asrFreeHit 时可编辑。"))
    append(make_param(key="releaseDepthList[*].asrFreeThreshold", label="ASRFree 门限", group="深度调优", param_type="integer", default=None, default_known=False, value_range={"min": -512, "max": 0, "recommended_min": -234, "recommended_max": -57, "recommended_step": 35}, source=depth_sources, notes="当前 free 类型的默认值行存在异常时，以接口原始返回和告警为准。"))
    append(make_param(key="releaseDepthList[*].type", label="词条类型", group="深度调优", param_type="text", default=None, default_known=False, source=depth_sources, confidence="medium", directly_editable=False, notes="表格展示字段，用于区分唤醒词/命令词。"))
    append(make_param(key="releaseDepthList[*].category", label="调优类别", group="深度调优", param_type="enum", default=None, default_known=False, enum_values=static_options([("wakeup", "唤醒等待状态"), ("command", "唤醒后命令词")]), source=depth_sources, confidence="medium", directly_editable=False, notes="前端通过页签切换，不是逐行单独修改。"))
    return result


def build_catalog_payload(args: argparse.Namespace) -> Dict[str, Any]:
    selected, resolution = resolve_target(args)
    source_release_id = choose_source_release_id(selected, args.source_release_id)

    if args.token:
        client = ListenAIParameterClient(args.token)
        dict_tree = extract_data(client.get("/dev/dict/tree"), "dict tree") or []
        feature_result = extract_data(client.get("/fw/config/details", {"id": selected["defId"]}), "config details") or {}
        sensitivity_rows = extract_data(client.get("/fw/config/sensitivity", {"configId": selected["defId"]}), "config sensitivity") or []
        recommended = extract_data(client.get("/fw/config/recommended", {"id": selected["defId"]}), "config recommended") or []
        release_defaults = {}
        if source_release_id:
            release_defaults = extract_data(client.get("/fw/release/detail", {"id": source_release_id}), "release detail") or {}
        data_sources = [
            "live:/dev/dict/tree",
            f"live:/fw/config/details?id={selected['defId']}",
            f"live:/fw/config/sensitivity?configId={selected['defId']}",
            f"live:/fw/config/recommended?id={selected['defId']}",
        ]
        if source_release_id:
            data_sources.append(f"live:/fw/release/detail?id={source_release_id}")
    else:
        dict_tree = extract_data(read_json(args.dict_tree_in), "dict tree") or []
        feature_result = extract_data(read_json(args.config_details_in), "config details") or {}
        sensitivity_rows = extract_data(read_json(args.config_sensitivity_in), "config sensitivity") or []
        recommended = extract_data(read_json(args.config_recommended_in), "config recommended") or []
        release_defaults = load_release_defaults(args.release_detail_in)
        data_sources = [
            f"local:{path}"
            for path in [args.dict_tree_in, args.config_details_in, args.config_sensitivity_in, args.config_recommended_in, args.release_detail_in]
            if path and Path(path).exists()
        ]

    feature_map = dict(feature_result.get("feature") or {})
    direct_parameters = build_direct_parameters(dict_tree, release_defaults, feature_map)
    structure_parameters = build_structure_parameters(dict_tree, feature_map)
    sensitivity_profiles, profile_warnings = build_sensitivity_profiles(sensitivity_rows)

    warnings = list(resolution.get("warnings") or [])
    warnings.extend(profile_warnings)
    if not dict_tree:
        warnings.append("未拿到字典树，枚举全集只能依赖前端硬编码或历史缓存。")
    if not sensitivity_rows:
        warnings.append("未拿到灵敏度接口数据，灵敏度阈值范围只能依赖前端提示。")
    if not release_defaults:
        warnings.append("未拿到参考模板 release detail，默认值只能部分展示。")
    if not recommended:
        warnings.append("当前 config 的 /fw/config/recommended 返回空数组。")
    warnings.extend(
        [
            "ctlIoPad 的合法引脚组全集仍未定位到权威来源，当前仅确认它在页面中以文本形式展示。",
            "again / dgain 的合法上下界当前仍未在前端或接口中确认。",
            "release detail 当前只返回基础/顶层默认值，不包含算法词条、自学习、多唤醒、深度调优的完整默认矩阵。",
        ]
    )

    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "input": {
            "product": args.product,
            "module": args.module,
            "language": args.language,
            "version": args.version,
            "scene": args.scene,
        },
        "selected": selected,
        "resolution": resolution,
        "versionFamily": family_of_version(selected.get("versionLabel") or ""),
        "sourceReleaseId": source_release_id,
        "featureMap": feature_map,
        "releaseDefaults": release_defaults,
        "recommendedConfig": recommended,
        "dataSources": data_sources,
        "parameters": direct_parameters,
        "editableStructures": structure_parameters,
        "sensitivityProfiles": sensitivity_profiles,
        "warnings": dedupe_strings(warnings),
        "statistics": {
            "directParameterCount": len(direct_parameters),
            "structureParameterCount": len(structure_parameters),
            "sensitivityProfileCount": len(sensitivity_profiles),
            "defaultKnownCount": sum(1 for item in direct_parameters if item.get("default_known")),
        },
    }


def group_entries(entries: Sequence[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in entries:
        grouped.setdefault(str(item.get("group") or "未分组"), []).append(item)
    result: List[Tuple[str, List[Dict[str, Any]]]] = []
    seen = set()
    for group in GROUP_ORDER:
        if group in grouped:
            result.append((group, grouped[group]))
            seen.add(group)
    for group in sorted(grouped):
        if group in seen:
            continue
        result.append((group, grouped[group]))
    return result


def render_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def render_enum_values(options: Sequence[Dict[str, Any]], limit: int = 8) -> str:
    if not options:
        return "-"
    parts: List[str] = []
    for option in options[:limit]:
        value = render_json_value(option.get("value"))
        label = option.get("label")
        if label is not None and str(label) != value:
            parts.append(f"{value}({label})")
        else:
            parts.append(value)
    if len(options) > limit:
        parts.append(f"... 共 {len(options)} 项")
    return "；".join(parts)


def render_range(value_range: Optional[Dict[str, Any]]) -> str:
    if not value_range:
        return "-"
    parts: List[str] = []
    if "min" in value_range:
        parts.append(f"min={value_range['min']}")
    if "max" in value_range:
        parts.append(f"max={value_range['max']}")
    if "max_ref" in value_range:
        parts.append(f"max 跟随 {value_range['max_ref']}")
    if "unit" in value_range:
        parts.append(f"单位={value_range['unit']}")
    if "recommended_min" in value_range and "recommended_max" in value_range:
        parts.append(f"建议 {value_range['recommended_min']}..{value_range['recommended_max']}")
    if "recommended_step" in value_range:
        parts.append(f"建议步长 {value_range['recommended_step']}")
    return "；".join(parts) if parts else "-"


def escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def markdown_catalog(catalog: Dict[str, Any]) -> str:
    selected = catalog["selected"]
    lines: List[str] = []
    lines.append("# ListenAI 参数目录")
    lines.append("")
    lines.append(f"- 生成时间：`{catalog['generatedAt']}`")
    lines.append(
        f"- 目标：`{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`"
    )
    lines.append(f"- defId：`{selected.get('defId')}`")
    lines.append(f"- mode：`{selected.get('mode')}`")
    lines.append(f"- 版本家族：`{catalog.get('versionFamily')}`")
    lines.append(f"- 参考模板 sourceReleaseId：`{catalog.get('sourceReleaseId') or '未提供'}`")
    lines.append("")
    lines.append("## 1. 功能位")
    lines.append("")
    feature_map = catalog.get("featureMap") or {}
    if feature_map:
        for key in sorted(feature_map):
            lines.append(f"- `{key}` = `{feature_map[key]}`")
    else:
        lines.append("- 当前未拿到功能位。")

    lines.append("")
    lines.append("## 2. 可直接配置参数")
    lines.append("")
    for group, items in group_entries(catalog.get("parameters") or []):
        lines.append(f"### {group}")
        lines.append("")
        lines.append("| 参数 | 说明 | 默认值 | 取值/范围 | 直改 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in items:
            default_text = render_json_value(item["default"]) if item.get("default_known") else "未知"
            enum_text = render_enum_values(item.get("enum_values") or [])
            range_text = render_range(item.get("range"))
            if enum_text != "-" and range_text != "-":
                value_text = f"{enum_text}<br>{range_text}"
            elif enum_text != "-":
                value_text = enum_text
            else:
                value_text = range_text
            notes = item.get("notes") or ""
            if item.get("constraints"):
                notes = "；".join(list(item["constraints"]) + ([notes] if notes else []))
            if item.get("feature_gate"):
                notes = f"特性位 {item['feature_gate']}；{notes}" if notes else f"特性位 {item['feature_gate']}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        escape_md(f"`{item['key']}`"),
                        escape_md(str(item.get("label") or "")),
                        escape_md(default_text),
                        escape_md(value_text),
                        "是" if item.get("directly_editable") else "否",
                        escape_md(notes or "-"),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.append("## 3. 灵敏度档位表")
    lines.append("")
    profiles = catalog.get("sensitivityProfiles") or []
    if profiles:
        for profile in profiles:
            lines.append(f"### {profile['category_label']} / {profile['metric_label']}")
            lines.append("")
            lines.append("| 档位 | 最小 | 默认 | 最大 | 推荐范围 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for level in profile.get("levels") or []:
                recommended = level.get("recommended") or {}
                recommended_text = "-"
                if recommended:
                    recommended_text = f"{recommended.get('min')}..{recommended.get('max')}"
                    if "step" in recommended:
                        recommended_text += f"（步长 {recommended['step']}）"
                lines.append(f"| {level.get('level_label')} | {level.get('min')} | {level.get('default')} | {level.get('max')} | {recommended_text} |")
            lines.append("")
    else:
        lines.append("- 当前未拿到灵敏度档位数据。")
        lines.append("")

    lines.append("## 4. 复杂结构字段")
    lines.append("")
    for group, items in group_entries(catalog.get("editableStructures") or []):
        lines.append(f"### {group}")
        lines.append("")
        lines.append("| 参数 | 说明 | 类型 | 取值/范围 | 直改 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in items:
            enum_text = render_enum_values(item.get("enum_values") or [])
            range_text = render_range(item.get("range"))
            if enum_text != "-" and range_text != "-":
                value_text = f"{enum_text}<br>{range_text}"
            elif enum_text != "-":
                value_text = enum_text
            else:
                value_text = range_text
            notes = item.get("notes") or ""
            if item.get("constraints"):
                notes = "；".join(list(item["constraints"]) + ([notes] if notes else []))
            if item.get("feature_gate"):
                notes = f"特性位 {item['feature_gate']}；{notes}" if notes else f"特性位 {item['feature_gate']}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        escape_md(f"`{item['key']}`"),
                        escape_md(str(item.get("label") or "")),
                        escape_md(str(item.get("type") or "")),
                        escape_md(value_text),
                        "是" if item.get("directly_editable") else "否",
                        escape_md(notes or "-"),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.append("## 5. 待确认项与注意事项")
    lines.append("")
    for warning in catalog.get("warnings") or []:
        lines.append(f"- {warning}")

    lines.append("")
    lines.append("## 6. 数据来源")
    lines.append("")
    for source in catalog.get("dataSources") or []:
        lines.append(f"- `{source}`")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, allow_missing=True, persist=True)
    catalog = build_catalog_payload(args)
    write_json(args.json_out, catalog)
    write_text(args.md_out, markdown_catalog(catalog))
    print(f"selected defId: {catalog['selected']['defId']}")
    print(f"markdown      : {os.path.abspath(args.md_out)}")
    print(f"json          : {os.path.abspath(args.json_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
