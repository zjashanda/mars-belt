from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from listenai_executable_case_suite import load_web_config
from listenai_task_support import MAIL_SEND_SCRIPT, RUNTIME_ROOT, TASKS_ROOT, resolve_listenai_token, safe_segment
from listenai_weekly_validation_runner import (
    DEFAULT_BURN_PORT,
    DEFAULT_CTRL_PORT,
    DEFAULT_PROTOCOL_PORT,
    DEFAULT_TRACE_PORT,
    ROOT,
    VOICE,
    copy_variant_artifacts,
    generate_weekly_email_report,
    jload,
    jsave,
    latest_dir,
    load_device_xlsx_summary,
    refresh_variant_human_fields,
    resolve_runtime_serials,
    scalar_text,
    sum_cfg,
    sum_dev,
)


CLI = ROOT / "mars_belt.py"


@dataclass
class RetestSpec:
    variant_id: str
    title: str
    purpose: str
    feature_keys: List[str]
    cases: List[str]
    overrides: Dict[str, Any]
    device_required: bool = True
    enable_algo_words: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run targeted round-2 retests and update weekly manifest/report.")
    parser.add_argument("--task-dir", required=True, help="Existing weekly task dir under scripts/result")
    parser.add_argument("--manifest", default="", help="Explicit manifest path; default resolves from task dir")
    parser.add_argument("--recipient", default="bszheng@listenai.com")
    parser.add_argument("--send-email", action="store_true", help="Send updated email after report regeneration")
    parser.add_argument("--only", default="", help="Only run specified variant ids, comma-separated")
    return parser


def log(text: str, payload: Optional[Dict[str, Any]] = None) -> None:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}")
    if payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.flush()


def run(cmd: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> int:
    log("执行命令", {"cwd": str(cwd or ROOT), "command": " ".join(str(item) for item in cmd)})
    return subprocess.run([str(item) for item in cmd], cwd=str(cwd or ROOT), env=env, check=False).returncode


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def find_latest_file(roots: Sequence[Path], name: str) -> Path:
    matches: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        matches.extend(root.rglob(name))
    matches = sorted(matches, key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        joined = ", ".join(str(root) for root in roots)
        raise FileNotFoundError(f"未找到 {name}: {joined}")
    return matches[0]


def copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_filtered_suite(source_suite: Path, dest_suite: Path, case_ids: Sequence[str]) -> List[Dict[str, Any]]:
    case_set = set(case_ids)
    payload = json.loads((source_suite / "executable_cases.json").read_text(encoding="utf-8"))
    rows = [dict(item) for item in (payload.get("rows") or []) if scalar_text(item.get("用例编号")) in case_set]
    if not rows:
        raise RuntimeError(f"套件 {source_suite} 未找到指定 case: {case_ids}")

    csv_rows = list(csv.DictReader((source_suite / "testCases.csv").open("r", encoding="utf-8-sig", newline="")))
    filtered_csv_rows = [dict(item) for item in csv_rows if scalar_text(item.get("用例编号")) in case_set]
    fieldnames = list(csv_rows[0].keys()) if csv_rows else list(rows[0].keys())

    ensure_clean_dir(dest_suite)
    with (dest_suite / "testCases.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for item in filtered_csv_rows:
            writer.writerow({key: item.get(key, "") for key in fieldnames})

    (dest_suite / "executable_cases.json").write_text(
        json.dumps({"generatedAt": datetime.now().isoformat(timespec="seconds"), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for name in ["deviceInfo_generated.json", "README.md"]:
        copy_if_exists(source_suite / name, dest_suite / name)

    source_wav = source_suite / "wavSource"
    if source_wav.exists():
        shutil.copytree(source_wav, dest_suite / "wavSource", dirs_exist_ok=True)

    return rows


def seed_regex_from_baseline(dest_suite: Path, baseline_suite: Path) -> None:
    dest_info_path = dest_suite / "deviceInfo_generated.json"
    baseline_info_path = baseline_suite / "deviceInfo_generated.json"
    if not dest_info_path.exists() or not baseline_info_path.exists():
        return
    dest = json.loads(dest_info_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_info_path.read_text(encoding="utf-8"))
    dest_regex = (((dest.get("deviceListInfo") or {}).get("cskApLog") or {}).get("regex") or {})
    baseline_regex = (((baseline.get("deviceListInfo") or {}).get("cskApLog") or {}).get("regex") or {})
    updated = False
    for key, value in baseline_regex.items():
        if not scalar_text(dest_regex.get(key)) and scalar_text(value):
            dest_regex[key] = value
            updated = True
    if updated:
        (((dest.setdefault("deviceListInfo", {})).setdefault("cskApLog", {})))["regex"] = dest_regex
        dest_info_path.write_text(json.dumps(dest, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_result_dir(suite_dir: Path) -> Path:
    result_dir = latest_dir(suite_dir / "result")
    if result_dir is None:
        raise RuntimeError(f"未找到结果目录: {suite_dir / 'result'}")
    return result_dir


def package_comment(spec: RetestSpec) -> str:
    return spec.title


def encode_override_files(spec_root: Path, overrides: Dict[str, Any]) -> List[str]:
    args: List[str] = []
    override_dir = spec_root / "override_payloads"
    override_dir.mkdir(parents=True, exist_ok=True)
    for key, value in overrides.items():
        if isinstance(value, (dict, list)):
            payload_path = override_dir / f"{safe_segment(key, fallback='override', max_len=40)}.json"
            payload_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            args.append(f"{key}=@{payload_path}")
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = "null"
        else:
            rendered = str(value)
        args.append(f"{key}={rendered}")
    return args


def locate_package_summary(search_roots: Sequence[Path]) -> Dict[str, Any]:
    summary_path = find_latest_file(search_roots, "summary.json")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def build_specs(state: Dict[str, Any]) -> List[RetestSpec]:
    variants = {scalar_text(item.get("id")): dict(item) for item in (state.get("variants") or []) if scalar_text(item.get("id"))}
    mid = variants.get("pkg-01-mid-stable") or {}
    multi_off = variants.get("pkg-05-multi-save-off") or {}
    word_text = scalar_text(((mid.get("resolvedOverrides") or mid.get("overrides") or {}).get("word"))) or "欢迎使用边界法验证固件"
    multi_overrides = dict(multi_off.get("resolvedOverrides") or multi_off.get("overrides") or {})
    release_multi_wke = multi_overrides.get("releaseMultiWke")
    release_algo_list = multi_overrides.get("releaseAlgoList")
    if not isinstance(release_multi_wke, dict) or not isinstance(release_algo_list, list):
        raise RuntimeError("manifest 中未找到 pkg-05-multi-save-off 的多唤醒依赖配置，无法复测 wakeWordSave=false")

    return [
        RetestSpec(
            variant_id="pkg-06-r2-word-only",
            title="R2 欢迎语单参数包",
            purpose="仅修改欢迎语文案，确认平台是否真实把字符串写入固件。",
            feature_keys=["欢迎语 TTS 文案"],
            cases=["CHG-WORD-001"],
            overrides={"word": word_text},
            device_required=False,
        ),
        RetestSpec(
            variant_id="pkg-07-r2-timeout-1",
            title="R2 1秒超时单参数包",
            purpose="仅修改唤醒时长到 1 秒，排除左边界大包耦合影响。",
            feature_keys=["唤醒时长"],
            cases=["CORE-TIMEOUT-001"],
            overrides={"timeout": 1},
        ),
        RetestSpec(
            variant_id="pkg-08-r2-volume-low",
            title="R2 2档音量单参数包",
            purpose="仅验证左边界 2 档音量，依赖带上 defaultVol=1 并顺手补齐左边界默认音量。",
            feature_keys=["音量档位数", "初始化默认音量"],
            cases=["CORE-VOLUME-001", "CORE-DEFAULTVOL-001"],
            overrides={"volLevel": 2, "defaultVol": 1},
        ),
        RetestSpec(
            variant_id="pkg-09-r2-speed-1",
            title="R2 语速1单参数包",
            purpose="仅修改合成语速到 1，验证低边界配置不会影响设备正常运行。",
            feature_keys=["合成语速"],
            cases=["CHG-SPEED-001"],
            overrides={"speed": 1},
        ),
        RetestSpec(
            variant_id="pkg-10-r2-vol-1",
            title="R2 合成音量1单参数包",
            purpose="仅修改合成音量到 1，验证低边界配置不会影响设备正常运行。",
            feature_keys=["合成音量"],
            cases=["CHG-VOL-001"],
            overrides={"vol": 1},
        ),
        RetestSpec(
            variant_id="pkg-11-r2-compress-1",
            title="R2 压缩比1单参数包",
            purpose="仅修改播报音压缩比到 1，验证低边界配置不会影响设备正常运行。",
            feature_keys=["播报音压缩比"],
            cases=["CHG-COMPRESS-001"],
            overrides={"compress": "1"},
        ),
        RetestSpec(
            variant_id="pkg-12-r2-defaultvol-10",
            title="R2 默认音量10单参数包",
            purpose="仅修改默认音量右边界，依赖带上 volLevel=10，用于区分 defaultVol 与 volSave 的责任归因。",
            feature_keys=["初始化默认音量"],
            cases=["CORE-DEFAULTVOL-001"],
            overrides={"volLevel": 10, "defaultVol": 10},
        ),
        RetestSpec(
            variant_id="pkg-13-r2-volsave-on",
            title="R2 音量掉保存开包",
            purpose="仅打开音量掉电保存，确认 volSave=true 是否单独异常。",
            feature_keys=["音量掉电保存"],
            cases=["CHG-VOLSAVE-001"],
            overrides={"volSave": True},
        ),
        RetestSpec(
            variant_id="pkg-14-r2-wakewordsave-off",
            title="R2 唤醒词掉保存关包",
            purpose="保持其他配置默认，仅补最小多唤醒依赖，确认 wakeWordSave=false 的真实行为。",
            feature_keys=["唤醒词掉电保存"],
            cases=["MWK-SAVE-001"],
            overrides={
                "multiWkeEnable": True,
                "multiWkeMode": "specified",
                "releaseMultiWke": release_multi_wke,
                "releaseAlgoList": release_algo_list,
                "wakeWordSave": False,
            },
            enable_algo_words=True,
        ),
        RetestSpec(
            variant_id="pkg-15-r2-uart-route-only",
            title="R2 UART路由单参数包",
            purpose="仅切换 uportUart=0 / traceUart=1，确认串口路由本身是否就是左边界系统性异常根因。",
            feature_keys=["协议串口", "日志串口"],
            cases=["CHG-UPORTUART-001", "CHG-TRACEUART-001"],
            overrides={"uportUart": "0", "traceUart": "1"},
        ),
        RetestSpec(
            variant_id="pkg-16-r2-multiwke-core",
            title="R2 多唤醒最小依赖包",
            purpose="其余配置保持默认，仅打开多唤醒 specified 并带上 2 个候选唤醒词，判断多唤醒本体是否单独异常。",
            feature_keys=["多唤醒词"],
            cases=["MWK-001", "MWK-002", "MWK-003", "MWK-006"],
            overrides={
                "multiWkeEnable": True,
                "multiWkeMode": "specified",
                "releaseMultiWke": release_multi_wke,
                "releaseAlgoList": release_algo_list,
            },
            enable_algo_words=True,
        ),
        RetestSpec(
            variant_id="pkg-17-r2-baud-2400",
            title="R2 波特率2400单参数包",
            purpose="仅修改协议串口波特率到 2400，确认低边界波特率本身是否会影响基础链路。",
            feature_keys=["协议串口波特率"],
            cases=["CHG-UPORTBAUD-001"],
            overrides={"uportBaud": "2400"},
        ),
        RetestSpec(
            variant_id="pkg-18-r2-loglevel-0",
            title="R2 日志级别0单参数包",
            purpose="仅修改日志级别到 0，确认低边界日志级别本身是否会影响基础链路。",
            feature_keys=["日志级别"],
            cases=["CHG-LOGLEVEL-001"],
            overrides={"logLevel": "0"},
        ),
        RetestSpec(
            variant_id="pkg-19-r2-wakewordsave-on",
            title="R2 唤醒词掉保存开包",
            purpose="保持其他配置默认，仅补最小多唤醒依赖，确认 wakeWordSave=true 的真实行为。",
            feature_keys=["唤醒词掉电保存"],
            cases=["MWK-SAVE-001"],
            overrides={
                "multiWkeEnable": True,
                "multiWkeMode": "specified",
                "releaseMultiWke": release_multi_wke,
                "releaseAlgoList": release_algo_list,
                "wakeWordSave": True,
            },
            enable_algo_words=True,
        ),
    ]


def feature_item_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        scalar_text(item.get("feature")): dict(item)
        for item in (state.get("featureSummary") or [])
        if scalar_text(item.get("feature"))
    }


def config_failed_case_details(config_result: Dict[str, Any]) -> List[str]:
    result_dir = Path(str(config_result.get("resultDir") or ""))
    csv_path = result_dir / "suite_config_assert_result.csv"
    if not csv_path.exists():
        return []
    rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig", newline="")))
    failures: List[str] = []
    for row in rows:
        verdict = scalar_text(row.get("执行结果"))
        if verdict == "OK":
            continue
        failures.append(
            f"{scalar_text(row.get('用例编号')) or '-'} [{verdict or 'UNKNOWN'}] {scalar_text(row.get('结果详情')) or scalar_text(row.get('配置断言')) or '-'}"
        )
    return failures


def device_failed_case_details(device_result: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    for item in device_result.get("finalFailures") or []:
        failures.append(f"{scalar_text(item.get('caseId')) or '-'} [{scalar_text(item.get('verdict')) or 'UNKNOWN'}] {scalar_text(item.get('detail')) or '-'}")
    tail = scalar_text(device_result.get("logTail"))
    if tail:
        failures.append(tail)
    return failures


def device_result_is_pass(device_result: Dict[str, Any], exit_code: int) -> bool:
    if exit_code != 0:
        return False
    final_failures = device_result.get("finalFailures") or []
    if final_failures:
        return False
    counters = dict(device_result.get("counters") or {})
    bad = [key for key, value in counters.items() if key not in {"OK", "Skip", "Skip(人工)"} and int(value) > 0]
    return not bad


def copy_package_outputs(task_dir: Path, variant_id: str, summary: Dict[str, Any]) -> Dict[str, str]:
    artifacts = dict(summary.get("artifacts") or {})
    package_zip = Path(str(artifacts.get("packageZip") or ""))
    params_txt = Path(str(artifacts.get("packageParamsTxt") or ""))
    copied: Dict[str, str] = {}
    if package_zip.exists():
        zip_target = task_dir / f"{variant_id}_{package_zip.name}"
        copy_if_exists(package_zip, zip_target)
        copied["packageZip"] = str(zip_target.resolve())
    if params_txt.exists():
        params_target = task_dir / f"{variant_id}_package_params.txt"
        copy_if_exists(params_txt, params_target)
        copied["packageParamsTxt"] = str(params_target.resolve())
    return copied


def build_variant_record(spec: RetestSpec) -> Dict[str, Any]:
    return {
        "id": spec.variant_id,
        "title": spec.title,
        "kind": "targeted-retest",
        "pack": "round2",
        "comments": spec.title,
        "overrides": spec.overrides,
        "resolvedOverrides": spec.overrides,
        "resolvedVoiceRegLearnCommands": [],
        "status": "planned",
        "packageAttemptCount": 0,
        "downloadedFirmwarePath": "",
        "downloadedSdkPath": "",
        "sdkToolHits": [],
        "parameterTxtPath": "",
        "suiteDir": "",
        "configResult": {},
        "deviceResult": {},
        "error": "",
        "humanPurpose": spec.purpose,
        "humanResult": "",
        "humanEvidence": "",
        "humanFailures": [],
    }


def update_feature_summary(state: Dict[str, Any], results_by_variant: Dict[str, Dict[str, Any]]) -> None:
    features = feature_item_map(state)

    def set_feature(name: str, *, coverage: str, status: str, summary: str) -> None:
        current = features.get(name, {"feature": name})
        current["coverage"] = coverage
        current["status"] = status
        current["summary"] = summary
        features[name] = current

    pkg07 = results_by_variant.get("pkg-07-r2-timeout-1") or {}
    if scalar_text(pkg07.get("status")) == "done":
        set_feature("唤醒时长", coverage="1秒/30秒/60秒已验证", status="PASS", summary="1 秒单参数包已通过，左边界阻塞已排除出该功能点。")
    elif pkg07:
        set_feature("唤醒时长", coverage="30秒/60秒已验证；1秒已单独复测", status="FAIL", summary=scalar_text(pkg07.get("humanEvidence")) or "1 秒单参数包仍未通过。")

    pkg08 = results_by_variant.get("pkg-08-r2-volume-low") or {}
    if scalar_text(pkg08.get("status")) == "done":
        set_feature("音量档位数", coverage="2档/5档/10档已验证", status="PASS", summary="2 档音量单参数包已通过，左边界档位功能正常。")
    elif pkg08:
        set_feature("音量档位数", coverage="5档/10档已验证；2档已单独复测", status="FAIL", summary=scalar_text(pkg08.get("humanEvidence")) or "2 档音量单参数包未通过。")

    pkg09 = results_by_variant.get("pkg-09-r2-speed-1") or {}
    if scalar_text(pkg09.get("status")) == "done":
        set_feature("合成语速", coverage="1/50/100已验证", status="PASS", summary="语速 1 单参数包已通过，低边界不会影响设备正常运行。")
    elif pkg09:
        set_feature("合成语速", coverage="50/100已验证；1已单独复测", status="FAIL", summary=scalar_text(pkg09.get("humanEvidence")) or "语速 1 单参数包未通过。")

    pkg10 = results_by_variant.get("pkg-10-r2-vol-1") or {}
    if scalar_text(pkg10.get("status")) == "done":
        set_feature("合成音量", coverage="1/50/100已验证", status="PASS", summary="合成音量 1 单参数包已通过，低边界不会影响设备正常运行。")
    elif pkg10:
        set_feature("合成音量", coverage="50/100已验证；1已单独复测", status="FAIL", summary=scalar_text(pkg10.get("humanEvidence")) or "合成音量 1 单参数包未通过。")

    pkg11 = results_by_variant.get("pkg-11-r2-compress-1") or {}
    if scalar_text(pkg11.get("status")) == "done":
        set_feature("播报音压缩比", coverage="1/2/3已验证", status="PASS", summary="压缩比 1 单参数包已通过，低边界不会影响设备正常运行。")
    elif pkg11:
        set_feature("播报音压缩比", coverage="2/3已验证；1已单独复测", status="FAIL", summary=scalar_text(pkg11.get("humanEvidence")) or "压缩比 1 单参数包未通过。")

    pkg06 = results_by_variant.get("pkg-06-r2-word-only") or {}
    if scalar_text(pkg06.get("status")) == "done":
        set_feature("欢迎语 TTS 文案", coverage="已验证 1 次", status="PASS", summary="欢迎语单参数包配置断言通过，字符串已真实入包。")
    elif pkg06:
        set_feature("欢迎语 TTS 文案", coverage="已验证 1 次 + 单参数复测 1 次", status="FAIL", summary=scalar_text(pkg06.get("humanEvidence")) or "欢迎语单参数包仍未真实入包。")

    pkg12 = results_by_variant.get("pkg-12-r2-defaultvol-10") or {}
    if scalar_text(pkg08.get("status")) == "done" and scalar_text(pkg12.get("status")) == "done":
        set_feature("初始化默认音量", coverage="1档/3档/10档已验证", status="PASS", summary="左边界与右边界默认音量单参数包均通过，功能点本身正常。")
    elif scalar_text(pkg12.get("status")) == "done" and scalar_text(pkg08.get("status")) != "done":
        set_feature("初始化默认音量", coverage="3档/10档通过；1档已单独复测未过", status="FAIL", summary=scalar_text(pkg08.get("humanEvidence")) or "默认音量左边界单参数包未通过。")
    elif pkg12:
        set_feature("初始化默认音量", coverage="3档通过；10档已单独复测", status="FAIL", summary=scalar_text(pkg12.get("humanEvidence")) or "默认音量 10 档单参数包仍未通过。")

    pkg13 = results_by_variant.get("pkg-13-r2-volsave-on") or {}
    if scalar_text(pkg13.get("status")) == "done":
        set_feature("音量掉电保存", coverage="false/true已验证", status="PASS", summary="volSave=true 单参数包已通过，首轮异常来自其他参数耦合。")
    elif pkg13:
        set_feature("音量掉电保存", coverage="false通过；true已单独复测", status="FAIL", summary=scalar_text(pkg13.get("humanEvidence")) or "volSave=true 单参数包仍未通过。")

    pkg14 = results_by_variant.get("pkg-14-r2-wakewordsave-off") or {}
    pkg19 = results_by_variant.get("pkg-19-r2-wakewordsave-on") or {}
    if scalar_text(pkg14.get("status")) == "done" and scalar_text(pkg19.get("status")) == "done":
        set_feature("唤醒词掉电保存", coverage="开/关均已单独验证", status="PASS", summary="wakeWordSave=true/false 最小依赖包均通过，保存开关本身正常。")
    elif scalar_text(pkg19.get("status")) == "done" and scalar_text(pkg14.get("status")) != "done":
        set_feature("唤醒词掉电保存", coverage="开通过；关已单独复测", status="FAIL", summary=scalar_text(pkg14.get("humanEvidence")) or "wakeWordSave=false 最小依赖包仍未通过。")
    elif scalar_text(pkg14.get("status")) == "done" and scalar_text(pkg19.get("status")) != "done":
        set_feature("唤醒词掉电保存", coverage="关通过；开已单独复测", status="FAIL", summary=scalar_text(pkg19.get("humanEvidence")) or "wakeWordSave=true 最小依赖包仍未通过。")
    elif pkg19 or pkg14:
        summary = scalar_text(pkg19.get("humanEvidence")) or scalar_text(pkg14.get("humanEvidence")) or "wakeWordSave 最小依赖包仍未通过。"
        set_feature("唤醒词掉电保存", coverage="开/关均已单独复测", status="FAIL", summary=summary)

    pkg15 = results_by_variant.get("pkg-15-r2-uart-route-only") or {}
    if scalar_text(pkg15.get("status")) == "done":
        set_feature("协议串口", coverage="串口0/1已单独验证", status="PASS", summary="仅切换 UART 路由时设备可正常启动，协议串口参数本身可用。")
        set_feature("日志串口", coverage="串口0/1已单独验证", status="PASS", summary="仅切换 UART 路由时设备可正常启动，日志串口参数本身可用。")
    elif pkg15:
        evidence = scalar_text(pkg15.get("humanEvidence")) or "仅切换 UART 路由单参数包仍未通过。"
        set_feature("协议串口", coverage="串口1通过；串口0已单独复测", status="FAIL", summary=evidence)
        set_feature("日志串口", coverage="串口0通过；串口1已单独复测", status="FAIL", summary=evidence)

    pkg16 = results_by_variant.get("pkg-16-r2-multiwke-core") or {}
    if scalar_text(pkg16.get("status")) == "done":
        set_feature("多唤醒词", coverage="最小依赖包已单独验证", status="PASS", summary="仅保留多唤醒最小依赖时功能通过，主包异常来自其他参数耦合。")
    elif pkg16:
        set_feature(
            "多唤醒词",
            coverage="最小依赖包已单独复测",
            status="FAIL",
            summary=scalar_text(pkg16.get("humanEvidence")) or "仅保留多唤醒最小依赖时仍未通过。",
        )

    pkg17 = results_by_variant.get("pkg-17-r2-baud-2400") or {}
    if scalar_text(pkg17.get("status")) == "done":
        set_feature("协议串口波特率", coverage="2400/38400/921600已单独验证", status="PASS", summary="2400 单参数包已通过，低边界波特率本身不会影响基础链路。")
    elif pkg17:
        set_feature(
            "协议串口波特率",
            coverage="38400/921600已验证；2400已单独复测",
            status="FAIL",
            summary=scalar_text(pkg17.get("humanEvidence")) or "2400 单参数包仍未通过。",
        )

    pkg18 = results_by_variant.get("pkg-18-r2-loglevel-0") or {}
    if scalar_text(pkg18.get("status")) == "done":
        set_feature("日志级别", coverage="0/3/5已单独验证", status="PASS", summary="logLevel=0 单参数包已通过，低边界日志级别本身不会影响基础链路。")
    elif pkg18:
        set_feature(
            "日志级别",
            coverage="3/5已验证；0已单独复测",
            status="FAIL",
            summary=scalar_text(pkg18.get("humanEvidence")) or "logLevel=0 单参数包仍未通过。",
        )

    state["featureSummary"] = list(features.values())


def maybe_update_control_findings(state: Dict[str, Any], results_by_variant: Dict[str, Dict[str, Any]]) -> None:
    pkg15 = results_by_variant.get("pkg-15-r2-uart-route-only") or {}
    if not pkg15:
        return
    findings = [dict(item) for item in (state.get("controlFindings") or []) if isinstance(item, dict)]
    kept = [item for item in findings if scalar_text(item.get("id")) != "pkg-15-r2-uart-route-only"]
    kept.append(
        {
            "id": "pkg-15-r2-uart-route-only",
            "change": "仅切换到 `uportUart=0 / traceUart=1`，其余参数回默认",
            "result": scalar_text(pkg15.get("humanResult")) or scalar_text(pkg15.get("status")) or "-",
            "conclusion": scalar_text(pkg15.get("humanEvidence")) or "-",
            "artifacts": [
                Path(str(pkg15.get("downloadedFirmwarePath") or "")).name if scalar_text(pkg15.get("downloadedFirmwarePath")) else "",
                f"{scalar_text(pkg15.get('id'))}_testResult.xlsx" if scalar_text(pkg15.get("deviceResult", {}).get("resultDir")) else "",
                f"{scalar_text(pkg15.get('id'))}_serial_raw.log" if scalar_text(pkg15.get("deviceResult", {}).get("resultDir")) else "",
            ],
        }
    )
    state["controlFindings"] = kept
    if scalar_text(pkg15.get("status")) == "done":
        state["controlConclusion"] = "左边界系统性异常不是 UART 路由单参本身，需按组合参数继续归因。"
    else:
        state["controlConclusion"] = "左边界系统性异常在“仅切 UART 路由单参数包”上仍可复现，根因进一步收敛到 UART 路由本身。"


def send_email(recipient: str, task_dir: Path, product_name: str) -> None:
    html_path = task_dir / "test_report.html"
    zip_path = task_dir / "result.zip"
    subject = f"{scalar_text(product_name) or task_dir.parent.name} 周测汇总报告 {datetime.now().strftime('%Y-%m-%d')}（第二轮复测更新）"
    if not MAIL_SEND_SCRIPT.exists():
        raise FileNotFoundError(f"邮件脚本不存在: {MAIL_SEND_SCRIPT}")
    cmd = [
        sys.executable,
        str(MAIL_SEND_SCRIPT),
        recipient,
        subject,
        str(html_path),
        str(zip_path),
    ]
    exit_code = run(cmd, cwd=ROOT)
    if exit_code != 0:
        raise RuntimeError(f"发送邮件失败: exit={exit_code}")


def main() -> int:
    args = build_parser().parse_args()
    task_dir = Path(args.task_dir).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else (RUNTIME_ROOT / task_dir.relative_to(TASKS_ROOT) / "weekly" / "manifest.json")
    if not manifest_path.exists():
        alt = RUNTIME_ROOT / task_dir.relative_to(TASKS_ROOT) / "weekly" / "manifest.json"
        if alt.exists():
            manifest_path = alt
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest 不存在: {manifest_path}")

    state = jload(manifest_path)
    runtime_dir = manifest_path.parent
    suites_root = runtime_dir / "suites"
    round2_root = task_dir / "_round2"
    selected = dict(state.get("selectedMeta") or {})
    source_release_id = scalar_text(state.get("sourceReleaseId"))
    product_name = scalar_text(state.get("productName"))
    specs = build_specs(state)
    if args.only.strip():
        allow = {item.strip() for item in args.only.split(",") if item.strip()}
        specs = [item for item in specs if item.variant_id in allow]
        if not specs:
            raise RuntimeError(f"--only 未匹配到任何 variant: {args.only}")

    baseline_suite = suites_root / "pkg-05-multi-save-off"
    if not baseline_suite.exists():
        baseline_suite = suites_root / "pkg-03-right-boundary"

    token = resolve_listenai_token()
    env = dict(os.environ)
    env["LISTENAI_TOKEN"] = token

    variant_index = {scalar_text(item.get("id")): idx for idx, item in enumerate(state.get("variants") or []) if scalar_text(item.get("id"))}
    results_by_variant: Dict[str, Dict[str, Any]] = {}

    for spec in specs:
        log("开始执行第二轮单参数复测", {"variant": spec.variant_id, "purpose": spec.purpose, "cases": spec.cases, "overrides": spec.overrides})
        spec_root = round2_root / spec.variant_id
        ensure_clean_dir(spec_root)
        runtime_spec_root = RUNTIME_ROOT / spec_root.relative_to(TASKS_ROOT)
        if runtime_spec_root.exists():
            shutil.rmtree(runtime_spec_root)
        override_args = encode_override_files(spec_root, spec.overrides)
        package_cmd: List[str] = [
            sys.executable,
            str(CLI),
            "package-custom",
            "--product",
            scalar_text(selected.get("productLabel")) or "取暖器",
            "--module",
            "3021",
            "--language",
            scalar_text(selected.get("language")) or "中文",
            "--version",
            scalar_text(selected.get("versionLabel")),
            "--scene",
            scalar_text(selected.get("sceneLabel")) or "纯离线",
            "--source-release-id",
            source_release_id,
            "--product-name",
            product_name,
            "--run-id",
            spec.variant_id,
            "--result-root",
            str(spec_root),
            "--package-root",
            str(spec_root),
            "--comments",
            package_comment(spec),
        ]
        for item in override_args:
            package_cmd.extend(["--override", item])
        if spec.enable_algo_words:
            package_cmd.append("--enable-algo-words")
        package_exit = run(package_cmd, cwd=ROOT, env=env)

        record = build_variant_record(spec)
        record["packageAttemptCount"] = 1
        summary: Optional[Dict[str, Any]] = None
        try:
            summary = locate_package_summary([spec_root, runtime_spec_root])
        except FileNotFoundError:
            summary = None

        if package_exit != 0 and summary is None:
            record["status"] = "pack_failed"
            record["error"] = f"package exit={package_exit}"
            record["humanResult"] = "打包失败；未进入后续验证。"
            record["humanEvidence"] = f"打包命令退出码 {package_exit}"
            record["humanFailures"] = [record["humanEvidence"]]
            results_by_variant[spec.variant_id] = record
            if spec.variant_id in variant_index:
                state["variants"][variant_index[spec.variant_id]] = record
            else:
                state.setdefault("variants", []).append(record)
            state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            jsave(manifest_path, state)
            continue

        if summary is None:
            raise FileNotFoundError(f"打包命令已返回，但未找到 summary.json: {spec.variant_id}")
        copied_outputs = copy_package_outputs(task_dir, spec.variant_id, summary)
        package_zip = Path(str(copied_outputs.get("packageZip") or summary.get("artifacts", {}).get("packageZip") or ""))
        package_params = Path(str(copied_outputs.get("packageParamsTxt") or summary.get("artifacts", {}).get("packageParamsTxt") or ""))
        source_suite = Path(str(summary.get("artifacts", {}).get("suiteDir") or ""))
        if not package_zip.exists():
            raise FileNotFoundError(f"未找到打包产物: {package_zip}")
        if not source_suite.exists():
            raise FileNotFoundError(f"未找到源 suite: {source_suite}")

        suite_dir = suites_root / spec.variant_id
        filtered_rows = write_filtered_suite(source_suite, suite_dir, spec.cases)
        if baseline_suite.exists():
            seed_regex_from_baseline(suite_dir, baseline_suite)
        config_cmd = [
            sys.executable,
            "-X",
            "utf8",
            str(VOICE),
            "--suite-dir",
            str(suite_dir),
            "--package-zip",
            str(package_zip),
            "--config-only",
            "-l",
            f"{spec.variant_id}_config",
        ]
        cfg_exit = run(config_cmd, cwd=ROOT, env=env)
        config_result_dir = latest_result_dir(suite_dir)
        config_result = {"exitCode": cfg_exit, **sum_cfg(config_result_dir)}
        copy_variant_artifacts(task_dir, f"{spec.variant_id}_config", suite_dir)

        record["downloadedFirmwarePath"] = str(package_zip.resolve())
        record["parameterTxtPath"] = str(package_params.resolve()) if package_params.exists() else ""
        record["suiteDir"] = str(suite_dir.resolve())
        record["configResult"] = config_result
        record["resolvedOverrides"] = dict(spec.overrides)
        record["overrides"] = dict(spec.overrides)

        if cfg_exit != 0:
            failures = config_failed_case_details(config_result)
            record["status"] = "config_failed"
            record["deviceResult"] = {"status": "skip_due_config_fail", "reason": "config-only validation failed"}
            record["humanResult"] = "打包成功；配置断言失败；未继续执行设备验证。"
            record["humanEvidence"] = failures[0] if failures else "config-only validation failed"
            record["humanFailures"] = failures[:3] or [record["humanEvidence"]]
            results_by_variant[spec.variant_id] = record
            if spec.variant_id in variant_index:
                state["variants"][variant_index[spec.variant_id]] = record
            else:
                state.setdefault("variants", []).append(record)
            state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            jsave(manifest_path, state)
            continue

        if not spec.device_required:
            record["status"] = "done"
            record["deviceResult"] = {"status": "skip_by_design", "reason": "config-only retest"}
            record["humanResult"] = "打包成功；目标配置断言通过。"
            record["humanEvidence"] = f"配置断言通过；命中 case={','.join(spec.cases)}"
            record["humanFailures"] = []
            results_by_variant[spec.variant_id] = record
            if spec.variant_id in variant_index:
                state["variants"][variant_index[spec.variant_id]] = record
            else:
                state.setdefault("variants", []).append(record)
            state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            jsave(manifest_path, state)
            continue

        runtime_serial = resolve_runtime_serials(
            load_web_config(str(package_zip), "") or {},
            SimpleNamespace(
                log_port=DEFAULT_TRACE_PORT,
                protocol_port=DEFAULT_PROTOCOL_PORT,
                uart0_port="",
                uart1_port="",
            ),
        )
        burn_cmd = [
            sys.executable,
            str(CLI),
            "burn",
            "--package-zip",
            str(package_zip),
            "--ctrl-port",
            DEFAULT_CTRL_PORT,
            "--burn-port",
            DEFAULT_BURN_PORT,
            "--runtime-log-port",
            str(runtime_serial.get("logPort") or DEFAULT_TRACE_PORT),
            "--runtime-log-baud",
            str(runtime_serial.get("logBaud") or 115200),
        ]
        burn_exit = run(burn_cmd, cwd=ROOT, env=env)
        if burn_exit != 0:
            record["status"] = "device_burn_failed"
            record["deviceResult"] = {"status": "burn_failed", "exitCode": burn_exit}
            record["humanResult"] = "打包成功；烧录失败；未进入设备验证。"
            record["humanEvidence"] = f"burn exit={burn_exit}"
            record["humanFailures"] = [record["humanEvidence"]]
            results_by_variant[spec.variant_id] = record
            if spec.variant_id in variant_index:
                state["variants"][variant_index[spec.variant_id]] = record
            else:
                state.setdefault("variants", []).append(record)
            state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            jsave(manifest_path, state)
            continue

        device_cmd = [
            sys.executable,
            "-X",
            "utf8",
            str(VOICE),
            "--suite-dir",
            str(suite_dir),
            "--package-zip",
            str(package_zip),
            "-l",
            spec.variant_id,
            "-p",
            str(runtime_serial.get("logPort") or DEFAULT_TRACE_PORT),
            "--ctrl-port",
            DEFAULT_CTRL_PORT,
            "--protocol-port",
            str(runtime_serial.get("protocolPort") or DEFAULT_PROTOCOL_PORT),
        ]
        dev_exit = run(device_cmd, cwd=ROOT, env=env)
        device_result_dir = latest_result_dir(suite_dir)
        device_result = {"exitCode": dev_exit, **sum_dev(device_result_dir)}
        copy_variant_artifacts(task_dir, spec.variant_id, suite_dir)

        record["deviceResult"] = device_result
        if device_result_is_pass(device_result, dev_exit):
            record["status"] = "done"
            record["humanResult"] = "打包成功；目标功能点验证通过。"
            record["humanEvidence"] = f"目标 case 通过：{','.join(spec.cases)}"
            record["humanFailures"] = []
        else:
            failures = device_failed_case_details(device_result)
            record["status"] = "device_done_with_issue"
            record["humanResult"] = "打包成功；目标功能点验证失败。"
            record["humanEvidence"] = failures[0] if failures else "device validation failed"
            record["humanFailures"] = failures[:3] or [record["humanEvidence"]]

        results_by_variant[spec.variant_id] = record
        if spec.variant_id in variant_index:
            state["variants"][variant_index[spec.variant_id]] = record
        else:
            state.setdefault("variants", []).append(record)
        state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        jsave(manifest_path, state)

    merged_variant_results = {
        scalar_text(item.get("id")): dict(item)
        for item in (state.get("variants") or [])
        if scalar_text(item.get("id"))
    }
    merged_variant_results.update(results_by_variant)
    refreshed_variants = [dict(item) for item in (state.get("variants") or []) if isinstance(item, dict)]
    refresh_variant_human_fields(task_dir, refreshed_variants)
    refreshed_map = {
        scalar_text(item.get("id")): dict(item)
        for item in refreshed_variants
        if scalar_text(item.get("id"))
    }
    if refreshed_map:
        merged_variant_results.update(refreshed_map)
    update_feature_summary(state, merged_variant_results)
    maybe_update_control_findings(state, merged_variant_results)
    state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    jsave(manifest_path, state)
    generate_weekly_email_report(task_dir, runtime_dir, state)

    if args.send_email:
        send_email(args.recipient, task_dir, product_name)

    print(json.dumps({"taskDir": str(task_dir), "manifest": str(manifest_path), "variants": results_by_variant}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
