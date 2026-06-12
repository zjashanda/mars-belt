#!/usr/bin/env python3
"""Package 3021 tea/curtain minimal coverage firmware and SDK.

This keeps platform validation aligned with UI-equivalent release creation while
avoiding full recommended command lists that bloat algorithm resources.
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

from listenai_auto_package import ListenAIClient, poll_release_success, require_ok
from listenai_task_support import resolve_listenai_token
from listenai_advanced_combo_trials import (
    build_default_voice_reg_payload,
    apply_voice_reg_learn_commands,
    ensure_multi_wakeup_words_in_algo_payload,
    normalize_release_algo_list,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = ROOT / "artifacts/tasks/3021-茶吧机-窗帘-固件SDK验证-20260609-130235"

BASE_RELEASE = {
    "timeout": 10,
    "volLevel": 5,
    "defaultVol": 5,
    "volMaxOverflow": "音量已最大",
    "volMinOverflow": "音量已最小",
    "again": 36,
    "dgain": 6,
    "uportUart": "1",
    "uportBaud": "9600",
    "traceUart": "0",
    "traceBaud": "115200",
    "logLevel": "1",
    "wakeWordSave": 1,
    "volSave": 0,
    "vcn": "x2_xiaoye",
    "speed": 50,
    "vol": 100,
    "compress": "2",
    "word": "欢迎使用聆思科技AI语音方案",
    "paConfigEnable": False,
    "ctlIoPad": "PB",
    "ctlIoNum": 11,
    "holdTime": 20000,
    "paConfigEnableLevel": "high",
    "sensitivity": "mid_sensitivity",
    "multiWkeMode": "specified",
    "algoViewMode": "full",
}

TARGETS: Dict[str, Dict[str, Any]] = {
    "tea": {
        "label": "茶吧机",
        "productName": "AUTO_TEST_3021_TEA_BASE_20260609",
        "productId": "2064211722817789953",
        "categoryName": "茶吧机",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064188250183983106",
        "versionLabel": "茶吧机垂类-V2.1.1_F3.0.1_A1.2.5.0",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Unsupported",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Unsupported",
        },
        "voiceRegEnable": False,
        "wakewords": ["小聆小聆", "茶吧管家", "小茶小茶"],
        "selectedWords": [
            "小聆小聆",
            "开机",
            "关机",
            "开始烧水",
            "关闭烧水",
            "开始取水",
            "关闭取水",
            "打开保温",
            "关闭保温",
            "最大音量",
            "最小音量",
            "调大音量",
            "调小音量",
            "打开语音",
            "关闭语音",
        ],
        "runtimeSmokeWords": ["小聆小聆", "开机", "开始烧水", "开始取水", "最大音量", "切换唤醒词", "茶吧管家"],
    },
    "curtain": {
        "label": "智能窗帘",
        "productName": "AUTO_TEST_3021_CURTAIN_VR_20260609",
        "productId": "2064216740031651842",
        "categoryName": "智能窗帘",
        "mark": "CSK3021-CHIP",
        "scene": "纯离线",
        "language": "中文",
        "configId": "2064186976629710849",
        "versionLabel": "窗帘垂类-V2.1.1_F3.0.1_A1.5.4.0",
        "featureMap": {
            "e2e_cmd": "Optional",
            "main_cmd": "Optional",
            "dec_cmd": "Optional",
            "multi_wakeup": "Optional",
            "voice_regist": "Optional",
        },
        "voiceRegEnable": True,
        "wakewords": ["小聆小聆", "窗帘管家", "小窗小窗"],
        "selectedWords": [
            "小聆小聆",
            "打开窗帘",
            "关闭窗帘",
            "停止窗帘",
            "窗帘打开百分之五十",
            "半遮光模式",
            "全遮光模式",
            "打开布帘",
            "关闭布帘",
            "音量最大",
            "音量最小",
            "增大音量",
            "减小音量",
            "打开语音",
            "关闭语音",
        ],
        "voiceRegLearnCommands": ["打开窗帘", "关闭窗帘"],
        "runtimeSmokeWords": ["小聆小聆", "打开窗帘", "关闭窗帘", "音量最大", "切换唤醒词", "窗帘管家", "学习命令词", "删除命令词"],
    },
}

FEATURE_TEST_PLAN: Dict[str, List[Dict[str, str]]] = {
    "tea": [
        {"feature": "固件打包", "status": "supported", "test": "release/add -> algoUnifiedSave -> ready -> package -> firmware zip"},
        {"feature": "SDK 打包", "status": "supported", "test": "pkgSDKUrl 下载 -> mars-sdk.zip -> app.bin/app.img"},
        {"feature": "默认唤醒", "status": "supported", "test": "小聆小聆唤醒日志与 TX"},
        {"feature": "业务命令", "status": "e2e_cmd/dec_cmd Optional", "test": "开关机/烧水/取水/保温代表命令"},
        {"feature": "音量控制", "status": "supported", "test": "最大/最小/调大/调小/开关语音"},
        {"feature": "多唤醒/唤醒切换", "status": "multi_wakeup Optional", "test": "新增 2 个唤醒词，切换/查询/恢复默认/断电保持"},
        {"feature": "语音注册", "status": "Unsupported", "test": "不纳入，报告说明"},
    ],
    "curtain": [
        {"feature": "固件打包", "status": "supported", "test": "release/add -> algoUnifiedSave -> ready -> package -> firmware zip"},
        {"feature": "SDK 打包", "status": "supported", "test": "pkgSDKUrl 下载 -> mars-sdk.zip -> app.bin/app.img"},
        {"feature": "默认唤醒", "status": "supported", "test": "小聆小聆唤醒日志与 TX"},
        {"feature": "业务命令", "status": "main_cmd/e2e_cmd/dec_cmd Optional", "test": "开/关/停/比例/遮光模式/布帘代表命令"},
        {"feature": "音量控制", "status": "supported", "test": "最大/最小/增大/减小/开关语音"},
        {"feature": "多唤醒/唤醒切换", "status": "multi_wakeup Optional", "test": "新增 2 个唤醒词，切换/查询/恢复默认/断电保持"},
        {"feature": "语音注册", "status": "voice_regist Optional", "test": "学习命令词、回测、删除命令词、冲突/长度/重复不一致反例"},
    ],
}


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def multi_wke_protocol(code: int, *, recv: bool = False) -> str:
    command = "82" if recv else "81"
    tail = (code + (0x21 if recv else 0x20)) & 0xFF
    return f"A5 FA 00 {command} {code:02X} 00 {tail:02X} FB"


def build_multi_wke_payload(wakewords: Sequence[str]) -> Dict[str, Any]:
    words: List[str] = []
    for raw in wakewords:
        word = str(raw or "").strip()
        if word and word not in words:
            words.append(word)
    if len(words) < 3:
        raise ValueError("multi wake validation requires default + at least two candidate wake words")
    wkelist = []
    for idx, word in enumerate(words):
        code = 0x31 + idx
        wkelist.append(
            {
                "condition": word,
                "reply": f"已切换到{word}",
                "sndProtocol": multi_wke_protocol(code, recv=False),
                "recProtocol": multi_wke_protocol(code, recv=True),
                "isDefault": idx == 0,
                "isFrozen": False,
            }
        )
    return {
        "multiWkeEnable": True,
        "multiWkeMode": "specified",
        "releaseMultiWke": {
            "common": [
                {"type": "query", "condition": "查询唤醒词", "reply": ""},
                {"type": "restore", "condition": "恢复默认唤醒词", "reply": ""},
                {"type": "switch", "condition": "切换唤醒词", "reply": "请说您想切换的唤醒词"},
            ],
            "wkelist": wkelist,
        },
    }


def normalize_word(item: Dict[str, Any]) -> str:
    return str(item.get("word") or item.get("intent") or "").strip()


def select_algo_words(algo_data: Sequence[Dict[str, Any]], selected_words: Sequence[str]) -> List[Dict[str, Any]]:
    by_word: Dict[str, Dict[str, Any]] = {}
    for item in normalize_release_algo_list(algo_data):
        word = normalize_word(item)
        if word and word not in by_word:
            by_word[word] = item
    selected: List[Dict[str, Any]] = []
    missing: List[str] = []
    for word in selected_words:
        item = by_word.get(word)
        if item is None:
            missing.append(word)
        else:
            selected.append(deepcopy(item))
    if missing:
        raise RuntimeError(f"selected words missing in platform algo data: {missing}")
    for idx, item in enumerate(selected, start=1):
        item["idx"] = idx
        item["pid"] = "0"
        item["children"] = item.get("children") or []
    return selected


def release_add_payload(target: Dict[str, Any], tag: str) -> Dict[str, Any]:
    payload = deepcopy(BASE_RELEASE)
    payload.update(
        {
            "prodId": target["productId"],
            "voiceRegEnable": bool(target.get("voiceRegEnable")),
            "multiWkeEnable": True,
            "comments": f"AUTO_TEST 3021 {target['label']} minimal feature coverage {tag}",
        }
    )
    return payload


def package_params(target: Dict[str, Any], release_id: str) -> Dict[str, str]:
    return {
        "id": str(release_id),
        "categoryName": str(target["categoryName"]),
        "mark": str(target["mark"]),
        "scene": str(target["scene"]),
        "productName": str(target["productName"]),
        "language": str(target["language"]),
        "configId": str(target["configId"]),
    }


def make_algo_payload(target: Dict[str, Any], release_id: str, algo_data: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "language": target["language"],
        "configId": target["configId"],
        "releaseId": str(release_id),
        "sensitivity": "mid_sensitivity",
        "voiceRegEnable": bool(target.get("voiceRegEnable")),
        "multiWkeEnable": True,
        "multiWkeMode": "specified",
        "algoViewMode": "full",
        "releaseAlgoList": select_algo_words(algo_data, target["selectedWords"]),
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


def assert_zip_ok(path: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
    if bad:
        raise RuntimeError(f"zip test failed for {path}: {bad}")
    return {"path": str(path), "size": path.stat().st_size, "entries": len(names), "head": names[:20]}


def download_outputs(client: ListenAIClient, final_release: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    firmware = out_dir / "firmware.zip"
    sdk = out_dir / "sdk.zip"
    client.download(str(final_release.get("pkgUrl") or ""), str(firmware))
    client.download(str(final_release.get("pkgSDKUrl") or ""), str(sdk))
    return {
        "firmware": assert_zip_ok(firmware),
        "sdk": assert_zip_ok(sdk),
    }


def package_one(client: ListenAIClient, key: str, run_dir: Path, source_run_root: Path, timeout_sec: int) -> Dict[str, Any]:
    target = TARGETS[key]
    tag = now_slug()
    target_dir = run_dir / key
    api_dir = target_dir / "api"
    downloads_dir = target_dir / "downloads"
    api_dir.mkdir(parents=True, exist_ok=True)

    add_payload = release_add_payload(target, tag)
    write_json(api_dir / "release_add_payload.json", add_payload)
    add_result = require_ok(client.post_json("/fw/release/add", add_payload), f"{key} release add")
    write_json(api_dir / "release_add_response.json", add_result)
    release_id = str((add_result.get("data") or {}).get("id") or "")
    if not release_id:
        raise RuntimeError(f"{key} release add did not return release id")

    algo_result = require_ok(client.get("/fw/release/getAlgoData", params={"id": release_id}), f"{key} get algo data")
    write_json(api_dir / "algo_data_response.json", algo_result)
    algo_data = algo_result.get("data") or []
    if not algo_data:
        # First-release add can return an empty getAlgoData result. Reuse the
        # platform-recommended template captured from the same vertical, then
        # submit the reduced releaseAlgoList through algoUnifiedSave.
        fallback_name = "tea_algo_unified_payload_all.json" if key == "tea" else "curtain_algo_unified_payload_all_vr.json"
        fallback_path = source_run_root / "api_probes" / fallback_name
        fallback_payload = json.loads(fallback_path.read_text(encoding="utf-8"))
        algo_data = fallback_payload.get("releaseAlgoList") or []
        write_json(api_dir / "algo_data_fallback_source.json", {"path": str(fallback_path), "count": len(algo_data)})
    algo_payload = make_algo_payload(target, release_id, algo_data)
    write_json(api_dir / "algo_unified_payload_minimal.json", algo_payload)
    require_ok(client.post_json("/fw/release/algoUnifiedSave", algo_payload), f"{key} algo unified save")

    detail = require_ok(client.get("/fw/release/detail", params={"id": release_id}), f"{key} release detail after algo").get("data") or {}
    write_json(api_dir / "release_detail_after_algo.json", detail)
    ready_payload = deepcopy(detail)
    ready_payload["status"] = "ready"
    write_json(api_dir / "release_ready_payload.json", ready_payload)
    require_ok(client.post_json("/fw/release/edit", ready_payload), f"{key} release ready")
    time.sleep(5)

    params = package_params(target, release_id)
    write_json(api_dir / "package_request_params.json", params)
    package_result = require_ok(client.get("/fw/release/package", params=params), f"{key} package")
    write_json(api_dir / "package_request_response.json", package_result)
    final_release = poll_release_success(client, release_id, timeout_sec=timeout_sec)
    write_json(api_dir / "package_final_detail.json", final_release)

    downloads = download_outputs(client, final_release, downloads_dir)
    summary = {
        "key": key,
        "target": {k: v for k, v in target.items() if k not in {"selectedWords", "runtimeSmokeWords"}},
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
        "principle": "按平台能力裁剪范围；每个 supported/Optional 功能进入最小覆盖包，不支持项只在报告中说明。",
        "targets": {
            key: {
                "label": target["label"],
                "productName": target["productName"],
                "versionLabel": target["versionLabel"],
                "featureMap": target["featureMap"],
                "selectedWords": target["selectedWords"],
                "wakewords": target["wakewords"],
                "voiceRegLearnCommands": target.get("voiceRegLearnCommands") or [],
                "runtimeSmokeWords": target["runtimeSmokeWords"],
                "testPlan": FEATURE_TEST_PLAN[key],
            }
            for key, target in TARGETS.items()
        },
    }
    path = run_dir / "minimal_function_test_matrix.json"
    write_json(path, matrix)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package 3021 tea/curtain minimal feature coverage firmware and SDK.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--out-dir", default="", help="Default: <run-root>/minimal_coverage_<timestamp>")
    parser.add_argument("--target", choices=["tea", "curtain", "all"], default="all")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true", help="Only write local matrix; do not call platform.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root)
    out_dir = Path(args.out_dir) if args.out_dir else run_root / f"minimal_coverage_{now_slug()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = write_matrix(out_dir)
    print(f"matrix: {matrix_path}")
    if args.dry_run:
        return 0

    token = resolve_listenai_token(args.token, persist=False)
    if not token:
        raise RuntimeError("Missing LISTENAI_TOKEN. Update TOOLS.md or pass --token.")
    client = ListenAIClient(token, timeout=90)
    targets = ["tea", "curtain"] if args.target == "all" else [args.target]
    summaries = []
    for key in targets:
        print(f"packaging {key} minimal coverage...")
        summaries.append(package_one(client, key, out_dir, run_root, args.timeout_sec))
    result = {"generatedAt": datetime.now().isoformat(timespec="seconds"), "outDir": str(out_dir), "summaries": summaries}
    write_json(out_dir / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
