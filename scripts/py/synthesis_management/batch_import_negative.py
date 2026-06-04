#!/usr/bin/env python3
"""API negative-matrix validation for 播报合成 batchImport.

This uses the backend /biz/audiofile/batchImport endpoint directly. Treat the
result as backend robustness evidence, not as proof that the browser UI can
produce or submit the same file set.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
from openpyxl import Workbook

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import (
    ListenAISynthesisClient,
    build_broadcast_release_payload,
    delete_records,
    dict_children,
    first_value,
    query_first_by_field,
    require_ok,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"


def run_process(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def make_mp3(path: Path, *, sample_rate: int = 16000, channels: int = 1, bitrate: str = "16k", seconds: float = 0.8) -> Tuple[bool, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run_process([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", f"sine=frequency=880:duration={seconds}",
        "-ac", str(channels), "-ar", str(sample_rate), "-b:a", bitrate,
        "-codec:a", "libmp3lame", "-write_xing", "0", "-id3v2_version", "0",
        "-y", str(path),
    ])
    return proc.returncode == 0, proc.stderr.strip()


def make_wav(path: Path, *, sample_rate: int = 16000, channels: int = 1, seconds: float = 0.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes((b"\x00\x00" * channels) * nframes)


def make_xlsx(path: Path, *, audio_stem: str, reply: str, rec_protocol: str, columns: Optional[List[str]] = None, row_values: Optional[Dict[str, Any]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or ["播报内容", "音频描述", "接收协议"]
    values = row_values or {"播报内容": reply, "音频描述": audio_stem, "接收协议": rec_protocol}
    wb = Workbook()
    ws = wb.active
    ws.title = "播报映射"
    ws.append(columns)
    ws.append([values.get(col, "") for col in columns])
    wb.save(path)


def ffprobe(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    proc = run_process([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
        "-of", "json", str(path),
    ])
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": proc.stdout[:1000]}


def create_case_files(upload_dir: Path, stamp: str, case: Dict[str, Any]) -> Tuple[Path, Path, Dict[str, Any]]:
    stem = f"{case['id']}_{stamp}"
    audio = upload_dir / f"{stem}{case.get('ext', '.mp3')}"
    xlsx = upload_dir / f"{stem}{case.get('xlsx_ext', '.xlsx')}"
    meta: Dict[str, Any] = {}

    if case.get("zero"):
        audio.write_bytes(b"")
    elif case.get("corrupt"):
        audio.write_bytes(os.urandom(512))
    elif case.get("wav"):
        make_wav(audio, sample_rate=case.get("sample_rate", 16000), channels=case.get("channels", 1), seconds=case.get("seconds", 0.4))
    elif case.get("rename_from_mp3"):
        tmp = upload_dir / f"{stem}_tmp.mp3"
        ok, err = make_mp3(tmp, sample_rate=case.get("sample_rate", 16000), channels=case.get("channels", 1), bitrate=case.get("bitrate", "16k"), seconds=case.get("seconds", 0.8))
        meta.update({"encodeOk": ok, "encodeError": err})
        shutil.copyfile(tmp, audio)
    else:
        ok, err = make_mp3(audio, sample_rate=case.get("sample_rate", 16000), channels=case.get("channels", 1), bitrate=case.get("bitrate", "16k"), seconds=case.get("seconds", 0.8))
        meta.update({"encodeOk": ok, "encodeError": err})
        if not ok:
            make_wav(audio, sample_rate=case.get("sample_rate", 16000), channels=case.get("channels", 1), seconds=0.4)
            meta["fallback"] = "wav-content-with-requested-extension"

    row_values = case.get("row_values")
    if row_values and row_values.get("音频描述") is None:
        row_values = {**row_values, "音频描述": audio.stem}
    make_xlsx(
        xlsx,
        audio_stem=case.get("mismatch_stem") or audio.stem,
        reply=f"AUTO_TEST_NEG_REPLY_{stamp}",
        rec_protocol="A5 FA 00 81 08 00 2B FB",
        columns=case.get("columns"),
        row_values=row_values,
    )
    return audio, xlsx, meta


def call_batch_import(session: requests.Session, audio: Optional[Path], xlsx: Optional[Path]) -> Dict[str, Any]:
    handles = []
    files = []
    try:
        if audio is not None:
            handle = audio.open("rb")
            handles.append(handle)
            ctype = "audio/mpeg" if audio.suffix.lower() == ".mp3" else "audio/wav" if audio.suffix.lower() == ".wav" else "application/octet-stream"
            files.append(("files", (audio.name, handle, ctype)))
        if xlsx is not None:
            handle = xlsx.open("rb")
            handles.append(handle)
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if xlsx.suffix.lower() in {".xlsx", ".xls"} else "text/plain"
            files.append(("files", (xlsx.name, handle, ctype)))
        response = session.post(BASE_URL + "/biz/audiofile/batchImport", files=files, timeout=120)
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {"raw": response.text[:2000]}
        return {"httpStatus": response.status_code, "body": body}
    finally:
        for handle in handles:
            handle.close()


def case_matrix() -> List[Dict[str, Any]]:
    return [
        {"id": "control_valid_16k_16kbps", "expect": "PASS", "desc": "合法基线：16K/mono/16kbps/mp3+xlsx"},
        {"id": "boundary_bitrate_8kbps", "expect": "PASS", "desc": "允许范围内低码率 8kbps", "bitrate": "8k"},
        {"id": "boundary_bitrate_32kbps", "expect": "PASS", "desc": "码率边界 32kbps", "bitrate": "32k"},
        {"id": "oversize_gt_20kb", "expect": "FAIL", "desc": "文件大小超过 20KB", "seconds": 12.0, "bitrate": "32k"},
        {"id": "sample_rate_32000", "expect": "FAIL", "desc": "采样率 32000Hz", "sample_rate": 32000},
        {"id": "sample_rate_48000", "expect": "FAIL", "desc": "采样率 48000Hz", "sample_rate": 48000},
        {"id": "sample_rate_64000_wav", "expect": "FAIL", "desc": "采样率 64000Hz WAV", "sample_rate": 64000, "wav": True, "ext": ".wav", "seconds": 0.05},
        {"id": "stereo_2_channels", "expect": "FAIL", "desc": "双声道 mp3", "channels": 2},
        {"id": "bitrate_40kbps", "expect": "FAIL", "desc": "码率 40kbps > 32kbps", "bitrate": "40k"},
        {"id": "bitrate_64kbps", "expect": "FAIL", "desc": "码率 64kbps > 32kbps", "bitrate": "64k"},
        {"id": "wav_extension_valid_wav_content", "expect": "FAIL", "desc": "音频后缀 wav", "wav": True, "ext": ".wav"},
        {"id": "txt_extension_mp3_content", "expect": "FAIL", "desc": "音频后缀 txt 但内容为 mp3", "ext": ".txt", "rename_from_mp3": True},
        {"id": "aac_extension_mp3_content", "expect": "FAIL", "desc": "音频后缀 aac 但内容为 mp3", "ext": ".aac", "rename_from_mp3": True},
        {"id": "corrupt_mp3", "expect": "FAIL", "desc": "mp3 内容损坏", "corrupt": True},
        {"id": "zero_size_mp3", "expect": "FAIL", "desc": "0 字节 mp3", "zero": True},
        {"id": "xlsx_audio_description_mismatch", "expect": "FAIL", "desc": "xlsx 音频描述与文件名不匹配", "mismatch_stem": "NOT_MATCH_AUDIO_NAME"},
        {"id": "xlsx_missing_reply_column", "expect": "FAIL", "desc": "xlsx 缺少 播报内容 列", "columns": ["音频描述", "接收协议"]},
        {"id": "xlsx_missing_audio_desc_column", "expect": "FAIL", "desc": "xlsx 缺少 音频描述 列", "columns": ["播报内容", "接收协议"]},
        {"id": "xlsx_missing_protocol_column", "expect": "FAIL", "desc": "xlsx 缺少 接收协议 列", "columns": ["播报内容", "音频描述"]},
        {"id": "xlsx_empty_reply", "expect": "FAIL", "desc": "播报内容为空", "row_values": {"播报内容": "", "音频描述": None, "接收协议": "A5 FA 00 81 08 00 2B FB"}},
        {"id": "xlsx_empty_audio_desc", "expect": "FAIL", "desc": "音频描述为空", "row_values": {"播报内容": "AUTO_TEST_EMPTY_AUDIO_DESC", "音频描述": "", "接收协议": "A5 FA 00 81 08 00 2B FB"}},
        {"id": "xlsx_empty_protocol", "expect": "FAIL", "desc": "接收协议为空", "row_values": {"播报内容": "AUTO_TEST_EMPTY_PROTOCOL", "音频描述": None, "接收协议": ""}},
        {"id": "xlsx_invalid_protocol", "expect": "FAIL", "desc": "接收协议非法", "row_values": {"播报内容": "AUTO_TEST_BAD_PROTOCOL", "音频描述": None, "接收协议": "NOT_HEX_PROTOCOL"}},
        {"id": "mapping_csv_extension", "expect": "FAIL", "desc": "映射文件后缀 csv", "xlsx_ext": ".csv"},
        {"id": "mapping_txt_extension", "expect": "FAIL", "desc": "映射文件后缀 txt", "xlsx_ext": ".txt"},
        {"id": "missing_xlsx", "expect": "FAIL", "desc": "只上传 mp3", "missing_xlsx": True},
        {"id": "missing_audio", "expect": "FAIL", "desc": "只上传 xlsx", "missing_audio": True},
    ]


def run_downstream_check(token: str, risk_rows: List[Dict[str, Any]], out_dir: Path) -> Dict[str, Any]:
    if not risk_rows:
        return {"summary": {"total": 0, "created": 0, "rejected": 0}, "results": []}

    client = ListenAISynthesisClient(token)
    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")
    options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
    option = next((item for item in options if item.get("board") == "CSK3021"), None) or options[0]
    version = (option.get("versionOptions") or [])[0]
    stamp = time.strftime("%Y%m%d%H%M%S")
    product_name = f"AUTO_TEST_NEG_DOWNSTREAM_{stamp}"
    require_ok(client.post_json("/biz/broadcast/add", {"name": product_name, "chipName": option.get("board") or "CSK3021", "defId": version.get("value"), "chipVersion": version.get("label")}), "product add")
    product = query_first_by_field(client, "/biz/broadcast/page", {"current": 1, "size": 50, "name": product_name}, "name", product_name)
    product_id = str(product.get("id"))
    release_ids: List[str] = []
    results: List[Dict[str, Any]] = []

    try:
        for row in risk_rows:
            data = (row.get("data") or [{}])[0] if isinstance(row.get("data"), list) else {}
            payload = build_broadcast_release_payload(product_id, voice_options, compress_options, stamp, auto_play=False, comments=f"AUTO_TEST_NEG_RELEASE_{row['id']}_{stamp}"[:120], play_rows=[data])
            response = client.post_json("/biz/broadcastrelease/add", payload)
            created = False
            release_id = None
            if response.get("code") == 200:
                release = query_first_by_field(client, "/biz/broadcastrelease/page", {"current": 1, "size": 100, "prodId": product_id}, "comments", payload["comments"])
                if release:
                    created = True
                    release_id = str(release.get("id"))
                    release_ids.append(release_id)
            results.append({"id": row["id"], "releaseAddCode": response.get("code"), "releaseAddMsg": response.get("msg"), "created": created, "releaseId": release_id, "importData": data})
    finally:
        delete_records(client, "/biz/broadcastrelease/delete", release_ids, [], "neg downstream release")
        delete_records(client, "/biz/broadcast/delete", [product_id], [], "neg downstream product")

    payload = {"summary": {"total": len(results), "created": sum(1 for item in results if item.get("created")), "rejected": sum(1 for item in results if not item.get("created"))}, "product": {"id": product_id, "name": product_name}, "results": results}
    (out_dir / "negative_downstream_release_add_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def write_report(out_dir: Path, payload: Dict[str, Any], downstream: Dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# 播报合成批量导入异常矩阵验证", "",
        f"时间：{payload['createdAt']}", "", "## 结论", "",
        "- 口径：本脚本直接调用 `/biz/audiofile/batchImport`，属于后端/API 健壮性探测；不能单独作为浏览器 UI 上传结论。",
        f"- 总用例：{summary['total']}",
        f"- 符合预期：{summary['expectedMatched']}",
        f"- 预期拒绝但接口放行：{summary['unexpectedPass']}",
        f"- 预期通过但接口拒绝：{summary['unexpectedFail']}", "",
        "## 明细", "",
        "| 用例 | 场景 | 预期 | 实际 | 判定 | 返回 | 文件大小 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in payload["results"]:
        msg = str(result.get("msg") or "").replace("|", "\\|")
        lines.append(f"| {result['id']} | {result['desc']} | {result['expected']} | {result['actual']} | {result['verdict']} | code={result['code']} {msg} | {result['audioSize']} |")

    risks = [item for item in payload["results"] if item["verdict"] == "RISK_UNEXPECTED_PASS"]
    lines.extend(["", "## 风险项", ""])
    if risks:
        for item in risks:
            lines.append(f"- `{item['id']}`：{item['desc']}，按页面规则应拒绝，但接口返回 code=200；返回 data=`{json.dumps(item.get('data'), ensure_ascii=False)}`")
    else:
        lines.append("- 无")

    lines.extend([
        "", "## 下游播报版本创建复核", "",
        f"- 对 {downstream['summary']['total']} 个“预期拒绝但 batchImport 放行”的返回行继续调用 `/biz/broadcastrelease/add`。",
        f"- 结果：{downstream['summary']['created']}/{downstream['summary']['total']} 创建成功；复核用临时产品和版本已清理。",
        f"- 明细 JSON：`{out_dir / 'negative_downstream_release_add_result.json'}`",
        "", "## 产物", "",
        f"- JSON：`{out_dir / 'negative_batch_import_result.json'}`",
        f"- 上传构造目录：`{out_dir / 'uploads'}`",
    ])
    (out_dir / "negative_batch_import_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate negative cases for broadcast batchImport.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token; defaults to env/local config")
    parser.add_argument("--out-dir", default="", help="Output directory")
    parser.add_argument("--skip-downstream", action="store_true", help="Skip broadcastrelease/add downstream check")
    parser.add_argument("--no-persist-token", action="store_true", help="Do not write token back to TOOLS.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    stamp = time.strftime("%Y%m%d%H%M%S")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "synthesis-validation" / f"{stamp}-broadcast-batch-import-negative"
    upload_dir = out_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.verify = False
    session.headers.update({"token": token})

    results: List[Dict[str, Any]] = []
    for case in case_matrix():
        audio, xlsx, meta = create_case_files(upload_dir, stamp, case)
        response = call_batch_import(session, None if case.get("missing_audio") else audio, None if case.get("missing_xlsx") else xlsx)
        body = response.get("body") or {}
        code = body.get("code") if isinstance(body, dict) else None
        actual = "PASS" if code == 200 else "FAIL"
        verdict = "OK" if actual == case["expect"] else ("RISK_UNEXPECTED_PASS" if actual == "PASS" else "UNEXPECTED_FAIL")
        results.append({
            "id": case["id"], "desc": case["desc"], "expected": case["expect"], "actual": actual, "verdict": verdict,
            "httpStatus": response["httpStatus"], "code": code, "msg": body.get("msg") if isinstance(body, dict) else None,
            "data": body.get("data") if isinstance(body, dict) else body, "audio": str(audio), "audioSize": audio.stat().st_size if audio.exists() else 0,
            "audioProbe": ffprobe(audio), "xlsx": str(xlsx), "meta": meta,
        })

    summary = {
        "total": len(results),
        "expectedMatched": sum(1 for item in results if item["verdict"] == "OK"),
        "unexpectedPass": sum(1 for item in results if item["verdict"] == "RISK_UNEXPECTED_PASS"),
        "unexpectedFail": sum(1 for item in results if item["verdict"] == "UNEXPECTED_FAIL"),
    }
    payload = {"createdAt": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "results": results}
    (out_dir / "negative_batch_import_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    downstream = {"summary": {"total": 0, "created": 0, "rejected": 0}, "results": []} if args.skip_downstream else run_downstream_check(token, [item for item in results if item["verdict"] == "RISK_UNEXPECTED_PASS"], out_dir)
    write_report(out_dir, payload, downstream)
    print(str(out_dir))
    return 0 if summary["unexpectedFail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
