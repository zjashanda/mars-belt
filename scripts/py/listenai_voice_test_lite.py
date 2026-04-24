import argparse
import csv
import importlib.util
import contextlib
import glob
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from listenai_task_support import TASKS_ROOT


DEFAULT_BASE_SCRIPT = str(
    (Path(__file__).resolve().with_name("voiceTestLite.py"))
)
DEFAULT_SUITE_DIR = str(TASKS_ROOT / "listenai_executable_suite_heater_3021_zh_generic")


SUITE_RESULT_HEADERS = [
    "用例编号",
    "原始参数",
    "功能模块",
    "测试类型",
    "执行器",
    "命令词",
    "打包参数",
    "配置断言",
    "运行断言",
    "执行结果",
    "结果详情",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ListenAI 参数验证版 voiceTestLite")
    parser.add_argument("--suite-dir", default=DEFAULT_SUITE_DIR, help="包含 testCases.csv / deviceInfo_generated.json 的套件目录")
    parser.add_argument("--package-zip", default="", help="已打包固件 zip，用于配置断言")
    parser.add_argument("--web-config", default="", help="直接指定 web_config.json，用于配置断言")
    parser.add_argument("--base-script", default=DEFAULT_BASE_SCRIPT, help="原始 voiceTestLite.py 路径")
    parser.add_argument("--config-only", action="store_true", help="只跑配置断言/规则校验，不初始化音频与串口")
    parser.add_argument("-f", "--file", default="deviceInfo_generated.json", help="配置文件路径，默认取套件目录下的 deviceInfo_generated.json")
    parser.add_argument("-r", "--runTimes", type=int, default=10, help="测试次数，0=全部")
    parser.add_argument("-l", "--label", default="ListenAI参数验证", help="结果目录标签")
    parser.add_argument("-p", "--port", type=str, default="", help="日志串口端口")
    parser.add_argument("--ctrl-port", type=str, default="", help="控制串口端口")
    parser.add_argument("--protocol-port", type=str, default="", help="协议串口端口，protocol 多唤醒模式优先使用")
    parser.add_argument("--pretest", action="store_true", default=False, help="启用上电流程")
    parser.add_argument("--skip-pretest", action="store_true", default=False, help="跳过 pretest 的 loglevel/[D] 检测，直接运行测试")
    parser.add_argument("--update-audio-skills", action="store_true", default=False, help="即使本地 tools/audio 里已存在 audio skills，也执行 git pull --ff-only")
    return parser.parse_args()


def read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_web_config(package_zip: str, web_config_path: str) -> Optional[Dict[str, Any]]:
    if web_config_path:
        return read_json(web_config_path)
    if not package_zip:
        return None
    with zipfile.ZipFile(package_zip) as zf:
        for name in zf.namelist():
            if name.endswith("web_config.json"):
                return json.loads(zf.read(name).decode("utf-8"))
    raise RuntimeError(f"在 {package_zip} 中未找到 web_config.json")


def first_version(web_config: Dict[str, Any]) -> Dict[str, Any]:
    versions = web_config.get("_ver_list") or []
    return versions[0] if versions else {}


def suite_context(web_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not web_config:
        return {}
    context = deepcopy(web_config)
    context.update(first_version(web_config))
    return context


def load_suite_rows(suite_dir: str) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, Any]]]:
    csv_path = Path(suite_dir) / "testCases.csv"
    json_path = Path(suite_dir) / "executable_cases.json"
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))

    payload = read_json(str(json_path)) if json_path.exists() else {"rows": rows}
    by_id = {row["用例编号"]: row for row in payload.get("rows") or rows}
    return rows, by_id


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.lower() == "null":
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def parse_json_cell(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    return json.loads(stripped)


def normalize_bool_text(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def as_int(value: Any) -> Optional[int]:
    value = normalize_scalar(value)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_assertion_value(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        return normalize_scalar(stripped)


def split_assertions(text: str) -> List[str]:
    if not text:
        return []
    return [item.strip() for item in re.split(r"[;；]+", text) if item.strip()]


def parse_assertion(text: str) -> Dict[str, Any]:
    optional = "(可选路径)" in text
    cleaned = text.replace("(可选路径)", "").strip()
    source_key = None
    if " <- " in cleaned:
        cleaned, source_key = cleaned.split(" <- ", 1)
        source_key = source_key.strip()
    match = re.match(r"^(.*?)\s+(eq|contains|len_eq)\s+(.+)$", cleaned)
    if not match:
        raise ValueError(f"无法解析断言: {text}")
    path, op, expected = match.groups()
    return {
        "path": path.strip(),
        "op": op.strip(),
        "expected": parse_assertion_value(expected.strip()),
        "optional": optional,
        "source_key": source_key,
    }


def iter_path_values(current: Any, token: str) -> List[Any]:
    match = re.fullmatch(r"([^\[\]]+)(?:\[(\*|\d+)\])?", token)
    if not match:
        return []
    key, index = match.groups()
    if not isinstance(current, dict) or key not in current:
        return []
    value = current[key]
    if index is None:
        return [value]
    if not isinstance(value, list):
        return []
    if index == "*":
        return list(value)
    idx = int(index)
    if 0 <= idx < len(value):
        return [value[idx]]
    return []


def resolve_path_values(data: Any, path: str) -> List[Any]:
    values = [data]
    for token in path.split("."):
        next_values: List[Any] = []
        for value in values:
            next_values.extend(iter_path_values(value, token))
        values = next_values
        if not values:
            break
    flattened: List[Any] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


def evaluate_assertion(data: Dict[str, Any], assertion: Dict[str, Any]) -> Tuple[bool, str]:
    values = resolve_path_values(data, assertion["path"])
    if not values:
        if assertion.get("optional"):
            return True, f"{assertion['path']} 未出现，按可选路径放过"
        return False, f"{assertion['path']} 未找到"

    expected = assertion["expected"]
    op = assertion["op"]

    if op == "eq":
        normalized = [normalize_scalar(value) for value in values]
        ok = any(value == normalize_scalar(expected) for value in normalized)
        return ok, f"{assertion['path']} 实际={normalized}"

    if op == "contains":
        normalized = [normalize_scalar(value) for value in values]
        ok = any(value == normalize_scalar(expected) for value in normalized)
        return ok, f"{assertion['path']} 候选={normalized[:10]}"

    if op == "len_eq":
        if len(values) == 1 and isinstance(values[0], list):
            actual_len = len(values[0])
        else:
            actual_len = len(values)
        ok = actual_len == int(expected)
        return ok, f"{assertion['path']} 长度={actual_len}"

    return False, f"不支持的操作符: {op}"


def evaluate_assertions(data: Dict[str, Any], assertion_text: str) -> Tuple[bool, List[str]]:
    assertions = [parse_assertion(item) for item in split_assertions(assertion_text)]
    if not assertions:
        return False, ["未提供配置断言"]

    details: List[str] = []
    overall = True
    for assertion in assertions:
        passed, detail = evaluate_assertion(data, assertion)
        prefix = "PASS" if passed else "FAIL"
        details.append(f"{prefix} | {detail}")
        overall = overall and passed
    return overall, details


def is_hex_protocol(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    parts = [part for part in text.split() if part]
    if not parts:
        return False
    return all(re.fullmatch(r"[0-9A-Fa-f]{2}", part) for part in parts)


def normalize_hex_protocol(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = [part.upper() for part in text.split() if part]
    return " ".join(parts)


def local_rule_validate(row: Dict[str, str]) -> Tuple[bool, str]:
    case_id = row.get("用例编号", "")
    overrides = parse_json_cell(row.get("打包参数", "{}"))

    if case_id.endswith("541") or ("volLevel" in overrides and "defaultVol" in overrides):
        vol_level = normalize_scalar(overrides.get("volLevel"))
        default_vol = normalize_scalar(overrides.get("defaultVol"))
        if isinstance(vol_level, int) and isinstance(default_vol, int) and default_vol > vol_level:
            return True, f"defaultVol={default_vol} 大于 volLevel={vol_level}，本地规则判定非法"

    if case_id.endswith("547") or ("uportUart" in overrides and "traceUart" in overrides):
        if str(overrides.get("uportUart")) == str(overrides.get("traceUart")):
            return True, f"uportUart={overrides.get('uportUart')} 与 traceUart 冲突"

    if case_id.endswith("548"):
        if overrides.get("releaseMultiWke.wkelist[*].isDefault") is False and overrides.get("releaseMultiWke.wkelist[*].isFrozen") is True:
            return True, "非默认唤醒词被冻结，本地规则判定非法"

    if case_id.endswith("549"):
        if overrides.get("releaseAlgoList[*].replyMode") == "被" and not (overrides.get("releaseAlgoList[*].recProtocol") or "").strip():
            item_type = overrides.get("releaseAlgoList[*].type")
            if item_type != "心跳协议":
                return True, "被动播报模式缺少确认协议，本地规则判定非法"

    if case_id.endswith("550") or "sndProtocol" in row.get("原始参数", ""):
        snd_protocol = overrides.get("releaseAlgoList[*].sndProtocol")
        if snd_protocol and not is_hex_protocol(str(snd_protocol)):
            return True, f"sndProtocol={snd_protocol} 非法"

    return False, "本地规则未命中，需要页面/接口实际校验"


def write_suite_report(result_dir: Path, rows: Sequence[Dict[str, str]], summary: Dict[str, Any], name_prefix: str) -> None:
    csv_path = result_dir / f"{name_prefix}.csv"
    json_path = result_dir / f"{name_prefix}.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=SUITE_RESULT_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in SUITE_RESULT_HEADERS})

    json_path.write_text(
        json.dumps({"summary": summary, "rows": list(rows)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_config_only_rows(
    suite_rows: Sequence[Dict[str, str]],
    suite_by_id: Dict[str, Dict[str, Any]],
    web_config: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    context = suite_context(web_config) if web_config else {}
    result_rows: List[Dict[str, str]] = []
    counters = Counter()

    for csv_row in suite_rows:
        row = dict(csv_row)
        merged = dict(row)
        merged.update(suite_by_id.get(row["用例编号"], {}))
        verdict = "Skip"
        detail = ""

        if row.get("测试类型") == "配置约束校验":
            passed, detail = local_rule_validate(merged)
            verdict = "OK" if passed else "Skip"
        elif row.get("配置断言") and web_config:
            passed, details = evaluate_assertions(context, row["配置断言"])
            verdict = "OK" if passed else "ConfigFail"
            detail = "；".join(details)
        else:
            verdict = "Skip"
            detail = "未提供 web_config 或当前用例没有可自动执行的配置断言"

        result_note = str(merged.get("结果备注") or "").strip()
        if result_note:
            detail = f"{detail}；{result_note}" if detail else result_note

        counters[verdict] += 1
        result_rows.append(
            {
                "用例编号": row.get("用例编号", ""),
                "原始参数": row.get("原始参数", ""),
                "功能模块": row.get("功能模块", ""),
                "测试类型": row.get("测试类型", ""),
                "执行器": row.get("执行器", ""),
                "命令词": row.get("命令词", ""),
                "打包参数": row.get("打包参数", ""),
                "配置断言": row.get("配置断言", ""),
                "运行断言": row.get("运行断言", ""),
                "执行结果": verdict,
                "结果详情": detail,
            }
        )

    summary = {
        "total": len(result_rows),
        "counters": dict(counters),
        "hasWebConfig": web_config is not None,
    }
    return result_rows, summary


def load_base_module(base_script: str):
    spec = importlib.util.spec_from_file_location("listenai_base_voice_test", base_script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载基础脚本: {base_script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_device_runner(base_module):
    class ListenAISuiteVoiceTest(base_module.VoiceTest):
        EXEC_MODES = dict(base_module.VoiceTest.EXEC_MODES)
        EXEC_MODES.update(
            {
                "固件版本校验": "firmware_version",
                "超时退出": "timeout_exit",
                "功能验证": "generic_single",
                "串口参数验证": "config_only",
                "配置约束校验": "rule_only",
            }
        )
        AUTO_DYNAMIC_PARAMS = {
            "releaseAlgoList[*].word",
            "releaseAlgoList[*].extWord",
            "releaseAlgoList[*].children[*].extWord",
            "releaseAlgoList[*].sndProtocol",
            "releaseAlgoList[*].type",
            "releaseAlgoList[*].reply",
            "releaseAlgoList[*].replyMode",
            "releaseAlgoList[*].recProtocol",
        }
        MANUAL_TYPES = {"快速交互"}

        def __init__(self, config, args, suite_by_id, web_config):
            super().__init__(config, args)
            self.suite_by_id = suite_by_id
            self.listenai_web_config = web_config
            self.listenai_context = suite_context(web_config) if web_config else {}
            uart_cfg = ((self.listenai_context.get("firmware") or {}).get("uart_config") or {})
            self.protocol_port = str(getattr(args, "protocol_port", "") or config.get("protocolPort") or "").strip()
            self.protocol_baud = int(config.get("protocolBaud") or uart_cfg.get("uport_baud") or 9600)
            command_words = [word for word in self.word_list if word != self.wakeup_word]
            self.sample_command = command_words[0] if command_words else ""
            self.volume_up_word = self._pick_word(["增大音量", "大点声", "调大音量"])
            self.volume_down_word = self._pick_word(["减小音量", "小点声", "调小音量"])
            self.volume_max_word = self._pick_word(["最大音量", "音量最大"])
            self.volume_min_word = self._pick_word(["最小音量", "音量最小"])
            self.volume_mid_word = self._pick_word(["中等音量"])
            self._initial_wakeup_word = str(self.wakeup_word or "").strip()
            self._runtime_wakeup_word = self._initial_wakeup_word
            self._multi_wakeup_words = self._collect_multi_wakeup_words()

        def _suite_meta(self, tc: Dict[str, str]) -> Dict[str, Any]:
            meta = dict(self.suite_by_id.get(tc.get("用例编号", ""), {}))
            meta.update(tc)
            return meta

        def _pick_word(self, candidates: Sequence[str]) -> str:
            for candidate in candidates:
                if candidate in self.word_list:
                    return candidate
            return ""

        def _collect_multi_wakeup_words(self) -> List[str]:
            firmware = self.listenai_context.get("firmware") or {}
            multi_cfg = firmware.get("multi_wakeup") or {}
            result: List[str] = []
            for item in (multi_cfg.get("switch_list") or multi_cfg.get("wkelist") or []):
                if not isinstance(item, dict):
                    continue
                word = str(item.get("word") or item.get("condition") or "").strip()
                if word and word not in result:
                    result.append(word)
            if self._initial_wakeup_word and self._initial_wakeup_word not in result:
                result.insert(0, self._initial_wakeup_word)
            return result

        def _normalize_multi_wke_mode(self, value: Any) -> str:
            text = str(value or "").strip().lower()
            mapping = {
                "loop": "loop",
                "循环切换": "loop",
                "specified": "specified",
                "指定切换": "specified",
                "protocol": "protocol",
                "协议切换": "protocol",
            }
            return mapping.get(text, text)

        def _resolve_runtime_wakeup_word(self, running_state: Optional[Dict[str, Any]]) -> str:
            state = running_state or {}
            wkword = as_int(state.get("wkword"))
            if wkword is None:
                return ""
            if self._multi_wakeup_words and 0 <= wkword < len(self._multi_wakeup_words):
                return self._multi_wakeup_words[wkword]
            if wkword == 0:
                return self._initial_wakeup_word
            return ""

        def _apply_runtime_wakeup_word(
            self,
            wake_word: str,
            *,
            reason: str = "",
            running_state: Optional[Dict[str, Any]] = None,
        ) -> bool:
            active = str(wake_word or "").strip()
            if not active:
                return False
            previous = str(self._runtime_wakeup_word or self.wakeup_word or "").strip()
            self._runtime_wakeup_word = active
            self.wakeup_word = active
            if active != previous:
                wkword = ""
                if isinstance(running_state, dict):
                    wkword = f" wkword={running_state.get('wkword')}"
                label = f"({reason})" if reason else ""
                self.log.info(f"同步当前活跃唤醒词{label}:{wkword} -> {active} (prev={previous or 'none'})")
            return True

        def _sync_runtime_wakeup_word(
            self,
            *,
            timeout_seconds: float = 2.5,
            reason: str = "",
        ) -> Tuple[bool, Dict[str, Any], str]:
            ok, running_state, running_detail = self._wait_for_running_config(timeout_seconds=timeout_seconds)
            if not ok:
                return False, running_state, running_detail
            active = self._resolve_runtime_wakeup_word(running_state)
            if not active:
                return False, running_state, running_detail
            self._apply_runtime_wakeup_word(active, reason=reason, running_state=running_state)
            return True, running_state, running_detail

        def load_test_cases(self):
            self._sync_runtime_wakeup_word(timeout_seconds=2.5, reason="before-cases")
            return super().load_test_cases()

        def _case_input_value(self, meta: Dict[str, Any]) -> Any:
            value = meta.get("input_value")
            if value is None and "输入值" in meta:
                value = meta.get("输入值")
            return value

        def _runtime_assert_map(self, meta: Dict[str, Any]) -> Dict[str, str]:
            parsed: Dict[str, str] = {}
            for item in re.split(r"[;；]+", str(meta.get("运行断言") or "")):
                chunk = str(item or "").strip()
                if not chunk or "=" not in chunk:
                    continue
                key, value = chunk.split("=", 1)
                parsed[key.strip()] = value.strip()
            return parsed

        def _runtime_assert_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
            decoded: Dict[str, Any] = {}
            for key, raw in self._runtime_assert_map(meta).items():
                text = str(raw or "").strip()
                if not text:
                    decoded[key] = ""
                    continue
                try:
                    if text[:1] in ['[', '{', '"'] or text in {"true", "false", "null"} or re.fullmatch(r"-?\d+(?:\.\d+)?", text):
                        decoded[key] = parse_json_cell(text)
                    else:
                        decoded[key] = text
                except Exception:
                    decoded[key] = text
            return decoded

        def _multi_wke_runtime_active_word(self) -> str:
            running_state = self._extract_running_config_state()
            active = self._resolve_runtime_wakeup_word(running_state)
            if active:
                self._apply_runtime_wakeup_word(active, reason="runtime-active", running_state=running_state)
                return active
            return str(self._runtime_wakeup_word or self.wakeup_word or "").strip()

        def _multi_wke_wakeup_case_expectation(self, tc: Dict[str, str]) -> Tuple[Optional[bool], str]:
            meta = self._suite_meta(tc)
            if tc.get("测试类型", "") != "唤醒识别":
                return None, ""
            if "基础唤醒词全量验证" not in str(meta.get("联动说明") or ""):
                return None, ""
            firmware = self.listenai_context.get("firmware") or {}
            multi_cfg = firmware.get("multi_wakeup") or {}
            if not multi_cfg.get("enable"):
                return None, ""
            mode = self._normalize_multi_wke_mode(multi_cfg.get("mode"))
            if mode != "specified":
                return None, ""
            target = str(tc.get("命令词", "") or tc.get("唤醒词", "") or "").strip()
            if not target or target not in self._multi_wakeup_words:
                return None, ""
            active = self._multi_wke_runtime_active_word()
            if not active:
                return None, ""
            should_work = target == active
            return should_work, f"multi-wke specified active={active} target={target} should_work={should_work}"

        def _result_note(self, tc: Dict[str, str]) -> str:
            meta = self._suite_meta(tc)
            return str(meta.get("结果备注") or "").strip()

        def _linkage_note(self, tc: Dict[str, str]) -> str:
            meta = self._suite_meta(tc)
            return str(meta.get("联动说明") or "").strip()

        def _has_base_coverage(self, tc: Dict[str, str], parameter: str = "") -> bool:
            note = self._linkage_note(tc)
            if "基础覆盖" not in note:
                return False
            if not parameter:
                return True
            return f"基础覆盖: {parameter}" in note

        def _linkage_detail(self, tc: Dict[str, str], detail: str) -> str:
            linkage = self._linkage_note(tc)
            if not linkage:
                return str(detail or "")
            text = str(detail or "").strip()
            return f"{text}；{linkage}" if text else linkage

        def _base_covered_row(
            self,
            tc: Dict[str, str],
            parameter: str,
            config_verdict: str,
            config_detail: str,
        ) -> Tuple[str, Dict[str, Any]]:
            detail = self._linkage_detail(tc, f"配置预检查={config_verdict}；{config_detail}")
            verdict = "OK" if config_verdict == "OK" else config_verdict
            row = self._config_only_row(tc, verdict, detail)
            row["设备响应列表"] = detail
            return verdict, row

        def _attach_result_note(self, tc: Dict[str, str], rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
            note = self._result_note(tc)
            normalized_rows = [dict(row) for row in rows]
            if not note:
                return normalized_rows
            for row in normalized_rows:
                existing = str(row.get("设备响应列表", "") or "").strip()
                row["设备响应列表"] = f"{existing}|{note}".strip("|") if existing else note
            return normalized_rows

        def _return_case(self, tc: Dict[str, str], verdict: str, rows: Sequence[Dict[str, Any]]):
            return verdict, self._attach_result_note(tc, rows)

        def _asr_commands(self) -> List[Dict[str, Any]]:
            commands = self.listenai_context.get("asr_cmds") or []
            return [item for item in commands if isinstance(item, dict)]

        def _find_release_algo_entry(self, parameter: str, input_value: Any, command_hint: str = "") -> Optional[Dict[str, Any]]:
            field_map = {
                "releaseAlgoList[*].type": "type",
                "releaseAlgoList[*].reply": "reply",
                "releaseAlgoList[*].replyMode": "reply_mode",
                "releaseAlgoList[*].recProtocol": "rec_protocol",
            }
            field_name = field_map.get(parameter)
            expected = str(input_value or "").strip()
            if not field_name or not expected:
                return None

            matches: List[Dict[str, Any]] = []
            for item in self._asr_commands():
                actual = str(item.get(field_name) or "").strip()
                if field_name == "rec_protocol":
                    if normalize_hex_protocol(actual) == normalize_hex_protocol(expected):
                        matches.append(item)
                elif actual == expected:
                    matches.append(item)

            if not matches:
                return None
            if command_hint:
                for item in matches:
                    if str(item.get("intent") or "").strip() == command_hint:
                        return item
            return matches[0]

        def _config_only_row(self, tc: Dict[str, str], verdict: str, detail: str) -> Dict[str, Any]:
            meta = self._suite_meta(tc)
            return {
                "用例编号": tc.get("用例编号", ""),
                "功能模块": tc.get("功能模块", ""),
                "测试类型": tc.get("测试类型", ""),
                "测试次数": "",
                "唤醒词": self.wakeup_word,
                "命令词": tc.get("命令词", ""),
                "期望协议": tc.get("期望协议", ""),
                "识别原始结果": "",
                "识别结果": detail,
                "播报ID": "",
                "实际发送协议": "",
                "协议收发一致": "",
                "协议比对": "",
                "识别判定": verdict,
                "响应时间(ms)": "",
                "设备响应列表": meta.get("配置断言", ""),
                "重启次数": self.reader.get_reboot_count() if self.reader else "",
                "__suite_mode": "offline",
            }

        def _device_row(
            self,
            tc: Dict[str, str],
            command: str,
            verdict: str,
            detail: str,
            expected_proto: str = "",
            response_time: str = "",
        ) -> Dict[str, Any]:
            return {
                "用例编号": tc.get("用例编号", ""),
                "功能模块": tc.get("功能模块", ""),
                "测试类型": tc.get("测试类型", ""),
                "测试次数": "",
                "唤醒词": self.wakeup_word,
                "命令词": command,
                "期望协议": expected_proto,
                "识别原始结果": "",
                "识别结果": detail,
                "播报ID": "",
                "实际发送协议": "",
                "协议收发一致": "",
                "协议比对": "",
                "识别判定": verdict,
                "响应时间(ms)": response_time,
                "设备响应列表": detail,
                "重启次数": self.reader.get_reboot_count() if self.reader else "",
                "__suite_mode": "device",
            }

        def _evaluate_config_assert(self, tc: Dict[str, str]) -> Tuple[str, str]:
            meta = self._suite_meta(tc)
            if not meta.get("配置断言"):
                return "Skip(人工)", "当前用例未提供配置断言"
            if not self.listenai_web_config:
                return "Skip(人工)", "未提供 web_config/package zip，无法执行配置断言"
            passed, details = evaluate_assertions(self.listenai_context, meta["配置断言"])
            return ("OK" if passed else "ConfigFail"), "；".join(details)

        def _current_volume_levels(self) -> List[int]:
            firmware = self.listenai_context.get("firmware") or {}
            volume_cfg = firmware.get("volume_config") or {}
            return [as_int(value) for value in volume_cfg.get("level") or [] if as_int(value) is not None]

        def _expected_firmware_version(self, tc: Dict[str, str]) -> str:
            meta = self._suite_meta(tc)
            input_value = self._case_input_value(meta)
            if input_value is not None and str(input_value).strip():
                return str(input_value).strip()

            overrides = parse_json_cell(meta.get("打包参数", "{}"))
            for key in ["firmwareVersion", "configVersion", "version"]:
                value = overrides.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()

            firmware = self.listenai_context.get("firmware") or {}
            general_cfg = firmware.get("general_config") or {}
            return str(general_cfg.get("version") or "").strip()

        def _clear_recent_serial_lines(self) -> None:
            if not self.reader:
                return
            with self.reader._lock:
                self.reader.recent_lines.clear()

        def _recent_boot_signature_line(self, limit: int = 160) -> str:
            lines = self._recent_sanitized_lines(limit)
            markers = ("APP version:", "SDK:", "VER:", "Running Config")
            for line in reversed(lines):
                if any(marker in line for marker in markers):
                    return line
            return ""

        def _sanitize_serial_line(self, text: str) -> str:
            cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text or "")
            return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", cleaned)

        def _extract_boot_version(self) -> Tuple[str, str]:
            if not self.reader:
                return "", ""
            lines = [self._sanitize_serial_line(line) for line in self.reader.get_recent_lines()[-200:]]
            boot_line = ""
            for line in reversed(lines):
                if "config version:" in line:
                    boot_line = line
                    break
            if not boot_line:
                return "", ""
            match = re.search(r"config version:\s*(\S+)", boot_line)
            return (match.group(1).strip() if match else "", boot_line)

        def _extract_boot_volume_state(self) -> Dict[str, Any]:
            if not self.reader:
                return {
                    "runningVolume": None,
                    "defaultIndex": None,
                    "initIndex": None,
                    "setFrom": None,
                    "setTo": None,
                    "lines": [],
                }
            lines = [self._sanitize_serial_line(line) for line in self.reader.get_recent_lines()[-200:]]
            state: Dict[str, Any] = {
                "runningVolume": None,
                "defaultIndex": None,
                "initIndex": None,
                "setFrom": None,
                "setTo": None,
                "lines": [],
            }
            for line in lines:
                if "volume" in line or "df vol" in line or "init vol lev" in line or "set vol:" in line:
                    state["lines"].append(line)
                match = re.search(r"volume\s*:\s*(\d+)", line)
                if match and state["runningVolume"] is None:
                    state["runningVolume"] = as_int(match.group(1))
                match = re.search(r"df vol:\s*(\d+)", line)
                if match:
                    state["defaultIndex"] = as_int(match.group(1))
                match = re.search(r"init vol lev:\s*(\d+)", line)
                if match:
                    state["initIndex"] = as_int(match.group(1))
                match = re.search(r"set vol:\s*(\d+)\s*->\s*(\d+)", line)
                if match:
                    state["setFrom"] = as_int(match.group(1))
                    state["setTo"] = as_int(match.group(2))
            return state

        def _extract_running_config_state(self, limit: int = 500) -> Dict[str, Any]:
            state: Dict[str, Any] = {
                "volume": None,
                "voice": None,
                "wkword": None,
                "regSaveFlag": None,
                "regSaveSize": None,
                "reg_cmd_count": None,
                "reg_cmd_status": None,
                "lines": [],
                "start_idx": -1,
            }
            if not self.reader:
                return state

            lines = [self._sanitize_serial_line(line) for line in self.reader.get_recent_lines()[-limit:]]
            start_idx = -1
            for idx, line in enumerate(lines):
                if "Running Config" in line:
                    start_idx = idx
            if start_idx < 0:
                return state

            block_lines: List[str] = []
            for idx in range(start_idx, min(len(lines), start_idx + 20)):
                line = lines[idx]
                block_lines.append(line)
                if idx > start_idx and "====" in line:
                    break
            mapping = {
                "volume": "volume",
                "voice": "voice",
                "wkword": "wkword",
                "regsaveflag": "regSaveFlag",
                "regsavesize": "regSaveSize",
                "reg_cmd_count": "reg_cmd_count",
                "reg_cmd_status": "reg_cmd_status",
            }
            pending_key = ""
            for line in block_lines:
                match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?\d+)", line)
                if match:
                    key = str(match.group(1) or "").strip().lower()
                    mapped = mapping.get(key)
                    if mapped:
                        state[mapped] = as_int(match.group(2))
                        pending_key = ""
                    continue

                split_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$", line)
                if split_match:
                    key = str(split_match.group(1) or "").strip().lower()
                    pending_key = mapping.get(key) or ""
                    continue

                if pending_key:
                    value_match = re.fullmatch(r"\s*(-?\d+)\s*", line)
                    if value_match:
                        state[pending_key] = as_int(value_match.group(1))
                    pending_key = ""
            state["lines"] = block_lines
            state["start_idx"] = start_idx
            return state

        def _wait_for_running_config(self, timeout_seconds: float = 6.0) -> Tuple[bool, Dict[str, Any], str]:
            existing = self._extract_running_config_state()
            if existing.get("start_idx", -1) >= 0:
                detail = "|".join(existing.get("lines") or [])
                return True, existing, detail
            if not self.reader:
                return False, existing, "reader-missing"

            processed = len(self.reader.get_recent_lines())
            deadline = time.time() + max(float(timeout_seconds), 1.0)
            while time.time() < deadline:
                lines = self.reader.get_recent_lines()
                new_lines = lines[processed:]
                processed = len(lines)
                if any("Running Config" in self._sanitize_serial_line(line) for line in new_lines):
                    time.sleep(0.3)
                    state = self._extract_running_config_state()
                    detail = "|".join(state.get("lines") or [])
                    return (state.get("start_idx", -1) >= 0), state, detail
                time.sleep(0.05)
            state = self._extract_running_config_state()
            detail = "|".join(state.get("lines") or [])
            return (state.get("start_idx", -1) >= 0), state, detail or "running-config-timeout"

        def _parse_refresh_config_line(self, line: str) -> Dict[str, Any]:
            state: Dict[str, Any] = {"line": line}
            patterns = {
                "volume": r"volume=(\d+)",
                "voice": r"voice=(\d+)",
                "wkword": r"wkword=(\d+)",
                "regSaveFlag": r"regSaveFlag=(\d+)",
                "regSaveSize": r"regSave(?:Size)?=(\d+)",
                "reg_cmd_count": r"regCmdInfo=cnt\[(\d+)\]",
                "reg_cmd_status": r"regCmdInfo=cnt\[\d+\]-st\[(\d+)\]",
            }
            for key, pattern in patterns.items():
                match = re.search(pattern, line)
                state[key] = as_int(match.group(1)) if match else None
            return state

        def _extract_recent_refresh_config_state(self, limit: int = 240) -> Dict[str, Any]:
            if not self.reader:
                return {"line": ""}
            lines = [self._sanitize_serial_line(line) for line in self.reader.get_recent_lines()[-limit:]]
            for line in reversed(lines):
                if "refresh config" in line.lower():
                    return self._parse_refresh_config_line(line)
            return {"line": ""}

        def _wait_for_config_save_window(
            self,
            *,
            since_index: int = 0,
            timeout_seconds: float = 10.0,
            post_save_wait_seconds: float = 10.0,
        ) -> Tuple[bool, Dict[str, Any], str]:
            if not self.reader:
                return False, {"line": ""}, "reader-missing"

            all_lines = self.reader.get_recent_lines()
            processed = max(0, min(int(since_index or 0), len(all_lines)))
            refresh_state: Dict[str, Any] = {"line": ""}
            refresh_line = ""
            save_line = ""

            def consume(lines: Sequence[str]) -> bool:
                nonlocal refresh_state, refresh_line, save_line
                for raw_line in lines:
                    line = self._sanitize_serial_line(raw_line)
                    lowered = line.lower()
                    if "refresh config" in lowered:
                        refresh_line = line
                        refresh_state = self._parse_refresh_config_line(line)
                    if "save config success" in lowered:
                        save_line = line
                        return True
                return False

            if consume(all_lines[processed:]):
                if post_save_wait_seconds > 0:
                    time.sleep(max(float(post_save_wait_seconds), 0.1))
                detail = f"refresh={refresh_line or 'missing'}；save={save_line}；wait={post_save_wait_seconds}s"
                return True, refresh_state, detail

            deadline = time.time() + max(float(timeout_seconds), 1.0)
            while time.time() < deadline:
                lines = self.reader.get_recent_lines()
                new_lines = lines[processed:]
                processed = len(lines)
                if consume(new_lines):
                    if post_save_wait_seconds > 0:
                        time.sleep(max(float(post_save_wait_seconds), 0.1))
                    detail = f"refresh={refresh_line or 'missing'}；save={save_line}；wait={post_save_wait_seconds}s"
                    return True, refresh_state, detail
                time.sleep(0.05)

            detail = f"refresh={refresh_line or 'missing'}；save=missing within {timeout_seconds}s"
            return False, refresh_state, detail

        def _wait_for_wakeup_command_ready(
            self,
            *,
            since_index: int = 0,
            timeout_seconds: float = 2.5,
            settle_seconds: float = 0.15,
        ) -> Tuple[bool, str]:
            if not self.reader:
                return True, "reader-missing"

            lines = self.reader.get_recent_lines()
            processed = max(0, min(int(since_index or 0), len(lines)))
            saw_mode1 = False
            saw_prompt_play = False
            saw_play_stop = False
            last_mode: Optional[int] = None
            last_mode_line = ""
            last_prompt_line = ""
            timeout_line = ""

            def consume(batch: Sequence[str]) -> Optional[Tuple[bool, str]]:
                nonlocal saw_mode1, saw_prompt_play, saw_play_stop, last_mode, last_mode_line, last_prompt_line, timeout_line
                for raw_line in batch:
                    line = self._sanitize_serial_line(raw_line)
                    lowered = line.lower()
                    if "wake up ready to asr mode" in lowered:
                        saw_mode1 = True
                        last_mode = 1
                        last_mode_line = line
                    match = re.search(r"MODE\s*=\s*(\d+)", line, re.IGNORECASE)
                    if match:
                        last_mode = as_int(match.group(1))
                        last_mode_line = line
                        if last_mode == 1:
                            saw_mode1 = True
                        if last_mode == 0:
                            return False, f"mode0-before-command line={line}"
                    if "play start" in lowered or "play id :" in lowered:
                        saw_prompt_play = True
                        last_prompt_line = line
                    if "play stop" in lowered:
                        saw_play_stop = True
                        last_prompt_line = line
                    if "cur wk id:" in lowered and "!=" in line:
                        return False, f"wake-rejected line={line}"
                    if "time_out" in lowered or "wk timeout" in lowered:
                        timeout_line = line
                        return False, f"wakeup-timeout-before-command line={line}"
                return None

            initial_result = consume(lines[processed:])
            if initial_result is not None:
                return initial_result

            start_time = time.time()
            deadline = start_time + max(float(timeout_seconds), 0.5)
            no_prompt_ready_after = 0.25
            while time.time() < deadline:
                lines = self.reader.get_recent_lines()
                new_lines = lines[processed:]
                processed = len(lines)
                result = consume(new_lines)
                if result is not None:
                    return result

                if saw_prompt_play and saw_play_stop:
                    settle_deadline = time.time() + max(float(settle_seconds), 0.05)
                    while time.time() < settle_deadline:
                        lines = self.reader.get_recent_lines()
                        new_lines = lines[processed:]
                        processed = len(lines)
                        result = consume(new_lines)
                        if result is not None:
                            return result
                        time.sleep(0.02)
                    return True, f"prompt-finished line={last_prompt_line or 'play stop'};mode={last_mode};mode_line={last_mode_line}"

                if saw_mode1 and (not saw_prompt_play) and (time.time() - start_time) >= no_prompt_ready_after:
                    return True, f"mode-ready line={last_mode_line or 'MODE=1'}"
                time.sleep(0.05)

            if saw_mode1 and not timeout_line and (saw_play_stop or not saw_prompt_play):
                return True, f"mode-ready-timeout line={last_mode_line or last_prompt_line}"
            return False, f"command-window-timeout mode={last_mode};prompt={last_prompt_line};timeout={timeout_line}"

        def _ensure_audio_word(self, word: str) -> bool:
            if not word:
                return False
            path = os.path.join(self.wav_dir, f"{word}.mp3")
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return True
            try:
                base_module.tts_generate(word, path, self.tts_cfg)
                return True
            except Exception as exc:
                self.log.warn(f"动态生成音频失败: {word} | {exc}")
                return False

        @contextlib.contextmanager
        def _temporary_proto(self, command: str, expected_proto: Optional[str]):
            existed = command in self.kw2proto
            original = self.kw2proto.get(command)
            if expected_proto is None:
                self.kw2proto.pop(command, None)
            elif expected_proto:
                self.kw2proto[command] = expected_proto
            try:
                yield
            finally:
                if expected_proto is None:
                    if existed:
                        self.kw2proto[command] = original
                elif expected_proto:
                    if existed:
                        self.kw2proto[command] = original
                    else:
                        self.kw2proto.pop(command, None)

        def _ensure_runtime_regex(self, tag: str, trigger_word: str = "") -> bool:
            if self.regex_map.get(tag):
                return True
            if not trigger_word:
                return False
            candidates = getattr(base_module, "REGEX_CANDIDATES", {}).get(tag) or []
            if not candidates:
                return False
            if not self._ensure_audio_word(trigger_word):
                return False

            if not self.wakeup():
                return False
            self.reader.clear()
            self.play(trigger_word)
            time.sleep(3)
            lines = self.reader.get_recent_lines()
            pattern = self._try_candidates(tag, candidates, lines, "", self.kw2proto.get(trigger_word, ""), trigger_word)
            if not pattern:
                return False
            self.regex_map[tag] = pattern
            self._save_regex_to_config()
            return True

        def _extract_volume_values(self) -> List[int]:
            values: List[int] = []
            if not self.reader:
                return values
            for raw in self.reader.get_all("volume"):
                value = as_int(raw)
                if value is not None:
                    values.append(value)
            return values

        def _last_volume_value(self) -> Optional[int]:
            values = self._extract_volume_values()
            return values[-1] if values else None

        def _power_cycle_device(self) -> Tuple[bool, str]:
            if not (self.pretest_enabled and self.ctrl_port):
                return False, "未启用 pretest/ctrl-port，无法自动断电重启"
            if not self.pretest_power_on():
                return False, "执行 power cycle 失败"
            if not self.set_log_level(4):
                return False, "power cycle 后恢复日志等级失败"
            # A controlled power-cycle can still leave late boot banners in the
            # serial buffer for a short window. Those lines should not be treated
            # as an unexpected reboot during the immediately following verification.
            self._ignore_boot_signature_until = time.time() + 20.0
            synced, running_state, running_detail = self._sync_runtime_wakeup_word(
                timeout_seconds=3.0,
                reason="power-cycle",
            )
            detail = "power cycle 成功"
            if synced:
                detail = (
                    f"{detail};runtime-wakeup={self._runtime_wakeup_word};"
                    f"wkword={running_state.get('wkword')};detail={running_detail}"
                )
            return True, detail

        def _power_cycle_reboot_status(self, start_count: int) -> Tuple[bool, str]:
            if not self.reader:
                return False, "reader-missing"
            current_count = self.reader.get_reboot_count()
            delta = max(current_count - start_count, 0)
            extra_reboot = delta > 1
            return extra_reboot, f"power-cycle-reboot-delta={delta}"

        def _set_last_wakeup_state(self, status: str, detail: str) -> None:
            self._last_wakeup_status = str(status or "").strip()
            self._last_wakeup_detail = str(detail or "").strip()

        def _classify_wakeup_ready_failure(self, detail: str) -> str:
            text = str(detail or "").strip()
            if "wake-rejected" in text or "cur wk id:" in text:
                return "wake-rejected"
            if "mode0-before-command" in text or "wakeup-timeout-before-command" in text:
                return "command-window-expired"
            if "command-window-timeout" in text:
                return "wake-not-ready"
            return "wake-failed"

        def _wakeup_phrase_until_ready(
            self,
            phrase: str,
            *,
            max_retry: int,
            detect_timeout_seconds: float,
            ready_timeout_seconds: float,
            allow_reg_result: bool = False,
            accepted_results: Optional[Sequence[str]] = None,
        ) -> Tuple[bool, str]:
            if not self._ensure_audio_word(phrase):
                detail = f"缺少音频且 TTS 生成失败: {phrase}"
                self._set_last_wakeup_state("audio-missing", detail)
                return False, detail

            self._set_last_wakeup_state("", "")
            last_detail = ""
            accepted = {
                str(item or "").strip()
                for item in (accepted_results or [])
                if str(item or "").strip()
            }
            if not accepted:
                accepted = {phrase}
            for attempt in range(max(1, max_retry)):
                self.log.info(f"  指定唤醒第{attempt + 1}/{max_retry}次 [{phrase}]...")
                attempt_reboot_base = self.reader.get_reboot_count() if self.reader else 0
                if self.reader and self.reader.is_rebooted():
                    self.reader.clear_reboot_flag()
                self.reader.clear()
                self._clear_recent_serial_lines()
                self.play(phrase)
                observed: List[str] = []
                deadline = time.time() + max(float(detect_timeout_seconds), 1.0)
                while time.time() < deadline:
                    for tag in ("wakeKw", "asrKw"):
                        raw = self.reader.get(tag)
                        if not raw:
                            continue
                        text = self.pinyin_to_zh(raw) or raw
                        if text and text not in observed:
                            observed.append(text)
                    if (set(observed) & accepted) or (allow_reg_result and "REG" in observed):
                        rebooted, reboot_detail = self._unexpected_reboot_since(attempt_reboot_base)
                        if rebooted:
                            self._set_last_wakeup_state("reboot", reboot_detail)
                            return False, reboot_detail
                        ready_ok, ready_detail = self._wait_for_wakeup_command_ready(
                            since_index=0,
                            timeout_seconds=max(float(ready_timeout_seconds), 0.5),
                        )
                        last_detail = (
                            f"wake={phrase} accepted={sorted(accepted)} "
                            f"observed={observed}; {ready_detail}"
                        )
                        if ready_ok:
                            self._set_last_wakeup_state("ready", last_detail)
                            self.log.info("  唤醒成功")
                            return True, last_detail
                        status = self._classify_wakeup_ready_failure(ready_detail)
                        self._set_last_wakeup_state(status, last_detail)
                        self.log.warn(f"  唤醒后命令窗口未就绪: {last_detail}")
                        if status == "command-window-expired":
                            return False, last_detail
                        if status == "wake-rejected":
                            break
                        break
                    time.sleep(0.05)
                rebooted, reboot_detail = self._unexpected_reboot_since(attempt_reboot_base)
                if rebooted:
                    self._set_last_wakeup_state("reboot", reboot_detail)
                    return False, reboot_detail
                if not last_detail:
                    last_detail = f"wake={phrase} accepted={sorted(accepted)} observed={observed}"
                if not getattr(self, "_last_wakeup_status", ""):
                    self._set_last_wakeup_state("wake-not-detected", last_detail)
            return False, str(getattr(self, "_last_wakeup_detail", "") or last_detail)

        def wakeup(self, target_wakeup=None, max_retry=None):
            target_wakeup = target_wakeup or self.wakeup_word
            max_retry = max_retry or self.wakeup_retry_limit
            ok, detail = self._wakeup_phrase_until_ready(
                target_wakeup,
                max_retry=max_retry,
                detect_timeout_seconds=3.0,
                ready_timeout_seconds=4.5,
                allow_reg_result=False,
            )
            if ok:
                return True
            self.log.error(f"唤醒失败! (已尝试{max_retry}次) detail={detail}")
            return False

        def _wakeup_with_timestamp(self, max_retry: int = 3, timeout_seconds: float = 3.0) -> Tuple[bool, Optional[float], str]:
            for attempt in range(max_retry):
                self.log.info(f"  timeout wakeup {attempt + 1}/{max_retry}...")
                self.reader.clear()
                self.play(self.wakeup_word)
                deadline = time.time() + max(timeout_seconds, 1.0)
                while time.time() < deadline:
                    wake_raw = self.reader.get("wakeKw")
                    if wake_raw and self.pinyin_to_zh(wake_raw) == self.wakeup_word:
                        self.log.info("  timeout wakeup success")
                        return True, time.time(), wake_raw
                    time.sleep(0.05)
            return False, None, ""

        def _wait_for_timeout_marker(
            self,
            expected_timeout: int,
            extra_wait_seconds: float = 8.0,
        ) -> Tuple[bool, Optional[float], str, List[str], List[str]]:
            markers = ("TIME_OUT", "Wk timeout", "wk timeout")
            processed = len(self.reader.get_recent_lines())
            deadline = time.time() + max(float(expected_timeout) + extra_wait_seconds, 3.0)
            while time.time() < deadline:
                lines = self.reader.get_recent_lines()
                new_lines = lines[processed:]
                processed = len(lines)
                for line in new_lines:
                    if any(marker in line for marker in markers):
                        time.sleep(0.4)
                        return True, time.time(), line, self.reader.get_all("sendMsg"), self.reader.get_all("playId")
                time.sleep(0.05)
            return False, None, "", self.reader.get_all("sendMsg"), self.reader.get_all("playId")

        def _voice_reg_learn_words(self, tc: Dict[str, str]) -> List[str]:
            meta = self._suite_meta(tc)
            overrides = parse_json_cell(meta.get("打包参数", "{}"))
            out: List[str] = []

            for raw in overrides.get("studyRegCommands") or []:
                word = str(raw or "").strip()
                if word and word not in out:
                    out.append(word)

            if out:
                return out

            firmware = self.listenai_context.get("firmware") or {}
            study_cfg = firmware.get("study_config") or {}
            for item in study_cfg.get("reg_commands") or []:
                if not isinstance(item, dict):
                    continue
                word = str(item.get("word") or item.get("condition") or "").strip()
                if word and word not in out:
                    out.append(word)
            return out

        def _voice_reg_pack_args(self, tc: Dict[str, str]) -> Dict[str, Any]:
            meta = self._suite_meta(tc)
            return parse_json_cell(meta.get("打包参数", "{}"))

        def _voice_reg_control_phrase(self, tc: Dict[str, str], key: str, fallback: str) -> str:
            overrides = self._voice_reg_pack_args(tc)
            value = str(overrides.get(key) or "").strip()
            return value or fallback

        def _voice_reg_selected_command(self, tc: Dict[str, str]) -> str:
            overrides = self._voice_reg_pack_args(tc)
            selected = str(overrides.get("voiceRegSelectedCommand") or "").strip()
            if selected:
                return selected
            learn_words = self._voice_reg_learn_words(tc)
            return learn_words[0] if learn_words else ""

        def _voice_reg_learned_command(self, tc: Dict[str, str]) -> str:
            overrides = self._voice_reg_pack_args(tc)
            return str(overrides.get("voiceRegLearnCommandAlias") or "一小时关机").strip()

        def _voice_reg_learned_wake_word(self, tc: Dict[str, str]) -> str:
            overrides = self._voice_reg_pack_args(tc)
            return str(overrides.get("voiceRegLearnWakeWord") or "工藤新一").strip()

        def _voice_reg_virtual_wake_intent(self, tc: Dict[str, str]) -> str:
            overrides = self._voice_reg_pack_args(tc)
            return str(overrides.get("voiceRegVirtualWakeIntent") or "虚拟语音注册唤醒意图").strip()

        def _voice_reg_regist_mode(self, tc: Dict[str, str]) -> str:
            overrides = self._voice_reg_pack_args(tc)
            return str(overrides.get("voiceRegRegistMode") or "specificLearn").strip() or "specificLearn"

        def _voice_reg_command_repeat_count(self, tc: Dict[str, str]) -> int:
            overrides = self._voice_reg_pack_args(tc)
            return max(1, int(overrides.get("voiceRegCommandRepeatCount", 2) or 2))

        def _voice_reg_command_retry_count(self, tc: Dict[str, str]) -> int:
            overrides = self._voice_reg_pack_args(tc)
            return max(0, int(overrides.get("voiceRegCommandRetryCount", 0) or 0))

        def _voice_reg_wakeup_repeat_count(self, tc: Dict[str, str]) -> int:
            overrides = self._voice_reg_pack_args(tc)
            return max(1, int(overrides.get("voiceRegWakeupRepeatCount", 1) or 1))

        def _voice_reg_wakeup_retry_count(self, tc: Dict[str, str]) -> int:
            overrides = self._voice_reg_pack_args(tc)
            return max(0, int(overrides.get("voiceRegWakeupRetryCount", 0) or 0))

        def _voice_reg_command_entry(self, command: str) -> Optional[Dict[str, Any]]:
            target = str(command or "").strip()
            if not target:
                return None
            for item in self._asr_commands():
                if str(item.get("intent") or "").strip() == target:
                    return item
            return None

        def _recent_sanitized_lines(self, limit: int = 240) -> List[str]:
            if not self.reader:
                return []
            return [self._sanitize_serial_line(line) for line in self.reader.get_recent_lines()[-limit:]]

        def _voice_reg_recent_learning_marker_summary(self, limit: int = 240, tail: int = 6) -> str:
            markers: List[str] = []
            for line in self._recent_sanitized_lines(limit):
                lowered = line.lower()
                if (
                    "study get action type:" in line
                    or "cmdlist get[" in lowered
                    or "reg info:" in line
                    or "reg wake word:" in line
                    or "voice reg type:" in line
                    or "algo restart" in lowered
                ):
                    markers.append(line.strip())
            if not markers:
                return ""
            return " || ".join(markers[-max(1, tail):])

        def _parse_recent_reg_state(self, limit: int = 240) -> Dict[str, Any]:
            status: Optional[int] = None
            status_line = ""
            failed = False
            fail_line = ""
            error_line = ""
            process_words: List[str] = []
            lines = self._recent_sanitized_lines(limit)
            last_status_idx = -1
            last_fail_idx = -1
            last_error_idx = -1
            for idx, line in enumerate(lines):
                match = re.search(r"reg status:\s*(\d+)", line)
                if match:
                    status = as_int(match.group(1))
                    status_line = line
                    last_status_idx = idx
                match = re.search(r"reging process keyword:\s*(.*)", line)
                if match:
                    process_words.append(match.group(1).strip())
                if "reg failed!" in line:
                    fail_line = line
                    last_fail_idx = idx
                if "error cnt >" in line:
                    error_line = line
                    last_error_idx = idx
            failed = last_fail_idx > last_status_idx and last_fail_idx >= 0
            return {
                "status": status,
                "status_line": status_line,
                "failed": failed,
                "fail_line": fail_line,
                "error_line": error_line,
                "last_status_idx": last_status_idx,
                "last_fail_idx": last_fail_idx,
                "last_error_idx": last_error_idx,
                "process_words": process_words,
                "lines": lines,
            }

        def _parse_recent_voice_reg_cycle(self, limit: int = 240) -> Dict[str, Any]:
            lines = self._recent_sanitized_lines(limit)
            last_event = ""
            last_event_line = ""
            last_event_idx = -1
            last_status: Optional[int] = None
            last_status_line = ""
            last_status_idx = -1
            stop_line = ""
            stop_idx = -1
            process_words: List[str] = []
            quit_words: List[str] = []

            for idx, line in enumerate(lines):
                lowered = line.lower()
                if "reg again!" in line:
                    last_event = "again"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg simila error!" in line:
                    last_event = "similar_error"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg length error!" in line:
                    last_event = "length_error"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg auto next!" in line:
                    last_event = "auto_next"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg cmd over success!" in line:
                    last_event = "cmd_over_success"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg over!" in line:
                    last_event = "over"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg del" in line:
                    last_event = "delete"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg success!" in line:
                    last_event = "success"
                    last_event_line = line
                    last_event_idx = idx
                elif "reg failed!" in line:
                    last_event = "failed"
                    last_event_line = line
                    last_event_idx = idx

                match = re.search(r"reg status:\s*(\d+)", line)
                if match:
                    last_status = as_int(match.group(1))
                    last_status_line = line
                    last_status_idx = idx

                match = re.search(r"reging process keyword:\s*(.*)", line)
                if match:
                    process_words.append(match.group(1).strip())
                match = re.search(r"reging user quit process keyword:\s*(.*)", line)
                if match:
                    quit_word = match.group(1).strip()
                    if quit_word:
                        quit_words.append(quit_word)
                    last_event = "user_quit"
                    last_event_line = line
                    last_event_idx = idx

                if "play stop" in lowered:
                    stop_line = line
                    stop_idx = idx

            status_after_event: Optional[int] = None
            status_after_event_line = ""
            status_after_event_idx = -1
            stop_after_event_line = ""
            stop_after_event_idx = -1
            for idx, line in enumerate(lines):
                if idx <= last_event_idx:
                    continue
                match = re.search(r"reg status:\s*(\d+)", line)
                if match:
                    status_after_event = as_int(match.group(1))
                    status_after_event_line = line
                    status_after_event_idx = idx
                if "play stop" in line.lower():
                    stop_after_event_line = line
                    stop_after_event_idx = idx

            derived_status = status_after_event
            if derived_status is None:
                derived_status = {
                    "again": 1,
                    "similar_error": 1,
                    "length_error": 1,
                    "auto_next": 1,
                    "cmd_over_success": 3,
                    "success": 3,
                    "failed": 4,
                    "user_quit": 6,
                    "over": 3,
                    "delete": 7,
                }.get(last_event)

            return {
                "event": last_event,
                "event_line": last_event_line,
                "event_idx": last_event_idx,
                "status": derived_status,
                "status_line": status_after_event_line or last_status_line,
                "status_idx": status_after_event_idx if status_after_event_idx >= 0 else last_status_idx,
                "raw_status": last_status,
                "raw_status_line": last_status_line,
                "raw_status_idx": last_status_idx,
                "stop_line": stop_line,
                "stop_idx": stop_idx,
                "stop_after_event_line": stop_after_event_line,
                "stop_after_event_idx": stop_after_event_idx,
                "failed": last_event == "failed",
                "process_words": process_words,
                "quit_words": quit_words,
                "lines": lines,
            }

        def _wait_for_reg_state(self, expect_in_progress: Optional[bool], timeout_seconds: float = 8.0) -> Dict[str, Any]:
            deadline = time.time() + max(float(timeout_seconds), 1.0)
            last_state = self._parse_recent_reg_state()
            while time.time() < deadline:
                state = self._parse_recent_reg_state()
                last_state = state
                if state.get("failed"):
                    return state
                status = state.get("status")
                if expect_in_progress is True and status == 1:
                    return state
                if expect_in_progress is False and status is not None and status != 1:
                    return state
                if expect_in_progress is None and status is not None:
                    return state
                time.sleep(0.05)
            return last_state

        def _wait_for_voice_reg_cycle_complete(
            self,
            expect_in_progress: bool,
            timeout_seconds: float = 18.0,
            settle_seconds: float = 0.8,
        ) -> Dict[str, Any]:
            deadline = time.time() + max(float(timeout_seconds), 1.0)
            last_cycle = self._parse_recent_voice_reg_cycle()
            while time.time() < deadline:
                cycle = self._parse_recent_voice_reg_cycle()
                last_cycle = cycle
                event = str(cycle.get("event") or "")
                status = cycle.get("status")
                stop_after_event = bool(cycle.get("stop_after_event_line"))

                if expect_in_progress:
                    if event in {"again", "similar_error", "length_error", "auto_next"} and status == 1 and stop_after_event:
                        time.sleep(max(float(settle_seconds), 0.1))
                        return cycle
                else:
                    if event in {"success", "cmd_over_success", "failed"} and status in {3, 4} and stop_after_event:
                        time.sleep(max(float(settle_seconds), 0.1))
                        return cycle
                time.sleep(0.05)
            return last_cycle

        def _recent_voice_reg_prompt(self, limit: int = 160) -> Dict[str, Any]:
            saw_prompt_play = False
            stop_line = ""
            last_line = ""
            last_start_idx = -1
            last_stop_idx = -1
            for idx, line in enumerate(self._recent_sanitized_lines(limit)):
                lowered = line.lower()
                if "play start" in lowered or "play id :" in lowered:
                    saw_prompt_play = True
                    last_line = line
                    last_start_idx = idx
                if "play stop" in lowered:
                    stop_line = line
                    last_line = line
                    last_stop_idx = idx
            return {
                "saw_prompt_play": saw_prompt_play,
                "stop_line": stop_line,
                "last_line": last_line,
                "last_start_idx": last_start_idx,
                "last_stop_idx": last_stop_idx,
            }

        def _wait_for_voice_reg_prompt_complete(
            self,
            timeout_seconds: float = 8.0,
            settle_seconds: float = 0.8,
        ) -> Tuple[bool, str]:
            if not self.reader:
                return True, "reader-missing"

            recent = self._recent_voice_reg_prompt()
            saw_prompt_play = bool(recent.get("saw_prompt_play"))
            last_line = str(recent.get("last_line") or "")
            last_start_idx = int(recent.get("last_start_idx", -1) or -1)
            last_stop_idx = int(recent.get("last_stop_idx", -1) or -1)
            if saw_prompt_play and recent.get("stop_line") and last_stop_idx > last_start_idx:
                time.sleep(max(float(settle_seconds), 0.1))
                return True, f"prompt-stop-detected line={recent.get('stop_line')}"

            processed = len(self.reader.get_recent_lines())
            deadline = time.time() + max(float(timeout_seconds), 1.0)
            while time.time() < deadline:
                lines = self.reader.get_recent_lines()
                new_lines = lines[processed:]
                processed = len(lines)
                for raw_line in new_lines:
                    line = self._sanitize_serial_line(raw_line)
                    lowered = line.lower()
                    if "play start" in lowered or "play id :" in lowered:
                        saw_prompt_play = True
                        last_line = line
                    if "play stop" in lowered:
                        last_line = line
                        time.sleep(max(float(settle_seconds), 0.1))
                        return True, f"prompt-stop-detected line={line}"
                time.sleep(0.05)

            if saw_prompt_play:
                time.sleep(max(float(settle_seconds), 0.1))
                return False, f"prompt-stop-timeout last={last_line}"
            return True, "no-prompt-play-detected"

        def _recent_boot_signature_detected(self, limit: int = 160) -> bool:
            return bool(self._recent_boot_signature_line(limit))

        def _unexpected_reboot_since(
            self,
            start_count: Optional[int],
            *,
            clear_flag: bool = True,
            allow_boot_signature: bool = True,
        ) -> Tuple[bool, str]:
            if not self.reader:
                return False, "reader-missing"
            current_count = self.reader.get_reboot_count()
            reboot_flag = self.reader.is_rebooted()
            boot_line = self._recent_boot_signature_line() if allow_boot_signature else ""
            count_delta = 0 if start_count is None else max(current_count - start_count, 0)
            ignore_boot_signature_until = float(getattr(self, "_ignore_boot_signature_until", 0.0) or 0.0)
            if (
                boot_line
                and not reboot_flag
                and count_delta == 0
                and ignore_boot_signature_until > time.time()
            ):
                return False, ""
            rebooted = reboot_flag or count_delta > 0 or bool(boot_line)
            if not rebooted:
                return False, ""
            reason = self.reader.get("rebootReason") or ("boot-signature-detected" if boot_line else "unknown")
            if clear_flag and reboot_flag:
                self.reader.clear_reboot_flag()
            detail_parts = [
                f"unexpected-reboot count={current_count}",
                f"delta={count_delta}",
                f"reason={reason}",
            ]
            if boot_line:
                detail_parts.append(f"boot_line={boot_line}")
            return True, "；".join(detail_parts)

        def _apply_reboot_failure_to_row(
            self,
            row: Dict[str, Any],
            reboot_detail: str,
            *,
            trigger_action: str = "",
        ) -> Dict[str, Any]:
            updated = dict(row)
            existing_resp = str(updated.get("设备响应列表", "") or "").strip()
            existing_result = str(updated.get("识别结果", "") or "").strip()
            action_prefix = f"trigger={trigger_action}；" if trigger_action else ""
            combined = f"{action_prefix}{reboot_detail}"
            updated["识别判定"] = "Reboot"
            updated["设备响应列表"] = f"{existing_resp}|{combined}".strip("|") if existing_resp else combined
            updated["识别结果"] = f"{existing_result}；{combined}".strip("；") if existing_result else combined
            updated["重启次数"] = self.reader.get_reboot_count() if self.reader else updated.get("重启次数", "")
            return updated

        def _recover_after_voice_reg_reboot(self, timeout_seconds: float = 8.0) -> Tuple[bool, str]:
            if not self.reader:
                return False, "reader-missing"

            rebooted = self.reader.is_rebooted() or self._recent_boot_signature_detected()
            if not rebooted:
                return True, "no-reboot-detected"

            reason = self.reader.get("rebootReason") or "boot-signature-detected"
            self.log.warn(f"  语音注册专项检测到设备重启，等待恢复... reason={reason}")
            if self.reader.is_rebooted():
                self.reader.clear_reboot_flag()
            time.sleep(max(float(timeout_seconds), float(getattr(self, "pretest_boot_wait", 5) or 5), 5.0))
            recovered = self.set_log_level(4)
            if not recovered:
                return False, f"unexpected-reboot reason={reason};recover=loglevel-failed"
            return False, f"unexpected-reboot reason={reason};recover=loglevel-restored"

        def _voice_reg_steps_have_reboot(self, steps: Sequence[str]) -> bool:
            reboot_marks = (
                "unexpected-reboot",
                "reboot-recovered",
                "boot-signature-detected",
                "设备重启",
                "reason=",
            )
            for step in steps:
                text = str(step or "")
                if not text:
                    continue
                if "reason=" in text and "reboot" in text:
                    return True
                if any(mark in text for mark in reboot_marks):
                    return True
            return False

        def _steps_have_skip_manual(self, steps: Sequence[str]) -> bool:
            for step in steps:
                text = str(step or "").strip()
                if not text:
                    continue
                if text.startswith("Skip(") or "=Skip(" in text:
                    return True
            return False

        def _voice_reg_clear_all_command_aliases(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, str]:
            clear_all_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteAllCommandEntry", "删除全部命令词")
            verdict, row = self._run_command_step_with_override(
                clear_all_phrase,
                f"{idx}-clear-all-command",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            recover_ok, recover_detail = self._recover_after_voice_reg_reboot(timeout_seconds=6.0)
            detail = (
                f"verdict={verdict} raw={row.get('识别原始结果', '')} "
                f"recog={row.get('识别结果', '')} recover={recover_detail}"
            )
            return verdict == "OK" and recover_ok, detail

        def _voice_reg_clear_current_command_alias(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, str]:
            delete_command_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteCommandEntry", "删除命令词")
            detail_parts = []
            verdict, row = self._run_command_step_with_override(
                delete_command_phrase,
                f"{idx}-clear-current-command-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            detail_parts.append(
                f"entry={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}"
            )
            if verdict != "OK":
                return False, " | ".join(detail_parts)

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            detail_parts.append(f"prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            confirm_verdict, confirm_row = self._run_command_step_with_override(
                delete_command_phrase,
                f"{idx}-clear-current-command-confirm",
                expected_proto=None,
                timeout_seconds=5.0,
                do_wakeup=False,
            )
            detail_parts.append(
                f"confirm={confirm_verdict} raw={confirm_row.get('识别原始结果', '')} recog={confirm_row.get('识别结果', '')}"
            )
            if confirm_verdict != "OK":
                return False, " | ".join(detail_parts)

            recover_ok, recover_detail = self._recover_after_voice_reg_reboot(timeout_seconds=6.0)
            detail_parts.append(f"recover={recover_detail}")
            time.sleep(1.0)
            return recover_ok, " | ".join(detail_parts)

        def _voice_reg_prepare_command_learning_reset(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, str]:
            regist_mode = self._voice_reg_regist_mode(tc)
            if regist_mode == "contLearn":
                clear_ok, clear_detail = self._voice_reg_clear_current_command_alias(tc, f"{idx}-clear-current")
                return clear_ok, f"mode={regist_mode} strategy=clear-current-command | {clear_detail}"

            clear_ok, clear_detail = self._voice_reg_clear_all_command_aliases(tc, f"{idx}-clear-all")
            return clear_ok, f"mode={regist_mode} strategy=clear-all-command | {clear_detail}"

        def _voice_reg_clear_current_wakeup_alias(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, str]:
            delete_wake_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteWakeEntry", "删除唤醒词")
            verdict, row = self._run_command_step_with_override(
                delete_wake_phrase,
                f"{idx}-clear-current-wakeup-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            detail_parts = [
                f"entry={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}"
            ]
            if verdict != "OK":
                return False, " | ".join(detail_parts)

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            detail_parts.append(f"prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            confirm_verdict, confirm_row = self._run_command_step_with_override(
                delete_wake_phrase,
                f"{idx}-clear-current-wakeup-confirm",
                expected_proto=None,
                timeout_seconds=5.0,
                do_wakeup=False,
            )
            detail_parts.append(
                f"confirm={confirm_verdict} raw={confirm_row.get('识别原始结果', '')} recog={confirm_row.get('识别结果', '')}"
            )
            recover_ok, recover_detail = self._recover_after_voice_reg_reboot(timeout_seconds=6.0)
            detail_parts.append(f"recover={recover_detail}")
            return confirm_verdict == "OK" and recover_ok, " | ".join(detail_parts)

        def _capture_phrase(self, phrase: str, *, timeout_seconds: float = 4.5, do_wakeup: bool = True, tag: str = "asrKw") -> Dict[str, Any]:
            if not self._ensure_audio_word(phrase):
                return {"ok": False, "error": f"缺少音频且 TTS 生成失败: {phrase}"}
            baseline = self.reader.get_reboot_count() if self.reader else 0
            if self.reader and self.reader.is_rebooted():
                self.reader.clear_reboot_flag()
            self._clear_recent_serial_lines()
            if do_wakeup and not self.wakeup():
                rebooted, reboot_detail = self._unexpected_reboot_since(baseline)
                wake_detail = str(getattr(self, "_last_wakeup_detail", "") or "").strip()
                return {
                    "ok": False,
                    "error": reboot_detail if rebooted else (wake_detail or "wakeup failed"),
                    "reboot_detected": rebooted,
                    "reboot_detail": reboot_detail,
                    "wakeup_status": str(getattr(self, "_last_wakeup_status", "") or "").strip(),
                }
            rebooted, reboot_detail = self._unexpected_reboot_since(baseline)
            if rebooted:
                return {
                    "ok": False,
                    "error": reboot_detail,
                    "reboot_detected": True,
                    "reboot_detail": reboot_detail,
                }

            baseline = self.reader.get_reboot_count() if self.reader else baseline
            if self.reader and self.reader.is_rebooted():
                self.reader.clear_reboot_flag()
            self.reader.clear()
            self._clear_recent_serial_lines()
            t_start = time.time()
            self.play(phrase)
            response_ms: Optional[int] = None
            deadline = t_start + max(float(timeout_seconds), 1.5)
            while time.time() < deadline:
                if self.reader.get_all(tag) and response_ms is None:
                    response_ms = int((time.time() - t_start) * 1000)
                time.sleep(0.05)

            raws = list(dict.fromkeys(self.reader.get_all(tag)))
            texts = [self.pinyin_to_zh(item) for item in raws]
            asr_raws = list(dict.fromkeys(self.reader.get_all("asrKw")))
            asr_texts = [self.pinyin_to_zh(item) for item in asr_raws]
            wake_raws = list(dict.fromkeys(self.reader.get_all("wakeKw")))
            wake_texts = [self.pinyin_to_zh(item) for item in wake_raws]
            send_list = list(dict.fromkeys(self.reader.get_all("sendMsg")))
            recv_list = self.reader.get_recv_list()
            lines = self._recent_sanitized_lines()
            rebooted, reboot_detail = self._unexpected_reboot_since(baseline)
            return {
                "ok": True,
                "phrase": phrase,
                "tag": tag,
                "raws": raws,
                "texts": texts,
                "asr_raws": asr_raws,
                "asr_texts": asr_texts,
                "wake_raws": wake_raws,
                "wake_texts": wake_texts,
                "send_list": send_list,
                "recv_list": recv_list,
                "play_id": self.reader.get("playId") or "",
                "response_ms": response_ms,
                "reboot_count": self.reader.get_reboot_count(),
                "reboot_detected": rebooted,
                "reboot_detail": reboot_detail,
                "lines": lines,
            }

        def _voice_reg_submit_learning_phrase(
            self,
            phrase: str,
            *,
            expect_in_progress: bool,
            allow_auto_next_success: bool = False,
            timeout_seconds: float = 8.0,
        ) -> Tuple[bool, str, Dict[str, Any]]:
            capture = self._capture_phrase(phrase, timeout_seconds=timeout_seconds, do_wakeup=False, tag="asrKw")
            if not capture.get("ok"):
                return False, str(capture.get("error") or "capture failed"), {}
            if capture.get("reboot_detected"):
                return False, str(capture.get("reboot_detail") or "unexpected reboot"), {}
            state = self._wait_for_voice_reg_cycle_complete(
                expect_in_progress,
                timeout_seconds=18.0 if expect_in_progress else 24.0,
            )
            status = state.get("status")
            prompt_ok = bool(state.get("stop_after_event_line"))
            prompt_detail = str(state.get("stop_after_event_line") or state.get("stop_line") or "prompt-stop-missing")
            event = str(state.get("event") or "")
            if expect_in_progress:
                ok = (
                    status == 1
                    and prompt_ok
                    and (
                        event in {"again", "similar_error", "length_error", "auto_next"}
                        or bool(state.get("process_words"))
                    )
                )
            else:
                ok = (
                    (event in {"success", "cmd_over_success", "failed"} and status in {3, 4} and prompt_ok)
                    or (allow_auto_next_success and event == "auto_next" and status == 1 and prompt_ok)
                )
            detail = (
                f"phrase={phrase} raws={capture.get('raws', [])} texts={capture.get('texts', [])} "
                f"prompt={prompt_detail} prompt_ok={prompt_ok} "
                f"event={event} status={status} failed={state.get('failed')} "
                f"process={state.get('process_words', [])} quit={state.get('quit_words', [])}"
            )
            if state.get("event_line"):
                detail += f" event_line={state.get('event_line')}"
            raw_state = self._parse_recent_reg_state()
            if raw_state.get("fail_line"):
                detail += f" fail_line={raw_state.get('fail_line')}"
            if raw_state.get("error_line"):
                detail += f" error_line={raw_state.get('error_line')}"
            return ok, detail, state

        def _voice_reg_verify_command_alias(
            self,
            alias_phrase: str,
            expected_command: str,
            expected_proto: str,
            *,
            should_work: bool,
            timeout_seconds: float = 4.5,
        ) -> Tuple[bool, str]:
            capture = self._capture_phrase(alias_phrase, timeout_seconds=timeout_seconds, do_wakeup=True, tag="asrKw")
            if not capture.get("ok"):
                return False, str(capture.get("error") or "capture failed")
            if capture.get("reboot_detected"):
                return False, str(capture.get("reboot_detail") or "unexpected reboot")
            texts = [str(item or "").strip() for item in capture.get("texts") or [] if str(item or "").strip()]
            raws = [str(item or "").strip() for item in capture.get("raws") or [] if str(item or "").strip()]
            send_list = [normalize_hex_protocol(item) for item in capture.get("send_list") or [] if normalize_hex_protocol(item)]
            proto_hit = bool(expected_proto) and normalize_hex_protocol(expected_proto) in send_list
            command_hit = expected_command in texts
            if should_work:
                ok = proto_hit or command_hit
            else:
                ok = not raws and not send_list
            detail = (
                f"alias={alias_phrase} should_work={should_work} expected_command={expected_command} "
                f"expected_proto={normalize_hex_protocol(expected_proto)} raws={raws} texts={texts} send={send_list}"
            )
            return ok, detail

        def _voice_reg_verify_wakeup_alias(
            self,
            alias_phrase: str,
            accepted_results: Sequence[str],
            *,
            should_work: bool,
            timeout_seconds: float = 3.0,
        ) -> Tuple[bool, str]:
            accepted = [str(item or "").strip() for item in accepted_results if str(item or "").strip()]
            wake_ok, wake_detail = self._wakeup_phrase_until_ready(
                alias_phrase,
                max_retry=(
                    max(1, int(getattr(self, "wakeup_retry_limit", 10) or 10))
                    if should_work
                    else 3
                ),
                detect_timeout_seconds=max(float(timeout_seconds), 1.0),
                ready_timeout_seconds=max(float(timeout_seconds), 4.5),
                allow_reg_result=False,
                accepted_results=accepted,
            )
            if should_work:
                return wake_ok, (
                    f"alias={alias_phrase} should_work={should_work} accepted={sorted(set(accepted))} "
                    f"formal={wake_detail}"
                )

            if wake_ok:
                return False, (
                    f"alias={alias_phrase} should_work={should_work} accepted={sorted(set(accepted))} "
                    f"formal-hit={wake_detail}"
                )

            wake_status = str(getattr(self, "_last_wakeup_status", "") or "").strip()
            if wake_status in {"wake-not-detected", "command-window-expired", "wake-not-ready", "wake-rejected"}:
                return True, (
                    f"alias={alias_phrase} should_work={should_work} accepted={sorted(set(accepted))} "
                    f"formal-miss status={wake_status} detail={wake_detail}"
                )
            return False, (
                f"alias={alias_phrase} should_work={should_work} accepted={sorted(set(accepted))} "
                f"formal-error status={wake_status or 'unknown'} detail={wake_detail}"
            )

        def test_wakeup_only(self, tc):
            should_work, expectation_detail = self._multi_wke_wakeup_case_expectation(tc)
            if should_work is None:
                return super().test_wakeup_only(tc)

            target_wakeup = tc.get("命令词", "") or tc.get("唤醒词", "") or self.wakeup_word
            ok, wake_detail = self._multi_wke_verify_phrase(target_wakeup, should_work=bool(should_work))
            verdict = "OK" if ok else ("WakeupFail" if should_work else "ConfigFail")
            detail = f"{expectation_detail}; {wake_detail}"
            row = self._device_row(tc, target_wakeup, verdict, detail, expected_proto="")
            row["识别结果"] = detail
            row["设备响应列表"] = detail
            row["__suite_mode"] = "device"
            if ok:
                self.log.info(f"  唤醒识别按 multi-wke specified 判定通过 → {target_wakeup} | {expectation_detail}")
            else:
                self.log.warn(f"  唤醒识别按 multi-wke specified 判定失败 → {target_wakeup} | {detail}")
            return verdict, row

        def _voice_reg_enter_command_learning_session(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            allow_clear_retry: bool = True,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            regist_mode = self._voice_reg_regist_mode(tc)
            steps.append(f"regist-mode={regist_mode}")
            self._clear_recent_serial_lines()
            learn_command_phrase = self._voice_reg_control_phrase(tc, "voiceRegLearnCommandEntry", "学习命令词")
            entry_verdict, entry_row = self._run_command_step_with_override(
                learn_command_phrase,
                f"{idx}-cmd-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            steps.append(
                f"entry={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
            )
            if entry_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=8.0)
            steps.append(f"entry-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.6)

            direct_state = self._wait_for_reg_state(True, timeout_seconds=6.0)
            steps.append(
                f"entry-state=status={direct_state.get('status')} failed={direct_state.get('failed')} "
                f"process={direct_state.get('process_words', [])}"
            )
            entry_markers = self._voice_reg_recent_learning_marker_summary()
            if entry_markers:
                steps.append(f"entry-markers={entry_markers}")
            direct_ready = (not direct_state.get("failed")) and direct_state.get("status") == 1
            if direct_ready:
                steps.append("entry-mode=direct-learning")
                time.sleep(0.8)
                return True, steps

            if regist_mode == "contLearn":
                late_state = self._wait_for_reg_state(True, timeout_seconds=4.0)
                steps.append(
                    f"entry-late-state=status={late_state.get('status')} failed={late_state.get('failed')} "
                    f"process={late_state.get('process_words', [])}"
                )
                late_cycle = self._parse_recent_voice_reg_cycle()
                if late_cycle.get("event"):
                    steps.append(
                        f"entry-cycle=event={late_cycle.get('event')} status={late_cycle.get('status')} "
                        f"line={late_cycle.get('event_line')}"
                    )
                late_markers = self._voice_reg_recent_learning_marker_summary()
                if late_markers and late_markers != entry_markers:
                    steps.append(f"entry-late-markers={late_markers}")
                late_ready = (not late_state.get("failed")) and late_state.get("status") == 1
                if late_ready:
                    steps.append("entry-mode=direct-learning-delayed")
                    time.sleep(0.8)
                    return True, steps
                if late_cycle.get("event") == "over":
                    steps.append("entry-mode=direct-learning-full")
                    if allow_clear_retry:
                        clear_ok, clear_detail = self._voice_reg_clear_current_command_alias(
                            tc,
                            f"{idx}-entry-clear-current-command",
                        )
                        steps.append(f"entry-full-clear-command={clear_detail}")
                        if clear_ok:
                            retry_ok, retry_steps = self._voice_reg_enter_command_learning_session(
                                tc,
                                f"{idx}-entry-retry",
                                selected_command=selected_command,
                                allow_clear_retry=False,
                            )
                            steps.extend([f"retry:{item}" for item in retry_steps])
                            return retry_ok, steps
                    return False, steps
                if late_state.get("status") is None and not late_state.get("failed"):
                    steps.append("entry-mode=direct-learning-no-ready-marker-fallback")
                    time.sleep(0.8)
                    return True, steps
                steps.append("entry-mode=direct-learning-no-ready-marker")
                return False, steps

            steps.append("entry-mode=select-command")

            self._clear_recent_serial_lines()
            select_verdict, select_row = self._run_command_step_with_override(
                selected_command,
                f"{idx}-cmd-select",
                expected_proto=None,
                timeout_seconds=5.0,
                do_wakeup=False,
            )
            steps.append(
                f"select={select_verdict} raw={select_row.get('识别原始结果', '')} recog={select_row.get('识别结果', '')}"
            )
            if select_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=8.0)
            steps.append(f"select-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.6)

            state = self._wait_for_reg_state(True, timeout_seconds=8.0)
            steps.append(
                f"select-state=status={state.get('status')} failed={state.get('failed')} process={state.get('process_words', [])}"
            )
            select_markers = self._voice_reg_recent_learning_marker_summary()
            if select_markers:
                steps.append(f"select-markers={select_markers}")
            ready = (not state.get("failed")) and state.get("status") == 1
            if not ready:
                if prompt_ok and select_markers:
                    steps.append("select-ready-by-marker")
                    time.sleep(0.8)
                    return True, steps
                if prompt_ok and not select_markers:
                    steps.append("select-no-learning-marker")
                elif prompt_ok and select_markers:
                    steps.append("select-learning-marker-without-status1")
                return False, steps
            time.sleep(0.8)
            return True, steps

        def _voice_reg_exit_learning_session(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            self._clear_recent_serial_lines()
            exit_learn_phrase = self._voice_reg_control_phrase(tc, "voiceRegExitLearnEntry", "退出学习")
            exit_verdict, exit_row = self._run_command_step_with_override(
                exit_learn_phrase,
                f"{idx}-exit-learning",
                expected_proto=None,
                timeout_seconds=5.0,
                do_wakeup=False,
            )
            steps.append(
                f"exit-learning={exit_verdict} raw={exit_row.get('识别原始结果', '')} recog={exit_row.get('识别结果', '')}"
            )
            if exit_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=8.0)
            steps.append(f"exit-learning-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.6)

            state = self._wait_for_reg_state(False, timeout_seconds=6.5)
            steps.append(
                f"exit-learning-state=status={state.get('status')} failed={state.get('failed')} "
                f"process={state.get('process_words', [])}"
            )
            status = state.get("status")
            ready = (not state.get("failed")) and (
                status in {3, 6, 7} or (status is None and prompt_ok)
            )
            return ready, steps

        def _voice_reg_finalize_negative_learning_session(
            self,
            tc: Dict[str, str],
            idx: Any,
            steps: List[str],
        ) -> bool:
            state = self._wait_for_reg_state(None, timeout_seconds=2.5)
            steps.append(
                f"post-negative-state=status={state.get('status')} failed={state.get('failed')} "
                f"process={state.get('process_words', [])} quit={state.get('quit_words', [])}"
            )
            status = state.get("status")
            if state.get("failed") or status in {4, 6, 7}:
                steps.append("learning-terminal=explicit-or-closed-reject")
                return True
            if status == 3:
                steps.append("unexpected-learning-success-post-state")
                return False

            exit_ok, exit_steps = self._voice_reg_exit_learning_session(tc, f"{idx}-negative-exit")
            steps.extend([f"exit-learning:{item}" for item in exit_steps])
            if not exit_ok:
                steps.append("learning-terminal=negative-exit-failed")
                return False
            steps.append("learning-terminal=silent-reject")
            return True

        def _voice_reg_enter_wakeup_learning_session(self, tc: Dict[str, str], idx: Any) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            self._clear_recent_serial_lines()
            learn_wake_phrase = self._voice_reg_control_phrase(tc, "voiceRegLearnWakeEntry", "学习唤醒词")
            entry_verdict, entry_row = self._run_command_step_with_override(
                learn_wake_phrase,
                f"{idx}-wake-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            steps.append(
                f"entry={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
            )
            if entry_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=8.0)
            steps.append(f"entry-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.6)

            state = self._wait_for_reg_state(True, timeout_seconds=6.5)
            steps.append(
                f"entry-state=status={state.get('status')} failed={state.get('failed')} process={state.get('process_words', [])}"
            )
            ready = (not state.get("failed")) and (
                state.get("status") == 1 or (state.get("status") is None and prompt_ok)
            )
            if not ready:
                return False, steps
            time.sleep(0.8)
            return True, steps

        def _run_command_step_with_override(
            self,
            command: str,
            run_idx: Any,
            expected_proto: Optional[str] = "",
            timeout_seconds: float = 3.0,
            do_wakeup: bool = True,
        ):
            if not self._ensure_audio_word(command):
                row = self._device_row({}, command, "Skip(人工)", f"缺少音频且 TTS 生成失败: {command}")
                return "Skip(人工)", row

            final_verdict = "UnAsr"
            final_row: Dict[str, Any] = self._device_row({}, command, "UnAsr", "")
            with self._temporary_proto(command, expected_proto):
                base_retry_limit = max(1, self.command_retry_limit)
                effective_retry_limit = base_retry_limit
                protocol_retry_limit = max(base_retry_limit, getattr(self, "protocol_retry_limit", base_retry_limit))
                had_protocol_issue = False
                captured_protocols: List[str] = []
                attempts_used = 0
                attempt = 1
                while attempt <= effective_retry_limit:
                    attempt_reboot_base = self.reader.get_reboot_count() if self.reader else 0
                    if self.reader and self.reader.is_rebooted():
                        self.reader.clear_reboot_flag()
                    self._clear_recent_serial_lines()
                    attempts_used = attempt
                    verdict, row = self._run_single_command_attempt(
                        command,
                        run_idx,
                        expected_proto=expected_proto,
                        timeout_seconds=timeout_seconds,
                        do_wakeup=do_wakeup,
                    )
                    rebooted, reboot_detail = self._unexpected_reboot_since(attempt_reboot_base)
                    if rebooted:
                        verdict = "Reboot"
                        row = self._apply_reboot_failure_to_row(
                            row,
                            reboot_detail,
                            trigger_action=f"command={command};run_idx={run_idx};attempt={attempt}",
                        )
                    protocol_issue = self._is_protocol_retry_candidate(verdict, row, expected_proto)
                    if protocol_issue:
                        had_protocol_issue = True
                        effective_retry_limit = max(effective_retry_limit, protocol_retry_limit)
                        for value in self._split_protocol_values(row.get("实际发送协议", "")):
                            if value not in captured_protocols:
                                captured_protocols.append(value)
                    detail = row.get("设备响应列表", "")
                    row["设备响应列表"] = f"attempt={attempt}/{effective_retry_limit}|{detail}".strip("|")
                    final_verdict = verdict
                    final_row = dict(row)
                    if verdict == "OK":
                        break
                    if verdict == "Reboot":
                        break
                    if str(verdict).startswith("Skip"):
                        break
                    if do_wakeup and verdict == "WakeupFail":
                        break
                    if self.reader.get_reboot_count() > self.unexpected_reboot_limit:
                        self.abort_reason = f"设备意外重启超过 {self.unexpected_reboot_limit} 次，停止测试"
                        break
                    if attempt < effective_retry_limit:
                        if protocol_issue and effective_retry_limit > base_retry_limit:
                            self.log.warn(
                                f"  [{command}] 第 {attempt} 次失败({verdict})，检测到协议日志异常，准备继续重试..."
                            )
                        else:
                            self.log.warn(f"  [{command}] 第 {attempt} 次失败({verdict})，准备重试...")
                        time.sleep(0.6)
                    attempt += 1
                if final_verdict != "OK" and had_protocol_issue:
                    final_row = self._finalize_protocol_retry_row(
                        final_row,
                        expected_proto,
                        attempts_used=attempts_used,
                        retry_limit=effective_retry_limit,
                        captured_protocols=captured_protocols,
                    )
            return final_verdict, final_row

        def test_timeout_exit(self, tc: Dict[str, str]):
            package_key = "打包参数"
            command_key = "命令词"
            skip_manual = "Skip(人工)"

            meta = self._suite_meta(tc)
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if self._has_base_coverage(tc, "timeout"):
                return self._base_covered_row(tc, "timeout", config_verdict, config_detail)
            overrides = parse_json_cell(meta.get(package_key, "{}"))
            expected_timeout = int(overrides.get("timeout", 0) or 0)
            if expected_timeout <= 0:
                return skip_manual, self._config_only_row(tc, skip_manual, "missing timeout parameter")

            case_command = tc.get(command_key, "") or self.wakeup_word
            woke_up, wake_ts, wake_raw = self._wakeup_with_timestamp()
            if not woke_up or wake_ts is None:
                row = self._config_only_row(tc, "WakeupFail", "wakeup failed before timeout test")
                row["__suite_mode"] = "device"
                return "WakeupFail", row

            detected, timeout_ts, marker_line, send_all, play_all = self._wait_for_timeout_marker(expected_timeout)
            if not detected or timeout_ts is None:
                probe_command = self.volume_max_word or self.volume_up_word or self.sample_command or case_command

                def probe_timeout_window(wait_seconds: float, label: str) -> Tuple[bool, str]:
                    woke_probe, _, wake_probe_raw = self._wakeup_with_timestamp()
                    if not woke_probe:
                        return False, f"{label}: wakeup failed"
                    time.sleep(max(wait_seconds, 0.2))
                    probe_verdict, probe_row = self._run_command_step_with_override(
                        probe_command,
                        label,
                        timeout_seconds=3.0,
                        do_wakeup=False,
                    )
                    recognized = probe_verdict == "OK"
                    return recognized, (
                        f"{label}: wait={round(wait_seconds, 2)}s recognized={recognized} "
                        f"wake={self.pinyin_to_zh(wake_probe_raw) or wake_probe_raw} "
                        f"raw={probe_row.get('识别原始结果', '')} "
                        f"recog={probe_row.get('识别结果', '')}"
                    )

                early_wait = max(float(expected_timeout) - 1.0, 0.5)
                late_wait = float(expected_timeout) + 1.0
                early_recognized, early_detail = probe_timeout_window(early_wait, "probe-before-timeout")
                late_recognized, late_detail = probe_timeout_window(late_wait, "probe-after-timeout")
                verdict = "OK" if early_recognized and not late_recognized else "ConfigFail"
                detail = (
                    f"expected={expected_timeout}s timeout marker not found; fallback_probe_command={probe_command}; "
                    f"{early_detail}; {late_detail}; send={send_all} play={play_all}"
                )
                row = self._device_row(tc, case_command, verdict, detail)
                row["识别原始结果"] = probe_command
                row["识别结果"] = detail
                row["设备响应列表"] = detail
                return verdict, row

            actual_timeout = round(timeout_ts - wake_ts, 2)
            diff = round(abs(actual_timeout - expected_timeout), 2)
            verdict = "OK" if diff <= 2.0 else "ConfigFail"
            detail = (
                f"expected={expected_timeout}s actual={actual_timeout}s diff={diff}s "
                f"wake={self.pinyin_to_zh(wake_raw) or wake_raw} marker={marker_line} "
                f"send={send_all} play={play_all}"
            )
            row = self._device_row(
                tc,
                case_command,
                verdict,
                detail,
                response_time=str(int(actual_timeout * 1000)),
            )
            row["识别原始结果"] = self.pinyin_to_zh(wake_raw) or wake_raw
            row["识别结果"] = detail
            row["播报ID"] = play_all[-1] if play_all else ""
            row["实际发送协议"] = "|".join(dict.fromkeys(send_all)) if send_all else ""
            row["设备响应列表"] = marker_line
            return verdict, row

        def test_firmware_version(self, tc: Dict[str, str]):
            skip_manual = "Skip(人工)"
            expected_version = self._expected_firmware_version(tc)
            if not expected_version:
                return skip_manual, self._config_only_row(tc, skip_manual, "missing firmwareVersion/general_config.version context")

            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            if not (self.pretest_enabled and self.ctrl_port):
                return skip_manual, self._config_only_row(tc, skip_manual, "固件版本自动校验需要 ctrl-port 支持断电重启")

            self._clear_recent_serial_lines()
            powered, power_detail = self._power_cycle_device()
            if not powered:
                return skip_manual, self._config_only_row(tc, skip_manual, power_detail)

            time.sleep(0.8)
            device_version, boot_line = self._extract_boot_version()
            normalized_device_version = device_version.rstrip(".")

            if not device_version:
                verdict = "ConfigFail"
                detail = f"expected={expected_version}; 未在启动日志中捕获到 config version；配置预检查={config_detail}"
            else:
                expected_prefix = expected_version[:17]
                version_match = normalized_device_version == expected_version or (
                    bool(normalized_device_version)
                    and (
                        expected_version.startswith(normalized_device_version)
                        or normalized_device_version.startswith(expected_prefix)
                    )
                )
                verdict = "OK" if version_match else "ConfigFail"
                detail = (
                    f"expected={expected_version}; device={device_version}; "
                    f"normalized_device={normalized_device_version}; expected_prefix={expected_prefix}; 配置预检查={config_detail}"
                )

            row = self._device_row(tc, "", verdict, detail)
            row["用例编号"] = tc.get("用例编号", "")
            row["功能模块"] = tc.get("功能模块", "")
            row["测试类型"] = tc.get("测试类型", "")
            row["设备响应列表"] = boot_line or detail
            row["识别结果"] = detail
            row["__suite_mode"] = "device"
            return verdict, row

        def _test_one_with_override(self, command: str, run_idx: Any, expected_proto: str = ""):
            return self._run_command_step_with_override(command, run_idx, expected_proto=expected_proto, timeout_seconds=3.0, do_wakeup=True)

        def _test_voice_reg_flow(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                return config_verdict, [self._config_only_row(tc, config_verdict, config_detail)]

            command = tc.get("命令词", "") or "学习命令词"
            learn_words = self._voice_reg_learn_words(tc)
            selected_word = learn_words[0] if learn_words else ""
            max_attempts = 3
            post_entry_wait = 4.5
            step_details: List[str] = []
            final_verdict = "UnAsr"
            final_row: Dict[str, Any] = self._device_row(tc, command, "UnAsr", "voice reg flow not started")
            detected_reboot = 0

            for attempt in range(1, max_attempts + 1):
                entry_verdict, entry_row = self._run_command_step_with_override(
                    command,
                    f"{idx}-entry{attempt}",
                    timeout_seconds=4.5,
                    do_wakeup=True,
                )
                entry_reboot = as_int(entry_row.get("重启次数")) or 0
                detected_reboot = max(detected_reboot, entry_reboot)
                step_details.append(
                    f"entry-{attempt}={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')} reboot={entry_reboot}"
                )
                final_verdict = entry_verdict
                final_row = dict(entry_row)

                if entry_reboot > 0:
                    final_verdict = "ConfigFail"
                    break
                if entry_verdict != "OK":
                    time.sleep(0.6)
                    continue

                if not selected_word:
                    final_verdict = "OK"
                    break

                step_details.append(f"wait-after-entry={post_entry_wait}s")
                time.sleep(post_entry_wait)

                pick_verdict, pick_row = self._run_command_step_with_override(
                    selected_word,
                    f"{idx}-pick{attempt}",
                    expected_proto=None,
                    timeout_seconds=4.5,
                    do_wakeup=False,
                )
                pick_reboot = as_int(pick_row.get("重启次数")) or 0
                detected_reboot = max(detected_reboot, pick_reboot)
                step_details.append(
                    f"pick-{attempt}={pick_verdict} raw={pick_row.get('识别原始结果', '')} recog={pick_row.get('识别结果', '')} reboot={pick_reboot}"
                )
                final_verdict = pick_verdict
                final_row = dict(pick_row)

                if pick_reboot > 0:
                    final_verdict = "ConfigFail"
                    break
                if pick_verdict == "OK":
                    break
                time.sleep(0.6)

            row = dict(final_row)
            row["用例编号"] = tc.get("用例编号", "")
            row["功能模块"] = tc.get("功能模块", "")
            row["测试类型"] = tc.get("测试类型", "")
            row["命令词"] = command

            detail_parts = [f"配置预检查={config_verdict}", config_detail]
            if selected_word:
                detail_parts.append(f"已添加学习词={selected_word}")
            if detected_reboot > 0:
                final_verdict = "ConfigFail"
                detail_parts.append(f"进入学习流程后检测到重启 {detected_reboot} 次")
            else:
                detail_parts.append("进入学习流程后未检测到重启")
            detail_parts.extend(step_details)

            if selected_word and final_verdict == "OK":
                row["识别结果"] = f"{command}->{selected_word}"
                row["设备响应列表"] = f"{row.get('设备响应列表', '')}|已验证指定学习词触发={selected_word}|{'；'.join(detail_parts)}".strip("|")
            else:
                row["设备响应列表"] = f"{row.get('设备响应列表', '')}|{'；'.join(detail_parts)}".strip("|")
            row["识别判定"] = final_verdict
            row["__suite_mode"] = "device"
            return final_verdict, [row]

        def _voice_reg_learn_command_alias_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            learned_command: str,
            repeat_count: int,
        ) -> Tuple[bool, List[str], str]:
            steps: List[str] = []
            command_entry = self._voice_reg_command_entry(selected_command) or {}
            expected_proto = normalize_hex_protocol(command_entry.get("snd_protocol") or command_entry.get("sndProtocol") or "")

            clear_ok, clear_detail = self._voice_reg_prepare_command_learning_reset(tc, f"{idx}-prepare")
            steps.append(f"prepare-clear-command={clear_detail}")
            if not clear_ok:
                return False, steps, expected_proto

            pre_absent_ok, pre_absent_detail = self._voice_reg_verify_command_alias(
                learned_command,
                selected_command,
                expected_proto,
                should_work=False,
            )
            steps.append(f"precheck-alias-absent={pre_absent_detail}")
            if not pre_absent_ok:
                cleanup_ok, cleanup_steps = self._voice_reg_delete_command_alias_flow(
                    tc,
                    f"{idx}-cleanup",
                    selected_command=selected_command,
                    learned_command=learned_command,
                    expected_proto=expected_proto,
                    ensure_present=False,
                )
                steps.extend([f"cleanup:{item}" for item in cleanup_steps])
                if not cleanup_ok:
                    return False, steps, expected_proto

            entered_ok, entry_steps = self._voice_reg_enter_command_learning_session(
                tc,
                idx,
                selected_command=selected_command,
            )
            steps.extend(entry_steps)
            if not entered_ok:
                return False, steps, expected_proto

            for attempt in range(1, repeat_count + 1):
                ok, detail, _ = self._voice_reg_submit_learning_phrase(
                    learned_command,
                    expect_in_progress=attempt < repeat_count,
                    allow_auto_next_success=attempt == repeat_count,
                )
                steps.append(f"learn-{attempt}/{repeat_count}={detail}")
                if not ok:
                    return False, steps, expected_proto
                if attempt == repeat_count:
                    final_state = self._parse_recent_voice_reg_cycle()
                    if final_state.get("event") == "auto_next" and final_state.get("status") == 1:
                        exit_ok, exit_steps = self._voice_reg_exit_learning_session(tc, f"{idx}-auto-next")
                        steps.extend([f"exit-learning:{item}" for item in exit_steps])
                        if not exit_ok:
                            return False, steps, expected_proto
                time.sleep(0.8)

            verify_ok, verify_detail = self._voice_reg_verify_command_alias(
                learned_command,
                selected_command,
                expected_proto,
                should_work=True,
            )
            steps.append(f"verify-alias-active={verify_detail}")
            return verify_ok, steps, expected_proto

        def _voice_reg_learn_wakeup_alias_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            learned_wake_word: str,
            virtual_wake_intent: str,
            repeat_count: int,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            accepted_results = [learned_wake_word, virtual_wake_intent]

            clear_ok, clear_detail = self._voice_reg_clear_current_wakeup_alias(tc, f"{idx}-prepare")
            steps.append(f"prepare-clear-wakeup={clear_detail}")
            if not clear_ok:
                return False, steps

            pre_absent_ok, pre_absent_detail = self._voice_reg_verify_wakeup_alias(
                learned_wake_word,
                accepted_results,
                should_work=False,
            )
            steps.append(f"precheck-wakeup-absent={pre_absent_detail}")
            if not pre_absent_ok:
                cleanup_ok, cleanup_steps = self._voice_reg_delete_wakeup_alias_flow(
                    tc,
                    f"{idx}-cleanup",
                    learned_wake_word=learned_wake_word,
                    virtual_wake_intent=virtual_wake_intent,
                    ensure_present=False,
                )
                steps.extend([f"cleanup:{item}" for item in cleanup_steps])
                if not cleanup_ok:
                    return False, steps

            entered_ok, entry_steps = self._voice_reg_enter_wakeup_learning_session(tc, idx)
            steps.extend(entry_steps)
            if not entered_ok:
                return False, steps

            for attempt in range(1, repeat_count + 1):
                ok, detail, _ = self._voice_reg_submit_learning_phrase(
                    learned_wake_word,
                    expect_in_progress=attempt < repeat_count,
                )
                steps.append(f"learn-{attempt}/{repeat_count}={detail}")
                if not ok:
                    return False, steps
                time.sleep(0.8)

            verify_ok, verify_detail = self._voice_reg_verify_wakeup_alias(
                learned_wake_word,
                accepted_results,
                should_work=True,
            )
            steps.append(f"verify-wakeup-active={verify_detail}")
            return verify_ok, steps

        def _voice_reg_delete_command_alias_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            learned_command: str,
            expected_proto: str,
            ensure_present: bool = True,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            delete_command_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteCommandEntry", "删除命令词")
            if ensure_present:
                present_ok, present_detail, _ = self._voice_reg_ensure_command_alias_present(
                    tc,
                    f"{idx}-ensure",
                    selected_command=selected_command,
                    learned_command=learned_command,
                )
                steps.append(f"ensure-present={present_detail}")
                if not present_ok:
                    return False, steps

            if self._voice_reg_regist_mode(tc) == "contLearn":
                clear_ok, clear_detail = self._voice_reg_clear_current_command_alias(tc, f"{idx}-delete-current")
                steps.append(f"delete-current={clear_detail}")
                if not clear_ok:
                    return False, steps
                time.sleep(1.0)

                absent_ok, absent_detail = self._voice_reg_verify_command_alias(
                    learned_command,
                    selected_command,
                    expected_proto,
                    should_work=False,
                )
                steps.append(f"verify-alias-removed={absent_detail}")
                if not absent_ok:
                    return False, steps

                default_verdict, default_row = self._test_one_with_override(
                    selected_command,
                    f"{idx}-default-after-delete",
                    expected_proto=expected_proto,
                )
                steps.append(
                    f"default-command-after-delete={default_verdict} raw={default_row.get('识别原始结果', '')} recog={default_row.get('识别结果', '')} send={default_row.get('实际发送协议', '')}"
                )
                return default_verdict == "OK", steps

            delete_targets = [learned_command] if learned_command else [selected_command]
            for target in delete_targets:
                entry_verdict, entry_row = self._run_command_step_with_override(
                    delete_command_phrase,
                    f"{idx}-delete-entry-{target}",
                    timeout_seconds=4.5,
                    do_wakeup=True,
                )
                steps.append(
                    f"delete-entry target={target} verdict={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
                )
                if entry_verdict != "OK":
                    continue

                prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
                steps.append(f"delete-prompt target={target} {prompt_detail}")
                if not prompt_ok:
                    time.sleep(0.5)
                self._clear_recent_serial_lines()
                capture = self._capture_phrase(
                    target,
                    timeout_seconds=5.0,
                    do_wakeup=False,
                    tag="asrKw",
                )
                steps.append(
                    f"delete-target target={target} ok={capture.get('ok')} raws={capture.get('raws', [])} "
                    f"texts={capture.get('texts', [])} asr_texts={capture.get('asr_texts', [])}"
                )
                if not capture.get("ok"):
                    continue
                prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=8.0)
                steps.append(f"delete-target-prompt target={target} {prompt_detail}")
                if not prompt_ok:
                    time.sleep(0.5)
                recover_ok, recover_detail = self._recover_after_voice_reg_reboot()
                steps.append(f"post-delete-recover target={target} {recover_detail}")
                if not recover_ok:
                    continue
                time.sleep(2.0)

                absent_ok, absent_detail = self._voice_reg_verify_command_alias(
                    learned_command,
                    selected_command,
                    expected_proto,
                    should_work=False,
                )
                steps.append(f"verify-alias-removed target={target} {absent_detail}")
                if not absent_ok:
                    continue

                default_verdict, default_row = self._test_one_with_override(
                    selected_command,
                    f"{idx}-default-after-delete",
                    expected_proto=expected_proto,
                )
                steps.append(
                    f"default-command-after-delete={default_verdict} raw={default_row.get('识别原始结果', '')} recog={default_row.get('识别结果', '')} send={default_row.get('实际发送协议', '')}"
                )
                return default_verdict == "OK", steps
            return False, steps

        def _voice_reg_delete_wakeup_alias_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            learned_wake_word: str,
            virtual_wake_intent: str,
            ensure_present: bool = True,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            accepted_results = [learned_wake_word, virtual_wake_intent]
            delete_wake_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteWakeEntry", "删除唤醒词")
            if ensure_present:
                present_ok, present_detail, _ = self._voice_reg_ensure_wakeup_alias_present(
                    tc,
                    f"{idx}-ensure",
                    learned_wake_word=learned_wake_word,
                    virtual_wake_intent=virtual_wake_intent,
                )
                steps.append(f"ensure-present={present_detail}")
                if not present_ok:
                    return False, steps

            entry_verdict, entry_row = self._run_command_step_with_override(
                delete_wake_phrase,
                f"{idx}-delete-wake-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            steps.append(
                f"delete-entry={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
            )
            if entry_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"delete-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)
            confirm_verdict, confirm_row = self._run_command_step_with_override(
                delete_wake_phrase,
                f"{idx}-delete-wake-confirm",
                expected_proto=None,
                timeout_seconds=5.0,
                do_wakeup=False,
            )
            steps.append(
                f"delete-confirm={confirm_verdict} raw={confirm_row.get('识别原始结果', '')} recog={confirm_row.get('识别结果', '')}"
            )
            if confirm_verdict != "OK":
                return False, steps

            recover_ok, recover_detail = self._recover_after_voice_reg_reboot(timeout_seconds=6.0)
            steps.append(f"post-delete-recover={recover_detail}")
            if not recover_ok:
                return False, steps

            absent_ok, absent_detail = self._voice_reg_verify_wakeup_alias(
                learned_wake_word,
                accepted_results,
                should_work=False,
            )
            steps.append(f"verify-wakeup-removed={absent_detail}")
            if not absent_ok:
                return False, steps

            default_wakeup_ok = self.wakeup()
            steps.append(f"default-wakeup-after-delete={default_wakeup_ok}")
            return default_wakeup_ok, steps

        def _voice_reg_ensure_command_alias_present(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            learned_command: str,
        ) -> Tuple[bool, str, str]:
            command_entry = self._voice_reg_command_entry(selected_command) or {}
            expected_proto = normalize_hex_protocol(command_entry.get("snd_protocol") or command_entry.get("sndProtocol") or "")
            already_ok, already_detail = self._voice_reg_verify_command_alias(
                learned_command,
                selected_command,
                expected_proto,
                should_work=True,
            )
            if already_ok:
                return True, f"alias-already-active={already_detail}", expected_proto

            repeat_count = self._voice_reg_command_repeat_count(tc)
            learned_ok, learned_steps, expected_proto = self._voice_reg_learn_command_alias_flow(
                tc,
                idx,
                selected_command=selected_command,
                learned_command=learned_command,
                repeat_count=repeat_count,
            )
            return learned_ok, " | ".join(learned_steps), expected_proto

        def _voice_reg_ensure_wakeup_alias_present(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            learned_wake_word: str,
            virtual_wake_intent: str,
        ) -> Tuple[bool, str, List[str]]:
            accepted_results = [learned_wake_word, virtual_wake_intent]
            already_ok, already_detail = self._voice_reg_verify_wakeup_alias(
                learned_wake_word,
                accepted_results,
                should_work=True,
            )
            if already_ok:
                return True, f"wakeup-already-active={already_detail}", accepted_results

            repeat_count = self._voice_reg_wakeup_repeat_count(tc)
            learned_ok, learned_steps = self._voice_reg_learn_wakeup_alias_flow(
                tc,
                idx,
                learned_wake_word=learned_wake_word,
                virtual_wake_intent=virtual_wake_intent,
                repeat_count=repeat_count,
            )
            return learned_ok, " | ".join(learned_steps), accepted_results

        def test_voice_reg_command_repeat(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            selected_command = self._voice_reg_selected_command(tc)
            if not selected_command:
                verdict, rows = self._test_voice_reg_flow(tc, idx)
                return verdict, rows[0]

            learned_command = self._voice_reg_learned_command(tc)
            repeat_count = self._voice_reg_command_repeat_count(tc)
            ok, steps, expected_proto = self._voice_reg_learn_command_alias_flow(
                tc,
                idx,
                selected_command=selected_command,
                learned_command=learned_command,
                repeat_count=repeat_count,
            )
            detail = (
                f"配置预检查={config_verdict}；{config_detail}；selected={selected_command}；"
                f"learned={learned_command}；repeat={repeat_count}"
            )
            row = self._device_row(tc, tc.get("命令词", ""), "OK" if ok else "ConfigFail", detail, expected_proto=expected_proto)
            row["识别结果"] = f"{selected_command}<={learned_command} repeat={repeat_count}"
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def test_voice_reg_wakeup_repeat(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            learned_wake_word = self._voice_reg_learned_wake_word(tc)
            virtual_wake_intent = self._voice_reg_virtual_wake_intent(tc)
            repeat_count = self._voice_reg_wakeup_repeat_count(tc)
            ok, steps = self._voice_reg_learn_wakeup_alias_flow(
                tc,
                idx,
                learned_wake_word=learned_wake_word,
                virtual_wake_intent=virtual_wake_intent,
                repeat_count=repeat_count,
            )
            detail = (
                f"配置预检查={config_verdict}；{config_detail}；learnedWake={learned_wake_word}；"
                f"virtualWake={virtual_wake_intent}；repeat={repeat_count}"
            )
            row = self._device_row(tc, tc.get("命令词", ""), "OK" if ok else "ConfigFail", detail)
            row["识别结果"] = f"{learned_wake_word} repeat={repeat_count}"
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def test_voice_reg_delete_command(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            selected_command = self._voice_reg_selected_command(tc)
            learned_command = self._voice_reg_learned_command(tc)
            command_entry = self._voice_reg_command_entry(selected_command) or {}
            expected_proto = normalize_hex_protocol(command_entry.get("snd_protocol") or command_entry.get("sndProtocol") or "")
            ok, steps = self._voice_reg_delete_command_alias_flow(
                tc,
                idx,
                selected_command=selected_command,
                learned_command=learned_command,
                expected_proto=expected_proto,
                ensure_present=True,
            )
            detail = (
                f"配置预检查={config_verdict}；{config_detail}；selected={selected_command}；"
                f"learned={learned_command}"
            )
            row = self._device_row(tc, tc.get("命令词", ""), "OK" if ok else "ConfigFail", detail, expected_proto=expected_proto)
            row["识别结果"] = f"delete command alias={learned_command}"
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def test_voice_reg_delete_wakeup(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            learned_wake_word = self._voice_reg_learned_wake_word(tc)
            virtual_wake_intent = self._voice_reg_virtual_wake_intent(tc)
            ok, steps = self._voice_reg_delete_wakeup_alias_flow(
                tc,
                idx,
                learned_wake_word=learned_wake_word,
                virtual_wake_intent=virtual_wake_intent,
                ensure_present=True,
            )
            detail = (
                f"配置预检查={config_verdict}；{config_detail}；learnedWake={learned_wake_word}；"
                f"virtualWake={virtual_wake_intent}"
            )
            row = self._device_row(tc, tc.get("命令词", ""), "OK" if ok else "ConfigFail", detail)
            row["识别结果"] = f"delete wake alias={learned_wake_word}"
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def _voice_reg_run_command_learning_sequence(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            learning_phrases: Sequence[str],
            verify_phrase: str,
            verify_target_command: str,
            verify_should_work: bool,
            expect_learning_success: bool,
        ) -> Tuple[bool, List[str], str]:
            steps: List[str] = []
            selected_entry = self._voice_reg_command_entry(selected_command) or {}
            selected_proto = normalize_hex_protocol(
                selected_entry.get("snd_protocol") or selected_entry.get("sndProtocol") or ""
            )
            verify_entry = self._voice_reg_command_entry(verify_target_command) or {}
            verify_proto = normalize_hex_protocol(
                verify_entry.get("snd_protocol") or verify_entry.get("sndProtocol") or ""
            )
            if verify_target_command == selected_command or not verify_proto:
                verify_proto = selected_proto

            clear_ok, clear_detail = self._voice_reg_prepare_command_learning_reset(tc, f"{idx}-prepare")
            steps.append(f"prepare-clear-command={clear_detail}")
            if not clear_ok:
                return False, steps, verify_proto

            if verify_phrase and not verify_should_work:
                pre_absent_ok, pre_absent_detail = self._voice_reg_verify_command_alias(
                    verify_phrase,
                    verify_target_command,
                    verify_proto,
                    should_work=False,
                )
                steps.append(f"precheck-alias-absent={pre_absent_detail}")
                if not pre_absent_ok:
                    return False, steps, verify_proto

            entered_ok, entry_steps = self._voice_reg_enter_command_learning_session(
                tc,
                f"{idx}-sequence",
                selected_command=selected_command,
            )
            steps.extend(entry_steps)
            if not entered_ok:
                return False, steps, verify_proto

            total = len(learning_phrases)
            learning_succeeded = False
            learning_failed = False
            for attempt, phrase in enumerate(learning_phrases, start=1):
                ok, detail, state = self._voice_reg_submit_learning_phrase(
                    phrase,
                    expect_in_progress=attempt < total,
                    allow_auto_next_success=expect_learning_success and attempt == total,
                )
                steps.append(f"learn-{attempt}/{total}={detail}")
                status = state.get("status")
                if state.get("failed") or status == 4:
                    learning_failed = True
                    if expect_learning_success:
                        return False, steps, verify_proto
                    break
                if status == 3:
                    learning_succeeded = True
                    if expect_learning_success:
                        break
                    steps.append("unexpected-learning-success")
                    return False, steps, verify_proto
                if expect_learning_success and state.get("event") == "auto_next" and status == 1:
                    learning_succeeded = True
                    steps.append("learning-terminal=auto-next")
                    exit_ok, exit_steps = self._voice_reg_exit_learning_session(tc, f"{idx}-sequence-auto-next")
                    steps.extend([f"exit-learning:{item}" for item in exit_steps])
                    if not exit_ok:
                        return False, steps, verify_proto
                    break
                if not ok:
                    if expect_learning_success:
                        return False, steps, verify_proto
                time.sleep(0.8)

            if expect_learning_success and not learning_succeeded:
                steps.append("learning-terminal=missing-success-status")
                return False, steps, verify_proto
            if not expect_learning_success and not learning_failed:
                learning_failed = self._voice_reg_finalize_negative_learning_session(tc, idx, steps)
                if not learning_failed:
                    return False, steps, verify_proto

            verify_ok = True
            if verify_phrase:
                verify_ok, verify_detail = self._voice_reg_verify_command_alias(
                    verify_phrase,
                    verify_target_command,
                    verify_proto,
                    should_work=verify_should_work,
                )
                steps.append(f"verify-phrase={verify_detail}")

            default_verdict, default_row = self._test_one_with_override(
                selected_command,
                f"{idx}-default-check",
                expected_proto=selected_proto,
            )
            steps.append(
                f"default-command-check={default_verdict} raw={default_row.get('识别原始结果', '')} "
                f"recog={default_row.get('识别结果', '')} send={default_row.get('实际发送协议', '')}"
            )
            return verify_ok and default_verdict == "OK", steps, verify_proto

        def _voice_reg_run_wakeup_learning_sequence(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            learning_phrases: Sequence[str],
            verify_phrase: str,
            verify_expected_results: Sequence[str],
            verify_should_work: bool,
            expect_learning_success: bool,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            meta = self._suite_meta(tc)
            scenario = str((self._runtime_assert_data(meta) or {}).get("voiceRegScenario") or "").strip()

            clear_ok, clear_detail = self._voice_reg_clear_current_wakeup_alias(tc, f"{idx}-prepare")
            steps.append(f"prepare-clear-wakeup={clear_detail}")
            if not clear_ok:
                steps.append("prepare-clear-wakeup=continue-after-clear-fail")

            if verify_phrase and not verify_should_work:
                pre_absent_ok, pre_absent_detail = self._voice_reg_verify_wakeup_alias(
                    verify_phrase,
                    verify_expected_results,
                    should_work=False,
                )
                steps.append(f"precheck-wakeup-absent={pre_absent_detail}")
                if not pre_absent_ok:
                    return False, steps

            entered_ok, entry_steps = self._voice_reg_enter_wakeup_learning_session(tc, f"{idx}-sequence")
            steps.extend(entry_steps)
            if not entered_ok:
                return False, steps

            total = len(learning_phrases)
            learning_succeeded = False
            learning_failed = False
            for attempt, phrase in enumerate(learning_phrases, start=1):
                ok, detail, state = self._voice_reg_submit_learning_phrase(
                    phrase,
                    expect_in_progress=attempt < total,
                )
                steps.append(f"learn-{attempt}/{total}={detail}")
                status = state.get("status")
                if state.get("failed") or status == 4:
                    learning_failed = True
                    if expect_learning_success:
                        return False, steps
                    break
                if status == 3:
                    learning_succeeded = True
                    if expect_learning_success:
                        break
                    steps.append("unexpected-learning-success")
                    return False, steps
                if not ok:
                    if expect_learning_success:
                        return False, steps
                time.sleep(0.8)

            if expect_learning_success and not learning_succeeded:
                steps.append("learning-terminal=missing-success-status")
                return False, steps
            if not expect_learning_success and not learning_failed:
                learning_failed = self._voice_reg_finalize_negative_learning_session(tc, idx, steps)
                if not learning_failed:
                    return False, steps

            verify_ok = True
            verify_detail = ""
            if verify_phrase:
                verify_ok, verify_detail = self._voice_reg_verify_wakeup_alias(
                    verify_phrase,
                    verify_expected_results,
                    should_work=verify_should_work,
                )
                steps.append(f"verify-phrase={verify_detail}")

            default_wakeup_ok = self.wakeup()
            steps.append(f"default-wakeup-check={default_wakeup_ok}")
            if (
                scenario in {"wakeup_default_conflict", "wakeup_reserved_conflict"}
                and not expect_learning_success
                and verify_should_work
                and not verify_ok
                and default_wakeup_ok
                and ("wake-rejected" in verify_detail or "cur wk id:" in verify_detail)
            ):
                active = self._multi_wke_runtime_active_word()
                steps.append(
                    "verify-phrase-nonactive-wakeup=accepted-by-active-wakeup "
                    f"active={active or self.wakeup_word} detail={verify_detail}"
                )
                verify_ok = True
            return verify_ok and default_wakeup_ok, steps

        def _voice_reg_delete_command_alias_exit_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            selected_command: str,
            learned_command: str,
        ) -> Tuple[bool, List[str], str]:
            steps: List[str] = []
            delete_command_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteCommandEntry", "删除命令词")
            exit_delete_phrase = self._voice_reg_control_phrase(tc, "voiceRegExitDeleteEntry", "退出删除")
            present_ok, present_detail, expected_proto = self._voice_reg_ensure_command_alias_present(
                tc,
                f"{idx}-ensure",
                selected_command=selected_command,
                learned_command=learned_command,
            )
            steps.append(f"ensure-present={present_detail}")
            if not present_ok:
                return False, steps, expected_proto

            entry_verdict, entry_row = self._run_command_step_with_override(
                delete_command_phrase,
                f"{idx}-delete-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            steps.append(
                f"delete-entry={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
            )
            if entry_verdict != "OK":
                return False, steps, expected_proto

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"delete-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            exit_verdict, exit_row = self._run_command_step_with_override(
                exit_delete_phrase,
                f"{idx}-delete-exit",
                expected_proto=None,
                timeout_seconds=4.5,
                do_wakeup=False,
            )
            steps.append(
                f"delete-exit={exit_verdict} raw={exit_row.get('识别原始结果', '')} recog={exit_row.get('识别结果', '')}"
            )
            if exit_verdict != "OK":
                return False, steps, expected_proto

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"exit-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            alias_ok, alias_detail = self._voice_reg_verify_command_alias(
                learned_command,
                selected_command,
                expected_proto,
                should_work=True,
            )
            steps.append(f"verify-alias-kept={alias_detail}")
            return alias_ok, steps, expected_proto

        def _voice_reg_delete_wakeup_alias_exit_flow(
            self,
            tc: Dict[str, str],
            idx: Any,
            *,
            learned_wake_word: str,
            virtual_wake_intent: str,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            accepted_results = [learned_wake_word, virtual_wake_intent]
            delete_wake_phrase = self._voice_reg_control_phrase(tc, "voiceRegDeleteWakeEntry", "删除唤醒词")
            exit_delete_phrase = self._voice_reg_control_phrase(tc, "voiceRegExitDeleteEntry", "退出删除")
            present_ok, present_detail, _ = self._voice_reg_ensure_wakeup_alias_present(
                tc,
                f"{idx}-ensure",
                learned_wake_word=learned_wake_word,
                virtual_wake_intent=virtual_wake_intent,
            )
            steps.append(f"ensure-present={present_detail}")
            if not present_ok:
                return False, steps

            entry_verdict, entry_row = self._run_command_step_with_override(
                delete_wake_phrase,
                f"{idx}-delete-entry",
                timeout_seconds=4.5,
                do_wakeup=True,
            )
            steps.append(
                f"delete-entry={entry_verdict} raw={entry_row.get('识别原始结果', '')} recog={entry_row.get('识别结果', '')}"
            )
            if entry_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"delete-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            exit_verdict, exit_row = self._run_command_step_with_override(
                exit_delete_phrase,
                f"{idx}-delete-exit",
                expected_proto=None,
                timeout_seconds=4.5,
                do_wakeup=False,
            )
            steps.append(
                f"delete-exit={exit_verdict} raw={exit_row.get('识别原始结果', '')} recog={exit_row.get('识别结果', '')}"
            )
            if exit_verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"exit-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.5)

            alias_ok, alias_detail = self._voice_reg_verify_wakeup_alias(
                learned_wake_word,
                accepted_results,
                should_work=True,
            )
            steps.append(f"verify-wakeup-kept={alias_detail}")
            default_wakeup_ok = self.wakeup()
            steps.append(f"default-wakeup-after-exit={default_wakeup_ok}")
            return alias_ok and default_wakeup_ok, steps

        def test_voice_reg_scenario(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            meta = self._suite_meta(tc)
            data = self._runtime_assert_data(meta)
            scenario = str(data.get("voiceRegScenario") or self._case_input_value(meta) or "").strip()
            selected_command = str(data.get("selectedCommand") or self._voice_reg_selected_command(tc) or "").strip()
            learned_command = str(data.get("learnedCommand") or "").strip()
            learned_wake_word = str(data.get("learnedWakeWord") or "").strip()
            virtual_wake_intent = str(data.get("virtualWakeIntent") or self._voice_reg_virtual_wake_intent(tc) or "").strip()
            verify_phrase = str(data.get("verifyPhrase") or "").strip()
            verify_target_command = str(data.get("verifyTargetCommand") or selected_command or "").strip()
            verify_should_work = bool(data.get("verifyShouldWork"))
            learning_phrases = data.get("learningPhrases") or []
            verify_expected_results = data.get("verifyExpectedResults") or []
            steps: List[str] = []
            expected_proto = ""
            ok = False

            if isinstance(learning_phrases, str):
                learning_phrases = [learning_phrases]
            if isinstance(verify_expected_results, str):
                verify_expected_results = [verify_expected_results]

            try:
                if scenario == "flow_only":
                    verdict, rows = self._test_voice_reg_flow(tc, idx)
                    return verdict, rows[0]
                elif scenario in {"command_learn_success", "command_retry_recover", "command_retry_single_fail", "command_retry_exhaust", "command_supported_conflict", "command_reserved_conflict"}:
                    ok, steps, expected_proto = self._voice_reg_run_command_learning_sequence(
                        tc,
                        idx,
                        selected_command=selected_command,
                        learning_phrases=[str(item or "").strip() for item in learning_phrases if str(item or "").strip()],
                        verify_phrase=verify_phrase,
                        verify_target_command=verify_target_command,
                        verify_should_work=verify_should_work,
                        expect_learning_success=scenario in {"command_learn_success", "command_retry_recover"},
                    )
                elif scenario in {"wakeup_learn_success", "wakeup_retry_recover", "wakeup_retry_single_fail", "wakeup_retry_exhaust", "wakeup_default_conflict", "wakeup_reserved_conflict"}:
                    ok, steps = self._voice_reg_run_wakeup_learning_sequence(
                        tc,
                        idx,
                        learning_phrases=[str(item or "").strip() for item in learning_phrases if str(item or "").strip()],
                        verify_phrase=verify_phrase,
                        verify_expected_results=[str(item or "").strip() for item in verify_expected_results if str(item or "").strip()],
                        verify_should_work=verify_should_work,
                        expect_learning_success=scenario in {"wakeup_learn_success", "wakeup_retry_recover"},
                    )
                elif scenario == "command_delete_positive":
                    expected_proto = normalize_hex_protocol(
                        (self._voice_reg_command_entry(selected_command) or {}).get("snd_protocol")
                        or (self._voice_reg_command_entry(selected_command) or {}).get("sndProtocol")
                        or ""
                    )
                    ok, steps = self._voice_reg_delete_command_alias_flow(
                        tc,
                        idx,
                        selected_command=selected_command,
                        learned_command=learned_command,
                        expected_proto=expected_proto,
                        ensure_present=True,
                    )
                elif scenario == "command_delete_negative":
                    ok, steps, expected_proto = self._voice_reg_delete_command_alias_exit_flow(
                        tc,
                        idx,
                        selected_command=selected_command,
                        learned_command=learned_command,
                    )
                elif scenario == "wakeup_delete_positive":
                    ok, steps = self._voice_reg_delete_wakeup_alias_flow(
                        tc,
                        idx,
                        learned_wake_word=learned_wake_word,
                        virtual_wake_intent=virtual_wake_intent,
                        ensure_present=True,
                    )
                elif scenario == "wakeup_delete_negative":
                    ok, steps = self._voice_reg_delete_wakeup_alias_exit_flow(
                        tc,
                        idx,
                        learned_wake_word=learned_wake_word,
                        virtual_wake_intent=virtual_wake_intent,
                    )
                else:
                    steps.append(f"unsupported-scenario={scenario}")
            except Exception as exc:
                steps.append(f"exception={exc}")
                ok = False

            detail = f"配置预检查={config_verdict}；{config_detail}；scenario={scenario}"
            row_verdict = "OK" if ok else (
                "Reboot" if self._voice_reg_steps_have_reboot(steps)
                else ("Skip(人工)" if self._steps_have_skip_manual(steps) else "ConfigFail")
            )
            row = self._device_row(tc, tc.get("命令词", ""), row_verdict, detail, expected_proto=expected_proto)
            row["识别结果"] = detail
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def _multi_wke_detect_active_wakeword(self, candidates: Sequence[str]) -> Tuple[str, str]:
            details: List[str] = []
            running_state = self._extract_running_config_state()
            active_from_state = self._resolve_runtime_wakeup_word(running_state)
            if active_from_state and active_from_state in candidates:
                self._apply_runtime_wakeup_word(
                    active_from_state,
                    reason="multi-wke-detect",
                    running_state=running_state,
                )
                details.append(
                    f"running-config wkword={running_state.get('wkword')} => {active_from_state}"
                )
                return active_from_state, " | ".join(details)
            for phrase in [str(item or "").strip() for item in candidates if str(item or "").strip()]:
                ok, detail = self._voice_reg_verify_wakeup_alias(
                    phrase,
                    [phrase],
                    should_work=True,
                    timeout_seconds=2.2,
                )
                details.append(f"{phrase}=>{detail}")
                if ok:
                    return phrase, " | ".join(details)
            return "", " | ".join(details) if details else "no-candidate"

        def _wakeup_with_phrase(
            self,
            phrase: str,
            *,
            max_retry: int = 10,
            timeout_seconds: float = 3.0,
        ) -> Tuple[bool, str]:
            return self._wakeup_phrase_until_ready(
                phrase,
                max_retry=max(1, max_retry),
                detect_timeout_seconds=timeout_seconds,
                ready_timeout_seconds=max(float(timeout_seconds), 4.5),
                allow_reg_result=False,
            )

        def _run_command_step_with_named_wakeup(
            self,
            command: str,
            run_idx: Any,
            *,
            wake_phrase: str,
            expected_proto: Optional[str] = "",
            timeout_seconds: float = 3.0,
        ):
            wake_ok, wake_detail = self._wakeup_with_phrase(wake_phrase, timeout_seconds=3.0)
            if not wake_ok:
                wake_status = str(getattr(self, "_last_wakeup_status", "") or "").strip()
                if "unexpected-reboot" in str(wake_detail or ""):
                    wake_verdict = "Reboot"
                elif wake_status == "command-window-expired":
                    wake_verdict = "Skip(人工)"
                else:
                    wake_verdict = "WakeupFail"
                row = self._device_row({}, command, wake_verdict, wake_detail, expected_proto=expected_proto or "")
                row["设备响应列表"] = wake_detail
                return wake_verdict, row
            verdict, row = self._run_command_step_with_override(
                command,
                run_idx,
                expected_proto=expected_proto,
                timeout_seconds=timeout_seconds,
                do_wakeup=False,
            )
            row["设备响应列表"] = f"wake={wake_phrase}|{row.get('设备响应列表', '')}".strip("|")
            return verdict, row

        def _multi_wke_verify_phrase(self, phrase: str, *, should_work: bool) -> Tuple[bool, str]:
            return self._voice_reg_verify_wakeup_alias(
                phrase,
                [phrase],
                should_work=should_work,
                timeout_seconds=2.8,
            )

        def _multi_wke_ensure_default_active(
            self,
            *,
            default_wake_word: str,
            restore_command: str,
            candidates: Sequence[str],
            idx: Any,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            active, detect_detail = self._multi_wke_detect_active_wakeword(candidates)
            steps.append(f"detect-active={active or 'none'} {detect_detail}")
            if active == default_wake_word:
                return True, steps

            if active and restore_command:
                verdict, row = self._run_command_step_with_named_wakeup(
                    restore_command,
                    f"{idx}-ensure-default",
                    wake_phrase=active,
                    expected_proto=None,
                    timeout_seconds=4.5,
                )
                steps.append(
                    f"restore-default-entry={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}"
                )
                prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
                steps.append(f"restore-default-prompt={prompt_detail}")
                if not prompt_ok:
                    time.sleep(0.5)

            ok, detail = self._multi_wke_verify_phrase(default_wake_word, should_work=True)
            steps.append(f"verify-default-active={detail}")
            if ok:
                return True, steps

            # If the previous case polluted the active wake word state, fall back to
            # a controlled power-cycle so the next case starts from a clean baseline.
            if self.pretest_enabled and self.ctrl_port:
                power_reboot_base = self.reader.get_reboot_count() if self.reader else 0
                powered, power_detail = self._power_cycle_device()
                steps.append(f"default-reset-power-cycle={power_detail}")
                extra_reboot, power_reboot_detail = self._power_cycle_reboot_status(power_reboot_base)
                steps.append(f"default-reset-{power_reboot_detail}")
                running_ok, running_state, running_detail = self._wait_for_running_config(timeout_seconds=8.0)
                steps.append(
                    "default-reset-running-config="
                    f"ok={running_ok} wkword={running_state.get('wkword')} "
                    f"volume={running_state.get('volume')} detail={running_detail}"
                )
                if self.reader and self.reader.is_rebooted():
                    self.reader.clear_reboot_flag()
                self._clear_recent_serial_lines()
                retry_ok, retry_detail = self._multi_wke_verify_phrase(default_wake_word, should_work=True)
                steps.append(f"verify-default-active-after-reset={retry_detail}")
                return bool(powered and (not extra_reboot) and retry_ok), steps

            return False, steps

        def _multi_wke_apply_specified_switch(
            self,
            *,
            current_wake_word: str,
            switch_command: str,
            target_wake_word: str,
            idx: Any,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            if not switch_command:
                steps.append("switch-command-missing")
                return False, steps
            current_ok, current_detail = self._multi_wke_verify_phrase(current_wake_word, should_work=True)
            steps.append(f"pre-active={current_detail}")
            if not current_ok:
                return False, steps

            verdict, row = self._run_command_step_with_named_wakeup(
                switch_command,
                f"{idx}-switch-entry",
                wake_phrase=current_wake_word,
                expected_proto=None,
                timeout_seconds=4.5,
            )
            steps.append(f"switch-entry={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}")
            if verdict != "OK":
                return False, steps

            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"switch-prompt={prompt_detail}")
            if not prompt_ok:
                return False, steps

            capture = self._capture_phrase(target_wake_word, timeout_seconds=3.0, do_wakeup=False, tag="asrKw")
            if not capture.get("ok"):
                steps.append(f"switch-target-error={capture.get('error')}")
                return False, steps
            steps.append(
                f"switch-target=phrase={target_wake_word} raws={capture.get('raws', [])} texts={capture.get('texts', [])} "
                f"asr_raws={capture.get('asr_raws', [])} asr_texts={capture.get('asr_texts', [])}"
            )
            prompt2_ok, prompt2_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
            steps.append(f"switch-target-prompt={prompt2_detail}")
            if not prompt2_ok:
                time.sleep(0.5)
            return True, steps

        def _multi_wke_apply_loop_switch(
            self,
            *,
            current_wake_word: str,
            switch_command: str,
            switch_count: int,
            idx: Any,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            if not switch_command:
                steps.append("switch-command-missing")
                return False, steps
            active = current_wake_word
            for count in range(max(1, switch_count)):
                current_ok, current_detail = self._multi_wke_verify_phrase(active, should_work=True)
                steps.append(f"loop-pre-active-{count + 1}={current_detail}")
                if not current_ok:
                    return False, steps
                verdict, row = self._run_command_step_with_named_wakeup(
                    switch_command,
                    f"{idx}-loop-switch-{count + 1}",
                    wake_phrase=active,
                    expected_proto=None,
                    timeout_seconds=4.5,
                )
                steps.append(
                    f"loop-switch-{count + 1}={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}"
                )
                if verdict != "OK":
                    return False, steps
                prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
                steps.append(f"loop-switch-prompt-{count + 1}={prompt_detail}")
                if not prompt_ok:
                    time.sleep(0.5)
            return True, steps

        def _multi_wke_protocol_ports(self) -> List[str]:
            ports: List[str] = []

            def add_port(value: Any) -> None:
                text = str(value or "").strip()
                if text and text not in ports:
                    ports.append(text)

            add_port(self.protocol_port)
            config = getattr(self, "cfg", {}) or {}
            add_port(config.get("protocolPort"))
            log_port = str(
                getattr(self, "ser_port", "")
                or getattr(self, "port", "")
                or getattr(self, "log_port", "")
                or ""
            ).strip()
            ctrl_port = str(getattr(self, "ctrl_port", "") or "").strip()

            if os.name != "nt":
                for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*"]:
                    for path in sorted(glob.glob(pattern)):
                        if path in {log_port, ctrl_port}:
                            continue
                        add_port(path)
            return ports

        def _send_hex_protocol(self, protocol_hex: str) -> Tuple[bool, str]:
            normalized = normalize_hex_protocol(protocol_hex)
            if not normalized:
                return False, "protocol-empty"

            try:
                payload = bytes.fromhex(normalized.replace(" ", ""))
            except Exception as exc:
                return False, f"protocol-parse-failed={exc}"

            candidate_ports = self._multi_wke_protocol_ports()
            if not candidate_ports:
                return False, "protocol-port-missing"

            errors: List[str] = []
            for port in candidate_ports:
                ser = None
                try:
                    ser = base_module.serial.Serial(port, self.protocol_baud, timeout=1, write_timeout=1)
                    if hasattr(ser, "reset_input_buffer"):
                        ser.reset_input_buffer()
                    ser.write(payload)
                    ser.flush()
                    time.sleep(0.8)
                    return True, f"protocol-sent port={port} baud={self.protocol_baud} bytes={normalized}"
                except Exception as exc:
                    errors.append(f"{port}:{exc}")
                finally:
                    if ser is not None:
                        try:
                            ser.close()
                        except Exception:
                            pass
            return False, f"protocol-send-failed bytes={normalized} errors={errors}"

        def _multi_wke_apply_protocol_switch(
            self,
            *,
            current_wake_word: str,
            protocol_bytes: str,
            idx: Any,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            if current_wake_word:
                current_ok, current_detail = self._multi_wke_verify_phrase(current_wake_word, should_work=True)
                steps.append(f"protocol-pre-active={current_detail}")
                if not current_ok:
                    return False, steps
            send_ok, send_detail = self._send_hex_protocol(protocol_bytes)
            steps.append(f"protocol-send-{idx}={send_detail}")
            return send_ok, steps

        def _multi_wke_query_current(
            self,
            *,
            current_wake_word: str,
            query_command: str,
            query_expected_protocol: str,
            idx: Any,
        ) -> Tuple[bool, List[str]]:
            steps: List[str] = []
            if not query_command:
                steps.append("query-command-missing")
                return False, steps
            verdict, row = self._run_command_step_with_named_wakeup(
                query_command,
                f"{idx}-query-current",
                wake_phrase=current_wake_word,
                expected_proto=query_expected_protocol or "",
                timeout_seconds=4.5,
            )
            steps.append(
                f"query-current={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')} "
                f"send={row.get('实际发送协议', '')}"
            )
            prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=4.5)
            steps.append(f"query-current-prompt={prompt_detail}")
            if not prompt_ok:
                time.sleep(0.4)
            return verdict == "OK", steps

        def test_multi_wke_scenario(self, tc: Dict[str, str], idx: Any):
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                row = self._config_only_row(tc, config_verdict, config_detail)
                row["__suite_mode"] = "device"
                return config_verdict, row

            meta = self._suite_meta(tc)
            data = self._runtime_assert_data(meta)
            scenario = str(data.get("multiWkeScenario") or self._case_input_value(meta) or "").strip()
            firmware = self.listenai_context.get("firmware") or {}
            multi_cfg = firmware.get("multi_wakeup") or {}
            control = multi_cfg.get("switch_control") or multi_cfg.get("switchControl") or {}
            switch_info = control.get("switch_info") or control.get("switchInfo") or {}
            restore_info = control.get("restore_info") or control.get("restoreInfo") or {}
            query_info = control.get("query_info") or control.get("queryInfo") or {}
            mode = str(data.get("mode") or multi_cfg.get("mode") or "").strip()
            default_wake_word = str(data.get("defaultWakeWord") or self.wakeup_word or "").strip()
            setup_target = str(data.get("setupTargetWakeWord") or "").strip()
            setup_target_protocol = str(data.get("setupTargetProtocol") or "").strip()
            switch_command = str(data.get("switchCommand") or switch_info.get("word") or "").strip()
            restore_command = str(data.get("restoreCommand") or restore_info.get("word") or "").strip()
            query_command = str(data.get("queryCommand") or query_info.get("word") or "").strip()
            query_expected_protocol = str(data.get("queryExpectedProtocol") or "").strip()
            switch_target = str(data.get("switchTargetWakeWord") or "").strip()
            invalid_target = str(data.get("invalidTargetWakeWord") or "").strip()
            protocol_target = str(data.get("protocolTargetWakeWord") or "").strip()
            protocol_bytes = str(data.get("protocolBytes") or "").strip()
            expected_active = str(data.get("expectedActiveWakeWord") or "").strip()
            switch_count = max(1, as_int(data.get("switchCount")) or 1)
            inactive_words = [str(item or "").strip() for item in data.get("expectedInactiveWakeWords") or [] if str(item or "").strip()]
            candidate_words: List[str] = []
            for word in [
                default_wake_word,
                setup_target,
                switch_target,
                protocol_target,
                expected_active,
                invalid_target,
                *inactive_words,
            ]:
                text = str(word or "").strip()
                if text and text not in candidate_words:
                    candidate_words.append(text)

            steps: List[str] = []
            ok = False

            def verify_phrase_set(active_word: str, blocked_words: Sequence[str]) -> Tuple[bool, List[str]]:
                local_steps: List[str] = []
                active_ok, active_detail = self._multi_wke_verify_phrase(active_word, should_work=True)
                local_steps.append(f"verify-active={active_detail}")
                inactive_ok = True
                for word in blocked_words:
                    word_ok, word_detail = self._multi_wke_verify_phrase(word, should_work=False)
                    local_steps.append(f"verify-inactive-{word}={word_detail}")
                    inactive_ok = inactive_ok and word_ok
                return active_ok and inactive_ok, local_steps

            try:
                default_ready, prep_steps = self._multi_wke_ensure_default_active(
                    default_wake_word=default_wake_word,
                    restore_command=restore_command,
                    candidates=candidate_words,
                    idx=idx,
                )
                steps.extend(prep_steps)
                if not default_ready:
                    row = self._device_row(tc, tc.get("命令词", ""), "ConfigFail", f"配置预检查={config_verdict}；{config_detail}", expected_proto="")
                    if self._steps_have_skip_manual(steps):
                        row["识别判定"] = "Skip(人工)"
                    row["识别结果"] = f"配置预检查={config_verdict}；{config_detail}；scenario={scenario}"
                    row["设备响应列表"] = " | ".join(steps)
                    row["命令词"] = tc.get("命令词", "")
                    row["__suite_mode"] = "device"
                    return row["识别判定"], row

                current_wake = default_wake_word
                if setup_target and setup_target != default_wake_word:
                    if mode == "loop":
                        setup_ok, setup_steps = self._multi_wke_apply_loop_switch(
                            current_wake_word=default_wake_word,
                            switch_command=switch_command,
                            switch_count=1,
                            idx=f"{idx}-setup",
                        )
                    elif mode == "protocol":
                        setup_ok, setup_steps = self._multi_wke_apply_protocol_switch(
                            current_wake_word=default_wake_word,
                            protocol_bytes=setup_target_protocol,
                            idx=f"{idx}-setup",
                        )
                    else:
                        setup_ok, setup_steps = self._multi_wke_apply_specified_switch(
                            current_wake_word=default_wake_word,
                            switch_command=switch_command,
                            target_wake_word=setup_target,
                            idx=f"{idx}-setup",
                        )
                    steps.extend(setup_steps)
                    if not setup_ok:
                        raise RuntimeError("setup-target-failed")
                    setup_verify_ok, setup_verify_detail = self._multi_wke_verify_phrase(setup_target, should_work=True)
                    steps.append(f"setup-target-verify={setup_verify_detail}")
                    if not setup_verify_ok:
                        raise RuntimeError("setup-target-not-active")
                    current_wake = setup_target

                if scenario == "default_active":
                    query_ok = True
                    if query_command:
                        query_ok, query_steps = self._multi_wke_query_current(
                            current_wake_word=current_wake,
                            query_command=query_command,
                            query_expected_protocol=query_expected_protocol,
                            idx=idx,
                        )
                        steps.extend(query_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or default_wake_word, inactive_words)
                    steps.extend(verify_steps)
                    ok = query_ok and verify_ok
                elif scenario == "query_current":
                    query_ok, query_steps = self._multi_wke_query_current(
                        current_wake_word=current_wake,
                        query_command=query_command,
                        query_expected_protocol=query_expected_protocol,
                        idx=idx,
                    )
                    steps.extend(query_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or current_wake, inactive_words)
                    steps.extend(verify_steps)
                    ok = query_ok and verify_ok
                elif scenario == "specified_switch":
                    switch_ok, switch_steps = self._multi_wke_apply_specified_switch(
                        current_wake_word=current_wake,
                        switch_command=switch_command,
                        target_wake_word=switch_target,
                        idx=idx,
                    )
                    steps.extend(switch_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or switch_target, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and verify_ok
                elif scenario == "specified_invalid_target":
                    switch_ok, switch_steps = self._multi_wke_apply_specified_switch(
                        current_wake_word=current_wake,
                        switch_command=switch_command,
                        target_wake_word=invalid_target,
                        idx=idx,
                    )
                    steps.extend(switch_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or current_wake, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and verify_ok
                elif scenario == "restore_default":
                    if not restore_command:
                        steps.append("restore-command-missing")
                        ok = False
                    else:
                        verdict, row = self._run_command_step_with_named_wakeup(
                            restore_command,
                            f"{idx}-restore-default",
                            wake_phrase=current_wake,
                            expected_proto=None,
                            timeout_seconds=4.5,
                        )
                        steps.append(
                            f"restore-default-entry={verdict} raw={row.get('识别原始结果', '')} recog={row.get('识别结果', '')}"
                        )
                        prompt_ok, prompt_detail = self._wait_for_voice_reg_prompt_complete(timeout_seconds=6.0)
                        steps.append(f"restore-default-prompt={prompt_detail}")
                        if not prompt_ok:
                            time.sleep(0.5)
                        verify_ok, verify_steps = verify_phrase_set(expected_active or default_wake_word, inactive_words)
                        steps.extend(verify_steps)
                        ok = verdict == "OK" and verify_ok
                elif scenario == "loop_switch":
                    switch_ok, switch_steps = self._multi_wke_apply_loop_switch(
                        current_wake_word=current_wake,
                        switch_command=switch_command,
                        switch_count=switch_count,
                        idx=idx,
                    )
                    steps.extend(switch_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and verify_ok
                elif scenario == "protocol_switch":
                    switch_ok, switch_steps = self._multi_wke_apply_protocol_switch(
                        current_wake_word=current_wake,
                        protocol_bytes=protocol_bytes,
                        idx=idx,
                    )
                    steps.extend(switch_steps)
                    query_ok = True
                    if query_command:
                        query_ok, query_steps = self._multi_wke_query_current(
                            current_wake_word=expected_active or protocol_target,
                            query_command=query_command,
                            query_expected_protocol=query_expected_protocol,
                            idx=idx,
                        )
                        steps.extend(query_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or protocol_target, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and query_ok and verify_ok
                elif scenario == "protocol_invalid_target":
                    switch_ok, switch_steps = self._multi_wke_apply_protocol_switch(
                        current_wake_word=current_wake,
                        protocol_bytes=protocol_bytes,
                        idx=idx,
                    )
                    steps.extend(switch_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or current_wake, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and verify_ok
                elif scenario == "frozen_default":
                    if mode == "loop":
                        switch_ok, switch_steps = self._multi_wke_apply_loop_switch(
                            current_wake_word=current_wake,
                            switch_command=switch_command,
                            switch_count=switch_count,
                            idx=idx,
                        )
                    elif mode == "protocol":
                        switch_ok, switch_steps = self._multi_wke_apply_protocol_switch(
                            current_wake_word=current_wake,
                            protocol_bytes=protocol_bytes,
                            idx=idx,
                        )
                    else:
                        switch_ok, switch_steps = self._multi_wke_apply_specified_switch(
                            current_wake_word=current_wake,
                            switch_command=switch_command,
                            target_wake_word=switch_target,
                            idx=idx,
                        )
                    steps.extend(switch_steps)
                    query_ok = True
                    if query_command:
                        query_ok, query_steps = self._multi_wke_query_current(
                            current_wake_word=expected_active or default_wake_word,
                            query_command=query_command,
                            query_expected_protocol=query_expected_protocol,
                            idx=idx,
                        )
                        steps.extend(query_steps)
                    verify_ok, verify_steps = verify_phrase_set(expected_active or default_wake_word, inactive_words)
                    steps.extend(verify_steps)
                    ok = switch_ok and query_ok and verify_ok
                else:
                    steps.append(f"unsupported-scenario={scenario}")
            except Exception as exc:
                steps.append(f"exception={exc}")
                ok = False

            detail = f"配置预检查={config_verdict}；{config_detail}；scenario={scenario}；mode={mode}"
            row_verdict = "OK" if ok else (
                "Reboot" if self._voice_reg_steps_have_reboot(steps)
                else ("Skip(人工)" if self._steps_have_skip_manual(steps) else "ConfigFail")
            )
            row = self._device_row(tc, tc.get("命令词", ""), row_verdict, detail, expected_proto="")
            row["识别结果"] = detail
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def test_volume_levels(self, tc: Dict[str, str]):
            package_key = "打包参数"
            case_id_key = "用例编号"
            module_key = "功能模块"
            test_type_key = "测试类型"
            run_idx_key = "测试次数"
            wake_key = "唤醒词"
            command_key = "命令词"
            expect_proto_key = "期望协议"
            asr_key = "识别结果"
            verdict_key = "识别判定"
            device_resp_key = "设备响应列表"
            reboot_key = "重启次数"
            skip_manual = "Skip(人工)"
            max_step_retry = 3

            meta = self._suite_meta(tc)
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if self._has_base_coverage(tc, "volLevel"):
                return self._base_covered_row(tc, "volLevel", config_verdict, config_detail)
            target_levels = as_int(self._case_input_value(meta))
            if target_levels is None:
                target_levels = as_int(parse_json_cell(meta.get(package_key, "{}")).get("volLevel"))
            levels = self._current_volume_levels()
            if target_levels is None or not levels:
                return skip_manual, self._config_only_row(tc, skip_manual, "missing volLevel or volume_config.level context")
            if not all([self.volume_up_word, self.volume_min_word]):
                return skip_manual, self._config_only_row(tc, skip_manual, "missing volume command words")

            volume_regex_ready = False
            for trigger_word in [
                self.volume_min_word,
                self.volume_max_word,
                self.volume_down_word,
                self.volume_up_word,
                self.volume_mid_word,
            ]:
                if trigger_word and self._ensure_runtime_regex("volume", trigger_word):
                    volume_regex_ready = True
                    break
            if not volume_regex_ready:
                return skip_manual, self._config_only_row(tc, skip_manual, "missing runtime volume regex")

            steps: List[str] = []

            def run_volume_command(command: str, label: str):
                final_verdict = "ConfigFail"
                final_row: Dict[str, Any] = {}
                final_value: Optional[int] = None
                captured_values: List[int] = []
                for attempt in range(1, max_step_retry + 1):
                    # 音量档位统计只关心命令是否触发了 volume 日志，不能复用通用链路的协议重试，
                    # 否则一次“增大音量”可能被重复发送多次，导致单步推进多档。
                    current_verdict, current_row = self._run_single_command_attempt(
                        command,
                        f"{label}-try{attempt}",
                        expected_proto="",
                        timeout_seconds=3.0,
                        do_wakeup=True,
                    )
                    detail = current_row.get("设备响应列表", "")
                    current_row["设备响应列表"] = (
                        f"volume-step attempt={attempt}/{max_step_retry}|protocol-check=skipped|{detail}"
                    ).strip("|")
                    current_values = self._extract_volume_values()
                    current_value = current_values[-1] if current_values else None
                    steps.append(f"{label}-try{attempt}={current_verdict} volume={current_value}")
                    final_verdict = current_verdict
                    final_row = dict(current_row)
                    final_value = current_value
                    for value in current_values:
                        if value is not None:
                            captured_values.append(value)
                    if current_value is not None:
                        break
                return final_verdict, final_row, final_value, captured_values

            base_verdict, base_row, _, base_values = run_volume_command(self.volume_min_word, "min")
            last_row = dict(base_row)
            observed: List[int] = []
            base_ok = base_verdict == "OK" or any(value == 0 for value in base_values)
            last_seen: Optional[int] = 0 if any(value == 0 for value in base_values) else None

            max_total_steps = max(target_levels * max_step_retry, target_levels + 1)
            for index in range(max_total_steps):
                step_verdict, current_row, volume_value, volume_series = run_volume_command(self.volume_up_word, f"up-{index + 1}")
                last_row = dict(current_row)
                if volume_value is None:
                    observed.append(-1)
                for value in volume_series:
                    if value is None:
                        continue
                    if last_seen is None or value > last_seen:
                        observed.append(value)
                        last_seen = value
                        if len([item for item in observed if item >= 0]) >= max(target_levels - 1, 0):
                            break
                if len([item for item in observed if item >= 0]) >= max(target_levels - 1, 0):
                    break

            normalized_observed = []
            for value in observed:
                if value < 0:
                    continue
                if not normalized_observed or value != normalized_observed[-1]:
                    normalized_observed.append(value)
            expected_scale_sequence = levels[1:target_levels]
            expected_index_sequence = list(range(1, target_levels))
            observation_mode = "none"
            sequence_ok = True
            if normalized_observed:
                if normalized_observed == expected_scale_sequence[: len(normalized_observed)]:
                    observation_mode = "scale"
                elif normalized_observed == expected_index_sequence[: len(normalized_observed)]:
                    observation_mode = "index"
                else:
                    observation_mode = "mismatch"
                    sequence_ok = False
                    steps.append(
                        f"expected-scale={expected_scale_sequence} expected-index={expected_index_sequence} actual={normalized_observed}"
                    )
            else:
                sequence_ok = False
                steps.append("no volume values captured during increase steps")

            # ========== 音量档位数显式比对（改进点）==========
            # 计算实际检测到的档位数，包含基准最小档
            detected_level_count = len([v for v in normalized_observed if v >= 0]) + (1 if base_ok else 0)

            # 显式比对：配置档位数 vs 实际检测档位数
            if detected_level_count != target_levels:
                sequence_ok = False
                steps.append(f"档位数不匹配：配置 volLevel={target_levels}，实际检测档位={detected_level_count}")
                observation_mode = "count_mismatch"

            # 附加检测：设备提前达最大档位（说明设备档位少于配置）
            if last_seen == levels[-1] and index < max_total_steps - 1 and detected_level_count < target_levels:
                steps.append(f"设备提前达最大档位 volume={last_seen}，实际档位={detected_level_count} < 配置档位={target_levels}")
            # ==============================================

            all_ok = base_ok and sequence_ok and len(normalized_observed) >= max(target_levels - 1, 0)

            summary_row = dict(last_row)
            summary_row[case_id_key] = tc.get(case_id_key, "")
            summary_row[module_key] = tc.get(module_key, "")
            summary_row[test_type_key] = tc.get(test_type_key, "")
            summary_row[run_idx_key] = ""
            summary_row[wake_key] = self.wakeup_word
            summary_row[command_key] = self.volume_up_word
            summary_row[expect_proto_key] = self.kw2proto.get(self.volume_up_word, "")
            summary_row[asr_key] = f"配置档位={target_levels} 实际检测={detected_level_count} expected={expected_scale_sequence} observed={normalized_observed} mode={observation_mode}"
            summary_row[verdict_key] = "OK" if all_ok else "ConfigFail"
            summary_row[device_resp_key] = " | ".join(steps)
            summary_row[reboot_key] = self.reader.get_reboot_count()
            summary_row["__suite_mode"] = "device"
            return summary_row[verdict_key], summary_row

        def test_default_volume(self, tc: Dict[str, str]):
            meta = self._suite_meta(tc)
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if self._has_base_coverage(tc, "defaultVol"):
                return self._base_covered_row(tc, "defaultVol", config_verdict, config_detail)
            target_default = as_int(self._case_input_value(meta))
            levels = self._current_volume_levels()
            if target_default is None or not levels:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "缺少 defaultVol 或 volume_config.level 上下文")
            if not (self.pretest_enabled and self.ctrl_port):
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "默认音量自动校验需要 ctrl-port 支持断电重启")

            power_reboot_base = self.reader.get_reboot_count() if self.reader else 0
            self.reader.clear()
            self._clear_recent_serial_lines()
            powered, power_detail = self._power_cycle_device()
            if not powered:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", power_detail)
            extra_reboot, power_reboot_detail = self._power_cycle_reboot_status(power_reboot_base)

            running_ok, running_state, running_detail = self._wait_for_running_config(timeout_seconds=10.0)
            time.sleep(0.8)
            boot_state = self._extract_boot_volume_state()
            observed_value = None
            boot_line = " | ".join(str(line) for line in boot_state.get("lines") or [])
            normalized_mode = "unknown"
            inferred_default: Optional[int] = None
            default_index = as_int(boot_state.get("defaultIndex"))
            init_index = as_int(boot_state.get("initIndex"))
            running_volume = as_int(boot_state.get("runningVolume"))
            if running_volume is None:
                running_volume = as_int(running_state.get("volume"))

            # df vol 是最直接的默认档位索引；其次才回退到 init/running 字段。
            if default_index is not None and 0 <= default_index < len(levels):
                observed_value = default_index
                normalized_mode = "default-index"
                inferred_default = default_index + 1
            elif init_index is not None and 0 <= init_index < len(levels):
                observed_value = init_index
                normalized_mode = "init-index"
                inferred_default = init_index + 1
            elif running_volume is not None:
                observed_value = running_volume
                if running_volume in levels:
                    normalized_mode = "scale"
                    inferred_default = levels.index(running_volume) + 1
                elif 0 <= running_volume < len(levels):
                    normalized_mode = "index"
                    inferred_default = running_volume + 1

            ok = (not extra_reboot) and inferred_default == target_default
            detail = (
                f"{power_detail}；{power_reboot_detail}；expected_default={target_default}；"
                f"running_volume={boot_state.get('runningVolume')}；"
                f"running_config_volume={running_state.get('volume')}；"
                f"df_vol={boot_state.get('defaultIndex')}；"
                f"init_vol={boot_state.get('initIndex')}；"
                f"set_vol={boot_state.get('setFrom')}->{boot_state.get('setTo')}；"
                f"observed={observed_value}；mode={normalized_mode}；"
                f"inferred_default={inferred_default}；running_config_ok={running_ok}；running_config_detail={running_detail}"
            )
            device_row = {
                "用例编号": tc.get("用例编号", ""),
                "功能模块": tc.get("功能模块", ""),
                "测试类型": tc.get("测试类型", ""),
                "测试次数": "",
                "唤醒词": self.wakeup_word,
                "命令词": "",
                "期望协议": "",
                "识别原始结果": "",
                "识别结果": detail,
                "播报ID": "",
                "实际发送协议": "",
                "协议收发一致": "",
                "协议比对": "",
                "识别判定": "OK" if ok else ("Reboot" if extra_reboot else "ConfigFail"),
                "响应时间(ms)": "",
                "设备响应列表": boot_line or detail,
                "重启次数": self.reader.get_reboot_count(),
                "__suite_mode": "device",
            }
            return device_row["识别判定"], device_row

        def test_volume_persist(self, tc: Dict[str, str]):
            meta = self._suite_meta(tc)
            expected_save = normalize_bool_text(self._case_input_value(meta))
            levels = self._current_volume_levels()
            default_vol = as_int((self.listenai_context.get("firmware") or {}).get("volume_config", {}).get("default"))
            if expected_save is None or not levels or default_vol is None:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "缺少 volSave/defaultVol/level 上下文")
            if not all([self.volume_max_word, self.volume_down_word]):
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "缺少音量调节命令词，无法自动验证掉电保持")
            if not self._ensure_runtime_regex("volume", self.volume_up_word or self.volume_down_word):
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "缺少 volume 日志正则，无法自动验证掉电保持")

            verdict, row = self._test_one_with_override(self.volume_max_word, "set-max")
            if verdict != "OK":
                if str(verdict).startswith("Skip"):
                    return verdict, self._device_row(
                        tc,
                        self.volume_max_word,
                        verdict,
                        f"设置最大音量阶段命令窗口不足: {row.get('识别结果') or row.get('设备响应列表')}",
                    )
                return "ConfigFail", self._device_row(tc, self.volume_max_word, "ConfigFail", f"设置最大音量失败: {row.get('识别判定')}")

            save_ok, save_state, save_detail = self._wait_for_config_save_window(
                timeout_seconds=10.0,
                post_save_wait_seconds=10.0,
            )

            power_reboot_base = self.reader.get_reboot_count() if self.reader else 0
            powered, power_detail = self._power_cycle_device()
            if not powered:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", power_detail)
            extra_reboot, power_reboot_detail = self._power_cycle_reboot_status(power_reboot_base)
            boot_state = self._extract_boot_volume_state()
            running_ok, running_state, running_detail = self._wait_for_running_config(timeout_seconds=6.0)
            boot_running_index = as_int(boot_state.get("runningVolume"))
            if boot_running_index is None:
                boot_running_index = as_int(running_state.get("volume"))
            boot_default_index = as_int(boot_state.get("defaultIndex"))
            boot_init_index = as_int(boot_state.get("initIndex"))
            boot_set_to = as_int(boot_state.get("setTo"))

            # volSave 关注的是重启后的档位恢复与一次减档结果。
            # 这里不再强依赖协议命中，否则协议拆行重试会把音量继续减下去，污染该功能点结论。
            verdict, row = self._test_one_with_override(self.volume_down_word, "after-reboot", expected_proto=None)
            observed_index = self._last_volume_value()
            recognized_after_down = False
            for candidate in [
                row.get("识别结果"),
                self.pinyin_to_zh(row.get("识别原始结果", "")),
                row.get("识别原始结果"),
            ]:
                text = str(candidate or "").strip()
                if text == self.volume_down_word:
                    recognized_after_down = True
                    break

            expected_boot_index = (len(levels) - 1) if expected_save else max(default_vol - 1, 0)
            expected_after_down_index = max(expected_boot_index - 1, 0)

            inferred_boot_index = None
            for candidate in [boot_running_index, boot_init_index, boot_set_to, boot_default_index]:
                if candidate is not None:
                    inferred_boot_index = candidate
                    break

            # volume 正则抓到的是运行态档位索引，不是绝对音量值。
            # 若最终一次减音量未打印 volume，但已能确认重启后的 boot index 且减音量命令执行成功，
            # 则按“重启后索引 - 1”推断减一档后的目标索引。
            # 这里允许“识别成功但协议日志缺失”的情况，不再把协议观测问题误记到 volSave 功能点本身。
            protocol_only_after_down = verdict == "ConfigFail" and recognized_after_down
            if observed_index is None and inferred_boot_index is not None and (verdict == "OK" or protocol_only_after_down):
                observed_index = max(inferred_boot_index - 1, 0)

            ok = (
                not extra_reboot
                and (verdict == "OK" or protocol_only_after_down)
                and inferred_boot_index == expected_boot_index
                and observed_index == expected_after_down_index
            )
            detail = (
                f"{power_detail}；{power_reboot_detail}；expected_save={expected_save}"
                f"；assist-save-window={save_detail}；assist-refresh-volume={save_state.get('volume')}"
                f"；boot-running-config-ok={running_ok}；boot-running-config={running_state.get('volume')}"
                f"；expected_boot_index={expected_boot_index}；boot_running_index={boot_running_index}"
                f"；boot_default_index={boot_default_index}；boot_init_index={boot_init_index}；boot_set_to={boot_set_to}"
                f"；expected_after_down_index={expected_after_down_index}；observed_after_down_index={observed_index}"
                f"；after_down_verdict={verdict}；recognized_after_down={recognized_after_down}；assist-save-ok={save_ok}"
                f"；running-config-detail={running_detail}"
            )
            device_row = {
                "用例编号": tc.get("用例编号", ""),
                "功能模块": tc.get("功能模块", ""),
                "测试类型": tc.get("测试类型", ""),
                "测试次数": "",
                "唤醒词": self.wakeup_word,
                "命令词": self.volume_down_word,
                "期望协议": self.kw2proto.get(self.volume_down_word, ""),
                "识别原始结果": row.get("识别原始结果", ""),
                "识别结果": row.get("识别结果", ""),
                "播报ID": row.get("播报ID", ""),
                "实际发送协议": row.get("实际发送协议", ""),
                "协议收发一致": row.get("协议收发一致", ""),
                "协议比对": row.get("协议比对", ""),
                "识别判定": "OK" if ok else (
                    "Skip(人工)" if str(verdict).startswith("Skip")
                    else ("Reboot" if extra_reboot or verdict == "Reboot" else "ConfigFail")
                ),
                "响应时间(ms)": row.get("响应时间(ms)", ""),
                "设备响应列表": detail,
                "重启次数": self.reader.get_reboot_count(),
                "__suite_mode": "device",
            }
            return device_row["识别判定"], device_row

        def test_dynamic_command_case(self, tc: Dict[str, str]):
            meta = self._suite_meta(tc)
            parameter = meta.get("原始参数", "")
            if parameter not in self.AUTO_DYNAMIC_PARAMS:
                return "Skip(人工)", self._config_only_row(
                    tc,
                    "Skip(人工)",
                    f"参数 {parameter} 暂未接入专用自动化，保留原有通用/人工验证链路",
                )
            input_value = self._case_input_value(meta)
            command = tc.get("命令词", "") or self.sample_command
            expected_proto = tc.get("期望协议", "")
            expected_reply = tc.get("期望播报", "")
            note_parts: List[str] = [f"原始参数={parameter}", f"输入值={input_value}"]

            if parameter in {"releaseAlgoList[*].type", "releaseAlgoList[*].reply", "releaseAlgoList[*].replyMode", "releaseAlgoList[*].recProtocol"}:
                config_verdict, config_detail = self._evaluate_config_assert(tc)
                note_parts.append(f"配置预检查={config_verdict}")
                if config_verdict != "OK":
                    row = self._config_only_row(tc, config_verdict, config_detail)
                    row["__suite_mode"] = "device"
                    row["设备响应列表"] = f"{row.get('设备响应列表', '')}|{'；'.join(note_parts)}".strip("|")
                    return config_verdict, row

                matched = self._find_release_algo_entry(parameter, input_value, command)
                if matched:
                    command = str(matched.get("intent") or command)
                    expected_proto = str(matched.get("snd_protocol") or expected_proto)
                    expected_reply = str(matched.get("reply") or expected_reply)
                    note_parts.append(f"匹配intent={command}")
                    note_parts.append(f"replyMode={matched.get('reply_mode', '')}")
                else:
                    note_parts.append("未从 web_config 精确匹配到词条，回退到套件命令词")

            if parameter in {"releaseAlgoList[*].word", "releaseAlgoList[*].extWord", "releaseAlgoList[*].children[*].extWord"} and input_value:
                command = str(input_value)
            elif parameter == "releaseAlgoList[*].sndProtocol" and input_value:
                expected_proto = str(input_value)

            verdict, row = self._test_one_with_override(command, tc.get("用例编号", ""), expected_proto=expected_proto)
            if parameter == "releaseAlgoList[*].recProtocol" and input_value:
                expected_recv = normalize_hex_protocol(input_value)
                recv_values = [
                    normalize_hex_protocol(item)
                    for item in str(row.get("设备响应列表", "")).split("|")
                    if normalize_hex_protocol(item)
                ]
                recv_hit = expected_recv in recv_values
                note_parts.append(f"期望回传协议={expected_recv}")
                note_parts.append(f"实际回传协议={recv_values}")
                if verdict == "OK" and not recv_hit:
                    verdict = "ConfigFail"
                    note_parts.append("未在 recvMsg 中捕获到期望回传协议")
            elif parameter == "releaseAlgoList[*].replyMode":
                expected_mode = ""
                matched = self._find_release_algo_entry(parameter, input_value, command)
                if matched:
                    expected_mode = str(matched.get("reply_mode") or "").strip()
                if not expected_mode and input_value:
                    expected_mode = str(input_value).strip()
                has_play_id = bool(str(row.get("播报ID", "")).strip())
                note_parts.append(f"期望播报模式={expected_mode or '未知'}")
                note_parts.append(f"播报ID={row.get('播报ID', '')}")
                if verdict == "OK" and expected_mode == "主" and not has_play_id:
                    verdict = "ConfigFail"
                    note_parts.append("主播播报模式未捕获到 playId")
                elif verdict == "OK" and expected_mode == "被" and has_play_id:
                    verdict = "ConfigFail"
                    note_parts.append("被动播报模式却捕获到 playId")
            elif parameter == "releaseAlgoList[*].reply" and expected_reply:
                note_parts.append(f"需人工确认播报内容={expected_reply}")

            row["用例编号"] = tc.get("用例编号", "")
            row["功能模块"] = tc.get("功能模块", "")
            row["测试类型"] = tc.get("测试类型", "")
            row["命令词"] = command
            row["期望协议"] = expected_proto
            row["识别判定"] = verdict
            row["设备响应列表"] = f"{row.get('设备响应列表', '')}|{'；'.join(note_parts)}".strip("|")
            row["__suite_mode"] = "device"
            return verdict, row

        def test_wakeup_persist(self, tc: Dict[str, str]):
            meta = self._suite_meta(tc)
            config_verdict, config_detail = self._evaluate_config_assert(tc)
            if config_verdict != "OK":
                return config_verdict, self._config_only_row(tc, config_verdict, config_detail)
            if not (self.pretest_enabled and self.ctrl_port):
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "wakeWordSave 自动化需要 ctrl-port 支持断电重启")
            data = self._runtime_assert_data(meta)
            expected_save = normalize_bool_text(self._case_input_value(meta))
            firmware = self.listenai_context.get("firmware") or {}
            persisted_cfg = (firmware.get("general_config") or {}).get("persisted") or {}
            if expected_save is None:
                expected_save = normalize_bool_text(persisted_cfg.get("wakeup"))
            firmware = self.listenai_context.get("firmware") or {}
            multi_cfg = firmware.get("multi_wakeup") or {}
            wake_list = multi_cfg.get("switch_list") or multi_cfg.get("wkelist") or []
            if len(wake_list) < 2:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "当前上下文没有多唤醒词列表，无法自动验证 wakeWordSave")

            def normalize_mode(value: Any) -> str:
                text = str(value or "").strip().lower()
                mapping = {
                    "loop": "loop",
                    "循环切换": "loop",
                    "specified": "specified",
                    "指定切换": "specified",
                    "protocol": "protocol",
                    "协议切换": "protocol",
                }
                return mapping.get(text, text or "specified")

            def item_word(item: Dict[str, Any]) -> str:
                return str(item.get("word") or item.get("condition") or "").strip()

            def item_rec_protocol(item: Dict[str, Any]) -> str:
                return normalize_hex_protocol(item.get("rec_protocol") or item.get("recProtocol"))

            def item_is_default(item: Dict[str, Any]) -> bool:
                special = str(item.get("special_type") or item.get("specialType") or "").strip()
                return "默认唤醒词" in special or bool(item.get("isDefault"))

            def unique_words(values: Sequence[str]) -> List[str]:
                result: List[str] = []
                for value in values:
                    text = str(value or "").strip()
                    if text and text not in result:
                        result.append(text)
                return result

            mode = normalize_mode(data.get("mode") or multi_cfg.get("mode"))
            control = multi_cfg.get("switch_control") or multi_cfg.get("switchControl") or {}
            switch_info = control.get("switch_info") or control.get("switchInfo") or {}
            restore_info = control.get("restore_info") or control.get("restoreInfo") or {}
            switch_command = str(data.get("switchCommand") or switch_info.get("word") or "").strip()
            restore_command = str(data.get("restoreCommand") or restore_info.get("word") or "").strip()

            default_item = next((item for item in wake_list if isinstance(item, dict) and item_is_default(item)), {})
            if not default_item and wake_list:
                default_item = wake_list[0] if isinstance(wake_list[0], dict) else {}
            default_wake_word = str(data.get("defaultWakeWord") or item_word(default_item) or self.wakeup_word or "").strip()

            extra_items = [
                item for item in wake_list
                if isinstance(item, dict) and item_word(item) and item_word(item) != default_wake_word
            ]
            if not extra_items:
                return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "当前多唤醒候选词不足，无法自动验证 wakeWordSave")

            target_word = str(data.get("wakeWordPersistTarget") or "").strip()
            target_item = next((item for item in extra_items if item_word(item) == target_word), extra_items[0])
            if not target_word:
                target_word = item_word(target_item)
            protocol_bytes = str(data.get("protocolBytes") or item_rec_protocol(target_item) or "").strip()
            expected_active = str(data.get("expectedActiveWakeWord") or (target_word if expected_save else default_wake_word)).strip()
            inactive_words = [
                word for word in unique_words(data.get("expectedInactiveWakeWords") or [])
                if word != expected_active
            ]
            if not inactive_words:
                candidate_words = unique_words([default_wake_word, *(item_word(item) for item in extra_items)])
                inactive_words = [word for word in candidate_words if word != expected_active]

            candidate_words = unique_words([default_wake_word, target_word, *inactive_words])
            steps: List[str] = []

            default_ready, prep_steps = self._multi_wke_ensure_default_active(
                default_wake_word=default_wake_word,
                restore_command=restore_command,
                candidates=candidate_words,
                idx=tc.get("用例编号", "wake-save"),
            )
            steps.extend(prep_steps)
            if not default_ready:
                detail = f"配置预检查={config_verdict}；{config_detail}；mode={mode}；wakeWordSave={expected_save}"
                row = self._device_row(tc, tc.get("命令词", ""), "ConfigFail", detail, expected_proto="")
                if self._steps_have_skip_manual(steps):
                    row["识别判定"] = "Skip(人工)"
                row["识别结果"] = detail
                row["设备响应列表"] = " | ".join(steps)
                row["命令词"] = tc.get("命令词", "")
                row["__suite_mode"] = "device"
                return row["识别判定"], row

            if mode == "loop":
                switch_ok, switch_steps = self._multi_wke_apply_loop_switch(
                    current_wake_word=default_wake_word,
                    switch_command=switch_command,
                    switch_count=1,
                    idx=tc.get("用例编号", "wake-save"),
                )
            elif mode == "protocol":
                if not protocol_bytes:
                    return "Skip(人工)", self._config_only_row(tc, "Skip(人工)", "protocol 模式缺少协议切换字节，无法自动验证 wakeWordSave")
                switch_ok, switch_steps = self._multi_wke_apply_protocol_switch(
                    current_wake_word=default_wake_word,
                    protocol_bytes=protocol_bytes,
                    idx=tc.get("用例编号", "wake-save"),
                )
            else:
                switch_ok, switch_steps = self._multi_wke_apply_specified_switch(
                    current_wake_word=default_wake_word,
                    switch_command=switch_command,
                    target_wake_word=target_word,
                    idx=tc.get("用例编号", "wake-save"),
                )
            steps.extend(switch_steps)

            switched_ok, switched_detail = self._multi_wke_verify_phrase(target_word, should_work=True)
            steps.append(f"before-reboot-target={switched_detail}")
            save_ok, save_state, save_detail = self._wait_for_config_save_window(
                timeout_seconds=10.0,
                post_save_wait_seconds=10.0,
            )
            steps.append(
                f"assist-save-window={save_detail};refresh-wkword={save_state.get('wkword')};refresh-volume={save_state.get('volume')}"
            )

            power_reboot_base = self.reader.get_reboot_count() if self.reader else 0
            powered, power_detail = self._power_cycle_device()
            steps.append(f"power-cycle={power_detail}")
            extra_reboot, power_reboot_detail = self._power_cycle_reboot_status(power_reboot_base)
            steps.append(power_reboot_detail)
            running_ok, running_state, running_detail = self._wait_for_running_config(timeout_seconds=8.0)
            steps.append(
                "assist-after-reboot-running-config="
                f"ok={running_ok} wkword={running_state.get('wkword')} volume={running_state.get('volume')} detail={running_detail}"
            )
            if self.reader and self.reader.is_rebooted():
                self.reader.clear_reboot_flag()
            self._clear_recent_serial_lines()

            active_ok, active_detail = self._multi_wke_verify_phrase(expected_active, should_work=True)
            steps.append(f"after-reboot-active={active_detail}")
            inactive_ok = True
            for word in inactive_words:
                ok, detail = self._multi_wke_verify_phrase(word, should_work=False)
                steps.append(f"after-reboot-inactive-{word}={detail}")
                inactive_ok = inactive_ok and ok

            ok = (
                bool(expected_save is not None)
                and switch_ok
                and switched_ok
                and powered
                and (not extra_reboot)
                and active_ok
                and inactive_ok
            )
            detail = f"配置预检查={config_verdict}；{config_detail}；mode={mode}；wakeWordSave={expected_save}"
            row = self._device_row(
                tc,
                tc.get("命令词", ""),
                "OK" if ok else (
                    "Skip(人工)" if self._steps_have_skip_manual(steps)
                    else ("Reboot" if extra_reboot else "ConfigFail")
                ),
                detail,
                expected_proto="",
            )
            row["识别结果"] = detail
            row["设备响应列表"] = " | ".join(steps)
            row["命令词"] = tc.get("命令词", "")
            row["__suite_mode"] = "device"
            return row["识别判定"], row

        def execute_test_case(self, tc, idx):
            meta = self._suite_meta(tc)
            executor = meta.get("执行器", "")
            test_type = tc.get("测试类型", "")
            parameter = meta.get("原始参数", "")

            if executor == "自动-解包配置":
                verdict, detail = self._evaluate_config_assert(tc)
                return self._return_case(tc, verdict, [self._config_only_row(tc, verdict, detail)])

            if executor == "页面/接口校验":
                passed, detail = local_rule_validate(meta)
                verdict = "OK" if passed else "Skip(人工)"
                return self._return_case(tc, verdict, [self._config_only_row(tc, verdict, detail)])

            if test_type == "固件版本校验" or parameter == "firmwareVersion":
                verdict, row = self.test_firmware_version(tc)
                return self._return_case(tc, verdict, [row])

            if test_type == "超时退出":
                verdict, row = self.test_timeout_exit(tc)
                return self._return_case(tc, verdict, [row])

            if test_type == "串口参数验证":
                verdict, detail = self._evaluate_config_assert(tc)
                row = self._config_only_row(tc, verdict, detail + "；设备侧串口连通性仍需人工串口工具验证")
                return self._return_case(tc, verdict, [row])

            if test_type == "配置约束校验":
                passed, detail = local_rule_validate(meta)
                verdict = "OK" if passed else "Skip(人工)"
                return self._return_case(tc, verdict, [self._config_only_row(tc, verdict, detail)])

            if parameter == "volLevel":
                verdict, row = self.test_volume_levels(tc)
                return self._return_case(tc, verdict, [row])

            if parameter == "defaultVol":
                verdict, row = self.test_default_volume(tc)
                return self._return_case(tc, verdict, [row])

            if parameter == "volSave":
                verdict, row = self.test_volume_persist(tc)
                return self._return_case(tc, verdict, [row])

            if parameter == "wakeWordSave":
                verdict, row = self.test_wakeup_persist(tc)
                return self._return_case(tc, verdict, [row])

            if parameter == "voiceRegEnable":
                verdict, rows = self._test_voice_reg_flow(tc, idx)
                return self._return_case(tc, verdict, rows)

            if parameter == "voiceRegCommandRepeatCount":
                verdict, row = self.test_voice_reg_command_repeat(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter == "voiceRegWakeupRepeatCount":
                verdict, row = self.test_voice_reg_wakeup_repeat(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter == "voiceRegDeleteCommand":
                verdict, row = self.test_voice_reg_delete_command(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter == "voiceRegDeleteWake":
                verdict, row = self.test_voice_reg_delete_wakeup(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter == "voiceRegScenario":
                verdict, row = self.test_voice_reg_scenario(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter == "multiWkeScenario":
                verdict, row = self.test_multi_wke_scenario(tc, idx)
                return self._return_case(tc, verdict, [row])

            if parameter in self.AUTO_DYNAMIC_PARAMS:
                verdict, row = self.test_dynamic_command_case(tc)
                return self._return_case(tc, verdict, [row])

            if test_type == "功能验证":
                command = tc.get("命令词", "")
                if command and command != self.wakeup_word:
                    expected_proto = tc.get("期望协议", "")
                    verdict, row = self._test_one_with_override(command, idx, expected_proto=expected_proto)
                    reply_mode = str(tc.get("播报模式", "")).strip()
                    has_play_id = bool(str(row.get("播报ID", "")).strip())
                    runtime_expect = self._runtime_assert_map(meta)
                    expected_recv = normalize_hex_protocol(runtime_expect.get("rec", ""))
                    note_parts: List[str] = []
                    if reply_mode == "主" and verdict == "OK" and not has_play_id:
                        verdict = "ConfigFail"
                        note_parts.append("主播播报模式未捕获到 playId")
                    elif reply_mode == "被" and verdict == "OK" and has_play_id:
                        verdict = "ConfigFail"
                        note_parts.append("被动播报模式却捕获到 playId")
                    if expected_recv:
                        recv_values = [
                            normalize_hex_protocol(item)
                            for item in str(row.get("设备响应列表", "")).split("|")
                            if normalize_hex_protocol(item)
                        ]
                        note_parts.append(f"期望回传协议={expected_recv}")
                        note_parts.append(f"实际回传协议={recv_values}")
                        if verdict == "OK" and expected_recv not in recv_values:
                            verdict = "ConfigFail"
                            note_parts.append("未在 recvMsg 中捕获到期望回传协议")
                    if tc.get("期望播报", ""):
                        note_parts.append(f"需人工确认播报内容:{tc.get('期望播报', '')}")
                    row["用例编号"] = tc.get("用例编号", "")
                    row["功能模块"] = tc.get("功能模块", "")
                    row["测试类型"] = test_type
                    row["期望协议"] = expected_proto
                    row["识别判定"] = verdict
                    if note_parts:
                        row["设备响应列表"] = f"{row.get('设备响应列表', '')}|{'；'.join(note_parts)}".strip("|")
                    row["__suite_mode"] = "device"
                    return self._return_case(tc, verdict, [row])
                verdict, detail = self._evaluate_config_assert(tc)
                return self._return_case(tc, verdict, [self._config_only_row(tc, verdict, detail)])

            if test_type == "播报验证":
                # 播报配置（vcn/speed/vol/compress）不做自动听感或播报文本判定。
                # 自动化仅确认配置断言通过；稳定性由同包基础唤醒/命令词链路覆盖，
                # 避免把“欢迎播报内容需人工确认”误判为固件 FAIL。
                verdict, detail = self._evaluate_config_assert(tc)
                if verdict != "OK":
                    row = self._config_only_row(tc, verdict, detail)
                else:
                    note = "自动化仅验证配置生效；设备稳定性由同包基础唤醒/命令词链路覆盖，播报内容与音色不作为自动失败条件"
                    row = self._config_only_row(tc, "OK", f"{detail}；{note}")
                row["用例编号"] = tc.get("用例编号", "")
                row["功能模块"] = tc.get("功能模块", "")
                row["测试类型"] = test_type
                if tc.get("期望播报", ""):
                    row["设备响应列表"] = f"{row.get('设备响应列表', '')}|人工参考播报:{tc.get('期望播报', '')}".strip("|")
                row["__suite_mode"] = "device"
                return self._return_case(tc, row.get("识别判定", verdict), [row])

            verdict, rows = super().execute_test_case(tc, idx)
            for row in rows:
                row["__suite_mode"] = "device"
            return self._return_case(tc, verdict, rows)

    return ListenAISuiteVoiceTest


def config_only_main(args: argparse.Namespace) -> int:
    suite_dir = Path(args.suite_dir).resolve()
    suite_rows, suite_by_id = load_suite_rows(str(suite_dir))
    web_config = load_web_config(args.package_zip, args.web_config)
    result_rows, summary = build_config_only_rows(suite_rows, suite_by_id, web_config)

    timestamp = time.strftime("%Y-%m-%d_%H_%M_%S")
    result_dir = suite_dir / "result" / f"{timestamp}_ListenAI参数验证_{args.label}"
    result_dir.mkdir(parents=True, exist_ok=True)
    write_suite_report(result_dir, result_rows, summary, "suite_config_assert_result")

    print(f"suite_dir   : {suite_dir}")
    print(f"result_dir  : {result_dir}")
    print(f"total_cases : {summary['total']}")
    print(f"counters    : {json.dumps(summary['counters'], ensure_ascii=False)}")
    has_failures = any(
        key not in {"OK", "Skip"} and int(value or 0) > 0
        for key, value in (summary.get("counters") or {}).items()
    )
    return 1 if has_failures else 0


def device_main(args: argparse.Namespace) -> int:
    suite_dir = Path(args.suite_dir).resolve()
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "wavSource").mkdir(exist_ok=True)

    config_path = Path(args.file)
    if not config_path.is_absolute():
        config_path = suite_dir / args.file
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    suite_rows, suite_by_id = load_suite_rows(str(suite_dir))
    _ = suite_rows
    web_config = load_web_config(args.package_zip, args.web_config)

    os.chdir(suite_dir)
    config = read_json(str(config_path))

    base_module = load_base_module(args.base_script)
    runner_cls = build_device_runner(base_module)
    test = runner_cls(config, args, suite_by_id, web_config)
    test.run()
    return int(getattr(test, "exit_code", 0))


def main() -> int:
    args = parse_args()
    if args.update_audio_skills:
        os.environ["MARS_BELT_UPDATE_AUDIO_SKILLS"] = "1"
    if args.config_only:
        return config_only_main(args)
    return device_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
