#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

PREFERRED_PRODUCTS = {
    "茶吧机垂类": ["茶吧机"],
    "窗帘垂类": ["智能窗帘", "窗帘开关"],
    "风扇垂类": ["风扇", "空调扇", "风扇灯"],
    "取暖器垂类": ["取暖器"],
    "取暖桌垂类": ["取暖桌"],
    "通用垂类": ["智能垃圾桶", "通用", "风扇", "取暖器"],
}

STUDY_WORDS = {
    "茶吧机垂类": ["打开茶吧机", "关闭茶吧机", "烧水", "保温"],
    "窗帘垂类": ["打开窗帘", "关闭窗帘", "停止窗帘", "查询状态"],
    "风扇垂类": ["打开风扇", "关闭风扇", "增大风速", "减小风速"],
    "取暖器垂类": ["打开取暖器", "关闭取暖器", "升高温度", "降低温度"],
    "取暖桌垂类": ["打开取暖桌", "关闭取暖桌", "升高温度", "降低温度", "查询状态"],
    "通用垂类": ["打开垃圾桶", "关闭垃圾桶", "查询状态", "开始清洁"],
}


def vertical_name(row: Dict[str, str]) -> str:
    label = row.get("versionLabel") or ""
    return label.split("-")[0] if label else "UNKNOWN"


def short_def(def_id: str) -> str:
    return str(def_id)[-6:]


def safe_id(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5.-]+", "_", text)


def product_name(row: Dict[str, str], stamp: str) -> str:
    vertical = vertical_name(row)
    label = row.get("productLabel") or row.get("productValue") or "产品"
    return f"3021{vertical}{label}{short_def(row['defId'])}{stamp}打包测试"


def version_description(profile: str) -> str:
    return {
        "pkg01_default_multi_specified": "默认+指定唤醒",
        "pkg02_left_multi_loop": "左边界+循环唤醒",
        "pkg03_right_multi_protocol": "右边界+协议唤醒",
        "pkg01_default_voice_specific_multi_specified": "默认+指定学习+指定唤醒",
        "pkg02_left_voice_cont_multi_loop": "左边界+连续学习+循环唤醒",
        "pkg03_right_voice_specific_multi_protocol": "右边界+指定学习+协议唤醒",
        "pkg04_off_negative_base": "专项关闭隔离",
        "pkg01_default_base": "默认基础",
        "pkg02_left_base": "左边界基础",
        "pkg03_right_base_protocol": "右边界+协议基础",
    }.get(profile, profile[:28])


def template_for(language: str, profile: str) -> str:
    lang = "en" if language == "英文" else "zh"
    return f"assets/templates/algo_{lang}_{profile}.xlsx"


def base_basic(kind: str) -> Dict[str, Any]:
    if kind == "default":
        return {}
    if kind == "left":
        return {
            "timeout": 1,
            "volLevel": 1,
            "defaultVol": 1,
            "uportBaud": "9600",
            "logLevel": "ERROR",
            "volSave": False,
            "wakeWordSave": False,
            "word": "A",
        }
    return {
        "timeout": 60,
        "volLevel": 10,
        "defaultVol": 10,
        "uportBaud": "115200",
        "logLevel": "DEBUG",
        "volSave": True,
        "wakeWordSave": True,
        "word": "Welcome to ListenAI packaging test",
    }


def coverage(vertical: str, profile: str, template_profile: str, feature: Dict[str, str]) -> List[str]:
    points = [
        "严格 UI-only：产品创建、配置、算法导入、生成均通过浏览器 UI 触发",
        "同一垂类固定一个代表产品，同一产品下生成多个 release",
        f"垂类/配置：{vertical} / {profile}",
        f"算法模板：{template_profile}",
        "基础配置采用向量覆盖：默认/左边界/右边界组合，不做单参数逐包浪费",
    ]
    if feature.get("multi_wakeup") == "Optional":
        points.append("多唤醒覆盖：指定切换、循环切换、协议切换")
    if feature.get("voice_regist") == "Optional":
        points.append("语音注册覆盖：指定学习、连续学习、模板/重试代表值、关闭隔离")
        points.append("语音注册控制词只由 UI special 配置生成，算法 xlsx 不导入普通协议控制词")
    return points


def algo_config(vertical: str, profile: str, feature: Dict[str, str]) -> Dict[str, Any]:
    words = STUDY_WORDS.get(vertical, ["打开风扇", "关闭风扇", "查询状态"])
    cfg: Dict[str, Any] = {
        "voiceRegEnable": False,
        "multiWkeEnable": False,
        "multiWkeMode": "",
        "registMode": "",
        "studyCommandWords": words,
    }
    if "multi_specified" in profile:
        cfg.update({"multiWkeEnable": feature.get("multi_wakeup") == "Optional", "multiWkeMode": "specified"})
    elif "multi_loop" in profile:
        cfg.update({"multiWkeEnable": feature.get("multi_wakeup") == "Optional", "multiWkeMode": "loop"})
    elif "multi_protocol" in profile:
        cfg.update({"multiWkeEnable": feature.get("multi_wakeup") == "Optional", "multiWkeMode": "protocol"})

    if "voice_specific" in profile:
        cfg.update({
            "voiceRegEnable": feature.get("voice_regist") == "Optional",
            "registMode": "specificLearn",
            "preserveVoiceRegOptionalDefaults": "default" in profile,
            "wakeupRepeatCount": 2,
            "commandRepeatCount": 2,
            "wakeupRetryCount": 2,
            "commandRetryCount": 2,
            "wakeupRegistMaxLimit": 1 if "default" in profile else 3,
            "commandRegistMaxLimit": 1 if "default" in profile else 3,
        })
    elif "voice_cont" in profile:
        cfg.update({
            "voiceRegEnable": feature.get("voice_regist") == "Optional",
            "registMode": "contLearn",
            "wakeupRepeatCount": 2,
            "commandRepeatCount": 2,
            "wakeupRetryCount": 1,
            "commandRetryCount": 1,
            "wakeupRegistMaxLimit": 1,
            "commandRegistMaxLimit": 1,
        })
    return cfg


def make_job(row: Dict[str, str], profile: str, template_profile: str, basic_kind: str, reason: str, feature: Dict[str, str], stamp: str) -> Dict[str, Any]:
    vertical = vertical_name(row)
    return {
        "jobId": safe_id(f"{vertical}_{row['productLabel']}_{short_def(row['defId'])}_{profile}"),
        "profile": profile,
        "reason": reason,
        "productName": product_name(row, stamp),
        "product": {
            "topCategory": row["topCategory"],
            "productPath": row["productPath"],
            "productLabel": row["productLabel"],
            "productValue": row["productValue"],
            "sceneLabel": row["sceneLabel"],
            "sceneValue": row["sceneValue"],
            "moduleBoard": row["moduleBoard"],
            "moduleMark": row["moduleMark"],
            "language": row["language"],
            "versionLabel": row["versionLabel"],
            "defId": row["defId"],
            "mode": row.get("mode") or "",
        },
        "description": version_description(profile),
        "coveragePoints": coverage(vertical, profile, template_profile, feature),
        "algoTemplate": template_for(row["language"], template_profile),
        "templateProfile": template_profile,
        "skipBasicConfig": basic_kind == "default",
        "basicConfig": base_basic(basic_kind),
        "skipAlgoImport": False,
        "algoConfig": algo_config(vertical, profile, feature),
        "feature": feature,
        "status": "pending",
    }


def choose_representative(rows: Sequence[Dict[str, str]], vertical: str) -> Dict[str, str]:
    preferred = PREFERRED_PRODUCTS.get(vertical, [])
    for label in preferred:
        for row in rows:
            if row.get("productLabel") == label:
                return row
    return sorted(rows, key=lambda r: (r.get("topCategory") or "", r.get("productPath") or "", r.get("productLabel") or ""))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build current-UI 3021 representative vertical minimal packaging plan.")
    parser.add_argument("--matrix-csv", required=True)
    parser.add_argument("--features-json", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--stamp", default=datetime.now().strftime("%m%d%H%M"))
    parser.add_argument("--include-legacy-v1", action="store_true")
    args = parser.parse_args()

    rows = list(csv.DictReader(open(args.matrix_csv, encoding="utf-8-sig")))
    features = json.loads(Path(args.features_json).read_text(encoding="utf-8"))
    rows = [r for r in rows if (r.get("moduleMark") == "CSK3021" or r.get("moduleBoard") == "CSK3021-CHIP") and r.get("language") == "中文"]
    if not args.include_legacy_v1:
        rows = [r for r in rows if not (r.get("versionLabel") or "").startswith("CSK3021-") and "V1.0" not in (r.get("versionLabel") or "")]
    by_vertical: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_vertical[vertical_name(row)].append(row)

    jobs: List[Dict[str, Any]] = []
    selected: List[Dict[str, Any]] = []
    for vertical in sorted(by_vertical):
        if vertical == "UNKNOWN":
            continue
        rep = choose_representative(by_vertical[vertical], vertical)
        feature = dict((features.get(rep["defId"]) or {}).get("feature") or {})
        selected.append({"vertical": vertical, "productLabel": rep["productLabel"], "productPath": rep["productPath"], "defId": rep["defId"], "versionLabel": rep["versionLabel"], "feature": feature})
        has_voice = feature.get("voice_regist") == "Optional"
        has_multi = feature.get("multi_wakeup") == "Optional"
        if has_voice and has_multi:
            jobs.extend([
                make_job(rep, "pkg01_default_voice_specific_multi_specified", "full_feature_stateful", "default", "默认主链路：指定学习 + 指定唤醒", feature, args.stamp),
                make_job(rep, "pkg02_left_voice_cont_multi_loop", "full_feature_stateful", "left", "左边界组合：连续学习 + 循环唤醒", feature, args.stamp),
                make_job(rep, "pkg03_right_voice_specific_multi_protocol", "full_feature_stateful", "right", "右边界组合：指定学习 + 协议唤醒", feature, args.stamp),
                make_job(rep, "pkg04_off_negative_base", "base_core", "left", "语音注册/多唤醒关闭隔离反例", feature, args.stamp),
            ])
            jobs[-1]["algoConfig"].update({"voiceRegEnable": False, "multiWkeEnable": False, "multiWkeMode": "", "registMode": "", "negativeMeaning": True})
        elif has_multi:
            jobs.extend([
                make_job(rep, "pkg01_default_multi_specified", "multi_wakeup_specified", "default", "默认主链路：指定唤醒", feature, args.stamp),
                make_job(rep, "pkg02_left_multi_loop", "multi_wakeup_loop", "left", "左边界组合：循环唤醒", feature, args.stamp),
                make_job(rep, "pkg03_right_multi_protocol", "multi_wakeup_protocol", "right", "右边界组合：协议唤醒", feature, args.stamp),
            ])
        else:
            jobs.extend([
                make_job(rep, "pkg01_default_base", "base_core", "default", "默认基础链路", feature, args.stamp),
                make_job(rep, "pkg02_left_base", "base_core", "left", "左边界基础链路", feature, args.stamp),
                make_job(rep, "pkg03_right_base_protocol", "protocol_active_passive", "right", "右边界 + 主被动协议", feature, args.stamp),
            ])

    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "scope": "3021 current UI Chinese non-legacy vertical representatives",
        "strategy": "One representative product per current 3021 vertical. Multiple releases under the same product. Each release covers a vector of basic, protocol, multi-wakeup, and voice-registration settings; no single-parameter package spam.",
        "selectedRepresentatives": selected,
        "counts": {"verticals": len(selected), "jobs": len(jobs), "voiceJobs": sum(1 for j in jobs if j["algoConfig"].get("voiceRegEnable")), "multiJobs": sum(1 for j in jobs if j["algoConfig"].get("multiWkeEnable"))},
        "jobs": jobs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
    for item in selected:
        print(f"- {item['vertical']} / {item['productLabel']} / {item['versionLabel']} / {item['defId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
