import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from listenai_auto_package import ListenAIClient as PackageClient
from listenai_packaging_rules import build_weekly_product_name
from listenai_product_options import ListenAIClient as OptionsClient
from listenai_product_options import build_catalog
from listenai_product_options_export import duplicate_version_rows
from listenai_product_options_export import markdown_lines
from listenai_product_options_export import matrix_rows
from listenai_product_options_export import module_rows
from listenai_product_options_export import product_rows
from listenai_product_options_export import write_csv
from listenai_shared_product_flow import ensure_shared_product, package_release_for_existing_product
from listenai_task_support import RUNTIME_ROOT, resolve_listenai_token


DEFAULT_SCENE = "纯离线"
DEFAULT_JSON_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_product_options.json")
DEFAULT_PRODUCTS_CSV_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_product_catalog.csv")
DEFAULT_MODULES_CSV_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_module_catalog.csv")
DEFAULT_MATRIX_CSV_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.csv")
DEFAULT_DUPLICATES_CSV_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_version_defid_duplicates.csv")
DEFAULT_MATRIX_MD_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.md")
DEFAULT_RESOLUTION_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_resolved_product.json")
DEFAULT_PACKAGE_OUT = str(RUNTIME_ROOT / "catalog" / "listenai_selected_package_summary.json")
DEFAULT_GENERIC_SOURCE_RELEASE_ID = "2034891019943743489"

LANGUAGE_ALIASES = {
    "中文": "中文",
    "zh": "中文",
    "zh-cn": "中文",
    "cn": "中文",
    "chinese": "中文",
    "英文": "英文",
    "en": "英文",
    "en-us": "英文",
    "english": "英文",
}

SCENE_ALIASES = {
    "纯离线": "纯离线",
    "离线": "纯离线",
    "offline": "纯离线",
}

FAMILY_KEYWORDS = {
    "generic": "通用垂类",
    "fan": "风扇垂类",
    "heater": "取暖器垂类",
    "curtain": "窗帘垂类",
    "table_heater": "取暖桌垂类",
    "tea_machine": "茶吧机垂类",
}

KNOWN_SOURCE_RELEASE_IDS = {
    "generic": DEFAULT_GENERIC_SOURCE_RELEASE_ID,
}


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def save_json(path: str, payload: Dict[str, Any]) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def flatten_version_numbers(label: str) -> Tuple[int, ...]:
    values: List[int] = []
    for prefix, version_text in re.findall(r"(?:^|[_-])([A-Z])(\d+(?:\.\d+)*)", (label or "").upper()):
        if prefix not in {"V", "F", "A"}:
            continue
        values.extend(int(part) for part in version_text.split("."))
    return tuple(values)


def family_of_version(version_label: str) -> str:
    for family, keyword in FAMILY_KEYWORDS.items():
        if keyword in (version_label or ""):
            return family
    return "other"


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def normalize_compare(value: str) -> str:
    return normalize_spaces(value).upper()


def normalize_language(value: str) -> str:
    key = normalize_compare(value).lower()
    return LANGUAGE_ALIASES.get(key, value)


def normalize_scene(value: str) -> str:
    key = normalize_compare(value).lower()
    return SCENE_ALIASES.get(key, value)


def normalize_module(value: str) -> str:
    text = normalize_compare(value)
    if not text:
        return value
    digits = re.search(r"(\d{4})", text)
    if digits:
        return f"CSK{digits.group(1)}-CHIP"
    if text.startswith("CSK") and not text.endswith("-CHIP"):
        return text + "-CHIP"
    return text


def build_product_name(product_label: str, module_board: str, language: str, version_label: str) -> str:
    return build_weekly_product_name(module_board, product_label, version_label)


def dedupe_rows(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    deduped: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        key = (
            row.get("productPath") or "",
            row.get("sceneLabel") or "",
            row.get("moduleBoard") or "",
            row.get("language") or "",
            row.get("versionLabel") or "",
            row.get("defId") or "",
            row.get("mode") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def refresh_catalog_outputs(token: str, scene: str, args: argparse.Namespace) -> Dict[str, Any]:
    client = OptionsClient(token=token)
    catalog = build_catalog(client, product_filters=[], scene_filters=[scene] if scene else [])
    save_json(args.json_out, catalog)

    products = product_rows(catalog)
    modules = module_rows(catalog)
    matrix = matrix_rows(catalog)
    duplicates = duplicate_version_rows(matrix)

    write_csv(
        args.products_csv_out,
        ["topCategory", "productPath", "productLabel", "productValue", "productId", "nickName"],
        products,
    )
    write_csv(
        args.modules_csv_out,
        [
            "moduleBoard",
            "moduleMark",
            "flash",
            "sram",
            "powerSupply",
            "language",
            "versionLabel",
            "defId",
            "mode",
        ],
        modules,
    )
    write_csv(
        args.matrix_csv_out,
        [
            "topCategory",
            "productPath",
            "productLabel",
            "productValue",
            "sceneLabel",
            "sceneValue",
            "moduleBoard",
            "moduleMark",
            "flash",
            "sram",
            "powerSupply",
            "language",
            "versionLabel",
            "defId",
            "mode",
        ],
        matrix,
    )
    write_csv(
        args.duplicates_csv_out,
        ["language", "versionLabel", "defIdCount", "defIds", "modules", "products"],
        duplicates,
    )
    ensure_parent(args.matrix_md_out)
    Path(args.matrix_md_out).write_text(
        "\n".join(markdown_lines(catalog, products, modules, matrix, duplicates)),
        encoding="utf-8",
    )
    return catalog


def ensure_matrix_data(args: argparse.Namespace, scene: str) -> List[Dict[str, str]]:
    needs_refresh = args.refresh_live or not os.path.exists(args.matrix_csv_out)
    if needs_refresh:
        if args.token:
            refresh_catalog_outputs(args.token, scene, args)
        elif os.path.exists(args.json_out):
            catalog = json.loads(Path(args.json_out).read_text(encoding="utf-8"))
            products = product_rows(catalog)
            modules = module_rows(catalog)
            matrix = matrix_rows(catalog)
            duplicates = duplicate_version_rows(matrix)
            write_csv(
                args.products_csv_out,
                ["topCategory", "productPath", "productLabel", "productValue", "productId", "nickName"],
                products,
            )
            write_csv(
                args.modules_csv_out,
                [
                    "moduleBoard",
                    "moduleMark",
                    "flash",
                    "sram",
                    "powerSupply",
                    "language",
                    "versionLabel",
                    "defId",
                    "mode",
                ],
                modules,
            )
            write_csv(
                args.matrix_csv_out,
                [
                    "topCategory",
                    "productPath",
                    "productLabel",
                    "productValue",
                    "sceneLabel",
                    "sceneValue",
                    "moduleBoard",
                    "moduleMark",
                    "flash",
                    "sram",
                    "powerSupply",
                    "language",
                    "versionLabel",
                    "defId",
                    "mode",
                ],
                matrix,
            )
            write_csv(
                args.duplicates_csv_out,
                ["language", "versionLabel", "defIdCount", "defIds", "modules", "products"],
                duplicates,
            )
            ensure_parent(args.matrix_md_out)
            Path(args.matrix_md_out).write_text(
                "\n".join(markdown_lines(catalog, products, modules, matrix, duplicates)),
                encoding="utf-8",
            )
        else:
            raise RuntimeError("需要 token 才能刷新实时选项，请传入 --token 或设置 LISTENAI_TOKEN。")

    if not os.path.exists(args.matrix_csv_out):
        raise RuntimeError("没有可用的产品矩阵，请先刷新实时选项。")
    return dedupe_rows(load_csv(args.matrix_csv_out))


def filter_rows(rows: Sequence[Dict[str, str]], predicate: Any) -> List[Dict[str, str]]:
    return [row for row in rows if predicate(row)]


def rank_version_candidate(row: Dict[str, str], requested_version: str) -> Tuple[Any, ...]:
    label = row.get("versionLabel") or ""
    requested = requested_version or ""
    exact = normalize_compare(label) == normalize_compare(requested)
    starts = label.startswith(requested)
    contains = requested in label
    not_board_prefixed = 0 if re.match(r"^CSK\d{4}-", label, re.IGNORECASE) else 1
    multi_lang = 1 if (row.get("mode") or "") == "multi_lang" else 0
    version_numbers = flatten_version_numbers(label)
    return (
        1 if exact else 0,
        1 if starts else 0,
        1 if contains else 0,
        multi_lang,
        not_board_prefixed,
        version_numbers,
        int(row.get("defId") or "0"),
    )


def pick_preferred_version(rows: Sequence[Dict[str, str]], requested_version: str) -> Tuple[Optional[Dict[str, str]], List[str]]:
    if not rows:
        return None, []
    ranked = sorted(rows, key=lambda row: rank_version_candidate(row, requested_version), reverse=True)
    selected = ranked[0]
    warnings: List[str] = []
    if len(ranked) > 1:
        warnings.append(
            f"版本关键字 `{requested_version}` 命中 {len(ranked)} 条记录，已按“精确匹配 > multi_lang > 非板级前缀正式版 > 最新版本号”自动选取首项。"
        )
    return selected, warnings


def find_duplicate_note(row: Dict[str, str], duplicate_rows: Sequence[Dict[str, str]]) -> Optional[str]:
    for item in duplicate_rows:
        if item.get("language") == row.get("language") and item.get("versionLabel") == row.get("versionLabel"):
            return (
                f"版本 `{row.get('versionLabel')}` 在语言 `{row.get('language')}` 下存在 {item.get('defIdCount')} 个 defId，"
                "必须以产品+场景+模组+语言+版本联合定位。"
            )
    return None


def resolve_rows(rows: Sequence[Dict[str, str]], args: argparse.Namespace) -> Dict[str, Any]:
    duplicate_rows = duplicate_version_rows(rows)
    current = list(rows)
    filters_applied: List[Tuple[str, str, int]] = []
    warnings: List[str] = []

    if args.scene:
        scene = normalize_scene(args.scene)
        current = filter_rows(current, lambda row: row.get("sceneLabel") == scene or row.get("sceneValue") == scene)
        filters_applied.append(("scene", scene, len(current)))

    if args.product:
        requested = args.product
        exact = filter_rows(
            current,
            lambda row: row.get("productLabel") == requested or row.get("productPath") == requested,
        )
        if not exact:
            wanted = normalize_compare(requested)
            exact = filter_rows(
                current,
                lambda row: wanted == normalize_compare(row.get("productLabel") or "")
                or wanted == normalize_compare(row.get("productPath") or ""),
            )
        if not exact:
            wanted = normalize_spaces(requested)
            exact = filter_rows(
                current,
                lambda row: wanted in normalize_spaces(row.get("productLabel") or "")
                or wanted in normalize_spaces(row.get("productPath") or ""),
            )
        current = exact
        filters_applied.append(("product", requested, len(current)))

    if args.language:
        language = normalize_language(args.language)
        current = filter_rows(current, lambda row: row.get("language") == language)
        filters_applied.append(("language", language, len(current)))

    if args.module:
        module = normalize_module(args.module)
        current = filter_rows(
            current,
            lambda row: normalize_module(row.get("moduleBoard") or "") == module
            or normalize_compare(row.get("moduleMark") or "") == normalize_compare(args.module),
        )
        filters_applied.append(("module", module, len(current)))

    version_candidates = list(current)
    selected: Optional[Dict[str, str]] = None
    if args.version:
        requested_version = args.version
        exact = filter_rows(
            version_candidates,
            lambda row: row.get("versionLabel") == requested_version,
        )
        if exact:
            version_candidates = exact
        else:
            wanted = normalize_compare(requested_version)
            exact = filter_rows(
                version_candidates,
                lambda row: wanted == normalize_compare(row.get("versionLabel") or ""),
            )
            if exact:
                version_candidates = exact
            else:
                version_candidates = filter_rows(
                    version_candidates,
                    lambda row: normalize_spaces(requested_version) in normalize_spaces(row.get("versionLabel") or ""),
                )
        filters_applied.append(("version", requested_version, len(version_candidates)))
        selected, version_warnings = pick_preferred_version(version_candidates, requested_version)
        warnings.extend(version_warnings)
    elif len(version_candidates) == 1:
        selected = version_candidates[0]

    if len(version_candidates) == 1:
        selected = version_candidates[0]

    if selected:
        duplicate_note = find_duplicate_note(selected, duplicate_rows)
        if duplicate_note:
            warnings.append(duplicate_note)

        family = family_of_version(selected.get("versionLabel") or "")
        if family in KNOWN_SOURCE_RELEASE_IDS:
            warnings.append(
                f"已找到该版本家族的默认模板 sourceReleaseId={KNOWN_SOURCE_RELEASE_IDS[family]}。"
            )
        else:
            warnings.append("当前版本家族没有内置的已验证模板 sourceReleaseId；实际打包前需要显式提供。")

    return {
        "filtersApplied": [
            {"field": field, "value": value, "remaining": remaining}
            for field, value, remaining in filters_applied
        ],
        "candidateCount": len(version_candidates),
        "candidates": version_candidates,
        "selected": selected,
        "warnings": warnings,
    }


def print_resolution(resolution: Dict[str, Any]) -> None:
    print("[resolve]")
    for item in resolution["filtersApplied"]:
        print(f"- {item['field']}: {item['value']} -> {item['remaining']}")
    print(f"候选数量: {resolution['candidateCount']}")

    selected = resolution.get("selected")
    if selected:
        print("[selected]")
        print(json.dumps(selected, ensure_ascii=False, indent=2))
    elif resolution["candidates"]:
        print("[candidates]")
        print(json.dumps(resolution["candidates"], ensure_ascii=False, indent=2))

    warnings = resolution.get("warnings") or []
    if warnings:
        print("[warnings]")
        for warning in warnings:
            print(f"- {warning}")


def build_package_payload(selected: Dict[str, str], product_name: Optional[str]) -> Dict[str, str]:
    resolved_name = str(product_name or "").strip() or build_product_name(
        selected.get("productLabel") or "product",
        selected.get("moduleBoard") or "module",
        selected.get("language") or "lang",
        selected.get("versionLabel") or "version",
    )
    return {
        "name": resolved_name,
        "language": selected.get("language") or "",
        "chipModule": selected.get("moduleBoard") or "",
        "defId": selected.get("defId") or "",
        "version": selected.get("versionLabel") or "",
        "type": selected.get("productLabel") or "",
        "scene": selected.get("sceneLabel") or "",
        "mode": selected.get("mode") or "",
    }


def choose_source_release_id(selected: Dict[str, str], override: str) -> str:
    if override:
        return override
    family = family_of_version(selected.get("versionLabel") or "")
    return KNOWN_SOURCE_RELEASE_IDS.get(family, "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve ListenAI firmware targets from friendly inputs and optionally package them."
    )
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI 登录 token")
    parser.add_argument(
        "--action",
        choices=["resolve", "prepare", "package"],
        default="resolve",
        help="仅解析参数，或实际触发打包",
    )
    parser.add_argument("--refresh-live", action="store_true", help="先从 ListenAI 平台刷新实时选项")
    parser.add_argument("--product", required=True, help="产品名或完整产品路径，例如 取暖器 或 小家电 / 取暖器")
    parser.add_argument("--module", required=True, help="模组，例如 3021、CSK3021、CSK3021-CHIP")
    parser.add_argument("--language", required=True, help="语言，例如 中文 或 英文")
    parser.add_argument("--version", required=True, help="版本标签或家族关键字，例如 通用垂类")
    parser.add_argument("--scene", default=DEFAULT_SCENE, help="场景，默认 纯离线")
    parser.add_argument("--product-name", default="", help="实际打包时创建的新产品名；不填则自动生成")
    parser.add_argument("--source-release-id", default="", help="复制模板用的 sourceReleaseId")
    parser.add_argument("--timeout-sec", type=int, default=600, help="实际打包时轮询等待时长")
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT, help="实时选项 json 输出")
    parser.add_argument("--products-csv-out", default=DEFAULT_PRODUCTS_CSV_OUT, help="产品目录 csv 输出")
    parser.add_argument("--modules-csv-out", default=DEFAULT_MODULES_CSV_OUT, help="模组目录 csv 输出")
    parser.add_argument("--matrix-csv-out", default=DEFAULT_MATRIX_CSV_OUT, help="扁平矩阵 csv 输出")
    parser.add_argument("--duplicates-csv-out", default=DEFAULT_DUPLICATES_CSV_OUT, help="重复版本 defId csv 输出")
    parser.add_argument("--matrix-md-out", default=DEFAULT_MATRIX_MD_OUT, help="矩阵 markdown 输出")
    parser.add_argument("--resolution-out", default=DEFAULT_RESOLUTION_OUT, help="解析结果 json 输出")
    parser.add_argument("--summary-out", default=DEFAULT_PACKAGE_OUT, help="实际打包结果 json 输出")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, allow_missing=args.action == "resolve", persist=True)
    scene = normalize_scene(args.scene)

    try:
        rows = ensure_matrix_data(args, scene)
        resolution = resolve_rows(rows, args)

        output_payload: Dict[str, Any] = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "action": args.action,
            "input": {
                "product": args.product,
                "module": args.module,
                "language": args.language,
                "version": args.version,
                "scene": scene,
            },
            "resolution": resolution,
        }
        save_json(args.resolution_out, output_payload)
        print_resolution(resolution)

        if args.action == "resolve":
            return 0

        selected = resolution.get("selected")
        if not selected:
            print("无法唯一定位目标版本，请先细化条件后再打包。", file=sys.stderr)
            return 1
        if not args.token:
            print("实际打包需要 token，请传入 --token 或设置 LISTENAI_TOKEN。", file=sys.stderr)
            return 1

        source_release_id = choose_source_release_id(selected, args.source_release_id)
        if not source_release_id:
            print("当前版本家族没有默认模板 sourceReleaseId，请显式传入 --source-release-id。", file=sys.stderr)
            return 1

        product_payload = build_package_payload(selected, args.product_name)
        package_client = PackageClient(token=args.token)
        shared_manifest = {
            "selectedMeta": dict(selected),
            "sharedProduct": {
                "productName": product_payload["name"],
                "productId": "",
                "productDetail": None,
            },
        }
        product_detail = ensure_shared_product(package_client, shared_manifest)
        summary = package_release_for_existing_product(
            client=package_client,
            product_detail=product_detail,
            source_release_id=source_release_id,
            timeout_sec=args.timeout_sec,
            release_overrides={},
            trigger_package=args.action == "package",
        )
        package_output = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "input": output_payload["input"],
            "selected": selected,
            "productPayload": product_payload,
            "sharedProduct": shared_manifest["sharedProduct"],
            "sourceReleaseId": source_release_id,
            "summary": summary,
        }
        save_json(args.summary_out, package_output)
        print("[prepare]" if args.action == "prepare" else "[package]")
        print(json.dumps(package_output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
