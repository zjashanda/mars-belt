import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from listenai_auto_package import (
    ListenAIClient,
    require_ok,
)
from listenai_batch_package_parameters import (
    DEFAULT_CATALOG_JSON,
    DEFAULT_PACKAGE_ROOT,
    DEFAULT_TEST_CATALOG_JSON,
    augment_overrides,
    ensure_dir,
    fetch_release_detail,
    load_json,
    now_stamp,
    select_cases,
    slugify,
    values_equal,
    verify_release,
)
from listenai_packaging_rules import build_short_release_comment_from_selected, build_weekly_product_name_from_selected
from listenai_shared_product_flow import ensure_shared_product, package_release_for_existing_product
from listenai_task_support import RUNTIME_ROOT, resolve_listenai_token


DEFAULT_BATCH_ROOT = str(RUNTIME_ROOT / "listenai_grouped_parameter_packages")

BUNDLE_SPECS = [
    {
        "bundle": "base-core",
        "title": "timeout + volLevel + defaultVol",
        "parameters": ["timeout", "volLevel", "defaultVol"],
    },
    {
        "bundle": "prompt-persist",
        "title": "volMaxOverflow + volMinOverflow + volSave",
        "parameters": ["volMaxOverflow", "volMinOverflow", "volSave"],
    },
    {
        "bundle": "uart-debug",
        "title": "uportUart + traceUart + uportBaud + logLevel",
        "parameters": ["uportUart", "traceUart", "uportBaud", "logLevel"],
    },
    {
        "bundle": "tts-voice",
        "title": "vcn + speed + vol + compress + word",
        "parameters": ["vcn", "speed", "vol", "compress", "word"],
    },
    {
        "bundle": "tts-midpoint",
        "title": "vcn + speed(mid) + vol(mid) + compress(mid) + word(len10)",
        "parameters": ["vcn", "speed", "vol", "compress", "word"],
        "custom_overrides": {
            "vcn": "x_xiaohou",
            "speed": 50,
            "vol": 50,
            "compress": "2",
            "word": "ABCDEFGHIJ",
        },
    },
    {
        "bundle": "amp-sense",
        "title": "paConfigEnable + sensitivity",
        "parameters": ["paConfigEnable", "sensitivity"],
    },
    {
        "bundle": "advanced-features",
        "title": "wakeWordSave + algoViewMode + timeout + uportBaud",
        "parameters": ["wakeWordSave", "algoViewMode", "timeout", "uportBaud"],
    },
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package grouped multi-parameter firmware variants under one product.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token")
    parser.add_argument("--catalog-json", default=DEFAULT_CATALOG_JSON, help="Parameter catalog json")
    parser.add_argument("--test-catalog-json", default=DEFAULT_TEST_CATALOG_JSON, help="Test catalog json")
    parser.add_argument("--batch-root", default=DEFAULT_BATCH_ROOT, help="Batch result root directory")
    parser.add_argument("--package-root", default=DEFAULT_PACKAGE_ROOT, help="Downloaded firmware root directory")
    parser.add_argument("--batch-id", default="", help="Batch id. Reuse it to resume.")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Per-package timeout")
    parser.add_argument("--dry-run", action="store_true", help="Only create the grouped manifest")
    return parser


def batch_paths(batch_root: str, package_root: str, batch_id: str) -> Dict[str, Path]:
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


def load_manifest(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(paths: Dict[str, Path], manifest: Dict[str, Any]) -> None:
    paths["manifest_json"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    headers = [
        "index",
        "bundle",
        "status",
        "sharedProductName",
        "sharedProductId",
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
        shared_product = manifest.get("sharedProduct") or {}
        for item in manifest.get("items") or []:
            writer.writerow(
                {
                    "index": item.get("index"),
                    "bundle": item.get("bundle"),
                    "status": item.get("status"),
                    "sharedProductName": shared_product.get("productName"),
                    "sharedProductId": shared_product.get("productId"),
                    "releaseId": ((item.get("summary") or {}).get("releaseId") if item.get("summary") else ""),
                    "releaseVersion": ((item.get("summary") or {}).get("releaseVersion") if item.get("summary") else ""),
                    "downloadedFirmwarePath": item.get("downloadedFirmwarePath"),
                    "verifyOk": item.get("verifyOk"),
                    "comments": item.get("comments"),
                    "error": item.get("error"),
                }
            )


def choose_release_download_path(package_dir: Path, index: int, bundle_name: str, release_version: str) -> Path:
    version_slug = slugify(release_version.replace(".", "-"), max_len=28)
    return package_dir / f"{index:02d}_{slugify(bundle_name, 24)}_{version_slug}.zip"


def build_case_pool(test_catalog: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    pool: Dict[str, List[Dict[str, Any]]] = {}
    for case in test_catalog.get("testCases") or []:
        parameter = str(case.get("parameter") or "")
        if not parameter:
            continue
        pool.setdefault(parameter, []).append(case)
    return pool


def choose_case_for_value(
    parameter: str,
    desired_value: Any,
    case_pool: Dict[str, List[Dict[str, Any]]],
    fallback_case: Dict[str, Any],
) -> Dict[str, Any]:
    for case in case_pool.get(parameter) or []:
        config_change = case.get("config_change") or {}
        if parameter not in config_change:
            continue
        if values_equal(desired_value, config_change.get(parameter)):
            return case
    return fallback_case


def build_grouped_items(
    selected_meta: Dict[str, Any],
    defaults: Dict[str, Any],
    source_release_id: str,
    case_map: Dict[str, Dict[str, Any]],
    case_pool: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for index, spec in enumerate(BUNDLE_SPECS, start=1):
        custom_overrides = dict(spec.get("custom_overrides") or {})
        overrides: Dict[str, Any] = {}
        source_cases: List[Dict[str, Any]] = []
        for parameter in spec["parameters"]:
            case = case_map[parameter]
            if parameter in custom_overrides:
                case = choose_case_for_value(parameter, custom_overrides[parameter], case_pool, case)
            source_cases.append(
                {
                    "caseId": case["id"],
                    "parameter": case["parameter"],
                    "title": case["title"],
                }
            )
            case_overrides = augment_overrides(case, defaults)
            overrides.update(case_overrides)

        if custom_overrides:
            overrides.update(custom_overrides)

        comments = build_short_release_comment_from_selected(selected_meta, overrides)
        items.append(
            {
                "index": index,
                "bundle": spec["bundle"],
                "title": spec["title"],
                "parameters": list(spec["parameters"]),
                "sourceCases": source_cases,
                "overrides": overrides,
                "comments": comments,
                "status": "planned",
                "summary": None,
                "releaseCheck": [],
                "verifyOk": False,
                "downloadedFirmwarePath": "",
                "resultJsonPath": "",
                "error": "",
                "sourceReleaseId": source_release_id,
                "selectedMeta": selected_meta,
            }
        )
    return items


def build_manifest(
    args: argparse.Namespace,
    batch_id: str,
    selected_meta: Dict[str, Any],
    source_release_id: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
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
            "productName": build_weekly_product_name_from_selected(selected_meta),
            "productId": "",
            "productDetail": None,
        },
        "items": items,
    }


def run_item(
    client: ListenAIClient,
    item: Dict[str, Any],
    product_detail: Dict[str, Any],
    package_dir: Path,
    timeout_sec: int,
) -> Dict[str, Any]:
    overrides = dict(item["overrides"])
    overrides["comments"] = item["comments"]

    summary = package_release_for_existing_product(
        client=client,
        product_detail=product_detail,
        source_release_id=str(item["sourceReleaseId"]),
        timeout_sec=timeout_sec,
        release_overrides=overrides,
    )
    final_release = fetch_release_detail(client, str(summary["releaseId"]))
    verify_ok, release_check = verify_release(final_release, overrides)

    download_path = choose_release_download_path(
        package_dir=package_dir,
        index=int(item["index"]),
        bundle_name=str(item["bundle"]),
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


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, persist=True)
    catalog = load_json(args.catalog_json)
    test_catalog = load_json(args.test_catalog_json)
    defaults = dict(catalog.get("releaseDefaults") or {})
    selected_meta = dict(catalog.get("selected") or {})
    source_release_id = str(catalog.get("sourceReleaseId") or "")
    if not selected_meta:
        raise RuntimeError("Missing selected product metadata in catalog json")
    if not source_release_id:
        raise RuntimeError("Missing sourceReleaseId in catalog json")

    selected_cases = select_cases(test_catalog, defaults)
    case_map = {str(case["parameter"]): case for case in selected_cases}
    case_pool = build_case_pool(test_catalog)
    missing = [parameter for spec in BUNDLE_SPECS for parameter in spec["parameters"] if parameter not in case_map]
    if missing:
        raise RuntimeError(f"Missing representative cases for parameters: {missing}")

    batch_id = args.batch_id or now_stamp()
    paths = batch_paths(args.batch_root, args.package_root, batch_id)

    planned_items = build_grouped_items(selected_meta, defaults, source_release_id, case_map, case_pool)
    manifest = load_manifest(paths["manifest_json"])
    if manifest is None:
        manifest = build_manifest(args, batch_id, selected_meta, source_release_id, planned_items)
    else:
        manifest["dryRun"] = args.dry_run
        manifest["timeoutSec"] = args.timeout_sec
        existing_items = manifest.get("items") or []
        existing_bundles = {str(item.get("bundle") or "") for item in existing_items}
        next_index = max((int(item.get("index") or 0) for item in existing_items), default=0) + 1
        for item in planned_items:
            if str(item.get("bundle") or "") in existing_bundles:
                continue
            item["index"] = next_index
            next_index += 1
            existing_items.append(item)
        manifest["items"] = existing_items

    write_manifest(paths, manifest)

    if args.dry_run:
        print(f"dry run manifest: {paths['manifest_json']}")
        print(f"planned grouped variants: {len(manifest.get('items') or [])}")
        return 0

    if not args.token:
        raise RuntimeError("Missing token. Use --token or set LISTENAI_TOKEN.")

    client = ListenAIClient(token=args.token, timeout=max(60, args.timeout_sec))
    product_detail = ensure_shared_product(client, manifest)
    write_manifest(paths, manifest)

    for item in manifest.get("items") or []:
        if item.get("status") == "success":
            continue

        item["status"] = "running"
        item["error"] = ""
        write_manifest(paths, manifest)

        try:
            result = run_item(
                client=client,
                item=item,
                product_detail=product_detail,
                package_dir=paths["package_dir"],
                timeout_sec=args.timeout_sec,
            )
            item["summary"] = result["summary"]
            item["releaseCheck"] = result["releaseCheck"]
            item["verifyOk"] = bool(result["verifyOk"])
            item["downloadedFirmwarePath"] = result["downloadedFirmwarePath"]
            result_json_path = paths["batch_dir"] / f"{int(item['index']):02d}_{slugify(str(item['bundle']), 24)}.json"
            result_json_path.write_text(
                json.dumps(
                    {
                        "generatedAt": datetime.now().isoformat(timespec="seconds"),
                        "sharedProduct": manifest.get("sharedProduct"),
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
    print(f"package dir: {paths['package_dir'].resolve()}")
    print(f"shared product: {manifest.get('sharedProduct', {}).get('productName')} ({manifest.get('sharedProduct', {}).get('productId')})")
    print(f"success={success_count} verify_failed={verify_failed_count} failed={failed_count}")
    return 0 if failed_count == 0 and verify_failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
