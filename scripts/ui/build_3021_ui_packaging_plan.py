#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def short_def(def_id: str) -> str:
    return str(def_id)[-6:]


def lang_code(language: str) -> str:
    return "en" if language == "英文" else "zh"


def template_for(language: str, profile: str) -> str:
    return f"assets/templates/algo_{lang_code(language)}_{profile}.xlsx"


def product_name(row: Dict[str, str], stamp: str) -> str:
    label = row["productLabel"] or (row["productPath"].split("/")[-1].strip())
    lang = "英" if row["language"] == "英文" else "中"
    # Product is the stable container. Different profiles are generated as
    # multiple firmware releases under the same product, not as new products.
    return f"3021{label}{lang}{short_def(row['defId'])}{stamp}打包测试"


def default_basic(profile: str) -> Dict[str, Any]:
    base = {
        "timeout": 10,
        "volLevel": 5,
        "defaultVol": 3,
        "uportBaud": "9600",
        "logLevel": "WARN",
        "volSave": False,
        "wakeWordSave": False,
        "word": "欢迎使用聆思科技AI语音方案",
    }
    profiles = {
        "base_left": {
            "timeout": 1,
            "volLevel": 1,
            "defaultVol": 1,
            "logLevel": "ERROR",
            "volSave": False,
            "wakeWordSave": False,
            "word": "A",
        },
        "base_right": {
            "timeout": 60,
            "volLevel": 10,
            "defaultVol": 10,
            "uportBaud": "115200",
            "logLevel": "DEBUG",
            "volSave": True,
            "wakeWordSave": True,
            "word": "Welcome to ListenAI packaging test",
        },
        "multi_loop": {"timeout": 12, "volLevel": 6, "defaultVol": 4, "logLevel": "DEBUG", "wakeWordSave": True, "volSave": True, "word": "多唤醒循环切换测试"},
        "multi_specified": {"timeout": 15, "volLevel": 6, "defaultVol": 5, "uportBaud": "115200", "wakeWordSave": True, "volSave": False, "word": "多唤醒指定切换测试"},
        "multi_protocol": {"timeout": 20, "volLevel": 7, "defaultVol": 5, "uportBaud": "115200", "logLevel": "DEBUG", "wakeWordSave": True, "volSave": True, "word": "多唤醒协议切换测试"},
        "multi_off_negative": {"timeout": 8, "volLevel": 4, "defaultVol": 2, "logLevel": "ERROR", "wakeWordSave": False, "volSave": False, "word": "多唤醒关闭反例测试"},
        "voice_specific": {"timeout": 18, "volLevel": 6, "defaultVol": 4, "logLevel": "DEBUG", "volSave": True, "wakeWordSave": False, "word": "语音注册指定学习测试"},
        "voice_cont": {"timeout": 18, "volLevel": 7, "defaultVol": 5, "uportBaud": "115200", "logLevel": "WARN", "volSave": False, "wakeWordSave": True, "word": "语音注册连续学习测试"},
        "voice_boundary": {"timeout": 3, "volLevel": 3, "defaultVol": 1, "logLevel": "ERROR", "volSave": True, "wakeWordSave": True, "word": "边界注册"},
        "voice_off_negative": {"timeout": 6, "volLevel": 4, "defaultVol": 2, "logLevel": "WARN", "volSave": False, "wakeWordSave": False, "word": "语音注册关闭反例测试"},
    }
    base.update(profiles.get(profile, {}))
    return base


def coverage_points(profile: str, template_profile: str) -> List[str]:
    common = [
        "基础配置: timeout/volLevel/defaultVol",
        "串口日志: uportBaud/logLevel",
        "掉电保存: wakeWordSave/volSave",
        "播报合成: word",
        f"算法模板: {template_profile}",
    ]
    extra = {
        "base_mid": ["全产品组合基础生成", "基础唤醒/命令/音量/负性词/主被动播报"],
        "base_left": ["左边界向量: 短超时/低音量/低默认音量/ERROR 日志/短播报词/保存关闭"],
        "base_right": ["右边界向量: 长超时/高音量/高默认音量/DEBUG/115200/保存开启", "主动+被动协议组合"],
        "multi_loop": ["多唤醒开启", "循环切换", "候选词/查询/恢复默认", "保存开关联动"],
        "multi_specified": ["多唤醒开启", "指定切换", "默认/冻结候选词", "协议串口联动"],
        "multi_protocol": ["多唤醒开启", "协议切换", "候选词 snd/recProtocol", "被动播报联动"],
        "multi_off_negative": ["多唤醒关闭反例", "多唤醒词数据隔离", "基础链路保持"],
        "voice_specific": ["语音注册开启", "指定学习", "重复/重试/模板数/字数上下限", "删除/退出控制词"],
        "voice_cont": ["语音注册开启", "连续学习", "自动下一条/两步删除", "重试恢复"],
        "voice_boundary": ["语音注册开启", "repeat/retry 左边界", "字数上下限边界", "删除闭环"],
        "voice_off_negative": ["语音注册关闭反例", "注册控制词隔离", "基础链路保持"],
    }
    return common + extra.get(profile, [])


def version_description(profile: str) -> str:
    descriptions = {
        "base_mid": "默认基础",
        "base_left": "左边界基础",
        "base_right": "右边界+协议",
        "multi_loop": "左边界+循环唤醒",
        "multi_specified": "默认+指定唤醒",
        "multi_protocol": "右边界+协议唤醒",
        "multi_off_negative": "多唤醒关闭隔离",
        "voice_specific": "默认+指定学习",
        "voice_cont": "左边界+连续学习",
        "voice_boundary": "注册边界+删除",
        "voice_off_negative": "语音注册关闭隔离",
    }
    return descriptions.get(profile, profile[:24])


def algo_config_for(profile: str, feature: Dict[str, str] | None) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "voiceRegEnable": bool(feature and feature.get("voice_regist") == "Optional" and profile.startswith("voice_") and profile != "voice_off_negative"),
        "multiWkeEnable": bool(feature and feature.get("multi_wakeup") == "Optional" and profile.startswith("multi_") and profile != "multi_off_negative"),
        "multiWkeMode": "protocol" if profile == "multi_protocol" else "specified" if profile == "multi_specified" else "loop" if profile == "multi_loop" else "",
        "registMode": "contLearn" if profile == "voice_cont" else "specificLearn" if profile.startswith("voice_") and profile != "voice_off_negative" else "",
        "negativeMeaning": profile.endswith("off_negative") or profile.endswith("ui_negative"),
    }
    if profile == "voice_boundary":
        cfg.update({
            "wakeupRepeatCount": 1,
            "commandRepeatCount": 1,
            "wakeupRetryCount": 1,
            "commandRetryCount": 1,
            "wakeupWordsMinLimit": 2,
            "wakeupWordsMaxLimit": 10,
            "commandWordsMinLimit": 2,
            "commandWordsMaxLimit": 10,
            "wakeupRegistMaxLimit": 1,
            "commandRegistMaxLimit": 1,
        })
    elif profile == "voice_cont":
        cfg.update({
            # UI repeat count supports 1/2, so keep it in-range. Retry/template
            # limits use representative numeric values instead of hard boundaries.
            "wakeupRepeatCount": 2,
            "commandRepeatCount": 2,
            "wakeupRetryCount": 3,
            "commandRetryCount": 3,
            "wakeupWordsMinLimit": 4,
            "wakeupWordsMaxLimit": 6,
            "commandWordsMinLimit": 4,
            "commandWordsMaxLimit": 6,
            "wakeupRegistMaxLimit": 5,
            "commandRegistMaxLimit": 5,
        })
    elif profile.startswith("voice_") and profile != "voice_off_negative":
        cfg.update({
            "wakeupRepeatCount": 2,
            "commandRepeatCount": 2,
            "wakeupRetryCount": 2,
            "commandRetryCount": 2,
            "wakeupWordsMinLimit": 4,
            "wakeupWordsMaxLimit": 5,
            "commandWordsMinLimit": 4,
            "commandWordsMaxLimit": 6,
            "wakeupRegistMaxLimit": 3,
            "commandRegistMaxLimit": 3,
        })
    return cfg


def make_job(row: Dict[str, str], *, profile: str, template_profile: str, reason: str, stamp: str, feature: Dict[str, str] | None = None, overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    language = row["language"]
    basic = default_basic(profile)
    if overrides:
        basic.update(overrides)
    return {
        "jobId": f"{row['productValue']}_{row['defId']}_{profile}",
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
            "language": language,
            "versionLabel": row["versionLabel"],
            "defId": row["defId"],
            "mode": row.get("mode") or "",
        },
        "description": version_description(profile),
        "coveragePoints": coverage_points(profile, template_profile),
        "algoTemplate": template_for(language, template_profile),
        "templateProfile": template_profile,
        "basicConfig": basic,
        "skipAlgoImport": "V1.0" in row.get("versionLabel", ""),
        "skipReason": "legacy V1.0 UI blocks depth reset after xlsx import; package with UI default algorithm" if "V1.0" in row.get("versionLabel", "") else "",
        "algoConfig": algo_config_for(profile, feature),
        "feature": feature or {},
        "status": "pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows-csv", required=True)
    parser.add_argument("--features-json", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--stamp", default=datetime.now().strftime("%m%d%H%M"))
    args = parser.parse_args()
    rows = list(csv.DictReader(open(args.rows_csv, encoding="utf-8-sig")))
    rows = [r for r in rows if r.get("moduleMark") == "CSK3021" or r.get("moduleBoard") == "CSK3021-CHIP"]
    features = json.loads(Path(args.features_json).read_text(encoding="utf-8"))

    jobs: List[Dict[str, Any]] = []
    # Full product-combination coverage: one base package per 3021 row.
    for row in rows:
        jobs.append(make_job(row, profile="base_mid", template_profile="base_core", reason="所有 3021 组合基础 UI 打包覆盖", stamp=args.stamp, feature=features.get(row["defId"], {}).get("feature", {})))

    # Parameter boundary coverage: one left/right package per unique defId representative.
    by_def: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_def[row["defId"]].append(row)
    for def_id, group in sorted(by_def.items()):
        rep = group[0]
        feature = features.get(def_id, {}).get("feature", {})
        jobs.append(make_job(rep, profile="base_left", template_profile="base_core", reason="A 类基础配置左边界覆盖", stamp=args.stamp, feature=feature))
        jobs.append(make_job(rep, profile="base_right", template_profile="protocol_active_passive", reason="A 类基础配置右边界 + 主被动协议覆盖", stamp=args.stamp, feature=feature))
        if feature.get("multi_wakeup") == "Optional":
            jobs.append(make_job(rep, profile="multi_loop", template_profile="multi_wakeup_loop", reason="多唤醒循环切换正例", stamp=args.stamp, feature=feature, overrides={"wakeWordSave": True}))
            jobs.append(make_job(rep, profile="multi_specified", template_profile="multi_wakeup_specified", reason="多唤醒指定切换正例", stamp=args.stamp, feature=feature, overrides={"wakeWordSave": True}))
            jobs.append(make_job(rep, profile="multi_protocol", template_profile="multi_wakeup_protocol", reason="多唤醒协议切换正例", stamp=args.stamp, feature=feature, overrides={"wakeWordSave": True}))
            jobs.append(make_job(rep, profile="multi_off_negative", template_profile="base_core", reason="多唤醒关闭反例/隔离包", stamp=args.stamp, feature=feature, overrides={"wakeWordSave": False}))
        if feature.get("voice_regist") == "Optional":
            jobs.append(make_job(rep, profile="voice_specific", template_profile="voice_reg_specific", reason="语音注册指定学习正例", stamp=args.stamp, feature=feature))
            jobs.append(make_job(rep, profile="voice_cont", template_profile="voice_reg_continuous", reason="语音注册连续学习正例", stamp=args.stamp, feature=feature))
            jobs.append(make_job(rep, profile="voice_boundary", template_profile="voice_reg_boundary_delete", reason="语音注册边界/删除配置包", stamp=args.stamp, feature=feature))
            jobs.append(make_job(rep, profile="voice_off_negative", template_profile="base_core", reason="语音注册关闭反例/隔离包", stamp=args.stamp, feature=feature))

    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "strategy": "Configuration-vector packaging: one product per 3021 product combination, multiple releases under the same product. Each release modifies a group of basic + algorithm + protocol/save/special parameters instead of one parameter per package. APIs are allowed only for current-option discovery, product-id reuse checks, and result polling; configuration and package generation are driven via browser UI.",
        "counts": {
            "rows3021": len(rows),
            "uniqueDefIds": len(by_def),
            "jobs": len(jobs),
            "baseJobs": sum(1 for j in jobs if j["profile"] == "base_mid"),
            "boundaryJobs": sum(1 for j in jobs if j["profile"] in {"base_left", "base_right"}),
            "multiJobs": sum(1 for j in jobs if j["profile"].startswith("multi_")),
            "voiceJobs": sum(1 for j in jobs if j["profile"].startswith("voice_")),
        },
        "jobs": jobs,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
