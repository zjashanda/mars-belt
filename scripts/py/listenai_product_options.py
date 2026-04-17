import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
import urllib3

from listenai_task_support import RUNTIME_ROOT, resolve_listenai_token


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"


class ListenAIClient:
    def __init__(self, token: str, timeout: int = 60) -> None:
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"token": token})
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return BASE_URL + path

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


def require_ok(result: Dict[str, Any], step: str) -> Dict[str, Any]:
    if result.get("code") != 200:
        raise RuntimeError(f"{step} failed: code={result.get('code')} msg={result.get('msg')}")
    return result


def category_options(client: ListenAIClient, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = require_ok(client.get("/biz/category/options", params=params), f"category options {params}")
    data = result.get("data") or []
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected category options payload for {params}: {type(data)!r}")
    return data


def flatten_tree(nodes: Sequence[Dict[str, Any]], path: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    path = path or []
    leaves: List[Dict[str, Any]] = []
    for node in nodes:
        item = {
            "id": node.get("id"),
            "label": node.get("label"),
            "value": node.get("value"),
            "icon": node.get("icon"),
            "nickName": node.get("nickName"),
        }
        next_path = path + [item]
        children = node.get("children") or []
        if children:
            leaves.extend(flatten_tree(children, next_path))
        else:
            leaves.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "value": item["value"],
                    "icon": item["icon"],
                    "nickName": item["nickName"],
                    "path": next_path,
                    "pathLabels": [x.get("label") for x in next_path if x.get("label")],
                    "pathValues": [x.get("value") for x in next_path if x.get("value")],
                }
            )
    return leaves


def filter_by_values(items: Sequence[Dict[str, Any]], values: Sequence[str]) -> List[Dict[str, Any]]:
    if not values:
        return list(items)
    allowed = set(values)
    filtered = [item for item in items if str(item.get("value")) in allowed or str(item.get("label")) in allowed]
    return filtered


def normalize_lang_option(option: Any) -> Dict[str, Any]:
    if isinstance(option, dict):
        return {
            "label": option.get("label"),
            "value": option.get("value"),
            "raw": option,
        }
    return {"label": option, "value": option, "raw": option}


def build_language_versions(version_options: Sequence[Dict[str, Any]], lang_options: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for lang in lang_options:
        lang_value = lang.get("value")
        if lang_value is None:
            continue
        matched = []
        for version in version_options:
            version_lang = version.get("lang")
            if isinstance(version_lang, list):
                ok = lang_value in version_lang
            else:
                ok = lang_value in str(version_lang or "")
            if ok:
                matched.append(version)
        result[str(lang_value)] = matched
    return result


def summarize_module(module: Dict[str, Any]) -> Dict[str, Any]:
    lang_options = [normalize_lang_option(x) for x in (module.get("langOptions") or [])]
    version_options = list(module.get("versionOptions") or [])
    return {
        "id": module.get("id"),
        "label": module.get("label"),
        "value": module.get("value"),
        "board": module.get("board"),
        "mark": module.get("mark"),
        "description": module.get("description"),
        "flash": module.get("flash"),
        "sram": module.get("sram"),
        "powerSupply": module.get("powerSupply"),
        "docsLink": module.get("docs_link"),
        "guideLink": module.get("guide_link"),
        "toolsLink": module.get("tools_link"),
        "langOptions": lang_options,
        "versionOptions": version_options,
        "languageVersions": build_language_versions(version_options, lang_options),
        "raw": module,
    }


def dedupe_strings(values: Iterable[Optional[str]]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    result.sort()
    return result


def dedupe_versions(version_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for row in version_rows:
        key = (str(row.get("value")), str(row.get("label")), str(row.get("mode")))
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "defId": row.get("value"),
                "label": row.get("label"),
                "mode": row.get("mode"),
                "lang": row.get("lang"),
            }
        )
    result.sort(key=lambda x: (str(x.get("label")), str(x.get("defId"))))
    return result


def build_catalog(
    client: ListenAIClient,
    product_filters: Sequence[str],
    scene_filters: Sequence[str],
) -> Dict[str, Any]:
    production_tree = category_options(client, {"category": "PRODUCTION"})
    scene_options = category_options(client, {"category": "SCENE"})
    product_leaves = filter_by_values(flatten_tree(production_tree), product_filters)
    scenes = filter_by_values(scene_options, scene_filters)

    combinations: List[Dict[str, Any]] = []
    all_modules: List[Dict[str, Any]] = []
    all_languages: List[str] = []
    all_versions: List[Dict[str, Any]] = []

    for product in product_leaves:
        for scene in scenes:
            params = {
                "category": "FIRMWARE",
                "type": product.get("value"),
                "scene": scene.get("value"),
                "pType": "固件打包",
                "pSubType": scene.get("value"),
            }
            modules_raw = category_options(client, params)
            modules = [summarize_module(module) for module in modules_raw]

            for module in modules:
                all_modules.append(module)
                all_languages.extend([str(x.get("value")) for x in module.get("langOptions") or [] if x.get("value")])
                all_versions.extend(module.get("versionOptions") or [])

            combinations.append(
                {
                    "product": product,
                    "scene": scene,
                    "query": params,
                    "moduleCount": len(modules),
                    "modules": modules,
                }
            )

    unique_modules = []
    seen_modules = set()
    for module in all_modules:
        key = (
            str(module.get("id")),
            str(module.get("board")),
            str(module.get("mark")),
        )
        if key in seen_modules:
            continue
        seen_modules.add(key)
        unique_modules.append(
            {
                "id": module.get("id"),
                "board": module.get("board"),
                "mark": module.get("mark"),
                "description": module.get("description"),
                "flash": module.get("flash"),
                "sram": module.get("sram"),
                "powerSupply": module.get("powerSupply"),
                "languages": dedupe_strings(x.get("value") for x in module.get("langOptions") or []),
                "versions": dedupe_versions(module.get("versionOptions") or []),
            }
        )
    unique_modules.sort(key=lambda x: (str(x.get("board")), str(x.get("mark"))))

    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "filters": {
            "products": list(product_filters),
            "scenes": list(scene_filters),
        },
        "productTree": production_tree,
        "productLeaves": product_leaves,
        "sceneOptions": scene_options,
        "selectedScenes": scenes,
        "combinations": combinations,
        "summary": {
            "productLeafCount": len(product_leaves),
            "sceneCount": len(scenes),
            "combinationCount": len(combinations),
            "languages": dedupe_strings(all_languages),
            "modules": unique_modules,
            "versions": dedupe_versions(all_versions),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch all user-selectable ListenAI product definition options before product creation."
    )
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI login token")
    parser.add_argument(
        "--product",
        action="append",
        default=[],
        help="Optional product leaf filter by value or label. Repeatable.",
    )
    parser.add_argument(
        "--scene",
        action="append",
        default=[],
        help="Optional scene filter by value or label. Repeatable.",
    )
    parser.add_argument(
        "--json-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_product_options.json"),
        help="Where to save the raw json catalog.",
    )
    return parser


def print_summary(catalog: Dict[str, Any]) -> None:
    summary = catalog["summary"]
    print("[summary]")
    print(f"product leaves : {summary['productLeafCount']}")
    print(f"scenes         : {summary['sceneCount']}")
    print(f"combinations   : {summary['combinationCount']}")
    print(f"languages      : {', '.join(summary['languages']) or '(none)'}")
    print(f"modules        : {len(summary['modules'])}")
    print(f"versions       : {len(summary['versions'])}")
    print()

    print("[products]")
    for item in catalog["productLeaves"]:
        path = " / ".join(item.get("pathLabels") or [])
        print(f"- {item.get('label')} ({item.get('value')}) :: {path}")
    print()

    print("[scenes]")
    for item in catalog["selectedScenes"]:
        print(f"- {item.get('label')} ({item.get('value')})")
    print()

    print("[modules]")
    for item in summary["modules"]:
        languages = ", ".join(item.get("languages") or [])
        print(f"- {item.get('board')} | {item.get('mark')} | langs=[{languages}] | versions={len(item.get('versions') or [])}")


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, persist=True)
    if not args.token:
        print(
            "Missing token. Set LISTENAI_TOKEN or pass --token. "
            "The frontend stores it in localStorage key 'TOKEN' and sends it as header 'token'.",
            file=sys.stderr,
        )
        return 1

    client = ListenAIClient(token=args.token)
    try:
        catalog = build_catalog(
            client=client,
            product_filters=args.product,
            scene_filters=args.scene,
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    out_path = os.path.abspath(args.json_out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(catalog, fp, ensure_ascii=False, indent=2)

    print_summary(catalog)
    print()
    print(f"json saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
