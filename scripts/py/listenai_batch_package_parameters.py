import argparse
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from listenai_auto_package import ListenAIClient, require_ok
from listenai_packaging_rules import build_short_release_comment_from_selected, build_weekly_product_name_from_selected
from listenai_shared_product_flow import ensure_shared_product, package_release_for_existing_product
from listenai_task_support import PACKAGE_CACHE_ROOT, RUNTIME_ROOT, resolve_listenai_token


DEFAULT_CATALOG_JSON = str(RUNTIME_ROOT / "catalog" / "listenai_parameter_catalog_heater_3021_zh_generic.json")
DEFAULT_TEST_CATALOG_JSON = str(RUNTIME_ROOT / "catalog" / "listenai_test_case_catalog_heater_3021_zh_generic.json")
DEFAULT_BATCH_ROOT = str(RUNTIME_ROOT / "listenai_batch_parameter_packages")
DEFAULT_PACKAGE_ROOT = str(PACKAGE_CACHE_ROOT)

EXCLUDED_PARAMETERS = {
    "protocolConfig",
}

STRING_LENGTH_SAMPLES = {
    "volMaxOverflow": "ABCDE",
    "volMinOverflow": "ABCDEFGHIJ",
    "word": "ABCDEFGHIJKLMNO",
}

ALGO_OVERRIDE_KEYS = {
    "sensitivity",
    "voiceRegEnable",
    "multiWkeEnable",
    "multiWkeMode",
    "algoViewMode",
    "releaseAlgoList",
    "releaseRegist",
    "releaseRegistConfig",
    "releaseMultiWke",
    "studyRegCommands",
}

RELEASE_DETAIL_VERIFY_KEYS = {
    "timeout",
    "volLevel",
    "defaultVol",
    "volMaxOverflow",
    "volMinOverflow",
    "uportUart",
    "uportBaud",
    "traceUart",
    "traceBaud",
    "logLevel",
    "wakeWordSave",
    "volSave",
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
    "sensitivity",
    "voiceRegEnable",
    "multiWkeEnable",
    "multiWkeMode",
    "algoViewMode",
    "comments",
}


def load_algo_helpers():
    from listenai_advanced_combo_trials import (
        build_default_multi_wke_payload,
        build_feature_toggle,
        build_specific_voice_reg_payload,
        package_release_with_algo_unified,
    )

    return {
        "build_default_multi_wke_payload": build_default_multi_wke_payload,
        "build_feature_toggle": build_feature_toggle,
        "build_specific_voice_reg_payload": build_specific_voice_reg_payload,
        "package_release_with_algo_unified": package_release_with_algo_unified,
    }


def load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def slugify(value: str, max_len: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
    return (text[:max_len] or "item").strip("-") or "item"


def render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalize_expected(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        lowered = stripped.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None
        if re.fullmatch(r"-?\d+", stripped):
            try:
                return int(stripped)
            except Exception:
                return stripped
        if re.fullmatch(r"-?\d+\.\d+", stripped):
            try:
                return float(stripped)
            except Exception:
                return stripped
        return stripped
    return value


def values_equal(expected: Any, actual: Any) -> bool:
    expected = normalize_expected(expected)
    actual = normalize_expected(actual)
    if isinstance(expected, bool):
        if isinstance(actual, str):
            actual = normalize_expected(actual)
        return expected == actual
    if isinstance(expected, int) and not isinstance(expected, bool):
        if isinstance(actual, str):
            actual = normalize_expected(actual)
        return expected == actual
    if isinstance(expected, float):
        if isinstance(actual, str):
            actual = normalize_expected(actual)
        return abs(expected - float(actual)) < 1e-9
    return expected == actual


def is_positive_direct_case(case: Dict[str, Any]) -> bool:
    if case.get("case_type") != "positive":
        return False
    if not case.get("directly_editable", False):
        return False
    if case.get("scope") != "direct":
        return False
    parameter = str(case.get("parameter") or "")
    if "+" in parameter:
        return False
    if parameter in EXCLUDED_PARAMETERS:
        return False
    return True


def config_differs(config_change: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
    for key, value in (config_change or {}).items():
        if not values_equal(value, defaults.get(key)):
            return True
    return False


def choose_case(cases: Iterable[Dict[str, Any]], defaults: Dict[str, Any]) -> Dict[str, Any]:
    preferred = [case for case in cases if config_differs(case.get("config_change") or {}, defaults)]
    if preferred:
        return preferred[0]
    return list(cases)[0]


def select_cases(test_catalog: Dict[str, Any], defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for case in test_catalog.get("testCases") or []:
        if not is_positive_direct_case(case):
            continue
        parameter = str(case["parameter"])
        if parameter not in grouped:
            grouped[parameter] = []
            order.append(parameter)
        grouped[parameter].append(case)

    selected: List[Dict[str, Any]] = []
    for parameter in order:
        selected.append(choose_case(grouped[parameter], defaults))
    return selected


def augment_overrides(case: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    overrides = dict(case.get("config_change") or {})
    parameter = str(case.get("parameter") or "")

    if parameter == "timeout":
        overrides["timeout"] = 30 if not values_equal(defaults.get("timeout"), 30) else 1

    if parameter == "uportBaud":
        overrides["uportBaud"] = "115200" if not values_equal(defaults.get("uportBaud"), "115200") else "9600"

    if parameter in STRING_LENGTH_SAMPLES:
        overrides[parameter] = STRING_LENGTH_SAMPLES[parameter]

    if parameter == "uportUart":
        target = str(overrides.get("uportUart"))
        current_trace = str(defaults.get("traceUart"))
        if target == current_trace:
            overrides["traceUart"] = "1" if target == "0" else "0"

    if parameter == "traceUart":
        target = str(overrides.get("traceUart"))
        current_uport = str(defaults.get("uportUart"))
        if target == current_uport:
            overrides["uportUart"] = "1" if target == "0" else "0"

    if parameter == "multiWkeMode" and not values_equal(True, defaults.get("multiWkeEnable")):
        overrides.setdefault("multiWkeEnable", True)
        overrides.update(load_algo_helpers()["build_default_multi_wke_payload"]())

    if parameter == "voiceRegEnable" and values_equal(True, overrides.get("voiceRegEnable")):
        overrides.setdefault("registMode", "specificLearn")
        overrides.update(load_algo_helpers()["build_specific_voice_reg_payload"]())

    return overrides


def build_comment(selected: Dict[str, Any], overrides: Dict[str, Any]) -> str:
    return build_short_release_comment_from_selected(selected, overrides)


def build_product_name(selected: Dict[str, Any]) -> str:
    return build_weekly_product_name_from_selected(selected)


def build_batch_paths(batch_root: str, package_root: str, batch_id: str) -> Dict[str, Path]:
    batch_dir = Path(batch_root) / batch_id
    package_dir = Path(package_root) / batch_id
    ensure_dir(batch_dir)
    ensure_dir(package_dir)
    return {
        "batch_dir": batch_dir,
        "package_dir": package_dir,
        "manifest_json": batch_dir / "manifest.json",
        "manifest_csv": batch_dir / "manifest.csv",
    }


def load_manifest(manifest_path: Path) -> Optional[Dict[str, Any]]:
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def manifest_item_map(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("caseId")): item for item in items}


def write_manifest(paths: Dict[str, Path], manifest: Dict[str, Any]) -> None:
    paths["manifest_json"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    headers = [
        "index",
        "caseId",
        "parameter",
        "title",
        "status",
        "productName",
        "releaseId",
        "releaseVersion",
        "downloadedFirmwarePath",
        "verifyOk",
        "comments",
        "error",
    ]
    with paths["manifest_csv"].open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=headers)
        writer.writeheader()
        for item in manifest.get("items") or []:
            writer.writerow(
                {
                    "index": item.get("index"),
                    "caseId": item.get("caseId"),
                    "parameter": item.get("parameter"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "productName": item.get("productName"),
                    "releaseId": ((item.get("summary") or {}).get("releaseId") if item.get("summary") else ""),
                    "releaseVersion": ((item.get("summary") or {}).get("releaseVersion") if item.get("summary") else ""),
                    "downloadedFirmwarePath": item.get("downloadedFirmwarePath"),
                    "verifyOk": item.get("verifyOk"),
                    "comments": item.get("comments"),
                    "error": item.get("error"),
                }
            )


def build_manifest(
    args: argparse.Namespace,
    batch_id: str,
    selected_meta: Dict[str, Any],
    source_release_id: str,
    cases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    defaults = args._defaults
    for index, case in enumerate(cases, start=1):
        overrides = augment_overrides(case, defaults)
        comments = build_comment(selected_meta, overrides)
        items.append(
            {
                "index": index,
                "caseId": case["id"],
                "parameter": case["parameter"],
                "title": case["title"],
                "group": case.get("group"),
                "status": "planned",
                "comments": comments,
                "overrides": overrides,
                "selectedMeta": selected_meta,
                "sourceReleaseId": source_release_id,
                "productName": "",
                "summary": None,
                "releaseCheck": [],
                "verifyOk": False,
                "downloadedFirmwarePath": "",
                "resultJsonPath": "",
                "error": "",
            }
        )
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "batchId": batch_id,
        "catalogJson": args.catalog_json,
        "testCatalogJson": args.test_catalog_json,
        "packageDir": str((Path(args.package_root) / batch_id).resolve()),
        "timeoutSec": args.timeout_sec,
        "dryRun": args.dry_run,
        "sourceReleaseId": source_release_id,
        "selectedMeta": selected_meta,
        "sharedProduct": {
            "productName": build_product_name(selected_meta),
            "productId": "",
            "productDetail": None,
        },
        "items": items,
    }


def verify_release(final_release: Dict[str, Any], expected: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    checks: List[Dict[str, Any]] = []
    ok = True
    for key, expected_value in expected.items():
        actual_value = final_release.get(key)
        matched = values_equal(expected_value, actual_value)
        checks.append(
            {
                "key": key,
                "expected": expected_value,
                "actual": actual_value,
                "matched": matched,
            }
        )
        if not matched:
            ok = False
    return ok, checks


def choose_download_path(package_dir: Path, index: int, parameter: str, release_version: str) -> Path:
    version_slug = slugify(release_version.replace(".", "-"), max_len=28)
    parameter_slug = slugify(parameter, max_len=24)
    return package_dir / f"{index:02d}_{parameter_slug}_{version_slug}.zip"


def fetch_release_detail(client: ListenAIClient, release_id: str) -> Dict[str, Any]:
    return require_ok(client.get("/fw/release/detail", params={"id": release_id}), "release detail after package").get("data") or {}


def should_use_algo_path(overrides: Dict[str, Any]) -> bool:
    for key in overrides:
        if key == "comments":
            continue
        if key in ALGO_OVERRIDE_KEYS or key.startswith("releaseAlgoList"):
            return True
    return False


def release_verification_overrides(overrides: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in (overrides or {}).items() if key in RELEASE_DETAIL_VERIFY_KEYS}


def run_item(
    client: ListenAIClient,
    item: Dict[str, Any],
    product_detail: Dict[str, Any],
    package_dir: Path,
    timeout_sec: int,
    feature_toggle: Dict[str, Any],
) -> Dict[str, Any]:
    overrides = dict(item["overrides"])
    overrides["comments"] = item["comments"]
    if should_use_algo_path(overrides):
        summary = load_algo_helpers()["package_release_with_algo_unified"](
            client=client,
            product_detail=product_detail,
            source_release_id=str(item["sourceReleaseId"]),
            timeout_sec=timeout_sec,
            release_overrides=overrides,
            feature_toggle=feature_toggle,
        )
    else:
        summary = package_release_for_existing_product(
            client=client,
            product_detail=product_detail,
            source_release_id=str(item["sourceReleaseId"]),
            timeout_sec=timeout_sec,
            release_overrides=overrides,
        )
    final_release = fetch_release_detail(client, str(summary["releaseId"]))
    verify_inputs = release_verification_overrides(dict(summary.get("appliedOverrides") or overrides))
    verify_ok, release_check = verify_release(final_release, verify_inputs)

    download_path = choose_download_path(
        package_dir=package_dir,
        index=int(item["index"]),
        parameter=str(item["parameter"]),
        release_version=str(summary.get("releaseVersion") or "package"),
    )
    downloaded = client.download(str(summary["pkgUrl"]), str(download_path))

    return {
        "summary": summary,
        "releaseCheck": release_check,
        "verifyOk": verify_ok,
        "downloadedFirmwarePath": downloaded,
        "finalRelease": final_release,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch package one unique firmware per direct parameter case.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token")
    parser.add_argument("--catalog-json", default=DEFAULT_CATALOG_JSON, help="Parameter catalog json")
    parser.add_argument("--test-catalog-json", default=DEFAULT_TEST_CATALOG_JSON, help="Test catalog json")
    parser.add_argument("--batch-root", default=DEFAULT_BATCH_ROOT, help="Batch result root directory")
    parser.add_argument("--package-root", default=DEFAULT_PACKAGE_ROOT, help="Downloaded firmware root directory")
    parser.add_argument("--batch-id", default="", help="Batch id. Reuse the same id to resume.")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Per-package timeout")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N selected cases")
    parser.add_argument("--dry-run", action="store_true", help="Only create the batch manifest without packaging")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, persist=True)

    catalog = load_json(args.catalog_json)
    test_catalog = load_json(args.test_catalog_json)
    defaults = dict(catalog.get("releaseDefaults") or {})
    feature_toggle = load_algo_helpers()["build_feature_toggle"](dict(catalog.get("featureMap") or {}))
    args._defaults = defaults

    selected_meta = dict(catalog.get("selected") or {})
    source_release_id = str(catalog.get("sourceReleaseId") or "")
    if not selected_meta:
        raise RuntimeError("Missing selected product metadata in catalog json")
    if not source_release_id:
        raise RuntimeError("Missing sourceReleaseId in catalog json")

    cases = select_cases(test_catalog, defaults)
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        raise RuntimeError("No eligible direct parameter cases found in test catalog")

    batch_id = args.batch_id or now_stamp()
    paths = build_batch_paths(args.batch_root, args.package_root, batch_id)

    manifest = load_manifest(paths["manifest_json"])
    if manifest is None:
        manifest = build_manifest(args, batch_id, selected_meta, source_release_id, cases)
    else:
        manifest["dryRun"] = args.dry_run
        manifest["timeoutSec"] = args.timeout_sec

    existing_by_case_id = manifest_item_map(manifest.get("items") or [])
    write_manifest(paths, manifest)

    if args.dry_run:
        print(f"dry run manifest: {paths['manifest_json']}")
        print(f"planned items: {len(manifest.get('items') or [])}")
        return 0

    if not args.token:
        raise RuntimeError("Missing token. Use --token or set LISTENAI_TOKEN.")

    client = ListenAIClient(token=args.token, timeout=max(60, args.timeout_sec))
    package_dir = paths["package_dir"]
    manifest.setdefault(
        "sharedProduct",
        {
            "productName": build_product_name(selected_meta),
            "productId": "",
            "productDetail": None,
        },
    )
    product_detail = ensure_shared_product(client, manifest)
    write_manifest(paths, manifest)

    for index, case in enumerate(cases, start=1):
        item = existing_by_case_id.get(case["id"])
        if item is None:
            continue
        if item.get("status") == "success":
            continue

        refreshed_overrides = augment_overrides(case, defaults)
        item["title"] = case["title"]
        item["group"] = case.get("group")
        item["overrides"] = refreshed_overrides
        item["comments"] = build_comment(selected_meta, refreshed_overrides)
        item["productName"] = str((manifest.get("sharedProduct") or {}).get("productName") or build_product_name(selected_meta))
        item["error"] = ""
        item["status"] = "running"
        write_manifest(paths, manifest)

        try:
            result = run_item(
                client=client,
                item=item,
                product_detail=product_detail,
                package_dir=package_dir,
                timeout_sec=args.timeout_sec,
                feature_toggle=feature_toggle,
            )
            item["summary"] = result["summary"]
            item["releaseCheck"] = result["releaseCheck"]
            item["verifyOk"] = bool(result["verifyOk"])
            item["downloadedFirmwarePath"] = result["downloadedFirmwarePath"]
            result_json_path = paths["batch_dir"] / f"{int(item['index']):02d}_{slugify(str(item['parameter']), 24)}.json"
            result_json_path.write_text(
                json.dumps(
                    {
                        "generatedAt": datetime.now().isoformat(timespec="seconds"),
                        "item": item,
                        "finalRelease": result["finalRelease"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            item["resultJsonPath"] = str(result_json_path.resolve())
            item["status"] = "success" if item["verifyOk"] else "verify_failed"
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)

        write_manifest(paths, manifest)

    success_count = sum(1 for item in manifest.get("items") or [] if item.get("status") == "success")
    verify_failed_count = sum(1 for item in manifest.get("items") or [] if item.get("status") == "verify_failed")
    failed_count = sum(1 for item in manifest.get("items") or [] if item.get("status") == "failed")
    print(f"manifest: {paths['manifest_json']}")
    print(f"package dir: {package_dir.resolve()}")
    print(f"success={success_count} verify_failed={verify_failed_count} failed={failed_count}")
    return 0 if failed_count == 0 and verify_failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
