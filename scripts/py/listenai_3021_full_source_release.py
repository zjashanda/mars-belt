#!/usr/bin/env python3
"""Create full-command source releases for 3021 vertical weekly validation.

The weekly 5-package runner copies a source release before applying boundary
and feature overrides. This helper creates that source release from the same
UI-equivalent first-release flow, but keeps the full recommended command list
instead of the reduced smoke matrix.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence
import re

from listenai_3021_more_vertical_package import checksum_frame, default_wakeup, recommended_by_title
from listenai_3021_vertical_minimal_package import BASE_RELEASE, now_slug, write_json
from listenai_auto_package import ListenAIClient, poll_release_success, require_ok
from listenai_shared_product_flow import ensure_shared_product
from listenai_task_support import resolve_listenai_token


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = ROOT / "artifacts/tasks/3021-五垂类5固件全链路-20260609"

TARGETS: Dict[str, Dict[str, Any]] = {
    "tea": {
        "label": "茶吧机",
        "productName": "AUTO_FULLSRC_3021_TEA_20260609",
        "categoryName": "茶吧机",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064188250183983106",
        "versionLabel": "茶吧机垂类-V2.1.1_F3.0.1_A1.2.5.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Unsupported",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Unsupported",
        },
    },
    "curtain": {
        "label": "智能窗帘",
        "productName": "AUTO_FULLSRC_3021_CURTAIN_20260609",
        "categoryName": "智能窗帘",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064186976629710849",
        "versionLabel": "窗帘垂类-V2.1.1_F3.0.1_A1.5.4.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Optional",
        },
    },
    "fan": {
        "label": "风扇",
        "productName": "AUTO_FULLSRC_3021_FAN_20260609",
        "categoryName": "风扇",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064188245549277186",
        "versionLabel": "风扇垂类-V2.1.1_F3.0.1_A1.3.0.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Unsupported",
        },
    },
    "heater": {
        "label": "取暖器",
        "productName": "AUTO_FULLSRC_3021_HEATER_20260609",
        "categoryName": "取暖器",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2061365894591832066",
        "versionLabel": "取暖器垂类-V2.1.1_F3.0.1_A1.4.1.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Unsupported",
        },
    },
    "table_heater": {
        "label": "取暖桌",
        "productName": "AUTO_FULLSRC_3021_TABLE_HEATER_20260609",
        "categoryName": "取暖桌",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064188249550643201",
        "versionLabel": "取暖桌垂类-V2.1.1_F3.0.1_A1.3.0.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Optional",
        },
    },
}


def build_product_manifest(target: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "selectedMeta": {
            "productLabel": target["categoryName"],
            "sceneLabel": target["scene"],
            "language": target["language"],
            "moduleBoard": target["mark"],
            "defId": target["configId"],
            "versionLabel": target["versionLabel"],
            "mode": target.get("mode") or "multi_lang",
        },
        "sharedProduct": {
            "productName": target["productName"],
            "productId": "",
            "productDetail": None,
        },
    }


def release_add_payload(target: Dict[str, Any], product_id: str, tag: str) -> Dict[str, Any]:
    payload = deepcopy(BASE_RELEASE)
    payload.update(
        {
            "prodId": str(product_id),
            "voiceRegEnable": False,
            "multiWkeEnable": False,
            "comments": f"AUTO_TEST 3021 {target['label']} full source release {tag}",
        }
    )
    return payload


def command_item(release_id: str, idx: int, word: str, recommended: Dict[str, Any]) -> Dict[str, Any]:
    code = 0x10 + idx
    return {
        "id": "",
        "releaseId": str(release_id),
        "pid": "0",
        "idx": idx,
        "word": word,
        "type": "命令词",
        "extWord": str(recommended.get("extWord") or ""),
        "reply": "好的",
        "replyMode": "主",
        "sndProtocol": checksum_frame(0x81, code),
        "recProtocol": checksum_frame(0x82, code),
        "recoId": str(recommended.get("key") or ""),
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


def invalid_pinyin_tokens(item: Dict[str, Any]) -> List[str]:
    """Return obvious platform pinyin tokens that cannot enter algo packing."""
    pinyin = str(item.get("pinyin") or "")
    tokens = [tok for tok in re.split(r"[-\s/]+", pinyin) if tok]
    # Tone 0 is kept because existing platform data uses it for neutral tone.
    return [tok for tok in tokens if not re.fullmatch(r"[a-züv]+[0-5]", tok)]


def full_algo_list(
    target: Dict[str, Any],
    release_id: str,
    recommended: Sequence[Dict[str, Any]],
    *,
    exclude_invalid_pinyin: bool = False,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_title = recommended_by_title(recommended)
    items = [default_wakeup(release_id)]
    excluded: List[Dict[str, Any]] = []
    for word in sorted(by_title):
        if word == "小聆小聆":
            continue
        bad_tokens = invalid_pinyin_tokens(by_title[word])
        if bad_tokens and exclude_invalid_pinyin:
            excluded.append(
                {
                    "word": word,
                    "key": by_title[word].get("key"),
                    "pinyin": by_title[word].get("pinyin"),
                    "extWord": by_title[word].get("extWord"),
                    "invalidTokens": bad_tokens,
                    "reason": "platform recommended pinyin contains token without tone digit; algo resource generation rejects it",
                }
            )
            continue
        items.append(command_item(release_id, len(items) + 1, word, by_title[word]))
    return items, excluded


def package_params(target: Dict[str, Any], product_name: str, release_id: str) -> Dict[str, str]:
    return {
        "id": str(release_id),
        "categoryName": str(target["categoryName"]),
        "mark": str(target["mark"]),
        "scene": str(target["scene"]),
        "productName": str(product_name),
        "language": str(target["language"]),
        "configId": str(target["configId"]),
    }


def package_one(
    client: ListenAIClient,
    key: str,
    run_dir: Path,
    timeout_sec: int,
    *,
    exclude_invalid_pinyin: bool = False,
) -> Dict[str, Any]:
    target = TARGETS[key]
    tag = now_slug()
    target_dir = run_dir / key
    api_dir = target_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)

    details = require_ok(client.get("/fw/config/details", params={"id": target["configId"]}), f"{key} config details")
    recommended_resp = require_ok(client.get("/fw/config/recommended", params={"id": target["configId"]}), f"{key} config recommended")
    recommended = recommended_resp.get("data") or []
    write_json(api_dir / "config_details.json", details)
    write_json(api_dir / "config_recommended.json", recommended_resp)

    manifest = build_product_manifest(target)
    product_detail = ensure_shared_product(client, manifest)
    write_json(api_dir / "shared_product_manifest.json", manifest)
    product_id = str(product_detail["id"])

    add_payload = release_add_payload(target, product_id, tag)
    write_json(api_dir / "release_add_payload.json", add_payload)
    add_result = require_ok(client.post_json("/fw/release/add", add_payload), f"{key} release add")
    write_json(api_dir / "release_add_response.json", add_result)
    release_id = str((add_result.get("data") or {}).get("id") or "")
    if not release_id:
        raise RuntimeError(f"{key} release add did not return release id")

    release_algo_list, excluded_commands = full_algo_list(
        target,
        release_id,
        recommended,
        exclude_invalid_pinyin=exclude_invalid_pinyin,
    )
    write_json(api_dir / "excluded_invalid_pinyin_commands.json", excluded_commands)
    algo_payload = {
        "language": target["language"],
        "configId": target["configId"],
        "releaseId": str(release_id),
        "sensitivity": "mid_sensitivity",
        "voiceRegEnable": False,
        "multiWkeEnable": False,
        "multiWkeMode": "specified",
        "algoViewMode": "full",
        "releaseAlgoList": release_algo_list,
        "featureToggle": target.get("featureMap") or {},
    }
    write_json(api_dir / "algo_unified_payload_full_source.json", algo_payload)
    require_ok(client.post_json("/fw/release/algoUnifiedSave", algo_payload), f"{key} algo unified save")

    detail = require_ok(client.get("/fw/release/detail", params={"id": release_id}), f"{key} release detail").get("data") or {}
    write_json(api_dir / "release_detail_after_algo.json", detail)
    ready_payload = deepcopy(detail)
    ready_payload["status"] = "ready"
    write_json(api_dir / "release_ready_payload.json", ready_payload)
    require_ok(client.post_json("/fw/release/edit", ready_payload), f"{key} release ready")
    time.sleep(3)

    params = package_params(target, str(product_detail.get("name") or target["productName"]), release_id)
    write_json(api_dir / "package_request_params.json", params)
    package_result = require_ok(client.get("/fw/release/package", params=params), f"{key} package")
    write_json(api_dir / "package_request_response.json", package_result)
    final_release = poll_release_success(client, release_id, timeout_sec=timeout_sec)
    write_json(api_dir / "package_final_detail.json", final_release)

    summary = {
        "key": key,
        "label": target["label"],
        "productId": product_id,
        "productName": str(product_detail.get("name") or target["productName"]),
        "configId": target["configId"],
        "versionLabel": target["versionLabel"],
        "releaseId": release_id,
        "releaseVersion": final_release.get("version"),
        "status": final_release.get("status"),
        "pkgTaskId": final_release.get("pkgTaskId"),
        "pkgPipelineId": final_release.get("pkgPipelineId"),
        "pkgUrlPresent": bool(final_release.get("pkgUrl")),
        "pkgSDKUrlPresent": bool(final_release.get("pkgSDKUrl")),
        "commandCount": len(algo_payload["releaseAlgoList"]) - 1,
        "excludedInvalidPinyinCount": len(excluded_commands),
        "excludedInvalidPinyinWords": [item["word"] for item in excluded_commands],
    }
    write_json(target_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create full-command 3021 source release for weekly validation.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--target", choices=[*TARGETS.keys(), "all"], default="all")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument(
        "--exclude-invalid-pinyin",
        action="store_true",
        help="Exclude recommended commands whose platform pinyin has impossible tokens, and write an exclusion report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else Path(args.run_root) / f"full_source_{now_slug()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    token = resolve_listenai_token(args.token, persist=False)
    if not token:
        raise RuntimeError("Missing LISTENAI_TOKEN. Update TOOLS.md or pass --token.")
    client = ListenAIClient(token, timeout=90)
    keys = list(TARGETS) if args.target == "all" else [args.target]
    summaries = []
    for key in keys:
        print(f"creating full source {key}...")
        sys.stdout.flush()
        summaries.append(
            package_one(
                client,
                key,
                out_dir,
                args.timeout_sec,
                exclude_invalid_pinyin=args.exclude_invalid_pinyin,
            )
        )
    result = {"generatedAt": datetime.now().isoformat(timespec="seconds"), "outDir": str(out_dir), "summaries": summaries}
    write_json(out_dir / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
