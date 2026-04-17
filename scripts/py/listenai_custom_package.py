from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from listenai_advanced_combo_trials import build_feature_toggle, package_release_with_algo_unified
from listenai_auto_package import ListenAIClient, parse_override_args
from listenai_batch_package_parameters import (
    fetch_release_detail,
    release_verification_overrides,
    slugify,
    values_equal,
    verify_release,
)
from listenai_executable_case_suite import first_version, load_web_config
from listenai_packaging_rules import build_short_release_comment_from_selected
from listenai_local_base_profiles import apply_local_base_profile, default_shared_product_name
from listenai_profile_suite import build_profile_payload, export_suite
from listenai_shared_product_flow import ensure_shared_product, package_release_for_existing_product
from listenai_parameter_catalog import (
    DEFAULT_CONFIG_DETAILS_IN,
    DEFAULT_CONFIG_RECOMMENDED_IN,
    DEFAULT_CONFIG_SENSITIVITY_IN,
    DEFAULT_DICT_TREE_IN,
    DEFAULT_RELEASE_DETAIL_IN,
    build_catalog_payload,
)
from listenai_task_support import PACKAGE_CACHE_ROOT, TASKS_ROOT, ensure_task_dir, infer_profile_name, resolve_listenai_token, runtime_dir_for_task


DEFAULT_RESULT_ROOT = str(TASKS_ROOT)
DEFAULT_PACKAGE_ROOT = str(PACKAGE_CACHE_ROOT)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def week_stamp() -> str:
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%m%d")


def language_tag(language: str) -> str:
    if language == "中文":
        return "zh"
    if language == "英文":
        return "en"
    return re.sub(r"[^0-9A-Za-z]+", "", language.lower()) or "lang"


def family_tag(version_label: str) -> str:
    text = str(version_label or "")
    if "通用" in text:
        return "generic"
    if "风扇" in text:
        return "fan"
    if "取暖器" in text:
        return "heater"
    if "窗帘" in text:
        return "curtain"
    if "取暖桌" in text:
        return "desk-heater"
    if "茶吧机" in text:
        return "tea-maker"
    return slugify(text, max_len=20)


def sdk_tag(version_label: str) -> str:
    matches = re.findall(r"(A\d+(?:\.\d+)*)", str(version_label or ""), re.I)
    return matches[-1].upper() if matches else "SDK"


def shared_product_name(selected: Dict[str, Any], explicit_name: str = "") -> str:
    return default_shared_product_name(selected, explicit_name)


def default_run_id(selected: Dict[str, Any]) -> str:
    module = re.search(r"(\d{4})", str(selected.get("moduleBoard") or ""))
    chip = module.group(1) if module else slugify(str(selected.get("moduleMark") or "module"), max_len=12)
    return f"custom-{chip}-{slugify(str(selected.get('productLabel') or 'product'), 16)}-{language_tag(str(selected.get('language') or ''))}-{family_tag(str(selected.get('versionLabel') or ''))}-{now_stamp()}"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def summarize_overrides(overrides: Dict[str, Any]) -> str:
    parts = [f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in overrides.items() if key != "comments"]
    return ", ".join(parts[:6]) if parts else "no-change"


def normalize_multi_wke_mode(value: Any) -> str:
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


def build_comment(selected: Dict[str, Any], overrides: Dict[str, Any], explicit_comments: str = "", *, local_base_applied: bool = False) -> str:
    if explicit_comments.strip():
        return explicit_comments.strip()
    effective = {key: value for key, value in (overrides or {}).items() if key != "comments"}
    effective_for_comment = dict(effective)
    if local_base_applied:
        effective_for_comment = {
            key: value
            for key, value in effective_for_comment.items()
            if key != "releaseAlgoList" and not key.startswith("releaseAlgoList")
        }
    extra_phrases: List[str] = []
    if not effective_for_comment:
        extra_phrases.append("英文基础配置" if local_base_applied else "基础配置")
    if not local_base_applied and ("releaseAlgoList" in effective or any(key.startswith("releaseAlgoList") for key in effective)):
        extra_phrases.append("指定词表")
    return build_short_release_comment_from_selected(selected, effective_for_comment, extra_phrases=extra_phrases)


def build_catalog_args(args: argparse.Namespace, catalog_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        token=args.token,
        refresh_live=args.refresh_live,
        product=args.product,
        module=args.module,
        language=args.language,
        version=args.version,
        scene=args.scene,
        source_release_id=args.source_release_id,
        md_out=str(catalog_dir / "parameter_catalog.md"),
        json_out=str(catalog_dir / "parameter_catalog.json"),
        dict_tree_in=DEFAULT_DICT_TREE_IN,
        config_details_in=DEFAULT_CONFIG_DETAILS_IN,
        config_sensitivity_in=DEFAULT_CONFIG_SENSITIVITY_IN,
        config_recommended_in=DEFAULT_CONFIG_RECOMMENDED_IN,
        release_detail_in=DEFAULT_RELEASE_DETAIL_IN,
        json_out_catalog=str(catalog_dir / "product_options.json"),
        products_csv_out=str(catalog_dir / "product_catalog.csv"),
        modules_csv_out=str(catalog_dir / "module_catalog.csv"),
        matrix_csv_out=str(catalog_dir / "product_options_matrix.csv"),
        duplicates_csv_out=str(catalog_dir / "version_defid_duplicates.csv"),
        matrix_md_out=str(catalog_dir / "product_options_matrix.md"),
        resolution_out=str(catalog_dir / "resolved_product.json"),
        summary_out=str(catalog_dir / "selected_package_summary.json"),
    )


def resolve_catalog(args: argparse.Namespace, catalog_dir: Path) -> Dict[str, Any]:
    if args.catalog_json:
        return json.loads(Path(args.catalog_json).read_text(encoding="utf-8"))
    payload = build_catalog_payload(build_catalog_args(args, catalog_dir))
    write_json(catalog_dir / "parameter_catalog.json", payload)
    return payload


def require_features(selected: Dict[str, Any], feature_map: Dict[str, Any], required: List[str]) -> None:
    failures: List[str] = []
    for key in required:
        state = feature_map.get(key)
        if str(state or "") != "Optional":
            failures.append(f"{key}={state!r}")
    if failures:
        raise RuntimeError(
            "required feature gates are not available for "
            f"{selected.get('productPath')} / {selected.get('moduleBoard')} / {selected.get('language')} / {selected.get('versionLabel')}: "
            + ", ".join(failures)
        )


def extract_web_value(version_payload: Dict[str, Any], key: str) -> Any:
    firmware = version_payload.get("firmware") or {}
    multi_cfg = version_payload.get("multi_wakeup") or firmware.get("multi_wakeup") or {}
    if key == "timeout":
        return ((firmware.get("timeout_config") or {}).get("time"))
    if key == "volLevel":
        return len(((firmware.get("volume_config") or {}).get("level")) or [])
    if key == "defaultVol":
        return ((firmware.get("volume_config") or {}).get("default"))
    if key == "volMaxOverflow":
        return ((firmware.get("volume_config") or {}).get("adj_max_reply"))
    if key == "volMinOverflow":
        return ((firmware.get("volume_config") or {}).get("adj_min_reply"))
    if key == "uportUart":
        return ((firmware.get("uart_config") or {}).get("uport_uart"))
    if key == "uportBaud":
        return ((firmware.get("uart_config") or {}).get("uport_baud"))
    if key == "traceUart":
        return ((firmware.get("uart_config") or {}).get("trace_uart"))
    if key == "traceBaud":
        return ((firmware.get("uart_config") or {}).get("trace_baud"))
    if key == "logLevel":
        return ((firmware.get("general_config") or {}).get("log_level"))
    if key == "wakeWordSave":
        return (((firmware.get("general_config") or {}).get("persisted") or {}).get("wakeup"))
    if key == "volSave":
        return (((firmware.get("general_config") or {}).get("persisted") or {}).get("volume"))
    if key == "vcn":
        return (((firmware.get("custom_voice") or {}).get("speaker") or {}).get("vcn"))
    if key == "speed":
        return (((firmware.get("custom_voice") or {}).get("speaker") or {}).get("speed"))
    if key == "vol":
        return (((firmware.get("custom_voice") or {}).get("speaker") or {}).get("volume"))
    if key == "compress":
        return (((firmware.get("custom_voice") or {}).get("speaker") or {}).get("compre_ratio"))
    if key == "word":
        return ((firmware.get("welcome_config") or {}).get("reply"))
    if key == "paConfigEnable":
        return ((firmware.get("pa_config") or {}).get("enable"))
    if key == "ctlIoPad":
        return ((firmware.get("pa_config") or {}).get("ctl_io_pad"))
    if key == "ctlIoNum":
        return ((firmware.get("pa_config") or {}).get("ctl_io_num"))
    if key == "holdTime":
        return ((firmware.get("pa_config") or {}).get("hold_time"))
    if key == "paConfigEnableLevel":
        return ((firmware.get("pa_config") or {}).get("enable_level"))
    if key == "voiceRegEnable":
        return ((firmware.get("study_config") or {}).get("enable"))
    if key == "multiWkeEnable":
        return (multi_cfg or {}).get("enable")
    if key == "multiWkeMode":
        return normalize_multi_wke_mode((multi_cfg or {}).get("mode"))
    return None


def verify_web_config(version_payload: Dict[str, Any], overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    for key, expected in overrides.items():
        if key == "comments":
            continue
        actual = extract_web_value(version_payload, key)
        supported = actual is not None
        matched = values_equal(expected, actual) if supported else None
        checks.append(
            {
                "key": key,
                "expected": expected,
                "actual": actual,
                "supported": supported,
                "matched": matched,
            }
        )
    return checks


def zip_verify_ok(checks: List[Dict[str, Any]]) -> bool:
    for item in checks:
        if item["supported"] and item["matched"] is False:
            return False
    return True


def inject_validation_params(zip_path: Path, parameter_text: str) -> None:
    with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("validation_params.txt", parameter_text)


def build_params_text(
    generated_at: str,
    selected: Dict[str, Any],
    product_detail: Dict[str, Any],
    source_release_id: str,
    overrides: Dict[str, Any],
    final_release: Dict[str, Any],
    release_checks: List[Dict[str, Any]],
    zip_checks: List[Dict[str, Any]],
) -> str:
    lines = [
        "ListenAI 自定义固件参数说明",
        "",
        f"generatedAt={generated_at}",
        "",
        "[目标信息]",
        f"productPath={selected.get('productPath')}",
        f"productLabel={selected.get('productLabel')}",
        f"scene={selected.get('sceneLabel')}",
        f"moduleBoard={selected.get('moduleBoard')}",
        f"moduleMark={selected.get('moduleMark')}",
        f"language={selected.get('language')}",
        f"versionLabel={selected.get('versionLabel')}",
        f"defId={selected.get('defId')}",
        f"mode={selected.get('mode')}",
        f"sourceReleaseId={source_release_id}",
        "",
        "[共享产品]",
        f"productName={product_detail.get('name')}",
        f"productId={product_detail.get('id')}",
        "",
        "[应用覆盖]",
        json.dumps(overrides, ensure_ascii=False, indent=2),
        "",
        "[最终发布单字段]",
        json.dumps(
            {key: final_release.get(key) for key in ["id", "prodId", "version", "status", "timeout", "volLevel", "defaultVol", "voiceRegEnable", "multiWkeEnable", "multiWkeMode", "comments", "pkgTaskId", "pkgPipelineId", "pkgUrl", "pkgSDKUrl"]},
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "[release detail 校验]",
        json.dumps(release_checks, ensure_ascii=False, indent=2),
        "",
        "[包内 web_config 校验]",
        json.dumps(zip_checks, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_summary_md(payload: Dict[str, Any]) -> str:
    selected = payload["selected"]
    package_summary = payload["packageSummary"]
    artifacts = payload["artifacts"]
    return "\n".join(
        [
            "# ListenAI 单包自定义打包结果",
            "",
            f"- 运行目录：`{payload['runId']}`",
            f"- 目标：`{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`",
            f"- 共享产品：`{package_summary.get('productName')}`",
            f"- sourceReleaseId：`{payload['sourceReleaseId']}`",
            f"- 覆盖参数：`{json.dumps(payload['appliedOverrides'], ensure_ascii=False)}`",
            f"- releaseId：`{package_summary.get('releaseId')}`",
            f"- releaseVersion：`{package_summary.get('releaseVersion')}`",
            f"- 后台 release detail 校验：`{'PASS' if payload['releaseVerifyOk'] else 'FAIL'}`",
            f"- 包内 web_config 校验：`{'PASS' if payload['zipVerifyOk'] else 'FAIL'}`",
            f"- 固件包：`{artifacts['packageZip']}`",
            f"- 参数说明：`{artifacts['packageParamsTxt']}`",
            f"- web_config：`{artifacts['webConfigJson']}`",
            f"- 汇总：`{artifacts['summaryJson']}`",
        ]
    ) + "\n"


def load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def download_with_retry(client: ListenAIClient, url: str, target_path: Path, retry_count: int = 3, sleep_sec: int = 5) -> Path:
    last_error: Optional[Exception] = None
    for index in range(1, max(1, retry_count) + 1):
        try:
            return Path(client.download(url, str(target_path)))
        except Exception as exc:
            last_error = exc
            if index >= max(1, retry_count):
                break
            time.sleep(max(1, sleep_sec))
    raise RuntimeError(f"download failed after {retry_count} attempts: {last_error}")


def build_attempt_log(attempt: int, exc: Exception, trace: str) -> str:
    return "\n".join(
        [
            f"attempt={attempt}",
            f"time={datetime.now().isoformat(timespec='seconds')}",
            f"error={exc}",
            "",
            trace.rstrip(),
        ]
    ).rstrip() + "\n"


def should_use_algo_path(overrides: Dict[str, Any]) -> bool:
    algo_keys = {
        "sensitivity",
        "voiceRegEnable",
        "multiWkeEnable",
        "multiWkeMode",
        "algoViewMode",
        "releaseRegist",
        "releaseRegistConfig",
        "releaseMultiWke",
        "releaseAlgoList",
        "studyRegCommands",
    }
    for key in overrides:
        if key == "comments":
            continue
        if key in algo_keys or key.startswith("releaseAlgoList"):
            return True
    return False


def retry_sleep_seconds(exc: Exception) -> int:
    text = str(exc or "").lower()
    explicit_markers = ["unsupported", "feature gate", "missing source", "missing token", "required feature", "invalid override", "not available"]
    if any(marker in text for marker in explicit_markers):
        return 5
    return 60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package one custom ListenAI firmware with arbitrary release overrides.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token")
    parser.add_argument("--catalog-json", default="", help="Existing parameter catalog json. Skip live resolution when provided.")
    parser.add_argument("--product", default="", help="Product leaf or full path, for example 通用 or 小家电 / 取暖器")
    parser.add_argument("--module", default="", help="Module mark or board, for example 3022")
    parser.add_argument("--language", default="", help="Language, for example 中文")
    parser.add_argument("--version", default="", help="Version keyword, for example 通用垂类")
    parser.add_argument("--scene", default="纯离线", help="Scene, default 纯离线")
    parser.add_argument("--refresh-live", action="store_true", help="Refresh live product matrix before resolving")
    parser.add_argument("--source-release-id", default="", help="Explicit sourceReleaseId")
    parser.add_argument("--product-name", default="", help="Explicit shared product name. Default reuses the weekly shared name.")
    parser.add_argument("--run-id", default="", help="Run id for outputs. Default is auto-generated.")
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT, help="Result root directory")
    parser.add_argument("--package-root", default=DEFAULT_PACKAGE_ROOT, help="Package root directory")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Per-attempt package timeout")
    parser.add_argument("--retry-limit", type=int, default=2, help="Retry count when package or verification fails")
    parser.add_argument("--override", action="append", default=[], help="Release override in KEY=VALUE form. Repeatable.")
    parser.add_argument("--comments", default="", help="Explicit release comments. Overrides comments from --override.")
    parser.add_argument("--require-feature", action="append", default=[], help="Require a feature gate such as voice_regist or multi_wakeup to be Optional.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve target and write a packaging plan without packaging.")
    return parser


def validate_inputs(args: argparse.Namespace) -> None:
    if args.catalog_json:
        return
    missing = [name for name in ["product", "module", "language", "version"] if not getattr(args, name)]
    if missing:
        raise RuntimeError(f"missing required inputs: {', '.join(missing)}")


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, allow_missing=bool(args.dry_run), persist=True)
    validate_inputs(args)
    if not args.token and not args.dry_run:
        raise RuntimeError("Missing token. Use --token or set LISTENAI_TOKEN.")

    raw_overrides = parse_override_args(args.override)
    raw_overrides.pop("comments", None)
    raw_overrides["comments"] = ""

    result_root = Path(args.result_root or DEFAULT_RESULT_ROOT)
    initial_catalog_dir = runtime_dir_for_task(result_root / "_pending_custom_package", args.run_id or "tmp-custom-package")
    catalog = resolve_catalog(args, initial_catalog_dir)
    selected = dict(catalog.get("selected") or {})
    if not selected:
        raise RuntimeError("catalog did not resolve a unique target")
    feature_map = dict(catalog.get("featureMap") or {})
    require_features(selected, feature_map, list(args.require_feature or []))

    shared_name = shared_product_name(selected, args.product_name)
    run_id = args.run_id or default_run_id(selected)
    client: Optional[ListenAIClient] = None
    manifest = {
        "selectedMeta": selected,
        "sharedProduct": {
            "productName": shared_name,
            "productId": "",
            "productDetail": None,
        },
    }
    prepared_product_detail: Dict[str, Any] = {}
    if not args.dry_run:
        client = ListenAIClient(token=args.token, timeout=max(60, args.timeout_sec))
        prepared_product_detail = ensure_shared_product(client, manifest)

    local_base = apply_local_base_profile(
        selected=selected,
        explicit_product_name=args.product_name,
        explicit_source_release_id=args.source_release_id,
        catalog_source_release_id=str(catalog.get("sourceReleaseId") or ""),
        overrides=raw_overrides,
        client=client,
        product_detail=prepared_product_detail,
    )
    shared_name = str(local_base.get("sharedProductName") or shared_name)
    overrides = dict(local_base.get("overrides") or raw_overrides)
    overrides["comments"] = build_comment(selected, overrides, args.comments, local_base_applied=bool(local_base.get("appliedLocalAlgo")))

    result_dir = ensure_task_dir(result_root, shared_name, overrides["comments"])
    runtime_dir = runtime_dir_for_task(result_dir)
    catalog_dir = runtime_dir / "catalog"
    write_json(catalog_dir / "parameter_catalog.json", catalog)
    package_dir = result_dir
    suite_dir = runtime_dir / "suite"
    attempt_dir = runtime_dir / "attempt_logs"
    checkpoint_path = runtime_dir / "package_checkpoint.json"
    summary_path = runtime_dir / "summary.json"
    ensure_dir(package_dir)
    ensure_dir(runtime_dir)
    ensure_dir(catalog_dir)
    ensure_dir(attempt_dir)

    source_release_id = str(local_base.get("sourceReleaseId") or args.source_release_id or str(catalog.get("sourceReleaseId") or ""))
    if not source_release_id and not args.dry_run:
        raise RuntimeError("Missing sourceReleaseId. Pass --source-release-id or provide a catalog that includes it.")
    plan_payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "runId": run_id,
        "selected": selected,
        "sourceReleaseId": source_release_id,
        "featureMap": feature_map,
        "requireFeature": list(args.require_feature or []),
        "appliedOverrides": overrides,
        "localBaseProfile": local_base.get("profile") or None,
        "localBaseMatched": bool(local_base.get("matchedProfile")),
        "localBaseApplied": bool(local_base.get("appliedLocalAlgo")),
        "localBaseSourceStrategy": str(local_base.get("sourceStrategy") or ""),
        "sharedProductName": shared_name,
        "retryLimit": args.retry_limit,
        "timeoutSec": args.timeout_sec,
        "dryRun": args.dry_run,
    }
    write_json(runtime_dir / "plan.json", plan_payload)
    write_text(
        runtime_dir / "plan.md",
        "\n".join(
            [
                "# ListenAI 单包自定义打包计划",
                "",
                f"- runId: `{run_id}`",
                f"- target: `{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`",
                f"- sourceReleaseId: `{source_release_id}`",
                f"- sharedProductName: `{shared_name}`",
                f"- localBaseProfile: `{json.dumps((local_base.get('profile') or {}).get('id') or '', ensure_ascii=False)}`",
                f"- localBaseApplied: `{json.dumps(bool(local_base.get('appliedLocalAlgo'))).lower()}`",
                f"- localBaseSourceStrategy: `{str(local_base.get('sourceStrategy') or '')}`",
                f"- requireFeature: `{json.dumps(list(args.require_feature or []), ensure_ascii=False)}`",
                f"- overrides: `{json.dumps(overrides, ensure_ascii=False)}`",
                f"- retryLimit: `{args.retry_limit}`",
                f"- timeoutSec: `{args.timeout_sec}`",
            ]
        )
        + "\n",
    )

    if args.dry_run:
        print(f"task_dir: {result_dir.resolve()}")
        print(f"plan_json: {(runtime_dir / 'plan.json').resolve()}")
        print(f"plan_md: {(runtime_dir / 'plan.md').resolve()}")
        print(json.dumps(plan_payload, ensure_ascii=False, indent=2))
        return 0

    feature_toggle = build_feature_toggle(feature_map)
    use_algo_path = should_use_algo_path(overrides)

    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, args.retry_limit) + 1):
        checkpoint = load_json_file(checkpoint_path) if checkpoint_path.exists() else {}
        try:
            product_detail = dict(checkpoint.get("productDetail") or prepared_product_detail)
            summary = dict(checkpoint.get("packageSummary") or {})
            final_release = dict(checkpoint.get("finalRelease") or {})
            if not summary or not final_release:
                if not product_detail:
                    product_detail = ensure_shared_product(client, manifest)
                if use_algo_path:
                    summary = package_release_with_algo_unified(
                        client=client,
                        product_detail=product_detail,
                        source_release_id=source_release_id,
                        timeout_sec=args.timeout_sec,
                        release_overrides=overrides,
                        feature_toggle=feature_toggle,
                    )
                else:
                    summary = package_release_for_existing_product(
                        client=client,
                        product_detail=product_detail,
                        source_release_id=source_release_id,
                        timeout_sec=args.timeout_sec,
                        release_overrides=overrides,
                    )
                final_release = fetch_release_detail(client, str(summary["releaseId"]))
                write_json(
                    checkpoint_path,
                    {
                        "generatedAt": datetime.now().isoformat(timespec="seconds"),
                        "attempt": attempt,
                        "productDetail": product_detail,
                        "packageSummary": summary,
                        "finalRelease": final_release,
                    },
                )
            verify_inputs = release_verification_overrides(dict(summary.get("appliedOverrides") or overrides))
            release_verify_ok, release_checks = verify_release(final_release, verify_inputs)

            release_version = str(summary.get("releaseVersion") or f"attempt-{attempt}")
            zip_name = f"{shared_name}_{slugify(run_id, 40)}_{slugify(release_version.replace('.', '-'), 32)}.zip"
            zip_path = package_dir / zip_name
            downloaded = download_with_retry(client, str(summary["pkgUrl"]), zip_path)

            web_config = load_web_config(str(downloaded), "")
            version_payload = first_version(web_config or {})
            zip_checks = verify_web_config(version_payload, overrides)
            zip_verify = zip_verify_ok(zip_checks)

            generated_at = datetime.now().isoformat(timespec="seconds")
            web_config_path = runtime_dir / "web_config.json"
            write_json(web_config_path, web_config or {})
            parameter_text = build_params_text(
                generated_at=generated_at,
                selected=selected,
                product_detail=product_detail,
                source_release_id=source_release_id,
                overrides=overrides,
                final_release=final_release,
                release_checks=release_checks,
                zip_checks=zip_checks,
            )
            params_path = runtime_dir / "custom_package_params.txt"
            write_text(params_path, parameter_text)
            inject_validation_params(downloaded, parameter_text)

            suite_profile = infer_profile_name(
                dict(summary.get("appliedOverrides") or overrides),
                list(summary.get("resolvedVoiceRegLearnCommands") or []),
            )
            suite_payload = build_profile_payload(
                web_config=web_config or {},
                profile=suite_profile,
                metadata={
                    "scalars": {
                        "product": selected.get("productLabel", ""),
                        "module": selected.get("moduleBoard", ""),
                        "language": selected.get("language", ""),
                        "version": selected.get("versionLabel", ""),
                        "scene": selected.get("sceneLabel", ""),
                        "defId": selected.get("defId", ""),
                        "generatedAt": generated_at,
                    },
                    "appliedOverrides": summary.get("appliedOverrides") or overrides,
                    "learnWords": list(summary.get("resolvedVoiceRegLearnCommands") or []),
                    "finalRelease": final_release,
                    "comments": overrides.get("comments", ""),
                },
                selected_meta=selected,
            )
            export_suite(suite_dir, suite_payload)

            payload = {
                "generatedAt": generated_at,
                "runId": run_id,
                "attempt": attempt,
                "selected": selected,
                "sourceReleaseId": source_release_id,
                "localBaseProfile": local_base.get("profile") or None,
                "localBaseMatched": bool(local_base.get("matchedProfile")),
                "localBaseApplied": bool(local_base.get("appliedLocalAlgo")),
                "localBaseSourceStrategy": str(local_base.get("sourceStrategy") or ""),
                "featureMap": feature_map,
                "requiredFeatures": list(args.require_feature or []),
                "appliedOverrides": overrides,
                "packageSummary": summary,
                "finalReleaseSubset": {
                    key: final_release.get(key)
                    for key in ["id", "prodId", "version", "status", "timeout", "volLevel", "defaultVol", "voiceRegEnable", "multiWkeEnable", "multiWkeMode", "comments", "pkgTaskId", "pkgPipelineId", "pkgUrl", "pkgSDKUrl"]
                },
                "releaseChecks": release_checks,
                "releaseVerifyOk": release_verify_ok,
                "zipChecks": zip_checks,
                "zipVerifyOk": zip_verify,
                "artifacts": {
                    "packageZip": str(downloaded.resolve()),
                    "packageParamsTxt": str(params_path.resolve()),
                    "webConfigJson": str(web_config_path.resolve()),
                    "suiteDir": str(suite_dir.resolve()),
                    "summaryJson": str((runtime_dir / "summary.json").resolve()),
                },
            }
            write_json(summary_path, payload)
            write_text(runtime_dir / "summary.md", build_summary_md(payload))
            if not release_verify_ok or not zip_verify:
                raise RuntimeError(
                    f"verification failed after packaging: releaseVerifyOk={release_verify_ok}, zipVerifyOk={zip_verify}"
                )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            last_error = exc
            trace = traceback.format_exc()
            write_text(attempt_dir / f"attempt_{attempt:02d}.txt", build_attempt_log(attempt, exc, trace))
            if attempt < max(1, args.retry_limit):
                time.sleep(retry_sleep_seconds(exc))

    raise RuntimeError(f"custom package failed after {args.retry_limit} attempts: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
