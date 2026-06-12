#!/usr/bin/env python3
"""Package 3021 fan/heater/table-heater minimal vertical coverage.

The platform has no stable sourceReleaseId for these vertical families, so this
script follows the UI-equivalent first-release path and builds the minimal
releaseAlgoList from the platform recommended-command API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from listenai_3021_vertical_minimal_package import (
    BASE_RELEASE,
    assert_zip_ok,
    build_multi_wke_payload,
    now_slug,
    write_json,
)
from listenai_advanced_combo_trials import (
    apply_voice_reg_learn_commands,
    build_default_voice_reg_payload,
    ensure_multi_wakeup_words_in_algo_payload,
)
from listenai_auto_package import ListenAIClient, poll_release_success, require_ok
from listenai_shared_product_flow import ensure_shared_product
from listenai_task_support import resolve_listenai_token


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = ROOT / "artifacts/tasks/3021-五垂类扩展验证-20260609"


TARGETS: Dict[str, Dict[str, Any]] = {
    "fan": {
        "label": "风扇",
        "productName": "AUTO_TEST_3021_FAN_MIN_20260609",
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
        "voiceRegEnable": False,
        "wakewords": ["小聆小聆", "风扇管家", "小风小风"],
        "selectedWords": [
            "打开风扇",
            "关闭风扇",
            "打开摇头",
            "关闭摇头",
            "调到最大风",
            "调到最小风",
            "最大音量",
            "最小音量",
            "增大音量",
            "减小音量",
            "打开语音",
            "关闭语音",
        ],
        "runtimeSmokeWords": ["小聆小聆", "打开风扇", "关闭风扇", "最大音量", "切换唤醒词", "风扇管家"],
    },
    "heater": {
        "label": "取暖器",
        "productName": "AUTO_TEST_3021_HEATER_MIN_20260609",
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
        "voiceRegEnable": False,
        "wakewords": ["小聆小聆", "暖风管家", "小暖小暖"],
        "selectedWords": [
            "打开取暖器",
            "关闭取暖器",
            "打开暖风",
            "关闭暖风",
            "调高温度",
            "调低温度",
            "最大音量",
            "最小音量",
            "增大音量",
            "减小音量",
            "打开语音",
            "关闭语音",
        ],
        "runtimeSmokeWords": ["小聆小聆", "打开取暖器", "关闭取暖器", "最大音量", "切换唤醒词", "暖风管家"],
    },
    "table_heater": {
        "label": "取暖桌",
        "productName": "AUTO_TEST_3021_TABLE_HEATER_MIN_20260609",
        "categoryName": "取暖桌",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2029449341250928641",
        "versionLabel": "取暖桌垂类-V2.0_F2.0.4_A1.3.0.0",
        "mode": "multi_lang",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Optional",
        },
        "voiceRegEnable": True,
        "wakewords": ["小聆小聆", "暖桌管家", "小桌小桌"],
        "selectedWords": [
            "开机",
            "关机",
            "打开下暖",
            "关闭下暖",
            "打开火焰",
            "关闭火焰",
            "最大取暖",
            "最小取暖",
            "最大音量",
            "最小音量",
            "增大音量",
            "减小音量",
            "打开语音",
            "关闭语音",
        ],
        "voiceRegLearnCommands": ["打开下暖", "关闭下暖"],
        "runtimeSmokeWords": ["小聆小聆", "开机", "关机", "最大音量", "切换唤醒词", "暖桌管家", "学习命令词", "删除命令词"],
    },
}


def checksum_frame(command: int, code: int) -> str:
    parts = [0xA5, 0xFA, 0x00, command, code & 0xFF, 0x00]
    parts.append(sum(parts) & 0xFF)
    parts.append(0xFB)
    return " ".join(f"{x:02X}" for x in parts)


def default_wakeup(release_id: str) -> Dict[str, Any]:
    return {
        "id": "",
        "releaseId": str(release_id),
        "pid": "0",
        "idx": 1,
        "word": "小聆小聆",
        "type": "唤醒词",
        "reply": "我在/在呢/我来啦",
        "replyMode": "主",
        "sndProtocol": "A5 FA 00 81 01 00 21 FB",
        "recProtocol": "A5 FA 00 82 01 00 22 FB",
        "extWord": "",
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


def recommended_by_title(recommended: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in recommended:
        title = str(item.get("title") or "").strip()
        if title and title not in result:
            result[title] = dict(item)
    return result


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


def make_release_algo_list(target: Dict[str, Any], release_id: str, recommended: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_title = recommended_by_title(recommended)
    missing = [word for word in target["selectedWords"] if word not in by_title]
    if missing:
        raise RuntimeError(f"{target['label']} selected words missing from recommended list: {missing}")
    items = [default_wakeup(release_id)]
    for word in target["selectedWords"]:
        items.append(command_item(release_id, len(items) + 1, word, by_title[word]))
    return items


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
            "voiceRegEnable": bool(target.get("voiceRegEnable")),
            "multiWkeEnable": True,
            "comments": f"AUTO_TEST 3021 {target['label']} extended vertical coverage {tag}",
        }
    )
    return payload


def make_algo_payload(target: Dict[str, Any], release_id: str, recommended: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "language": target["language"],
        "configId": target["configId"],
        "releaseId": str(release_id),
        "sensitivity": "mid_sensitivity",
        "voiceRegEnable": bool(target.get("voiceRegEnable")),
        "multiWkeEnable": True,
        "multiWkeMode": "specified",
        "algoViewMode": "full",
        "releaseAlgoList": make_release_algo_list(target, release_id, recommended),
        "featureToggle": target.get("featureMap") or {},
    }
    payload.update(build_multi_wke_payload(target["wakewords"]))
    ensure_multi_wakeup_words_in_algo_payload(payload)

    if target.get("voiceRegEnable"):
        payload.update(build_default_voice_reg_payload())
        payload["releaseRegist"].update(
            {
                "registMode": "contLearn",
                "registType": "command",
                "commandRepeatCount": 2,
                "commandRetryCount": 2,
                "commandWordsMinLimit": 2,
                "commandWordsMaxLimit": 10,
                "commandRegistMaxLimit": 2,
                "wakeupRegistMaxLimit": 1,
            }
        )
        apply_voice_reg_learn_commands(payload, target.get("voiceRegLearnCommands") or [])
    return payload


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


def download_outputs(client: ListenAIClient, final_release: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    firmware = out_dir / "firmware.zip"
    sdk = out_dir / "sdk.zip"
    client.download(str(final_release.get("pkgUrl") or ""), str(firmware))
    client.download(str(final_release.get("pkgSDKUrl") or ""), str(sdk))
    return {"firmware": assert_zip_ok(firmware), "sdk": assert_zip_ok(sdk)}


def package_one(client: ListenAIClient, key: str, run_dir: Path, timeout_sec: int) -> Dict[str, Any]:
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

    algo_payload = make_algo_payload(target, release_id, recommended)
    write_json(api_dir / "algo_unified_payload_minimal.json", algo_payload)
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

    downloads = download_outputs(client, final_release, target_dir / "downloads")
    summary = {
        "key": key,
        "label": target["label"],
        "productId": product_id,
        "productName": str(product_detail.get("name") or target["productName"]),
        "configId": target["configId"],
        "versionLabel": target["versionLabel"],
        "featureMap": target["featureMap"],
        "voiceRegEnable": bool(target.get("voiceRegEnable")),
        "wakewords": target["wakewords"],
        "selectedWords": target["selectedWords"],
        "runtimeSmokeWords": target["runtimeSmokeWords"],
        "releaseId": release_id,
        "releaseVersion": final_release.get("version"),
        "status": final_release.get("status"),
        "pkgTaskId": final_release.get("pkgTaskId"),
        "pkgPipelineId": final_release.get("pkgPipelineId"),
        "downloads": downloads,
    }
    write_json(target_dir / "summary.json", summary)
    return summary


def write_matrix(run_dir: Path) -> Path:
    matrix = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "principle": "3021 垂类最小覆盖：默认唤醒、代表业务命令、音量控制、多唤醒；语音注册仅在平台能力 Optional 时启用。",
        "targets": {
            key: {
                "label": value["label"],
                "productName": value["productName"],
                "configId": value["configId"],
                "versionLabel": value["versionLabel"],
                "featureMap": value["featureMap"],
                "wakewords": value["wakewords"],
                "selectedWords": value["selectedWords"],
                "voiceRegLearnCommands": value.get("voiceRegLearnCommands") or [],
                "runtimeSmokeWords": value["runtimeSmokeWords"],
            }
            for key, value in TARGETS.items()
        },
    }
    path = run_dir / "minimal_function_test_matrix.json"
    write_json(path, matrix)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package 3021 fan/heater/table-heater minimal feature coverage.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--target", choices=[*TARGETS.keys(), "all"], default="all")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root)
    out_dir = Path(args.out_dir) if args.out_dir else run_root / f"more_verticals_{now_slug()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = write_matrix(out_dir)
    print(f"matrix: {matrix_path}")
    if args.dry_run:
        return 0

    token = resolve_listenai_token(args.token, persist=False)
    if not token:
        raise RuntimeError("Missing LISTENAI_TOKEN. Update TOOLS.md or pass --token.")
    client = ListenAIClient(token, timeout=90)
    keys = list(TARGETS) if args.target == "all" else [args.target]
    summaries = []
    for key in keys:
        print(f"packaging {key} minimal coverage...")
        summaries.append(package_one(client, key, out_dir, args.timeout_sec))
    result = {"generatedAt": datetime.now().isoformat(timespec="seconds"), "outDir": str(out_dir), "summaries": summaries}
    write_json(out_dir / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
