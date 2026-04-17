from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from listenai_advanced_combo_trials import (
    build_feature_toggle,
    build_specific_voice_reg_payload,
    normalize_voice_reg_learn_commands,
    package_release_with_algo_unified,
)
from listenai_auto_package import ListenAIClient
from listenai_batch_package_parameters import fetch_release_detail, slugify, verify_release
from listenai_custom_package import (
    build_attempt_log,
    default_run_id,
    ensure_dir,
    inject_validation_params,
    require_features,
    resolve_catalog,
    shared_product_name,
    retry_sleep_seconds,
    validate_inputs,
    verify_web_config,
    write_json,
    write_text,
    zip_verify_ok,
)
from listenai_executable_case_suite import first_version, load_web_config
from listenai_local_base_profiles import apply_local_base_profile
from listenai_packaging_rules import build_short_release_comment_from_selected
from listenai_profile_suite import build_profile_payload, export_suite
from listenai_shared_product_flow import ensure_shared_product
from listenai_task_support import PACKAGE_CACHE_ROOT
from listenai_task_support import TASKS_ROOT
from listenai_task_support import ensure_task_dir
from listenai_task_support import resolve_listenai_token
from listenai_task_support import runtime_dir_for_task


DEFAULT_RESULT_ROOT = str(TASKS_ROOT)
DEFAULT_PACKAGE_ROOT = str(PACKAGE_CACHE_ROOT)


def build_comment(selected: Dict[str, Any], timeout_value: int, vol_level: int, algo_view_mode: str) -> str:
    return build_short_release_comment_from_selected(
        selected,
        {
            "timeout": int(timeout_value),
            "volLevel": int(vol_level),
            "voiceRegEnable": True,
            "algoViewMode": str(algo_view_mode),
            "registMode": "specificLearn",
        },
    )


def build_params_text(
    generated_at: str,
    selected: Dict[str, Any],
    product_detail: Dict[str, Any],
    source_release_id: str,
    applied_overrides: Dict[str, Any],
    requested_learn_words: List[str],
    resolved_learn_words: List[str],
    final_release: Dict[str, Any],
    release_checks: List[Dict[str, Any]],
    zip_checks: List[Dict[str, Any]],
) -> str:
    lines = [
        "ListenAI voice-reg custom package parameters",
        "",
        f"generatedAt={generated_at}",
        "",
        "[target]",
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
        "[sharedProduct]",
        f"productName={product_detail.get('name')}",
        f"productId={product_detail.get('id')}",
        "",
        "[appliedOverrides]",
        json.dumps(applied_overrides, ensure_ascii=False, indent=2),
        "",
        "[requestedVoiceRegLearnWords]",
        json.dumps(requested_learn_words, ensure_ascii=False, indent=2),
        "",
        "[resolvedVoiceRegLearnWords]",
        json.dumps(resolved_learn_words, ensure_ascii=False, indent=2),
        "",
        "[finalRelease]",
        json.dumps(
            {
                key: final_release.get(key)
                for key in [
                    "id",
                    "prodId",
                    "version",
                    "status",
                    "timeout",
                    "volLevel",
                    "defaultVol",
                    "voiceRegEnable",
                    "algoViewMode",
                    "comments",
                    "pkgTaskId",
                    "pkgPipelineId",
                    "pkgUrl",
                    "pkgSDKUrl",
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "[releaseChecks]",
        json.dumps(release_checks, ensure_ascii=False, indent=2),
        "",
        "[zipChecks]",
        json.dumps(zip_checks, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_summary_md(payload: Dict[str, Any]) -> str:
    selected = payload["selected"]
    package_summary = payload["packageSummary"]
    artifacts = payload["artifacts"]
    return "\n".join(
        [
            "# ListenAI voice-reg custom package result",
            "",
            f"- runId: `{payload['runId']}`",
            f"- target: `{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`",
            f"- sharedProductName: `{package_summary.get('productName')}`",
            f"- sourceReleaseId: `{payload['sourceReleaseId']}`",
            f"- requestedLearnWords: `{json.dumps(payload.get('requestedVoiceRegLearnWords') or [], ensure_ascii=False)}`",
            f"- resolvedLearnWords: `{json.dumps(payload.get('resolvedVoiceRegLearnWords') or [], ensure_ascii=False)}`",
            f"- appliedOverrides: `{json.dumps(payload['appliedOverrides'], ensure_ascii=False)}`",
            f"- releaseId: `{package_summary.get('releaseId')}`",
            f"- releaseVersion: `{package_summary.get('releaseVersion')}`",
            f"- releaseVerify: `{'PASS' if payload['releaseVerifyOk'] else 'FAIL'}`",
            f"- zipVerify: `{'PASS' if payload['zipVerifyOk'] else 'FAIL'}`",
            f"- packageZip: `{artifacts['packageZip']}`",
            f"- packageParamsTxt: `{artifacts['packageParamsTxt']}`",
            f"- webConfigJson: `{artifacts['webConfigJson']}`",
            f"- summaryJson: `{artifacts['summaryJson']}`",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package one ListenAI firmware with specific voice-registration flow and optional learning words."
    )
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token")
    parser.add_argument("--catalog-json", default="", help="Existing parameter catalog json. Skip live resolution when provided.")
    parser.add_argument("--product", default="", help="Product leaf or full path")
    parser.add_argument("--module", default="", help="Module mark or board, for example 3021")
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
    parser.add_argument("--retry-limit", type=int, default=5, help="Retry count when package or verification fails")
    parser.add_argument("--timeout-value", type=int, default=30, help="Wake timeout value to package")
    parser.add_argument("--vol-level", type=int, default=5, help="Volume level count to package")
    parser.add_argument("--algo-view-mode", default="full", help="algoViewMode field for algoUnifiedSave")
    parser.add_argument(
        "--study-reg-command",
        action="append",
        default=[],
        help="Explicit voice-registration learning command. Repeatable. If omitted, auto-select supported commands.",
    )
    parser.add_argument("--comments", default="", help="Explicit release comments")
    parser.add_argument("--require-feature", action="append", default=[], help="Require a feature gate such as voice_regist.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve target and write a packaging plan without packaging.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, allow_missing=bool(args.dry_run), persist=True)
    validate_inputs(args)
    if not args.token and not args.dry_run:
        raise RuntimeError("Missing token. Use --token or set LISTENAI_TOKEN.")

    requested_learn_words = normalize_voice_reg_learn_commands(args.study_reg_command)

    result_root = Path(args.result_root or DEFAULT_RESULT_ROOT)
    initial_catalog_dir = runtime_dir_for_task(result_root / "_pending_custom_voice_reg_package", args.run_id or "tmp-custom-voice-reg-package")
    catalog = resolve_catalog(args, initial_catalog_dir)
    selected = dict(catalog.get("selected") or {})
    if not selected:
        raise RuntimeError("catalog did not resolve a unique target")

    feature_map = dict(catalog.get("featureMap") or {})
    required_features = list(args.require_feature or [])
    if "voice_regist" not in required_features:
        required_features.append("voice_regist")
    require_features(selected, feature_map, required_features)

    run_id = args.run_id or f"{default_run_id(selected)}-voice-reg"
    shared_name = shared_product_name(selected)
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

    result_dir = ensure_task_dir(result_root, shared_name, args.comments.strip() or build_comment(selected, int(args.timeout_value), int(args.vol_level), str(args.algo_view_mode)))
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

    base_overrides: Dict[str, Any] = {
        "timeout": int(args.timeout_value),
        "volLevel": int(args.vol_level),
        "voiceRegEnable": True,
        "algoViewMode": str(args.algo_view_mode),
        **build_specific_voice_reg_payload(),
    }
    local_base = apply_local_base_profile(
        selected=selected,
        explicit_product_name="",
        explicit_source_release_id=args.source_release_id,
        catalog_source_release_id=str(catalog.get("sourceReleaseId") or ""),
        overrides=base_overrides,
        client=client,
        product_detail=prepared_product_detail,
    )
    shared_name = str(local_base.get("sharedProductName") or shared_name)
    source_release_id = str(local_base.get("sourceReleaseId") or args.source_release_id or str(catalog.get("sourceReleaseId") or ""))
    if not source_release_id and not args.dry_run:
        raise RuntimeError("Missing sourceReleaseId. Pass --source-release-id or provide a catalog that includes it.")

    overrides: Dict[str, Any] = dict(local_base.get("overrides") or base_overrides)
    comments = args.comments.strip() or build_comment(selected, int(args.timeout_value), int(args.vol_level), str(args.algo_view_mode))
    overrides["comments"] = comments

    plan_payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "runId": run_id,
        "selected": selected,
        "sourceReleaseId": source_release_id,
        "featureMap": feature_map,
        "requiredFeatures": required_features,
        "appliedOverrides": overrides,
        "localBaseProfile": local_base.get("profile") or None,
        "localBaseMatched": bool(local_base.get("matchedProfile")),
        "localBaseApplied": bool(local_base.get("appliedLocalAlgo")),
        "localBaseSourceStrategy": str(local_base.get("sourceStrategy") or ""),
        "sharedProductName": shared_name,
        "timeoutSec": args.timeout_sec,
        "retryLimit": args.retry_limit,
        "requestedVoiceRegLearnWords": requested_learn_words,
        "dryRun": args.dry_run,
    }
    write_json(runtime_dir / "plan.json", plan_payload)
    write_text(
        runtime_dir / "plan.md",
        "\n".join(
            [
                "# ListenAI voice-reg custom package plan",
                "",
                f"- runId: `{run_id}`",
                f"- target: `{selected.get('productPath')}` | `{selected.get('sceneLabel')}` | `{selected.get('moduleBoard')}` | `{selected.get('language')}` | `{selected.get('versionLabel')}`",
                f"- sourceReleaseId: `{source_release_id}`",
                f"- sharedProductName: `{shared_name}`",
                f"- localBaseProfile: `{json.dumps((local_base.get('profile') or {}).get('id') or '', ensure_ascii=False)}`",
                f"- localBaseApplied: `{json.dumps(bool(local_base.get('appliedLocalAlgo'))).lower()}`",
                f"- localBaseSourceStrategy: `{str(local_base.get('sourceStrategy') or '')}`",
                f"- requiredFeatures: `{json.dumps(required_features, ensure_ascii=False)}`",
                f"- requestedLearnWords: `{json.dumps(requested_learn_words, ensure_ascii=False)}`",
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
    verification_overrides = dict(overrides)
    verification_overrides.pop("comments", None)
    release_verification_keys = {
        "timeout",
        "volLevel",
        "defaultVol",
        "voiceRegEnable",
        "multiWkeEnable",
        "multiWkeMode",
        "algoViewMode",
    }
    release_verification_overrides = {
        key: value
        for key, value in verification_overrides.items()
        if key in release_verification_keys
    }

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
                summary = package_release_with_algo_unified(
                    client=client,
                    product_detail=product_detail,
                    source_release_id=source_release_id,
                    timeout_sec=args.timeout_sec,
                    release_overrides=overrides,
                    feature_toggle=feature_toggle,
                    study_reg_commands=requested_learn_words or None,
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
            release_verify_ok, release_checks = verify_release(final_release, release_verification_overrides)

            release_version = str(summary.get("releaseVersion") or f"attempt-{attempt}")
            zip_name = f"{shared_name}_{slugify(run_id, 40)}_{slugify(release_version.replace('.', '-'), 32)}.zip"
            zip_path = package_dir / zip_name
            downloaded = download_with_retry(client, str(summary["pkgUrl"]), zip_path)

            web_config = load_web_config(str(downloaded), "")
            version_payload = first_version(web_config or {})
            zip_checks = verify_web_config(version_payload, verification_overrides)
            zip_verify = zip_verify_ok(zip_checks)

            resolved_learn_words = list(summary.get("resolvedVoiceRegLearnCommands") or [])
            generated_at = datetime.now().isoformat(timespec="seconds")
            web_config_path = runtime_dir / "web_config.json"
            write_json(web_config_path, web_config or {})
            parameter_text = build_params_text(
                generated_at=generated_at,
                selected=selected,
                product_detail=product_detail,
                source_release_id=source_release_id,
                applied_overrides=summary.get("appliedOverrides") or overrides,
                requested_learn_words=requested_learn_words,
                resolved_learn_words=resolved_learn_words,
                final_release=final_release,
                release_checks=release_checks,
                zip_checks=zip_checks,
            )
            params_path = runtime_dir / "custom_voice_reg_package_params.txt"
            write_text(params_path, parameter_text)
            inject_validation_params(downloaded, parameter_text)

            suite_payload = build_profile_payload(
                web_config=web_config or {},
                profile="voice-reg",
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
                    "learnWords": resolved_learn_words,
                    "finalRelease": final_release,
                    "comments": comments,
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
                "requiredFeatures": required_features,
                "requestedVoiceRegLearnWords": requested_learn_words,
                "resolvedVoiceRegLearnWords": resolved_learn_words,
                "appliedOverrides": summary.get("appliedOverrides") or overrides,
                "packageSummary": summary,
                "finalReleaseSubset": {
                    key: final_release.get(key)
                    for key in [
                        "id",
                        "prodId",
                        "version",
                        "status",
                        "timeout",
                        "volLevel",
                        "defaultVol",
                        "voiceRegEnable",
                        "algoViewMode",
                        "comments",
                        "pkgTaskId",
                        "pkgPipelineId",
                        "pkgUrl",
                        "pkgSDKUrl",
                    ]
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

    raise RuntimeError(f"voice-reg custom package failed after {args.retry_limit} attempts: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
