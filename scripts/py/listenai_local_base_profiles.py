from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from listenai_auto_package import ListenAIClient, require_ok
from listenai_packaging_rules import build_weekly_product_name_from_selected


SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
LOCAL_BASE_PROFILES_PATH = SCRIPTS_ROOT / "config" / "local_base_profiles.json"


def _load_profiles() -> List[Dict[str, Any]]:
    if not LOCAL_BASE_PROFILES_PATH.exists():
        return []
    try:
        payload = json.loads(LOCAL_BASE_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _match_text(left: Any, right: Any) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def find_local_base_profile(selected: Dict[str, Any]) -> Dict[str, Any]:
    target = dict(selected or {})
    for profile in _load_profiles():
        if not _match_text(profile.get("productLabel"), target.get("productLabel")):
            continue
        if not _match_text(profile.get("sceneLabel"), target.get("sceneLabel")):
            continue
        if not _match_text(profile.get("moduleBoard"), target.get("moduleBoard")):
            continue
        if not _match_text(profile.get("language"), target.get("language")):
            continue
        if not _match_text(profile.get("versionLabel"), target.get("versionLabel")):
            continue
        return dict(profile)
    return {}


def default_shared_product_name(selected: Dict[str, Any], explicit_name: str = "") -> str:
    explicit = str(explicit_name or "").strip()
    if explicit:
        return explicit
    profile = find_local_base_profile(selected)
    shared_name = str(profile.get("sharedProductName") or "").strip()
    if shared_name:
        return shared_name
    return build_weekly_product_name_from_selected(selected)


def _template_path(profile: Dict[str, Any]) -> Path:
    raw = str(profile.get("algoTemplatePath") or "").strip()
    if not raw:
        raise RuntimeError(f"local base profile '{profile.get('id') or 'unknown'}' missing algoTemplatePath")
    path = Path(raw)
    if not path.is_absolute():
        path = SCRIPTS_ROOT / path
    return path


def _load_algo_template(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = _template_path(profile)
    if not path.exists():
        raise RuntimeError(f"local base algo template not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"local base algo template must be a JSON list: {path}")
    return [dict(item) for item in payload if isinstance(item, dict)]


def build_local_release_algo_list(profile: Dict[str, Any], source_release_id: str) -> List[Dict[str, Any]]:
    items = _load_algo_template(profile)
    stamped: List[Dict[str, Any]] = []
    for index, raw in enumerate(items, start=1):
        item = {
            "id": "",
            "releaseId": str(source_release_id or ""),
            "pid": "0",
            "idx": raw.get("idx") or index,
            "word": raw.get("word"),
            "extWord": raw.get("extWord"),
            "type": raw.get("type"),
            "reply": raw.get("reply"),
            "replyMode": raw.get("replyMode"),
            "sndProtocol": raw.get("sndProtocol"),
            "recProtocol": raw.get("recProtocol"),
            "recoId": "",
            "recoExtWordStr": None,
            "asrFreeEnable": None,
            "relatedId": None,
            "relatedType": None,
            "pinyin": None,
            "deleteFlag": "NOT_DELETE",
            "createTime": None,
            "createUser": None,
            "updateTime": None,
            "updateUser": None,
            "children": [],
        }
        stamped.append(item)
    return stamped


def _release_status_rank(status: Any) -> int:
    order = {
        "success": 0,
        "init": 1,
        "ready": 2,
        "pending": 3,
        "failed": 4,
    }
    return order.get(str(status or "").strip().lower(), 9)


def _list_product_releases(client: ListenAIClient, product_id: str) -> List[Dict[str, Any]]:
    result = require_ok(
        client.get("/fw/release/page", params={"current": 1, "size": 100, "prodId": product_id}),
        "local base profile release page",
    )
    records = list(((result.get("data") or {}).get("records")) or [])
    records.sort(key=lambda item: str(item.get("updateTime") or item.get("createTime") or ""), reverse=True)
    records.sort(key=lambda item: _release_status_rank(item.get("status")))
    return [dict(item) for item in records if item.get("id")]


def resolve_local_source_release_id(
    *,
    selected: Dict[str, Any],
    explicit_source_release_id: str,
    catalog_source_release_id: str,
    client: Optional[ListenAIClient] = None,
    product_detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    explicit = str(explicit_source_release_id or "").strip()
    if explicit:
        return {
            "sourceReleaseId": explicit,
            "sourceStrategy": "explicit",
            "matchedProfile": bool(find_local_base_profile(selected)),
        }

    profile = find_local_base_profile(selected)
    catalog_source = str(catalog_source_release_id or "").strip()
    if not profile:
        return {
            "sourceReleaseId": catalog_source,
            "sourceStrategy": "catalog",
            "matchedProfile": False,
        }

    preferred = str(profile.get("preferredSourceReleaseId") or "").strip()
    if client is None:
        return {
            "sourceReleaseId": preferred or catalog_source,
            "sourceStrategy": "profile-preferred-dry-run" if preferred else "catalog-dry-run",
            "matchedProfile": True,
            "profile": profile,
        }

    product_id = str((product_detail or {}).get("id") or "").strip()
    if preferred:
        try:
            detail = require_ok(client.get("/fw/release/detail", params={"id": preferred}), "local base profile preferred release detail").get("data") or {}
            detail_prod_id = str(detail.get("prodId") or "").strip()
            if not product_id or not detail_prod_id or detail_prod_id == product_id:
                return {
                    "sourceReleaseId": preferred,
                    "sourceStrategy": "profile-preferred",
                    "matchedProfile": True,
                    "profile": profile,
                }
        except Exception:
            pass

    if product_id:
        records = _list_product_releases(client, product_id)
        if records:
            return {
                "sourceReleaseId": str(records[0].get("id") or ""),
                "sourceStrategy": "shared-product-release-fallback",
                "matchedProfile": True,
                "profile": profile,
            }

    return {
        "sourceReleaseId": catalog_source,
        "sourceStrategy": "catalog-fallback",
        "matchedProfile": True,
        "profile": profile,
    }


def apply_local_base_profile(
    *,
    selected: Dict[str, Any],
    explicit_product_name: str,
    explicit_source_release_id: str,
    catalog_source_release_id: str,
    overrides: Optional[Dict[str, Any]] = None,
    client: Optional[ListenAIClient] = None,
    product_detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = find_local_base_profile(selected)
    resolved_overrides = deepcopy(dict(overrides or {}))
    shared_name = default_shared_product_name(selected, explicit_product_name)
    source_info = resolve_local_source_release_id(
        selected=selected,
        explicit_source_release_id=explicit_source_release_id,
        catalog_source_release_id=catalog_source_release_id,
        client=client,
        product_detail=product_detail,
    )
    source_release_id = str(source_info.get("sourceReleaseId") or "")
    applied_local_algo = False
    if profile and "releaseAlgoList" not in resolved_overrides:
        resolved_overrides["releaseAlgoList"] = build_local_release_algo_list(profile, source_release_id)
        applied_local_algo = True
    return {
        "profile": profile,
        "sharedProductName": shared_name,
        "sourceReleaseId": source_release_id,
        "sourceStrategy": str(source_info.get("sourceStrategy") or ""),
        "matchedProfile": bool(profile),
        "appliedLocalAlgo": applied_local_algo,
        "overrides": resolved_overrides,
    }
