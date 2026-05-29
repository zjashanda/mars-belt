#!/usr/bin/env python3
"""Boundary and negative validation for synthesis import flows.

This suite complements the normal synthesis full-chain and broadcast batch
negative matrix. It focuses on Excel/table import parsing, invalid fields, row
count boundaries, text length boundaries, and direct preview synthesis bounds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import urllib3
from openpyxl import Workbook

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import (
    AUTO_PREFIX,
    BASE_URL,
    ListenAISynthesisClient,
    cleanup_auto_test_records,
    delete_records,
    dict_children,
    first_value,
    require_ok,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class Case:
    area: str
    case_id: str
    desc: str
    expected: str
    actual: str
    verdict: str
    code: Optional[int]
    msg: str = ""
    data: Any = None
    extra: Optional[Dict[str, Any]] = None


def run_process(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def make_mp3(path: Path, seconds: float = 0.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run_process(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:duration={seconds}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "16k",
            "-codec:a",
            "libmp3lame",
            "-write_xing",
            "0",
            "-id3v2_version",
            "0",
            "-y",
            str(path),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()}")


def write_xlsx(path: Path, headers: List[str], rows: List[List[Any]], *, title: str = "Sheet1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


def write_voice_xlsx(path: Path, rows: List[Tuple[Any, Any, Any]], headers: Optional[List[str]] = None) -> None:
    headers = headers or ["序号", "音频名称", "播报文本"]
    values = []
    key_to_idx = {"序号": 0, "音频名称": 1, "播报文本": 2}
    for row in rows:
        values.append([row[key_to_idx[h]] for h in headers])
    write_xlsx(path, headers, values, title="语音合成")


def write_broadcast_xlsx(path: Path, rows: List[Tuple[Any, Any, Any]], headers: Optional[List[str]] = None) -> None:
    headers = headers or ["播报内容", "音频描述", "接收协议"]
    values = []
    key_to_idx = {"播报内容": 0, "音频描述": 1, "接收协议": 2}
    for row in rows:
        values.append([row[key_to_idx[h]] for h in headers])
    write_xlsx(path, headers, values, title="播报映射")


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in {"token", "authorization"}}
    if isinstance(value, list):
        return [scrub(v) for v in value[:20]] + ([f"<truncated total={len(value)}>"] if len(value) > 20 else [])
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + f"...<len={len(value)}>"
    return value


def is_weak_error_message(code: Optional[int], msg: str) -> bool:
    text = str(msg or "").strip()
    if not text:
        return True
    generic_messages = {"服务器异常", "系统异常", "操作失败", "失败"}
    if text in generic_messages:
        return True
    # Validation failures should point to the bad field or bad file type instead of a generic 5xx.
    return code is None


def classify(expected: str, actual: str, code: Optional[int], msg: str) -> str:
    if expected == "DISCOVER":
        return f"OBSERVED_{actual}"
    if expected == "FAIL" and actual == "FAIL" and is_weak_error_message(code, msg):
        return "RISK_WEAK_ERROR_MESSAGE"
    if expected == actual:
        return "OK"
    if actual == "PASS":
        return "RISK_UNEXPECTED_PASS"
    return "UNEXPECTED_FAIL"


def to_case(area: str, case_id: str, desc: str, expected: str, response: Dict[str, Any], *, extra: Optional[Dict[str, Any]] = None) -> Case:
    body = response.get("body") or {}
    code = body.get("code") if isinstance(body, dict) else None
    msg = str(body.get("msg") or "") if isinstance(body, dict) else ""
    actual = "PASS" if code == 200 else "FAIL"
    return Case(area, case_id, desc, expected, actual, classify(expected, actual, code, msg), code, msg, scrub(body.get("data") if isinstance(body, dict) else body), extra or {})


def post_multipart(session: requests.Session, path: str, files: List[Tuple[str, Tuple[str, Any, str]]]) -> Dict[str, Any]:
    response = session.post(BASE_URL + path, files=files, timeout=180)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {"raw": response.text[:2000]}
    return {"httpStatus": response.status_code, "body": body}


def post_json(session: requests.Session, path: str, payload: Any, timeout: int = 180) -> Dict[str, Any]:
    response = session.post(BASE_URL + path, json=payload, timeout=timeout)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {"raw": response.text[:2000]}
    return {"httpStatus": response.status_code, "body": body}


def call_import_rows(session: requests.Session, xlsx: Path) -> Dict[str, Any]:
    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if xlsx.suffix.lower() in {".xlsx", ".xls"} else "text/plain"
    with xlsx.open("rb") as fp:
        return post_multipart(session, "/fw/common/importRows", [("file", (xlsx.name, fp, ctype))])


def call_broadcast_batch_import(session: requests.Session, audio_files: Iterable[Path], xlsx: Path) -> Dict[str, Any]:
    handles = []
    files: List[Tuple[str, Tuple[str, Any, str]]] = []
    try:
        for audio in audio_files:
            fp = audio.open("rb")
            handles.append(fp)
            files.append(("files", (audio.name, fp, "audio/mpeg" if audio.suffix.lower() == ".mp3" else "application/octet-stream")))
        ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if xlsx.suffix.lower() in {".xlsx", ".xls"} else "text/plain"
        xfp = xlsx.open("rb")
        handles.append(xfp)
        files.append(("files", (xlsx.name, xfp, ctype)))
        return post_multipart(session, "/biz/audiofile/batchImport", files)
    finally:
        for handle in handles:
            handle.close()


def prepare_broadcast_audio_files(upload_dir: Path, rows: List[Tuple[Any, Any, Any]]) -> List[Path]:
    base = upload_dir / "broadcast_base.mp3"
    if not base.exists():
        make_mp3(base)
    paths = []
    for _, audio_desc, _ in rows:
        stem = str(audio_desc or "empty_audio_desc")
        safe_stem = stem.replace("/", "_").replace("\\", "_").strip() or "empty_audio_desc"
        if len(f"{safe_stem}.mp3".encode("utf-8")) > 240:
            digest = hashlib.sha1(safe_stem.encode("utf-8")).hexdigest()[:12]
            safe_stem = f"{safe_stem[:120]}_{digest}"
        path = upload_dir / f"{safe_stem}.mp3"
        shutil.copyfile(base, path)
        paths.append(path)
    return paths


def voice_import_cases(upload_dir: Path) -> List[Dict[str, Any]]:
    def row(idx: Any = 1, name: Any = "valid_name", text: Any = "合成文本") -> Tuple[Any, Any, Any]:
        return idx, name, text

    cases: List[Dict[str, Any]] = [
        {"id": "voice_valid_1_row", "expect": "PASS", "desc": "音频合成导入合法 1 行", "rows": [row()]},
        {"id": "voice_valid_10_rows", "expect": "PASS", "desc": "音频合成导入合法 10 行", "rows": [row(i, f"valid_{i}", f"合成文本{i}") for i in range(1, 11)]},
        {"id": "voice_empty_table", "expect": "FAIL", "desc": "仅表头无数据", "rows": []},
        {"id": "voice_missing_name_column", "expect": "FAIL", "desc": "缺少音频名称列", "headers": ["序号", "播报文本"], "rows": [row()]},
        {"id": "voice_missing_text_column", "expect": "FAIL", "desc": "缺少播报文本列", "headers": ["序号", "音频名称"], "rows": [row()]},
        {"id": "voice_missing_idx_column", "expect": "FAIL", "desc": "缺少序号列", "headers": ["音频名称", "播报文本"], "rows": [row()]},
        {"id": "voice_empty_name", "expect": "FAIL", "desc": "音频名称为空", "rows": [row(1, "", "合成文本")]},
        {"id": "voice_empty_text", "expect": "FAIL", "desc": "播报文本为空", "rows": [row(1, "empty_text", "")]},
        {"id": "voice_space_name", "expect": "FAIL", "desc": "音频名称仅空格", "rows": [row(1, "   ", "合成文本")]},
        {"id": "voice_space_text", "expect": "FAIL", "desc": "播报文本仅空格", "rows": [row(1, "space_text", "   ")]},
        {"id": "voice_invalid_idx_text", "expect": "FAIL", "desc": "序号非数字", "rows": [row("abc", "bad_idx", "合成文本")]},
        {"id": "voice_duplicate_name", "expect": "FAIL", "desc": "音频名称重复", "rows": [row(1, "dup", "文本1"), row(2, "dup", "文本2")]},
        {"id": "voice_duplicate_idx", "expect": "FAIL", "desc": "序号重复", "rows": [row(1, "dup_idx_1", "文本1"), row(1, "dup_idx_2", "文本2")]},
        {"id": "voice_filename_len_128", "expect": "DISCOVER", "desc": "音频名称 128 字符边界探测", "rows": [row(1, "n" * 128, "合成文本")]},
        {"id": "voice_filename_len_256", "expect": "DISCOVER", "desc": "音频名称 256 字符边界探测", "rows": [row(1, "n" * 256, "合成文本")]},
        {"id": "voice_filename_illegal_chars", "expect": "FAIL", "desc": "音频名称含文件非法字符", "rows": [row(1, "bad/name:*?", "合成文本")]},
        {"id": "voice_text_len_1", "expect": "PASS", "desc": "单条文本 1 字", "rows": [row(1, "len1", "开")]},
        {"id": "voice_text_len_200", "expect": "DISCOVER", "desc": "单条文本 200 字边界探测", "rows": [row(1, "len200", "测" * 200)]},
        {"id": "voice_text_len_500", "expect": "DISCOVER", "desc": "单条文本 500 字边界探测", "rows": [row(1, "len500", "测" * 500)]},
        {"id": "voice_text_len_1000", "expect": "DISCOVER", "desc": "单条文本 1000 字边界探测", "rows": [row(1, "len1000", "测" * 1000)]},
        {"id": "voice_text_len_2000", "expect": "DISCOVER", "desc": "单条文本 2000 字边界探测", "rows": [row(1, "len2000", "测" * 2000)]},
        {"id": "voice_text_len_5000", "expect": "DISCOVER", "desc": "单条文本 5000 字边界探测", "rows": [row(1, "len5000", "测" * 5000)]},
        {"id": "voice_rows_50", "expect": "DISCOVER", "desc": "导入 50 行条数边界探测", "rows": [row(i, f"r50_{i}", "合成文本") for i in range(1, 51)]},
        {"id": "voice_rows_100", "expect": "DISCOVER", "desc": "导入 100 行条数边界探测", "rows": [row(i, f"r100_{i}", "合成文本") for i in range(1, 101)]},
        {"id": "voice_rows_200", "expect": "DISCOVER", "desc": "导入 200 行条数边界探测", "rows": [row(i, f"r200_{i}", "合成文本") for i in range(1, 201)]},
        {"id": "voice_rows_500", "expect": "DISCOVER", "desc": "导入 500 行条数边界探测", "rows": [row(i, f"r500_{i}", "合成文本") for i in range(1, 501)]},
        {"id": "voice_rows_1000", "expect": "DISCOVER", "desc": "导入 1000 行条数边界探测", "rows": [row(i, f"r1000_{i}", "合成文本") for i in range(1, 1001)]},
    ]
    corrupt = upload_dir / "voice_corrupt.xlsx"
    corrupt.write_bytes(os.urandom(512))
    cases.append({"id": "voice_corrupt_xlsx", "expect": "FAIL", "desc": "损坏 xlsx", "path": corrupt})
    csv = upload_dir / "voice_mapping.csv"
    csv.write_text("序号,音频名称,播报文本\n1,csv_name,合成文本\n", encoding="utf-8")
    cases.append({"id": "voice_csv_extension", "expect": "FAIL", "desc": "csv 后缀映射文件", "path": csv})
    return cases


def broadcast_import_cases(upload_dir: Path) -> List[Dict[str, Any]]:
    def row(i: int = 1, reply: Any = "AUTO_TEST_REPLY", desc: Optional[str] = None, proto: Any = "A5 FA 00 81 08 00 2B FB") -> Tuple[Any, Any, Any]:
        return reply, desc or f"broadcast_audio_{i}", proto

    cases: List[Dict[str, Any]] = [
        {"id": "broadcast_valid_1_row", "expect": "PASS", "desc": "播报批量导入合法 1 行", "rows": [row()]},
        {"id": "broadcast_valid_10_rows", "expect": "PASS", "desc": "播报批量导入合法 10 行", "rows": [row(i, f"AUTO_TEST_REPLY_{i}") for i in range(1, 11)]},
        {"id": "broadcast_rows_50", "expect": "DISCOVER", "desc": "播报导入 50 行条数边界探测", "rows": [row(i, f"AUTO_TEST_REPLY_{i}") for i in range(1, 51)]},
        {"id": "broadcast_rows_100", "expect": "DISCOVER", "desc": "播报导入 100 行条数边界探测", "rows": [row(i, f"AUTO_TEST_REPLY_{i}") for i in range(1, 101)]},
        {"id": "broadcast_rows_200", "expect": "DISCOVER", "desc": "播报导入 200 行条数边界探测", "rows": [row(i, f"AUTO_TEST_REPLY_{i}") for i in range(1, 201)]},
        {"id": "broadcast_rows_500", "expect": "DISCOVER", "desc": "播报导入 500 行条数边界探测", "rows": [row(i, f"AUTO_TEST_REPLY_{i}") for i in range(1, 501)]},
        {"id": "broadcast_reply_len_1", "expect": "PASS", "desc": "播报内容 1 字", "rows": [row(1, "开")]},
        {"id": "broadcast_reply_len_100", "expect": "DISCOVER", "desc": "播报内容 100 字边界探测", "rows": [row(1, "播" * 100)]},
        {"id": "broadcast_reply_len_500", "expect": "DISCOVER", "desc": "播报内容 500 字边界探测", "rows": [row(1, "播" * 500)]},
        {"id": "broadcast_reply_len_1000", "expect": "DISCOVER", "desc": "播报内容 1000 字边界探测", "rows": [row(1, "播" * 1000)]},
        {"id": "broadcast_audio_desc_len_200", "expect": "DISCOVER", "desc": "音频描述 200 字边界探测", "rows": [row(1, "AUTO_TEST_REPLY", "a" * 200)]},
        {"id": "broadcast_audio_desc_len_500", "expect": "FAIL", "desc": "音频描述 500 字超出常规文件名长度，应拒绝或提示音频名不匹配", "rows": [row(1, "AUTO_TEST_REPLY", "a" * 500)]},
        {"id": "broadcast_space_reply", "expect": "FAIL", "desc": "播报内容仅空格", "rows": [row(1, "   ")]},
        {"id": "broadcast_space_audio_desc", "expect": "FAIL", "desc": "音频描述仅空格", "rows": [row(1, "AUTO_TEST_REPLY", "   ")]},
        {"id": "broadcast_empty_table", "expect": "FAIL", "desc": "仅表头无数据", "rows": []},
        {"id": "broadcast_duplicate_audio_desc", "expect": "FAIL", "desc": "音频描述重复", "rows": [row(1, "R1", "dup_desc"), row(2, "R2", "dup_desc")]},
        {"id": "broadcast_duplicate_protocol", "expect": "DISCOVER", "desc": "接收协议重复边界探测", "rows": [row(1, "R1", "dup_proto_1"), row(2, "R2", "dup_proto_2")]},
    ]
    return cases


def generate_audio_cases(voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], *, include_api_probe: bool = False) -> List[Dict[str, Any]]:
    vcn = first_value(voice_options, "x2_xiaoye")
    compress = first_value(compress_options, "2")

    def payload(text: str, *, speed: int = 50, vol: int = 70, current_vcn: Optional[str] = None) -> Dict[str, Any]:
        return {"text": text, "word": text, "vcn": current_vcn or vcn, "speed": speed, "vol": vol, "compress": compress}

    return [
        {"id": "generate_text_len_1", "expect": "PASS", "desc": "试听合成 1 字", "payload": payload("开")},
        {"id": "generate_empty_text", "expect": "FAIL", "desc": "试听合成空文本", "payload": payload("")},
        {"id": "generate_text_len_200", "expect": "DISCOVER", "desc": "试听合成 200 字边界探测", "payload": payload("测" * 200)},
        {"id": "generate_text_len_500", "expect": "DISCOVER", "desc": "试听合成 500 字边界探测", "payload": payload("测" * 500)},
        {"id": "generate_text_len_1000", "expect": "DISCOVER", "desc": "试听合成 1000 字边界探测", "payload": payload("测" * 1000)},
        {"id": "generate_text_len_2000", "expect": "DISCOVER", "desc": "试听合成 2000 字边界探测", "payload": payload("测" * 2000)},
        {"id": "generate_speed_1", "expect": "PASS", "desc": "语速下边界 1", "payload": payload("语速下边界", speed=1)},
        {"id": "generate_speed_100", "expect": "PASS", "desc": "语速上边界 100", "payload": payload("语速上边界", speed=100)},
        {"id": "generate_speed_negative", "expect": "FAIL", "desc": "语速负数", "payload": payload("语速非法", speed=-1)},
        {"id": "generate_speed_0", "expect": "FAIL", "desc": "语速低于前端下限 0", "payload": payload("语速非法", speed=0)},
        {"id": "generate_speed_101", "expect": "FAIL", "desc": "语速高于前端上限 101", "payload": payload("语速非法", speed=101)},
        {"id": "generate_speed_999", "expect": "FAIL", "desc": "语速远高于上限 999", "payload": payload("语速非法", speed=999)},
        {"id": "generate_vol_1", "expect": "PASS", "desc": "音量下边界 1", "payload": payload("音量下边界", vol=1)},
        {"id": "generate_vol_100", "expect": "PASS", "desc": "音量上边界 100", "payload": payload("音量上边界", vol=100)},
        {"id": "generate_vol_negative", "expect": "FAIL", "desc": "音量负数", "payload": payload("音量非法", vol=-1)},
        {"id": "generate_vol_0", "expect": "FAIL", "desc": "音量低于前端下限 0", "payload": payload("音量非法", vol=0)},
        {"id": "generate_vol_101", "expect": "FAIL", "desc": "音量高于前端上限 101", "payload": payload("音量非法", vol=101)},
        {"id": "generate_vol_999", "expect": "FAIL", "desc": "音量远高于上限 999", "payload": payload("音量非法", vol=999)},
    ]
    if include_api_probe:
        cases.append({"id": "generate_invalid_vcn", "expect": "FAIL", "desc": "非法发音人（API 健壮性探测，非 UI 可选项）", "payload": payload("非法发音人", current_vcn="AUTO_TEST_INVALID_VCN")})
    return cases


def run_voice_import_matrix(session: requests.Session, out_dir: Path) -> List[Case]:
    upload_dir = out_dir / "uploads" / "voice_import"
    upload_dir.mkdir(parents=True, exist_ok=True)
    results: List[Case] = []
    for case in voice_import_cases(upload_dir):
        path = case.get("path")
        if not path:
            path = upload_dir / f"{case['id']}.xlsx"
            write_voice_xlsx(path, case.get("rows", []), case.get("headers"))
        response = call_import_rows(session, Path(path))
        body = response.get("body") or {}
        returned_rows = body.get("data") if isinstance(body, dict) else None
        extra = {"path": str(path), "returnedRows": len(returned_rows) if isinstance(returned_rows, list) else None}
        results.append(to_case("音频合成导入表", case["id"], case["desc"], case["expect"], response, extra=extra))
    return results


def run_broadcast_import_matrix(session: requests.Session, out_dir: Path) -> List[Case]:
    upload_dir = out_dir / "uploads" / "broadcast_import"
    upload_dir.mkdir(parents=True, exist_ok=True)
    results: List[Case] = []
    for case in broadcast_import_cases(upload_dir):
        xlsx = upload_dir / f"{case['id']}.xlsx"
        rows = case.get("rows", [])
        write_broadcast_xlsx(xlsx, rows, case.get("headers"))
        audio_files = prepare_broadcast_audio_files(upload_dir / case["id"], rows)
        response = call_broadcast_batch_import(session, audio_files, xlsx)
        body = response.get("body") or {}
        returned_rows = body.get("data") if isinstance(body, dict) else None
        extra = {"path": str(xlsx), "audioCount": len(audio_files), "returnedRows": len(returned_rows) if isinstance(returned_rows, list) else None}
        results.append(to_case("播报合成导入表", case["id"], case["desc"], case["expect"], response, extra=extra))
    return results


def run_generate_audio_matrix(session: requests.Session, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], *, include_api_probe: bool = False) -> List[Case]:
    results: List[Case] = []
    for case in generate_audio_cases(voice_options, compress_options, include_api_probe=include_api_probe):
        response = post_json(session, "/fw/common/generateAudio", case["payload"], timeout=180)
        area = "试听合成接口健壮性探测" if case["id"] == "generate_invalid_vcn" else "试听合成边界"
        results.append(to_case(area, case["id"], case["desc"], case["expect"], response, extra={"payload": scrub(case["payload"])}))
    return results


def run_aux_endpoint_probe(session: requests.Session, batch_rows: List[Dict[str, Any]]) -> List[Case]:
    """Probe undocumented audiofile helper endpoints with plausible payloads."""
    results: List[Case] = []
    payload_variants = [
        ("list_payload", batch_rows),
        ("items_payload", {"items": batch_rows}),
        ("rows_payload", {"rows": batch_rows}),
    ]
    for suffix, payload in payload_variants:
        response = post_json(session, "/biz/audiofile/batchImportItems", payload)
        results.append(to_case("播报辅助接口探测", f"batchImportItems_{suffix}", f"batchImportItems 参数形态探测：{suffix}", "DISCOVER", response, extra={"payload": scrub(payload)}))
        if (response.get("body") or {}).get("code") == 200:
            break
    for suffix, payload in payload_variants:
        response = post_json(session, "/biz/audiofile/validate", payload)
        results.append(to_case("播报辅助接口探测", f"audiofile_validate_{suffix}", f"audiofile validate 参数形态探测：{suffix}", "DISCOVER", response, extra={"payload": scrub(payload)}))
        if (response.get("body") or {}).get("code") == 200:
            break
    return results


def cleanup_records(client: ListenAISynthesisClient, out_dir: Path, *, enabled: bool) -> Dict[str, Any]:
    if not enabled:
        payload = {"skipped": True, "reason": "AUTO_TEST global cleanup disabled; boundary import cases do not need platform cleanup."}
        (out_dir / "cleanup_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    steps = []
    cleanup_auto_test_records(client, steps, "boundary final")
    payload = {"steps": [step.__dict__ for step in steps]}
    (out_dir / "cleanup_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def summarize(results: List[Case]) -> Dict[str, Any]:
    verdict_keys = {
        "OK": "ok",
        "RISK_UNEXPECTED_PASS": "riskUnexpectedPass",
        "RISK_WEAK_ERROR_MESSAGE": "riskWeakErrorMessage",
        "UNEXPECTED_FAIL": "unexpectedFail",
        "OBSERVED_PASS": "observedPass",
        "OBSERVED_FAIL": "observedFail",
    }
    summary = {
        "total": len(results),
        "ok": sum(1 for item in results if item.verdict == "OK"),
        "riskUnexpectedPass": sum(1 for item in results if item.verdict == "RISK_UNEXPECTED_PASS"),
        "riskWeakErrorMessage": sum(1 for item in results if item.verdict == "RISK_WEAK_ERROR_MESSAGE"),
        "unexpectedFail": sum(1 for item in results if item.verdict == "UNEXPECTED_FAIL"),
        "observedPass": sum(1 for item in results if item.verdict == "OBSERVED_PASS"),
        "observedFail": sum(1 for item in results if item.verdict == "OBSERVED_FAIL"),
    }
    by_area: Dict[str, Dict[str, int]] = {}
    for item in results:
        area = by_area.setdefault(
            item.area,
            {"total": 0, "ok": 0, "riskUnexpectedPass": 0, "riskWeakErrorMessage": 0, "unexpectedFail": 0, "observedPass": 0, "observedFail": 0},
        )
        area["total"] += 1
        key = verdict_keys.get(item.verdict)
        if key:
            area[key] += 1
    return {"summary": summary, "byArea": by_area}


def write_reports(out_dir: Path, results: List[Case], cleanup: Dict[str, Any]) -> None:
    data = {
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        **summarize(results),
        "results": [
            {
                "area": item.area,
                "id": item.case_id,
                "desc": item.desc,
                "expected": item.expected,
                "actual": item.actual,
                "verdict": item.verdict,
                "code": item.code,
                "msg": item.msg,
                "data": scrub(item.data),
                "extra": scrub(item.extra or {}),
            }
            for item in results
        ],
        "cleanup": cleanup,
    }
    (out_dir / "synthesis_import_boundary_result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 合成导入与边界异常专项验证",
        "",
        f"时间：{data['createdAt']}",
        "",
        "## 汇总",
        "",
        f"- 总用例：{data['summary']['total']}",
        f"- 符合明确预期：{data['summary']['ok']}",
        f"- 预期拒绝但放行风险：{data['summary']['riskUnexpectedPass']}",
        f"- 拒绝但错误信息不合格：{data['summary']['riskWeakErrorMessage']}",
        f"- 预期通过但失败：{data['summary']['unexpectedFail']}",
        f"- 探测通过：{data['summary']['observedPass']}",
        f"- 探测失败：{data['summary']['observedFail']}",
        "",
        "## 分区结果",
        "",
        "| 区域 | 总数 | OK | 风险放行 | 弱错误信息 | 异常失败 | 探测通过 | 探测失败 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for area, stats in data["byArea"].items():
        lines.append(
            f"| {area} | {stats['total']} | {stats['ok']} | {stats['riskUnexpectedPass']} | {stats['riskWeakErrorMessage']} | {stats['unexpectedFail']} | {stats['observedPass']} | {stats['observedFail']} |"
        )

    lines.extend(["", "## 风险与异常明细", ""])
    flagged = [item for item in data["results"] if item["verdict"] in {"RISK_UNEXPECTED_PASS", "RISK_WEAK_ERROR_MESSAGE", "UNEXPECTED_FAIL", "OBSERVED_FAIL"}]
    if flagged:
        lines.extend(["| 区域 | 用例 | 预期 | 实际 | 判定 | 返回 |", "| --- | --- | --- | --- | --- | --- |"])
        for item in flagged:
            msg = str(item.get("msg") or "").replace("|", "/")
            lines.append(f"| {item['area']} | `{item['id']}` {item['desc']} | {item['expected']} | {item['actual']} | {item['verdict']} | code={item['code']} {msg} |")
    else:
        lines.append("- 无")

    lines.extend(["", "## 边界探测明细", ""])
    observed = [item for item in data["results"] if item["expected"] == "DISCOVER"]
    if observed:
        lines.extend(["| 区域 | 用例 | 判定 | 返回 | 备注 |", "| --- | --- | --- | --- | --- |"])
        for item in observed:
            extra = item.get("extra") or {}
            notes = []
            if extra.get("returnedRows") is not None:
                notes.append(f"returnedRows={extra['returnedRows']}")
            if extra.get("audioCount") is not None:
                notes.append(f"audioCount={extra['audioCount']}")
            msg = str(item.get("msg") or "").replace("|", "/")
            lines.append(f"| {item['area']} | `{item['id']}` {item['desc']} | {item['verdict']} | code={item['code']} {msg} | {'; '.join(notes)} |")
    else:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 产物",
            "",
            f"- JSON：`{out_dir / 'synthesis_import_boundary_result.json'}`",
            f"- 上传构造目录：`{out_dir / 'uploads'}`",
            f"- 清理记录：`{out_dir / 'cleanup_result.json'}`",
        ]
    )
    (out_dir / "synthesis_import_boundary_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate synthesis import negative/boundary cases.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--no-persist-token", action="store_true")
    parser.add_argument("--include-api-probe", action="store_true", help="Also run non-UI API robustness probes, e.g. invalid enum values.")
    parser.add_argument("--cleanup-auto-test", action="store_true", help="Delete existing AUTO_TEST_* platform records after the run. Default is off to preserve evidence.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    if not token:
        raise SystemExit("Missing LISTENAI token")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{stamp}-synthesis-import-boundary"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.verify = False
    session.headers.update({"token": token})
    client = ListenAISynthesisClient(token)

    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")

    results: List[Case] = []
    results.extend(run_voice_import_matrix(session, out_dir))
    results.extend(run_broadcast_import_matrix(session, out_dir))
    results.extend(run_generate_audio_matrix(session, voice_options, compress_options, include_api_probe=args.include_api_probe))

    # Use a known-good broadcast import row for undocumented helper endpoint probing.
    good_rows = [item for item in results if item.area == "播报合成导入表" and item.case_id == "broadcast_valid_1_row" and item.actual == "PASS"]
    batch_data = []
    if good_rows and isinstance(good_rows[0].data, list):
        batch_data = [row for row in good_rows[0].data if isinstance(row, dict)]
    results.extend(run_aux_endpoint_probe(session, batch_data))

    cleanup = cleanup_records(client, out_dir, enabled=args.cleanup_auto_test)
    write_reports(out_dir, results, cleanup)
    print(str(out_dir))
    summary = summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["summary"]["unexpectedFail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
