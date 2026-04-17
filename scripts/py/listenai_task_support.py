from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = SCRIPT_ROOT.parent
TOOLS_ROOT = Path(
    os.environ.get("MARS_BELT_TOOLS_ROOT", str(SKILL_ROOT / "tools"))
).expanduser()
ARTIFACTS_ROOT = Path(
    os.environ.get("MARS_BELT_ARTIFACTS_ROOT", str(SKILL_ROOT / "artifacts"))
).expanduser()
TASKS_ROOT = ARTIFACTS_ROOT / "tasks"
RUNTIME_ROOT = ARTIFACTS_ROOT / "runtime"
PACKAGE_CACHE_ROOT = ARTIFACTS_ROOT / "package"
BURN_LOG_ROOT = ARTIFACTS_ROOT / "burn"
STATE_ROOT = ARTIFACTS_ROOT / "state"
LEGACY_TASK_ROOTS = (
    SCRIPT_ROOT / "result",
    SKILL_ROOT / "result",
)
LEGACY_RUNTIME_ROOTS = (
    SCRIPT_ROOT / "_runtime",
    SKILL_ROOT / "_runtime",
)
LOCAL_TOOLS_MD = SKILL_ROOT / "TOOLS.md"
AUDIO_TOOLS_ROOT = TOOLS_ROOT / "audio"
MAIL_TOOLS_ROOT = TOOLS_ROOT / "mail"
MAIL_SEND_SCRIPT = MAIL_TOOLS_ROOT / "send_email.py"
ERROR_MD = SKILL_ROOT / "error.md"
GLOBAL_DEVICE_INFO = SKILL_ROOT / "deviceInfo_generated.json"
GLOBAL_TTS_CONFIG = SKILL_ROOT / "tts_config.json"


def ensure_local_tools_md() -> Path:
    if not LOCAL_TOOLS_MD.exists():
        LOCAL_TOOLS_MD.write_text(
            "# Mars-Belt local config\n\nLISTENAI_TOKEN=\n",
            encoding="utf-8",
        )
    return LOCAL_TOOLS_MD


def load_local_listenai_token() -> str:
    path = LOCAL_TOOLS_MD
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("LISTENAI_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


def write_local_listenai_token(token: Any) -> Path:
    resolved = str(token or "").strip()
    path = ensure_local_tools_md()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []

    output = []
    replaced = False
    for line in lines:
        if line.startswith("LISTENAI_TOKEN="):
            if not replaced:
                output.append(f"LISTENAI_TOKEN={resolved}")
                replaced = True
            continue
        output.append(line)

    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"LISTENAI_TOKEN={resolved}")

    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return path


def resolve_listenai_token(explicit: Any = "", *, allow_missing: bool = False, persist: bool = True) -> str:
    resolved = str(explicit or "").strip() or os.environ.get("LISTENAI_TOKEN", "").strip() or load_local_listenai_token()
    if resolved:
        os.environ["LISTENAI_TOKEN"] = resolved
        if persist:
            write_local_listenai_token(resolved)
        return resolved
    if allow_missing:
        return ""
    raise RuntimeError("Missing LISTENAI_TOKEN. Pass --token or update local TOOLS.md.")


def safe_segment(text: Any, fallback: str = "item", max_len: int = 80) -> str:
    value = str(text or "").strip()
    value = re.sub(r"[<>:\"/\\\\|?*]+", "_", value)
    value = re.sub(r"\s+", "", value)
    value = value.strip("._-")
    if not value:
        value = fallback
    if len(value) > max_len:
        value = value[:max_len].rstrip("._-") or fallback
    return value


def task_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%m%d%H%M")


def ensure_task_dir(result_root: Path, product_name: str, version_desc: str, now: Optional[datetime] = None) -> Path:
    moment = now or datetime.now()
    result_root.mkdir(parents=True, exist_ok=True)
    product_dir = result_root / safe_segment(product_name, fallback="product")
    task_name = f"{safe_segment(version_desc, fallback='task')}{task_stamp(moment)}"
    task_dir = product_dir / task_name
    suffix = 2
    while task_dir.exists():
        task_dir = product_dir / f"{task_name}_{suffix}"
        suffix += 1
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def ensure_runtime_dir(*parts: Any) -> Path:
    path = RUNTIME_ROOT
    path.mkdir(parents=True, exist_ok=True)
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        path = path / safe_segment(text, fallback="item", max_len=120)
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir_for_task(task_dir: Path, purpose: str = "") -> Path:
    resolved = Path(task_dir).resolve()
    path = None
    for root in (TASKS_ROOT, *LEGACY_TASK_ROOTS):
        try:
            rel = resolved.relative_to(root.resolve())
            path = RUNTIME_ROOT / rel
            break
        except Exception:
            continue
    if path is None:
        path = RUNTIME_ROOT / safe_segment(resolved.name, fallback="task", max_len=120)
    if purpose:
        path = path / safe_segment(purpose, fallback="runtime", max_len=120)
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_dir_for_runtime(runtime_dir: Path) -> Optional[Path]:
    resolved = Path(runtime_dir).resolve()
    for root in (RUNTIME_ROOT, *LEGACY_RUNTIME_ROOTS):
        try:
            rel = resolved.relative_to(root.resolve())
            parts = rel.parts
            if len(parts) >= 2:
                return TASKS_ROOT / parts[0] / parts[1]
            if len(parts) == 1:
                return TASKS_ROOT / parts[0]
            return TASKS_ROOT
        except Exception:
            continue
    return None


def resolve_user_path(path_value: Any, cwd: Optional[Path] = None) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    base = cwd or Path.cwd()
    path = Path(text)
    if not path.is_absolute():
        path = (base / path).resolve()
    else:
        path = path.resolve()
    return str(path)


def load_global_tts_config() -> Dict[str, str]:
    for path in [GLOBAL_TTS_CONFIG, GLOBAL_DEVICE_INFO]:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        tts = payload.get("ttsConfig") if isinstance(payload, dict) else None
        if not isinstance(tts, dict):
            continue
        app_id = str(tts.get("app_id") or "").strip()
        api_key = str(tts.get("api_key") or "").strip()
        if not app_id or not api_key:
            continue
        return {
            "app_id": app_id,
            "api_key": api_key,
            "vcn": str(tts.get("vcn") or "x4_yezi"),
            "speed": str(tts.get("speed") or "50"),
            "pitch": str(tts.get("pitch") or "50"),
            "volume": str(tts.get("volume") or "100"),
        }
    return {}


def _bool_from_any(value: Any, default: bool = False) -> bool:
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


def load_global_audio_card_config() -> Dict[str, Any]:
    if not GLOBAL_DEVICE_INFO.exists():
        return {}
    try:
        payload = json.loads(GLOBAL_DEVICE_INFO.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    device_list = payload.get("deviceListInfo") if isinstance(payload.get("deviceListInfo"), dict) else {}
    audio = device_list.get("audioCard")
    if not isinstance(audio, dict):
        audio = payload.get("audioCard")
    if not isinstance(audio, dict):
        return {}

    return {
        "deviceKey": str(audio.get("deviceKey") or "").strip(),
        "useDefault": _bool_from_any(audio.get("useDefault"), False),
        "fallbackToDefault": _bool_from_any(audio.get("fallbackToDefault"), True),
        "name": str(audio.get("name") or "").strip(),
        "backendTarget": str(audio.get("backendTarget") or "").strip(),
        "lastError": str(audio.get("lastError") or "").strip(),
    }


def _render_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return repr(value)


def append_error_entry(
    *,
    command_name: str,
    cli_args: Optional[Dict[str, Any]] = None,
    phenomenon: str,
    stdout_text: str = "",
    stderr_text: str = "",
    work_dir: str = "",
) -> None:
    ERROR_MD.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {command_name}",
        "",
    ]
    if work_dir:
        sections.append(f"- workDir: `{work_dir}`")
    sections.append(f"- phenomenon: {phenomenon}")
    sections.append("")
    if cli_args is not None:
        sections.extend(
            [
                "### cliArgs",
                "",
                "```json",
                _render_json(cli_args),
                "```",
                "",
            ]
        )
    if stdout_text.strip():
        sections.extend(
            [
                "### stdout",
                "",
                "```text",
                stdout_text.rstrip(),
                "```",
                "",
            ]
        )
    if stderr_text.strip():
        sections.extend(
            [
                "### stderr",
                "",
                "```text",
                stderr_text.rstrip(),
                "```",
                "",
            ]
        )
    with ERROR_MD.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(sections).rstrip() + "\n\n")


def _maybe_parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return stripped


def _parse_validation_text(text: str) -> Dict[str, Any]:
    sections: Dict[str, str] = {}
    scalars: Dict[str, str] = {}
    current_section = ""
    buffer: list[str] = []

    def flush_section() -> None:
        nonlocal buffer
        if current_section:
            sections[current_section] = "\n".join(buffer).strip()
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        section_match = re.fullmatch(r"\[(.+)\]", stripped)
        if section_match:
            flush_section()
            current_section = section_match.group(1).strip()
            continue
        if current_section:
            buffer.append(line)
            continue
        scalar_match = re.match(r"^([A-Za-z0-9_\u4e00-\u9fff]+)\s*[:=]\s*(.*)$", stripped)
        if scalar_match:
            scalars[scalar_match.group(1)] = scalar_match.group(2).strip()
    flush_section()

    def section_value(*names: str) -> Any:
        for name in names:
            if name in sections:
                return _maybe_parse_json(sections[name])
        return None

    applied_overrides = section_value("appliedOverrides", "应用覆盖") or {}
    if not isinstance(applied_overrides, dict):
        applied_overrides = {}

    learn_words = section_value(
        "resolvedVoiceRegLearnWords",
        "voiceRegLearnWords",
        "requestedVoiceRegLearnWords",
        "voiceRegLearnWords",
    )
    if not isinstance(learn_words, list):
        learn_words = []

    final_release = section_value("finalRelease", "最终发布单字段")
    if not isinstance(final_release, dict):
        final_release = {}

    return {
        "scalars": scalars,
        "sections": sections,
        "appliedOverrides": applied_overrides,
        "learnWords": [str(item).strip() for item in learn_words if str(item or "").strip()],
        "finalRelease": final_release,
        "variantId": scalars.get("variantId", ""),
        "variantTitle": scalars.get("variantTitle", ""),
        "comments": scalars.get("comments", final_release.get("comments", "")),
    }


def load_validation_metadata(package_zip: str) -> Dict[str, Any]:
    zip_path = Path(package_zip)
    if not zip_path.exists():
        return {
            "scalars": {},
            "sections": {},
            "appliedOverrides": {},
            "learnWords": [],
            "finalRelease": {},
            "variantId": "",
            "variantTitle": "",
            "comments": "",
        }
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if name.endswith("validation_params.txt"):
                raw = archive.read(name).decode("utf-8", errors="ignore")
                return _parse_validation_text(raw)
    return {
        "scalars": {},
        "sections": {},
        "appliedOverrides": {},
        "learnWords": [],
        "finalRelease": {},
        "variantId": "",
        "variantTitle": "",
        "comments": "",
    }


def infer_profile_name(applied_overrides: Dict[str, Any], learn_words: Iterable[str]) -> str:
    clean_overrides = {key: value for key, value in (applied_overrides or {}).items() if key not in {"comments", "saveMode"}}
    has_voice = bool(clean_overrides.get("voiceRegEnable") or list(learn_words))
    has_multi = bool(
        clean_overrides.get("multiWkeEnable")
        or clean_overrides.get("releaseMultiWke")
        or "wakeWordSave" in clean_overrides
    )
    if has_voice and not has_multi:
        return "voice-reg"
    if has_multi and not has_voice:
        return "multi-wke"
    if has_voice and has_multi:
        return "changed"
    if clean_overrides:
        return "changed"
    return "base"
