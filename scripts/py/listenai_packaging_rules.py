from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence


VERSION_LABEL_ALIASES = [
    ("通用垂类", "通用垂类"),
    ("风扇垂类", "风扇垂类"),
    ("取暖器垂类", "取暖器垂类"),
    ("窗帘垂类", "窗帘垂类"),
    ("取暖桌垂类", "取暖桌垂类"),
    ("茶吧机垂类", "茶吧机垂类"),
]

COMMENT_KEY_ORDER = [
    "timeout",
    "volLevel",
    "defaultVol",
    "voiceRegEnable",
    "multiWkeEnable",
    "multiWkeMode",
    "wakeWordSave",
    "volSave",
    "uportUart",
    "uportBaud",
    "traceUart",
    "traceBaud",
    "logLevel",
    "vcn",
    "speed",
    "vol",
    "compress",
    "word",
    "paConfigEnable",
    "ctlIoPad",
    "ctlIoNum",
    "holdTime",
    "paConfigEnableLevel",
    "algoViewMode",
    "registMode",
]

COMMENT_LABELS = {
    "uportUart": "上报串口",
    "uportBaud": "上报码率",
    "traceUart": "日志串口",
    "traceBaud": "日志波特率",
    "logLevel": "日志级别",
    "ctlIoPad": "功放Pad",
    "ctlIoNum": "功放IO",
    "holdTime": "功放保持",
    "paConfigEnableLevel": "功放电平",
}


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def week_stamp(current: Optional[date | datetime] = None) -> str:
    if current is None:
        today = datetime.now().date()
    elif isinstance(current, datetime):
        today = current.date()
    else:
        today = current
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%m%d")


def module_code(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(\d{4})", text)
    if match:
        return match.group(1)
    return _compact_text(text) or "module"


def product_label_text(value: Any) -> str:
    text = _compact_text(value)
    text = text.replace("/", "-").replace("\\", "-")
    return text or "产品"


def version_label_text(value: Any) -> str:
    text = _compact_text(value)
    for keyword, alias in VERSION_LABEL_ALIASES:
        if keyword in text:
            return alias
    text = text.replace("/", "-").replace("\\", "-")
    return text or "版本"


def build_weekly_product_name(module: Any, product_label: Any, version_label: Any) -> str:
    return "-".join(
        [
            module_code(module),
            product_label_text(product_label),
            version_label_text(version_label),
            week_stamp(),
        ]
    )


def build_weekly_product_name_from_selected(selected: Dict[str, Any]) -> str:
    return build_weekly_product_name(
        selected.get("moduleBoard") or selected.get("moduleMark"),
        selected.get("productLabel"),
        selected.get("versionLabel"),
    )


def _to_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return None


def _to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            return None
    return None


def _algo_view_mode_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "full":
        return "全量算法"
    if text:
        return f"算法视图{text}"
    return ""


def _regist_mode_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "specificlearn":
        return "指定学习"
    if text == "contlearn":
        return "连续学习"
    return ""


def _detail_phrase(key: str, value: Any) -> str:
    if key == "timeout":
        number = _to_int(value)
        return f"{number}秒超时" if number is not None else ""
    if key == "volLevel":
        number = _to_int(value)
        return f"{number}档音量" if number is not None else ""
    if key == "defaultVol":
        number = _to_int(value)
        return f"默认{number}档" if number is not None else ""
    if key == "voiceRegEnable":
        flag = _to_bool(value)
        return "语音注册" if flag else ""
    if key == "multiWkeEnable":
        flag = _to_bool(value)
        return "多唤醒" if flag else ""
    if key == "multiWkeMode":
        text = str(value or "").strip()
        return f"多唤醒模式{text}" if text else ""
    if key == "wakeWordSave":
        flag = _to_bool(value)
        if flag is None:
            return ""
        return "保存唤醒状态" if flag else "不保存唤醒状态"
    if key == "volSave":
        flag = _to_bool(value)
        if flag is None:
            return ""
        return "保存音量" if flag else "不保存音量"
    if key == "vcn":
        text = str(value or "").strip()
        return f"音色{text}" if text else ""
    if key == "speed":
        number = _to_int(value)
        return f"语速{number}" if number is not None else ""
    if key == "vol":
        number = _to_int(value)
        return f"播报音量{number}" if number is not None else ""
    if key == "compress":
        text = str(value or "").strip()
        return f"压缩比{text}" if text else ""
    if key == "word":
        text = str(value or "").strip()
        return "欢迎词已改" if text else ""
    if key == "paConfigEnable":
        flag = _to_bool(value)
        return "功放使能" if flag else ""
    if key == "algoViewMode":
        return _algo_view_mode_text(value)
    if key == "registMode":
        return _regist_mode_text(value)
    if key in COMMENT_LABELS:
        text = str(value or "").strip()
        return f"{COMMENT_LABELS[key]}{text}" if text else ""
    return ""


def _unique_phrases(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def build_short_release_comment(
    module: Any,
    product_label: Any,
    version_label: Any,
    overrides: Optional[Dict[str, Any]] = None,
    *,
    extra_phrases: Optional[Sequence[str]] = None,
    learn_words: Optional[Sequence[str]] = None,
    max_len: int = 240,
) -> str:
    base = f"{module_code(module)}{product_label_text(product_label)}{version_label_text(version_label)}"
    payload = dict(overrides or {})
    phrases: List[str] = []
    for key in COMMENT_KEY_ORDER:
        if key not in payload:
            continue
        phrase = _detail_phrase(key, payload.get(key))
        if phrase:
            phrases.append(phrase)
    for phrase in extra_phrases or []:
        if str(phrase or "").strip():
            phrases.append(str(phrase).strip())
    learn_word_items = [str(item or "").strip() for item in (learn_words or []) if str(item or "").strip()]
    if learn_word_items:
        preview = "、".join(learn_word_items[:3])
        if len(learn_word_items) > 3:
            preview += "等"
        phrases.append(f"注册词{preview}")
    detail_text = "，".join(_unique_phrases(phrases[:6]))
    comment = base if not detail_text else f"{base}，{detail_text}"
    return comment[:max_len]


def build_short_release_comment_from_selected(
    selected: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
    *,
    extra_phrases: Optional[Sequence[str]] = None,
    learn_words: Optional[Sequence[str]] = None,
    max_len: int = 240,
) -> str:
    return build_short_release_comment(
        selected.get("moduleBoard") or selected.get("moduleMark"),
        selected.get("productLabel"),
        selected.get("versionLabel"),
        overrides or {},
        extra_phrases=extra_phrases,
        learn_words=learn_words,
        max_len=max_len,
    )
