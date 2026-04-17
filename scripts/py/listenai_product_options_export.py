import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from listenai_task_support import RUNTIME_ROOT


def load_catalog(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_csv(path: str, fieldnames: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def product_rows(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in catalog.get("productLeaves") or []:
        path_labels = item.get("pathLabels") or []
        rows.append(
            {
                "topCategory": path_labels[0] if path_labels else "",
                "productPath": " / ".join(path_labels),
                "productLabel": item.get("label"),
                "productValue": item.get("value"),
                "productId": item.get("id"),
                "nickName": item.get("nickName") or "",
            }
        )
    rows.sort(key=lambda x: (x["topCategory"], x["productPath"], x["productValue"]))
    return rows


def module_rows(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in catalog.get("summary", {}).get("modules") or []:
        versions = item.get("versions") or []
        for version in versions:
            langs = version.get("lang") or []
            if langs:
                for language in langs:
                    rows.append(
                        {
                            "moduleBoard": item.get("board"),
                            "moduleMark": item.get("mark"),
                            "flash": item.get("flash"),
                            "sram": item.get("sram"),
                            "powerSupply": item.get("powerSupply"),
                            "language": language,
                            "versionLabel": version.get("label"),
                            "defId": version.get("defId"),
                            "mode": version.get("mode") or "",
                        }
                    )
            else:
                rows.append(
                    {
                        "moduleBoard": item.get("board"),
                        "moduleMark": item.get("mark"),
                        "flash": item.get("flash"),
                        "sram": item.get("sram"),
                        "powerSupply": item.get("powerSupply"),
                        "language": "",
                        "versionLabel": version.get("label"),
                        "defId": version.get("defId"),
                        "mode": version.get("mode") or "",
                    }
                )
    rows.sort(
        key=lambda x: (
            x["moduleBoard"] or "",
            x["moduleMark"] or "",
            x["language"] or "",
            x["versionLabel"] or "",
            x["defId"] or "",
        )
    )
    return rows


def matrix_rows(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()

    for combo in catalog.get("combinations") or []:
        product = combo.get("product") or {}
        scene = combo.get("scene") or {}
        path_labels = product.get("pathLabels") or []
        for module in combo.get("modules") or []:
            language_versions = module.get("languageVersions") or {}
            if not language_versions:
                key = (
                    product.get("value"),
                    scene.get("value"),
                    module.get("board"),
                    module.get("mark"),
                    "",
                    "",
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "topCategory": path_labels[0] if path_labels else "",
                        "productPath": " / ".join(path_labels),
                        "productLabel": product.get("label"),
                        "productValue": product.get("value"),
                        "sceneLabel": scene.get("label"),
                        "sceneValue": scene.get("value"),
                        "moduleBoard": module.get("board"),
                        "moduleMark": module.get("mark"),
                        "flash": module.get("flash"),
                        "sram": module.get("sram"),
                        "powerSupply": module.get("powerSupply"),
                        "language": "",
                        "versionLabel": "",
                        "defId": "",
                        "mode": "",
                    }
                )
                continue

            for language, versions in sorted(language_versions.items()):
                if not versions:
                    key = (
                        product.get("value"),
                        scene.get("value"),
                        module.get("board"),
                        module.get("mark"),
                        language,
                        "",
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "topCategory": path_labels[0] if path_labels else "",
                            "productPath": " / ".join(path_labels),
                            "productLabel": product.get("label"),
                            "productValue": product.get("value"),
                            "sceneLabel": scene.get("label"),
                            "sceneValue": scene.get("value"),
                            "moduleBoard": module.get("board"),
                            "moduleMark": module.get("mark"),
                            "flash": module.get("flash"),
                            "sram": module.get("sram"),
                            "powerSupply": module.get("powerSupply"),
                            "language": language,
                            "versionLabel": "",
                            "defId": "",
                            "mode": "",
                        }
                    )
                    continue

                for version in versions:
                    key = (
                        product.get("value"),
                        scene.get("value"),
                        module.get("board"),
                        module.get("mark"),
                        language,
                        version.get("value"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "topCategory": path_labels[0] if path_labels else "",
                            "productPath": " / ".join(path_labels),
                            "productLabel": product.get("label"),
                            "productValue": product.get("value"),
                            "sceneLabel": scene.get("label"),
                            "sceneValue": scene.get("value"),
                            "moduleBoard": module.get("board"),
                            "moduleMark": module.get("mark"),
                            "flash": module.get("flash"),
                            "sram": module.get("sram"),
                            "powerSupply": module.get("powerSupply"),
                            "language": language,
                            "versionLabel": version.get("label"),
                            "defId": version.get("value"),
                            "mode": version.get("mode") or "",
                        }
                    )

    rows.sort(
        key=lambda x: (
            x["topCategory"] or "",
            x["productPath"] or "",
            x["sceneValue"] or "",
            x["moduleBoard"] or "",
            x["moduleMark"] or "",
            x["language"] or "",
            x["versionLabel"] or "",
            x["defId"] or "",
        )
    )
    return rows


def duplicate_version_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        def_id = row.get("defId") or ""
        version_label = row.get("versionLabel") or ""
        if not def_id or not version_label:
            continue
        groups[(row.get("language") or "", version_label)].append(row)

    duplicates: List[Dict[str, Any]] = []
    for (language, version_label), group in groups.items():
        def_ids = sorted({str(item.get("defId")) for item in group if item.get("defId")})
        if len(def_ids) < 2:
            continue
        module_pairs = sorted(
            {
                f"{item.get('moduleBoard') or ''}/{item.get('moduleMark') or ''}"
                for item in group
            }
        )
        products = sorted({str(item.get("productPath") or "") for item in group})
        duplicates.append(
            {
                "language": language,
                "versionLabel": version_label,
                "defIdCount": len(def_ids),
                "defIds": " | ".join(def_ids),
                "modules": " | ".join(module_pairs),
                "products": " | ".join(products),
            }
        )

    duplicates.sort(key=lambda x: (-x["defIdCount"], x["language"], x["versionLabel"]))
    return duplicates


def markdown_lines(
    catalog: Dict[str, Any],
    products: List[Dict[str, Any]],
    modules: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    duplicates: List[Dict[str, Any]],
) -> List[str]:
    summary = catalog.get("summary") or {}
    lines: List[str] = []
    lines.append("# ListenAI Product Options Matrix")
    lines.append("")
    lines.append(f"- Generated at: {catalog.get('generatedAt', '')}")
    lines.append(f"- Product leaves: {summary.get('productLeafCount', 0)}")
    lines.append(f"- Scenes: {summary.get('sceneCount', 0)}")
    lines.append(f"- Product/scene combinations: {summary.get('combinationCount', 0)}")
    lines.append(f"- Flat matrix rows: {len(matrix)}")
    lines.append(f"- Languages: {', '.join(summary.get('languages') or [])}")
    lines.append(f"- Module/version rows: {len(modules)}")
    lines.append("")

    lines.append("## Product Tree")
    lines.append("")
    product_groups: Dict[str, List[str]] = defaultdict(list)
    for row in products:
        product_groups[row["topCategory"]].append(row["productLabel"])
    for top_category in sorted(product_groups):
        lines.append(f"### {top_category}")
        for product_label in sorted(set(product_groups[top_category])):
            lines.append(f"- {product_label}")
        lines.append("")

    lines.append("## Scenes")
    lines.append("")
    for item in catalog.get("selectedScenes") or []:
        lines.append(f"- {item.get('label')} ({item.get('value')})")
    lines.append("")

    lines.append("## Module Coverage")
    lines.append("")
    module_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in modules:
        module_groups[(row["moduleBoard"], row["moduleMark"])].append(row)
    for module_key in sorted(module_groups):
        group = module_groups[module_key]
        lines.append(f"### {module_key[0]} / {module_key[1]}")
        if group:
            lines.append(
                f"- Hardware: flash={group[0]['flash']}, sram={group[0]['sram']}, power={group[0]['powerSupply']}"
            )
        language_map: Dict[str, List[str]] = defaultdict(list)
        for row in group:
            language_map[row["language"]].append(f"{row['versionLabel']} | defId={row['defId']} | mode={row['mode'] or '-'}")
        for language in sorted(language_map):
            lines.append(f"- {language}")
            for item in sorted(set(language_map[language])):
                lines.append(f"  - {item}")
        lines.append("")

    lines.append("## Version Labels With Multiple defIds")
    lines.append("")
    if duplicates:
        for row in duplicates:
            lines.append(
                f"- {row['language']} | {row['versionLabel']} | defIds={row['defIdCount']} | {row['defIds']}"
            )
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Output Files")
    lines.append("")
    lines.append("- `listenai_product_catalog.csv`: product tree")
    lines.append("- `listenai_module_catalog.csv`: module/language/version catalog")
    lines.append("- `listenai_product_options_matrix.csv`: flat matrix for product + scene + module + language + version + defId")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export ListenAI product option json into CSV/Markdown matrices.")
    parser.add_argument(
        "--json-in",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options.json"),
        help="Input json generated by listenai_product_options.py",
    )
    parser.add_argument(
        "--products-csv-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_product_catalog.csv"),
        help="Product catalog csv output",
    )
    parser.add_argument(
        "--modules-csv-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_module_catalog.csv"),
        help="Module catalog csv output",
    )
    parser.add_argument(
        "--matrix-csv-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.csv"),
        help="Flat matrix csv output",
    )
    parser.add_argument(
        "--duplicates-csv-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_version_defid_duplicates.csv"),
        help="Repeated version-label to multi-defId csv output",
    )
    parser.add_argument(
        "--md-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options_matrix.md"),
        help="Markdown summary output",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = load_catalog(args.json_in)
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

    ensure_parent(args.md_out)
    Path(args.md_out).write_text("\n".join(markdown_lines(catalog, products, modules, matrix, duplicates)), encoding="utf-8")

    print(f"products   : {len(products)} -> {os.path.abspath(args.products_csv_out)}")
    print(f"modules    : {len(modules)} -> {os.path.abspath(args.modules_csv_out)}")
    print(f"matrix rows: {len(matrix)} -> {os.path.abspath(args.matrix_csv_out)}")
    print(f"duplicates : {len(duplicates)} -> {os.path.abspath(args.duplicates_csv_out)}")
    print(f"markdown   : {os.path.abspath(args.md_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
