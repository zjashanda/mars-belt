import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import urllib3

from listenai_task_support import RUNTIME_ROOT, resolve_listenai_token


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"

DEFAULT_PRODUCT_TEMPLATE = {
    "type": "取暖器",
    "scene": "纯离线",
    "language": "中文",
    "chipModule": "CSK3021-CHIP",
    "defId": "2031655114710286338",
    "version": "通用垂类-V2.0.1_F2.0.5_A1.7.2.0",
    "mode": "multi_lang",
    "registryType": "固件打包",
    "subType": "纯离线",
}


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

    def post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self.session.post(
            self._url(path),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def download(self, url: str, output_path: str) -> str:
        resp = self.session.get(url, timeout=self.timeout, stream=True)
        resp.raise_for_status()
        file_path = Path(output_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)
        return str(file_path.resolve())


def require_ok(result: Dict[str, Any], step: str) -> Dict[str, Any]:
    if result.get("code") != 200:
        raise RuntimeError(f"{step} failed: code={result.get('code')} msg={result.get('msg')}")
    return result


def print_step(title: str, payload: Optional[Dict[str, Any]] = None) -> None:
    print(f"\n[{title}]")
    if payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def coerce_override_value(raw: str) -> Any:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def parse_override_args(override_args: Optional[List[str]]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for item in override_args or []:
        if "=" not in item:
            raise ValueError(f"Invalid override '{item}', expected KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid override '{item}', key is empty")
        value = value.strip()
        # Allow large JSON overrides to be passed via file to avoid shell arg limits.
        if value.startswith("@"):
            file_path = Path(value[1:]).expanduser()
            value = file_path.read_text(encoding="utf-8")
        overrides[key] = coerce_override_value(value)
    return overrides


def apply_release_overrides(release_detail: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(release_detail)
    for key, value in overrides.items():
        original = updated.get(key)
        if isinstance(original, bool):
            if isinstance(value, str):
                lowered = value.lower()
                if lowered in {"1", "true", "yes", "on"}:
                    value = True
                elif lowered in {"0", "false", "no", "off"}:
                    value = False
        elif isinstance(original, int) and not isinstance(original, bool):
            if isinstance(value, bool):
                value = int(value)
            elif isinstance(value, str) and value.strip():
                value = int(value)
        elif isinstance(original, float):
            if isinstance(value, str) and value.strip():
                value = float(value)
        elif isinstance(original, str):
            original_text = original.strip()
            if original_text in {"0", "1"}:
                if isinstance(value, bool):
                    value = "1" if value else "0"
                elif isinstance(value, (int, float)) and str(int(value)) in {"0", "1"}:
                    value = str(int(value))
                elif isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in {"1", "true", "yes", "on"}:
                        value = "1"
                    elif lowered in {"0", "false", "no", "off"}:
                        value = "0"
                    else:
                        value = value
                else:
                    value = "" if value is None else str(value)
            else:
                value = "" if value is None else str(value)
        updated[key] = value
    return updated


def wait_release_stable(
    client: "ListenAIClient",
    release_id: str,
    expected_fields: Optional[Dict[str, Any]] = None,
    *,
    timeout_sec: int = 60,
    interval_sec: int = 2,
    stable_reads: int = 2,
    settle_sec: int = 8,
) -> Dict[str, Any]:
    deadline = time.time() + max(timeout_sec, interval_sec)
    expected = dict(expected_fields or {})
    stable_count = 0
    last_data: Dict[str, Any] = {}
    while time.time() < deadline:
        result = require_ok(client.get("/fw/release/detail", params={"id": release_id}), "release detail")
        data = result.get("data") or {}
        last_data = data
        matches = str(data.get("status") or "") == "ready"
        if matches and expected:
            for key, value in expected.items():
                if str(data.get(key)) != str(value):
                    matches = False
                    break
        stable_count = stable_count + 1 if matches else 0
        if stable_count >= max(stable_reads, 1):
            if settle_sec > 0:
                time.sleep(settle_sec)
            return data
        time.sleep(interval_sec)
    raise TimeoutError(f"Timed out waiting for release {release_id} to stabilize")


def guess_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name or "listenai_firmware_package.zip"


def choose_download_path(download_dir: str, summary: Dict[str, Any]) -> str:
    dir_path = Path(download_dir or ".").resolve()
    dir_path.mkdir(parents=True, exist_ok=True)
    pkg_url = str(summary.get("pkgUrl") or "")
    guessed = guess_filename_from_url(pkg_url)
    release_version = str(summary.get("releaseVersion") or "").replace(":", ".").replace("/", "-")
    product_name = str(summary.get("productName") or "listenai")
    if guessed.lower().endswith(".zip"):
        filename = guessed
    else:
        filename = f"{product_name}_{release_version or 'package'}.zip"
    return str(dir_path / filename)


def _query_product_page(
    client: ListenAIClient,
    *,
    name: str,
    page: int = 1,
    size: int = 100,
    registry_type: str = "",
    sub_type: str = "",
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "current": page,
        "size": size,
        "name": name,
    }
    if registry_type:
        params["type"] = registry_type
    if sub_type:
        params["subType"] = sub_type
    result = require_ok(
        client.get("/biz/prod/page", params=params),
        "query product page",
    )
    return list(((result.get("data") or {}).get("records")) or [])


def find_product_id(
    client: ListenAIClient,
    name: str,
    registry_type: str,
    sub_type: str,
    retries: int = 15,
    sleep_sec: int = 2,
) -> str:
    for _ in range(retries):
        exact: List[Dict[str, Any]] = []
        for current_registry_type, current_sub_type in (
            ("", ""),
            (registry_type, sub_type),
        ):
            records = _query_product_page(
                client,
                name=name,
                page=1,
                size=100,
                registry_type=current_registry_type,
                sub_type=current_sub_type,
            )
            exact = [item for item in records if str(item.get("name") or "") == name and item.get("id")]
            if exact:
                break
        if exact:
            exact.sort(key=lambda x: str(x.get("createTime") or ""), reverse=True)
            return str(exact[0]["id"])
        time.sleep(sleep_sec)
    raise RuntimeError(f"Product '{name}' not found after creation polling")


def find_copied_release_id(
    client: ListenAIClient,
    prod_id: str,
    source_release_id: str,
    existing_release_ids: Optional[List[str]] = None,
    retries: int = 20,
    sleep_sec: int = 2,
) -> str:
    existing = set(existing_release_ids or [])
    for _ in range(retries):
        result = require_ok(
            client.get("/fw/release/page", params={"current": 1, "size": 20, "prodId": prod_id}),
            "find copied release",
        )
        records = (result.get("data") or {}).get("records") or []
        candidates = [
            x
            for x in records
            if str(x.get("id")) != source_release_id and str(x.get("id")) not in existing
        ]
        if candidates:
            candidates.sort(key=lambda x: x.get("createTime") or "", reverse=True)
            return str(candidates[0]["id"])
        time.sleep(sleep_sec)
    raise RuntimeError("copied release not found after polling")


def poll_release_success(
    client: ListenAIClient,
    release_id: str,
    timeout_sec: int = 600,
    interval_sec: int = 5,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = require_ok(client.get("/fw/release/detail", params={"id": release_id}), "release detail")
        data = result.get("data") or {}
        status = data.get("status")
        print(f"poll status={status} task={data.get('pkgTaskId')} pipeline={data.get('pkgPipelineId')}")
        if status == "success":
            return data
        if status == "failed":
            raise RuntimeError(f"package failed: {data.get('pkgLogs')}")
        time.sleep(interval_sec)
    raise TimeoutError(f"Timed out waiting for release {release_id} to succeed")


def run_flow(
    client: ListenAIClient,
    product_payload: Dict[str, Any],
    source_release_id: str,
    timeout_sec: int,
    release_overrides: Optional[Dict[str, Any]] = None,
    trigger_package: bool = True,
) -> Dict[str, Any]:
    product_name = str(product_payload["name"])
    print_step("create product", product_payload)
    require_ok(client.post_json("/biz/prod/add", product_payload), "create product")

    product_id = find_product_id(
        client,
        name=product_name,
        registry_type=DEFAULT_PRODUCT_TEMPLATE["registryType"],
        sub_type=DEFAULT_PRODUCT_TEMPLATE["subType"],
    )
    print_step("product created", {"productId": product_id})

    product_detail = require_ok(
        client.get("/biz/prod/detail", params={"id": product_id}),
        "product detail",
    ).get("data") or {}
    print_step(
        "product detail",
        {
            "id": product_detail.get("id"),
            "name": product_detail.get("name"),
            "type": product_detail.get("type"),
            "scene": product_detail.get("scene"),
            "chipModule": product_detail.get("chipModule"),
            "language": product_detail.get("language"),
            "defId": product_detail.get("defId"),
            "version": product_detail.get("version"),
        },
    )

    source_release = require_ok(
        client.get("/fw/release/detail", params={"id": source_release_id}),
        "source release detail",
    ).get("data") or {}
    source_prod_id = str(source_release.get("prodId") or "")
    if not source_prod_id:
        raise RuntimeError("source release missing prodId")

    existing_release_ids = [
        str(item.get("id"))
        for item in (
            (
                require_ok(
                    client.get("/fw/release/page", params={"current": 1, "size": 50, "prodId": source_prod_id}),
                    "list releases before copy",
                ).get("data")
                or {}
            ).get("records")
            or []
        )
        if item.get("id")
    ]

    print_step("copy release source", {"sourceReleaseId": source_release_id})
    require_ok(client.get("/fw/release/copy", params={"id": source_release_id}), "copy release")

    release_id = find_copied_release_id(
        client,
        prod_id=source_prod_id,
        source_release_id=source_release_id,
        existing_release_ids=existing_release_ids,
    )
    print_step("copied release", {"releaseId": release_id, "sourceProdId": source_prod_id})

    release_detail = require_ok(
        client.get("/fw/release/detail", params={"id": release_id}),
        "copied release detail",
    ).get("data") or {}
    release_detail["prodId"] = product_id
    release_detail["status"] = "ready"
    for key in [
        "createTime",
        "createUser",
        "updateTime",
        "updateUser",
        "pkgLogs",
        "pkgUrl",
        "pkgSDKUrl",
        "pkgPipelineId",
        "pkgTaskId",
        "deleteFlag",
        "version",
    ]:
        release_detail.pop(key, None)
    if release_overrides:
        release_detail = apply_release_overrides(release_detail, release_overrides)
        print_step("release overrides", release_overrides)
    edit_result = require_ok(client.post_json("/fw/release/edit", release_detail), "rebind copied release")
    expected_fields = {
        key: release_detail.get(key)
        for key in (release_overrides or {})
        if key in release_detail and not isinstance(release_detail.get(key), (dict, list))
    }
    prepared_release = wait_release_stable(client, release_id, expected_fields)
    print_step(
        "release rebound",
        {
            "releaseId": release_id,
            "prodId": (edit_result.get("data") or {}).get("prodId"),
            "status": (edit_result.get("data") or {}).get("status"),
        },
    )

    if not trigger_package:
        return {
            "productId": product_id,
            "productName": product_detail["name"],
            "releaseId": release_id,
            "releaseVersion": prepared_release.get("version"),
            "status": prepared_release.get("status"),
            "pkgTaskId": prepared_release.get("pkgTaskId"),
            "pkgPipelineId": prepared_release.get("pkgPipelineId"),
            "pkgUrl": prepared_release.get("pkgUrl"),
            "pkgSDKUrl": prepared_release.get("pkgSDKUrl"),
            "preparedOnly": True,
            "appliedOverrides": release_overrides or {},
        }

    package_params = {
        "id": release_id,
        "categoryName": str(product_detail["type"]),
        "mark": str(product_detail["chipModule"]),
        "scene": str(product_detail["scene"]),
        "productName": str(product_detail["name"]),
        "language": str(product_detail["language"]),
        "configId": str(product_detail["defId"]),
    }
    print_step("package request", package_params)
    require_ok(client.get("/fw/release/package", params=package_params), "package")

    final_release = poll_release_success(client, release_id, timeout_sec=timeout_sec)
    return {
        "productId": product_id,
        "productName": product_detail["name"],
        "releaseId": release_id,
        "releaseVersion": final_release.get("version"),
        "status": final_release.get("status"),
        "pkgTaskId": final_release.get("pkgTaskId"),
        "pkgPipelineId": final_release.get("pkgPipelineId"),
        "pkgUrl": final_release.get("pkgUrl"),
        "pkgSDKUrl": final_release.get("pkgSDKUrl"),
        "preparedOnly": False,
        "appliedOverrides": release_overrides or {},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create ListenAI firmware product and package it automatically.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI login token")
    parser.add_argument("--product-name", default="test001", help="New product name")
    parser.add_argument("--category-name", default=DEFAULT_PRODUCT_TEMPLATE["type"], help="Business category, for example 取暖器")
    parser.add_argument("--scene", default=DEFAULT_PRODUCT_TEMPLATE["scene"], help="Scene, for example 纯离线")
    parser.add_argument("--language", default=DEFAULT_PRODUCT_TEMPLATE["language"], help="Language, for example 中文")
    parser.add_argument("--chip-module", default=DEFAULT_PRODUCT_TEMPLATE["chipModule"], help="Chip module, for example CSK3021-CHIP")
    parser.add_argument("--def-id", default=DEFAULT_PRODUCT_TEMPLATE["defId"], help="Platform-generated config definition ID")
    parser.add_argument("--version", default=DEFAULT_PRODUCT_TEMPLATE["version"], help="Platform version label")
    parser.add_argument("--mode", default=DEFAULT_PRODUCT_TEMPLATE["mode"], help="Mode, usually multi_lang")
    parser.add_argument(
        "--source-release-id",
        default="2034891019943743489",
        help="Successful release ID used as copy source",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=600,
        help="Seconds to wait for packaging to finish",
    )
    parser.add_argument(
        "--summary-out",
        default=str(RUNTIME_ROOT / "catalog" / "listenai_auto_package_summary.json"),
        help="Summary output json path",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        help="Override top-level release field with KEY=VALUE, may be passed multiple times",
    )
    parser.add_argument(
        "--download-dir",
        default="",
        help="If set, download packaged firmware zip into this directory after success",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Create product and rebind a copied release, but do not trigger packaging",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, persist=True)
    if not args.token:
        print("Missing token. Use --token or set LISTENAI_TOKEN.", file=sys.stderr)
        return 1

    client = ListenAIClient(token=args.token)
    product_payload = {
        "name": args.product_name,
        "language": args.language,
        "chipModule": args.chip_module,
        "defId": args.def_id,
        "version": args.version,
        "type": args.category_name,
        "scene": args.scene,
        "mode": args.mode,
    }
    try:
        release_overrides = parse_override_args(args.set)
    except Exception as exc:
        print(f"Invalid overrides: {exc}", file=sys.stderr)
        return 1

    try:
        summary = run_flow(
            client=client,
            product_payload=product_payload,
            source_release_id=args.source_release_id,
            timeout_sec=args.timeout_sec,
            release_overrides=release_overrides,
            trigger_package=not args.prepare_only,
        )
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    download_path = ""
    if args.download_dir and not args.prepare_only:
        pkg_url = str(summary.get("pkgUrl") or "")
        if not pkg_url:
            print("FAILED: package succeeded but pkgUrl is empty", file=sys.stderr)
            return 1
        target_path = choose_download_path(args.download_dir, summary)
        try:
            download_path = client.download(pkg_url, target_path)
            print_step("firmware downloaded", {"path": download_path})
        except Exception as exc:
            print(f"\nFAILED: firmware download failed: {exc}", file=sys.stderr)
            return 1

    out_path = os.path.abspath(args.summary_out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(
            {
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "productPayload": product_payload,
                "sourceReleaseId": args.source_release_id,
                "releaseOverrides": release_overrides,
                "summary": summary,
                "downloadedFirmwarePath": download_path,
            },
            fp,
            ensure_ascii=False,
            indent=2,
        )

    print_step("done", summary)
    print(f"\nsummary saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
