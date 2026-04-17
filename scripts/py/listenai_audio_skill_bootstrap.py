from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = SCRIPT_ROOT.parent
IS_WINDOWS = platform.system() == "Windows"
TOOLS_ROOT = Path(
    os.environ.get("MARS_BELT_TOOLS_ROOT", str(SKILL_ROOT / "tools"))
).expanduser()
AUDIO_TOOLS_ROOT = TOOLS_ROOT / "audio"
LEGACY_AUDIO_TOOLS_ROOT = TOOLS_ROOT / "audio-skills"
DEFAULT_CHECKOUT_ROOT = AUDIO_TOOLS_ROOT / "repos"
DEFAULT_CONFIG_PATH = AUDIO_TOOLS_ROOT / "sources.json"
LEGACY_CONFIG_PATH = TOOLS_ROOT / "audio_skill_sources.json"

DEFAULT_AUDIO_SKILL_SOURCES: Dict[str, Any] = {
    "schemaVersion": 1,
    "skillsRoot": "audio/repos",
    "repos": {
        "listenai-play": {
            "repoUrl": "git@github-zjashanda:zjashanda/listenai-play.git",
            "checkoutDir": "listenai-play",
            "entryScript": "scripts/listenai_play.py",
        },
        "listenai-laid-installer": {
            "repoUrl": "git@github-zjashanda:zjashanda/listenai-laid-installer.git",
            "checkoutDir": "listenai-laid-installer",
            "entryScripts": {
                "linux": "scripts/install_laid_linux.sh",
                "windows": "scripts/install_laid_windows.ps1",
            },
        },
    },
}

_AUDIO_SKILL_CACHE: Dict[str, Dict[str, Any]] = {}


def _boolish(value: Any, default: bool = False) -> bool:
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


def _log(log: Any, level: str, message: str) -> None:
    logger = log
    if logger is None:
        return
    if level == "warn" and hasattr(logger, "warning"):
        logger.warning(message)
        return
    target = getattr(logger, level, None)
    if callable(target):
        target(message)


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _normalize_skills_root(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text or text == "audio-skills":
        return "audio/repos"
    return text.replace("\\", "/")


def _migrate_legacy_audio_layout() -> None:
    AUDIO_TOOLS_ROOT.mkdir(parents=True, exist_ok=True)
    if LEGACY_AUDIO_TOOLS_ROOT.exists() and not DEFAULT_CHECKOUT_ROOT.exists():
        shutil.move(str(LEGACY_AUDIO_TOOLS_ROOT), str(DEFAULT_CHECKOUT_ROOT))
    if LEGACY_CONFIG_PATH.exists() and not DEFAULT_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_CONFIG_PATH), str(DEFAULT_CONFIG_PATH))


def ensure_audio_skill_config_file() -> Path:
    _migrate_legacy_audio_layout()
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DEFAULT_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.write_text(
            json.dumps(DEFAULT_AUDIO_SKILL_SOURCES, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return DEFAULT_CONFIG_PATH


def load_audio_skill_sources() -> Dict[str, Any]:
    ensure_audio_skill_config_file()
    payload = deepcopy(DEFAULT_AUDIO_SKILL_SOURCES)
    try:
        file_payload = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        file_payload = {}
    if isinstance(file_payload, dict):
        payload = _merge_dict(payload, file_payload)
    payload["skillsRoot"] = _normalize_skills_root(payload.get("skillsRoot"))

    env_repo_overrides = {
        "listenai-play": os.environ.get("MARS_BELT_LISTENAI_PLAY_REPO_URL", "").strip(),
        "listenai-laid-installer": os.environ.get("MARS_BELT_LISTENAI_LAID_INSTALLER_REPO_URL", "").strip(),
    }
    repos = payload.setdefault("repos", {})
    for name, repo_url in env_repo_overrides.items():
        if not repo_url:
            continue
        repo_payload = repos.setdefault(name, {})
        repo_payload["repoUrl"] = repo_url
    return payload


def audio_skills_checkout_root(config: Optional[Dict[str, Any]] = None) -> Path:
    current = config or load_audio_skill_sources()
    root_name = _normalize_skills_root(current.get("skillsRoot"))
    path = TOOLS_ROOT / root_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _entry_script_for_repo(repo_payload: Dict[str, Any]) -> str:
    direct = str(repo_payload.get("entryScript") or "").strip()
    if direct:
        return direct
    entries = repo_payload.get("entryScripts")
    if isinstance(entries, dict):
        if IS_WINDOWS:
            return str(entries.get("windows") or entries.get("default") or "").strip()
        return str(entries.get("linux") or entries.get("default") or "").strip()
    return ""


def _repo_checkout_dir(name: str, repo_payload: Dict[str, Any], config: Dict[str, Any]) -> Path:
    checkout_name = str(repo_payload.get("checkoutDir") or name).strip() or name
    return audio_skills_checkout_root(config) / checkout_name


def _run_git(args: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("本机缺少 git，无法自动拉取 audio skills") from exc


def _ensure_repo_checkout(
    name: str,
    repo_payload: Dict[str, Any],
    *,
    update: bool,
    config: Dict[str, Any],
    log: Any = None,
) -> Dict[str, Any]:
    repo_url = str(repo_payload.get("repoUrl") or "").strip()
    if not repo_url:
        raise RuntimeError(f"{name} 未配置 repoUrl")

    checkout_dir = _repo_checkout_dir(name, repo_payload, config)
    entry_script = _entry_script_for_repo(repo_payload)
    if not entry_script:
        raise RuntimeError(f"{name} 未配置 entryScript/entryScripts")
    entry_path = checkout_dir / entry_script
    git_dir = checkout_dir / ".git"

    status = "ready"
    changed = False
    output = ""

    if checkout_dir.exists():
        if git_dir.exists():
            if update:
                _log(log, "info", f"更新本地依赖仓库: {name}")
                result = _run_git(["pull", "--ff-only"], cwd=checkout_dir)
                output = (result.stdout or result.stderr or "").strip()
                if result.returncode != 0:
                    raise RuntimeError(f"{name} 更新失败: {output or 'git pull --ff-only failed'}")
                status = "updated"
                changed = True
        elif entry_path.exists():
            status = "ready_non_git"
            if update:
                raise RuntimeError(f"{name} 已存在但不是 git 仓库，无法执行更新: {checkout_dir}")
        else:
            raise RuntimeError(f"{name} 目录已存在但缺少入口脚本: {entry_path}")
    else:
        checkout_dir.parent.mkdir(parents=True, exist_ok=True)
        _log(log, "info", f"本地缺少依赖仓库，开始拉取: {name}")
        result = _run_git(["clone", "--depth", "1", repo_url, str(checkout_dir)])
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(f"{name} 拉取失败: {output or 'git clone failed'}")
        status = "cloned"
        changed = True

    if not entry_path.exists():
        raise RuntimeError(f"{name} 入口脚本不存在: {entry_path}")

    return {
        "name": name,
        "repoUrl": repo_url,
        "checkoutDir": str(checkout_dir.resolve()),
        "entryScript": str(entry_path.resolve()),
        "status": status,
        "changed": changed,
        "details": output,
    }


def ensure_audio_skill_set(update: bool = False, log: Any = None) -> Dict[str, Any]:
    cache_key = "update" if update else "reuse"
    if cache_key in _AUDIO_SKILL_CACHE:
        return deepcopy(_AUDIO_SKILL_CACHE[cache_key])

    config = load_audio_skill_sources()
    repos = config.get("repos") if isinstance(config.get("repos"), dict) else {}
    required = ["listenai-play", "listenai-laid-installer"]
    report = {
        "toolsRoot": str(TOOLS_ROOT.resolve()),
        "configPath": str(DEFAULT_CONFIG_PATH.resolve()),
        "checkoutRoot": str(audio_skills_checkout_root(config).resolve()),
        "update": bool(update),
        "repos": {},
    }

    for name in required:
        repo_payload = repos.get(name)
        if not isinstance(repo_payload, dict):
            raise RuntimeError(f"audio skill 配置缺少 {name}")
        item = _ensure_repo_checkout(name, repo_payload, update=update, config=config, log=log)
        report["repos"][name] = item

    report["listenaiPlayScript"] = report["repos"]["listenai-play"]["entryScript"]
    report["laidInstallerScript"] = report["repos"]["listenai-laid-installer"]["entryScript"]
    _AUDIO_SKILL_CACHE["reuse"] = deepcopy(report)
    _AUDIO_SKILL_CACHE[cache_key] = deepcopy(report)
    return report


def resolve_listenai_play_script(update: bool = False, log: Any = None) -> str:
    report = ensure_audio_skill_set(update=update, log=log)
    return str(report.get("listenaiPlayScript") or "").strip()


def ensure_laid_helper(update: bool = False, force: bool = False, log: Any = None) -> Dict[str, Any]:
    report = ensure_audio_skill_set(update=update, log=log)
    play_script = str(report.get("listenaiPlayScript") or "").strip()
    if not play_script:
        raise RuntimeError("listenai-play 入口脚本不可用，无法执行 ensure-laid")
    cmd = [sys.executable or shutil.which("python3") or "python3", play_script, "ensure-laid"]
    if force:
        cmd.append("--force")
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    details = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(f"ensure-laid 失败: {details or 'unknown error'}")
    _log(log, "info", "已完成 laid helper 校验/安装")
    return {
        "command": cmd,
        "details": details,
        "playScript": play_script,
        "laidInstallerScript": str(report.get("laidInstallerScript") or ""),
    }


def build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Ensure local audio skill dependencies under mars-belt/tools/audio")
    parser.add_argument("--update", action="store_true", help="本地依赖已存在时也执行 git pull --ff-only")
    parser.add_argument("--ensure-laid", action="store_true", help="完成 repo 校验后再执行 listenai-play ensure-laid")
    parser.add_argument("--force-laid", action="store_true", help="配合 --ensure-laid 使用，强制刷新 laid")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = ensure_audio_skill_set(update=args.update)
    if args.ensure_laid:
        report["laid"] = ensure_laid_helper(update=False, force=args.force_laid)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
