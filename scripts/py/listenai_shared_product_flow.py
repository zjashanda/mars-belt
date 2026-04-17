from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from listenai_auto_package import (
    DEFAULT_PRODUCT_TEMPLATE,
    ListenAIClient,
    apply_release_overrides,
    find_copied_release_id,
    find_product_id,
    poll_release_success,
    require_ok,
    wait_release_stable,
)
from listenai_task_support import STATE_ROOT

ROOT = Path(__file__).resolve().parent.parent
SHARED_PRODUCT_REGISTRY = STATE_ROOT / "shared_product_registry.json"


def _registry_key(product_name: str, selected_meta: Dict[str, Any]) -> str:
    selected = dict(selected_meta or {})
    parts = [
        str(product_name or "").strip(),
        str(selected.get("sceneLabel") or "").strip(),
        str(selected.get("moduleBoard") or "").strip(),
        str(selected.get("language") or "").strip(),
        str(selected.get("versionLabel") or "").strip(),
    ]
    return "||".join(parts)


def _load_registry() -> Dict[str, Any]:
    if not SHARED_PRODUCT_REGISTRY.exists():
        return {}
    try:
        return json.loads(SHARED_PRODUCT_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_registry(payload: Dict[str, Any]) -> None:
    SHARED_PRODUCT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    SHARED_PRODUCT_REGISTRY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_registered_product(
    client: ListenAIClient,
    product_name: str,
    selected_meta: Dict[str, Any],
) -> Dict[str, Any]:
    registry = _load_registry()
    entry = registry.get(_registry_key(product_name, selected_meta)) or {}
    product_id = str(entry.get("productId") or "")
    if not product_id:
        return {}
    try:
        product_detail = require_ok(client.get("/biz/prod/detail", params={"id": product_id}), "registered shared product detail").get("data") or {}
    except Exception:
        return {}
    if str(product_detail.get("name") or "") != product_name:
        return {}
    return dict(product_detail)


def _remember_product(product_name: str, selected_meta: Dict[str, Any], product_detail: Dict[str, Any]) -> None:
    product_id = str(product_detail.get("id") or "")
    if not product_id:
        return
    registry = _load_registry()
    registry[_registry_key(product_name, selected_meta)] = {
        "productId": product_id,
        "productName": str(product_detail.get("name") or product_name),
        "type": str(product_detail.get("type") or ""),
        "scene": str(product_detail.get("scene") or ""),
        "language": str(product_detail.get("language") or ""),
        "chipModule": str(product_detail.get("chipModule") or ""),
        "version": str(product_detail.get("version") or ""),
    }
    _save_registry(registry)


def find_existing_shared_product(
    client: ListenAIClient,
    product_name: str,
    *,
    selected_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected = dict(selected_meta or {})

    def query_records(registry_type: str = "", sub_type: str = "") -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "current": 1,
            "size": 100,
            "name": product_name,
        }
        if registry_type:
            params["type"] = registry_type
        if sub_type:
            params["subType"] = sub_type
        result = require_ok(
            client.get("/biz/prod/page", params=params),
            "query shared product page",
        )
        return list(((result.get("data") or {}).get("records")) or [])

    def record_matches_selected(record: Dict[str, Any]) -> bool:
        if not selected:
            return True
        pairs = [
            ("type", selected.get("productLabel")),
            ("scene", selected.get("sceneLabel")),
            ("language", selected.get("language")),
            ("chipModule", selected.get("moduleBoard")),
            ("version", selected.get("versionLabel")),
        ]
        for key, expected in pairs:
            text = str(expected or "").strip()
            if text and str(record.get(key) or "").strip() != text:
                return False
        return True

    exact: List[Dict[str, Any]] = []
    for registry_type, sub_type in (
        ("", ""),
        (DEFAULT_PRODUCT_TEMPLATE["registryType"], DEFAULT_PRODUCT_TEMPLATE["subType"]),
    ):
        records = query_records(registry_type=registry_type, sub_type=sub_type)
        exact = [
            item
            for item in records
            if str(item.get("name") or "") == product_name and item.get("id")
        ]
        if not exact:
            continue
        matched = [item for item in exact if record_matches_selected(item)]
        if matched:
            exact = matched
            break
        break

    if not exact:
        return {}
    exact.sort(key=lambda item: str(item.get("createTime") or ""), reverse=True)
    product_id = str(exact[0].get("id") or "")
    if not product_id:
        return {}
    product_detail = require_ok(client.get("/biz/prod/detail", params={"id": product_id}), "shared product detail").get("data") or {}
    return dict(product_detail)


def ensure_shared_product(client: ListenAIClient, manifest: Dict[str, Any]) -> Dict[str, Any]:
    shared_product = manifest.get("sharedProduct") or {}
    if shared_product.get("productDetail"):
        return dict(shared_product["productDetail"])

    product_name = str(shared_product["productName"])
    selected = manifest["selectedMeta"]
    product_payload = {
        "name": product_name,
        "language": selected["language"],
        "chipModule": selected["moduleBoard"],
        "defId": selected["defId"],
        "version": selected["versionLabel"],
        "type": selected["productLabel"],
        "scene": selected["sceneLabel"],
        "mode": selected.get("mode") or "",
    }

    if not shared_product.get("productId"):
        registered_detail = _load_registered_product(client, product_name, selected)
        if registered_detail:
            shared_product["productId"] = str(registered_detail.get("id") or "")
            shared_product["productDetail"] = registered_detail
            manifest["sharedProduct"] = shared_product
            return dict(registered_detail)

    if not shared_product.get("productId"):
        existing_detail = find_existing_shared_product(client, product_name, selected_meta=selected)
        if existing_detail:
            shared_product["productId"] = str(existing_detail.get("id") or "")
            shared_product["productDetail"] = existing_detail
            manifest["sharedProduct"] = shared_product
            _remember_product(product_name, selected, existing_detail)
            return dict(existing_detail)

    if not shared_product.get("productId"):
        add_result = client.post_json("/biz/prod/add", product_payload)
        if add_result.get("code") != 200 and "exist" not in str(add_result.get("msg", "")).lower():
            require_ok(add_result, "create shared product")

    product_id = shared_product.get("productId") or find_product_id(
        client,
        name=product_name,
        registry_type=DEFAULT_PRODUCT_TEMPLATE["registryType"],
        sub_type=DEFAULT_PRODUCT_TEMPLATE["subType"],
    )
    product_detail = require_ok(client.get("/biz/prod/detail", params={"id": product_id}), "shared product detail").get("data") or {}

    shared_product["productId"] = str(product_id)
    shared_product["productDetail"] = product_detail
    manifest["sharedProduct"] = shared_product
    _remember_product(product_name, selected, product_detail)
    return dict(product_detail)


def package_release_for_existing_product(
    client: ListenAIClient,
    product_detail: Dict[str, Any],
    source_release_id: str,
    timeout_sec: int,
    release_overrides: Dict[str, Any],
    *,
    trigger_package: bool = True,
) -> Dict[str, Any]:
    require_ok(client.get("/fw/release/copy", params={"id": source_release_id}), "copy release")

    source_release = require_ok(
        client.get("/fw/release/detail", params={"id": source_release_id}),
        "source release detail",
    ).get("data") or {}
    source_prod_id = str(source_release.get("prodId") or "")
    if not source_prod_id:
        raise RuntimeError("source release missing prodId")

    release_id = find_copied_release_id(
        client,
        prod_id=source_prod_id,
        source_release_id=source_release_id,
    )
    release_detail = require_ok(
        client.get("/fw/release/detail", params={"id": release_id}),
        "copied release detail",
    ).get("data") or {}
    release_detail["prodId"] = str(product_detail["id"])
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

    release_detail = apply_release_overrides(release_detail, release_overrides)
    require_ok(client.post_json("/fw/release/edit", release_detail), "rebind copied release")
    expected_fields = {
        key: release_detail.get(key)
        for key in (release_overrides or {})
        if key in release_detail and not isinstance(release_detail.get(key), (dict, list))
    }
    prepared_release = wait_release_stable(client, release_id, expected_fields)

    if not trigger_package:
        return {
            "productId": str(product_detail["id"]),
            "productName": str(product_detail["name"]),
            "releaseId": str(release_id),
            "releaseVersion": prepared_release.get("version"),
            "status": prepared_release.get("status"),
            "pkgTaskId": prepared_release.get("pkgTaskId"),
            "pkgPipelineId": prepared_release.get("pkgPipelineId"),
            "pkgUrl": prepared_release.get("pkgUrl"),
            "pkgSDKUrl": prepared_release.get("pkgSDKUrl"),
            "preparedOnly": True,
            "appliedOverrides": release_overrides,
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
    require_ok(client.get("/fw/release/package", params=package_params), "package")

    final_release = poll_release_success(client, release_id, timeout_sec=timeout_sec)
    return {
        "productId": str(product_detail["id"]),
        "productName": str(product_detail["name"]),
        "releaseId": str(release_id),
        "releaseVersion": final_release.get("version"),
        "status": final_release.get("status"),
        "pkgTaskId": final_release.get("pkgTaskId"),
        "pkgPipelineId": final_release.get("pkgPipelineId"),
        "pkgUrl": final_release.get("pkgUrl"),
        "pkgSDKUrl": final_release.get("pkgSDKUrl"),
        "preparedOnly": False,
        "appliedOverrides": release_overrides,
    }
