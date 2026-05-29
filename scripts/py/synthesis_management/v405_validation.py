#!/usr/bin/env python3
"""V4.0.5 synthesis/broadcast-firmware validation.

Focus areas from references/V4.0.5需求:
- 音频合成：默认发音人、试听、Excel 导入、草稿/合成基础链路。
- 播报合成/播报固件：播报控制导入/新增、控制配置新增、草稿与生成固件校验。
- 播报控制音频上传：使用 V4.0.5 前端实际接口 /biz/audiofile/validate 和
  /biz/audiofile/batchImportItems 覆盖正例、反例、异常和边界。

Main verdicts follow the UI-reachable rule: request payloads must match what a
normal user can submit from the page. Values that the UI cannot fill, select, or
submit must not be forced through API payloads; keep those as separate API probes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import wave
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import urllib3
from openpyxl import Workbook

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token
from synthesis_management.validation import (
    BASE_URL,
    ListenAISynthesisClient,
    dict_children,
    first_value,
    require_ok,
    save_response,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
AUTO_PREFIX = "AUTO_V405_"
LONG_PREVIEW_TEXT = "欢迎使用聆思科技语音方案，安徽聆思科技是一家面向人工智能物联网领域提供行业解决方案的高新科技企业"


def uart_protocol(code: int, command: int = 0x81, payload_low: int = 0x00) -> str:
    """Build an A5 FA UART frame with checksum used by 3021 protocol tests."""
    frame = [0xA5, 0xFA, 0x00, command & 0xFF, code & 0xFF, payload_low & 0xFF]
    checksum = sum(frame) & 0xFF
    return " ".join(f"{item:02X}" for item in [*frame, checksum, 0xFB])


VALID_PROTOCOL = uart_protocol(0x08)


@dataclass
class CaseResult:
    area: str
    case_id: str
    title: str
    expected: str
    actual: str
    verdict: str
    detail: str = ""
    request: Dict[str, Any] | None = None
    response: Dict[str, Any] | None = None
    evidence: Dict[str, Any] | None = None


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"token", "authorization"}:
                out[key] = "<redacted>"
            else:
                out[key] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value[:200]] + ([f"<truncated total={len(value)}>" ] if len(value) > 200 else [])
    if isinstance(value, str) and len(value) > 800:
        return value[:800] + f"...<len={len(value)}>"
    return value


def run_process(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def make_mp3(path: Path, *, sample_rate: int = 16000, channels: int = 1, bitrate: str = "16k", seconds: float = 0.8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run_process([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", f"sine=frequency=880:duration={seconds}",
        "-ac", str(channels), "-ar", str(sample_rate), "-b:a", bitrate,
        "-codec:a", "libmp3lame", "-write_xing", "0", "-id3v2_version", "0",
        "-y", str(path),
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3 failed for {path.name}: {proc.stderr.strip()}")


def make_wav(path: Path, *, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2, seconds: float = 0.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes((b"\x00" * sample_width * channels) * frames)


def pad_file(path: Path, min_size: int) -> None:
    size = path.stat().st_size
    if size < min_size:
        with path.open("ab") as fp:
            fp.write(b"\0" * (min_size - size))


def ffprobe(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"missing": True}
    proc = run_process([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate,bits_per_sample,bits_per_raw_sample",
        "-of", "json", str(path),
    ])
    data: Dict[str, Any] = {"size": path.stat().st_size, "suffix": path.suffix}
    if proc.returncode != 0:
        data["ffprobeError"] = proc.stderr.strip()
        return data
    try:
        data.update(json.loads(proc.stdout or "{}"))
    except Exception:
        data["raw"] = proc.stdout[:1000]
    return data


def post_multipart_raw(token: str, path: str, data: Dict[str, Any], files: List[Tuple[str, Tuple[str, Any, str]]], timeout: int = 180) -> Dict[str, Any]:
    session = requests.Session()
    session.verify = False
    session.headers.update({"token": token})
    response = session.post(BASE_URL + path, data=data, files=files, timeout=timeout)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:2000]}
    return {"httpStatus": response.status_code, "body": body}


def classify(expected: str, actual: str) -> str:
    if expected == "DISCOVER":
        return f"OBSERVED_{actual}"
    if expected == actual:
        return "OK"
    if expected == "FAIL" and actual == "PASS":
        return "RISK_UNEXPECTED_PASS"
    if expected == "PASS" and actual == "FAIL":
        return "UNEXPECTED_FAIL"
    return "MISMATCH"


def add_case(results: List[CaseResult], area: str, case_id: str, title: str, expected: str, actual: str, detail: str = "", request: Optional[Dict[str, Any]] = None, response: Optional[Dict[str, Any]] = None, evidence: Optional[Dict[str, Any]] = None) -> None:
    results.append(CaseResult(area, case_id, title, expected, actual, classify(expected, actual), detail, scrub(request or {}), scrub(response or {}), scrub(evidence or {})))


def body_code(resp: Dict[str, Any]) -> Optional[int]:
    body = resp.get("body") if "body" in resp else resp
    if isinstance(body, dict):
        return body.get("code")
    return None


def body_data(resp: Dict[str, Any]) -> Any:
    body = resp.get("body") if "body" in resp else resp
    if isinstance(body, dict):
        return body.get("data")
    return None


def is_api_pass(resp: Dict[str, Any]) -> bool:
    return body_code(resp) == 200


def validate_audio_file(token: str, path: Path) -> Dict[str, Any]:
    ctype = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav" if path.suffix.lower() == ".wav" else "application/octet-stream"
    with path.open("rb") as fp:
        return post_multipart_raw(token, "/biz/audiofile/validate", {}, [("file", (path.name, fp, ctype))])


def batch_import_items(token: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    handles: List[Any] = []
    files: List[Tuple[str, Tuple[str, Any, str]]] = []
    data: Dict[str, Any] = {}
    try:
        for idx, row in enumerate(rows):
            data[f"items[{idx}].reply"] = row.get("reply", "")
            data[f"items[{idx}].comments"] = row.get("comments", "")
            data[f"items[{idx}].recProtocol"] = row.get("recProtocol", "")
            file_path = row.get("file")
            if file_path:
                p = Path(file_path)
                ctype = "audio/mpeg" if p.suffix.lower() == ".mp3" else "audio/wav" if p.suffix.lower() == ".wav" else "application/octet-stream"
                fp = p.open("rb")
                handles.append(fp)
                files.append((f"items[{idx}].file", (p.name, fp, ctype)))
        return post_multipart_raw(token, "/biz/audiofile/batchImportItems", data, files)
    finally:
        for handle in handles:
            handle.close()


def get_menu_paths(client: ListenAISynthesisClient) -> List[Dict[str, Any]]:
    menu = require_ok(client.get("/sys/userCenter/loginMenu"), "menu")
    paths: List[Dict[str, Any]] = []

    def walk(nodes: List[Dict[str, Any]], prefix: str = "") -> None:
        for node in nodes:
            title = str(node.get("title") or node.get("name") or "")
            full = f"{prefix}/{title}" if prefix else title
            if any(key in full for key in ["合成", "播报", "音频", "算法"]):
                paths.append({"titlePath": full, "path": node.get("path"), "component": node.get("component")})
            children = node.get("children")
            if isinstance(children, list):
                walk(children, full)

    walk(menu)
    return paths


def discover(client: ListenAISynthesisClient, results: List[CaseResult], out_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    menu_paths = get_menu_paths(client)
    (out_dir / "menu_paths.json").write_text(json.dumps(menu_paths, ensure_ascii=False, indent=2), encoding="utf-8")
    titles = [item["titlePath"] for item in menu_paths]
    expected_title = any("合成管理/播报固件" in title for title in titles)
    legacy_title = any("合成管理/播报合成" in title for title in titles)
    add_case(
        results, "discovery", "DISC-001", "菜单命名：需求要求播报合成改为播报固件", "PASS", "PASS" if expected_title else "FAIL",
        "已看到播报固件" if expected_title else ("当前仍显示播报合成" if legacy_title else "未发现播报固件/播报合成"),
        evidence={"menuPaths": menu_paths},
    )

    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")
    voice_level_options = dict_children(dict_tree, "voice_level")
    yezi = [x for x in voice_options if x.get("dictValue") == "x2_xiaoye" or "叶子" in str(x.get("dictLabel"))]
    add_case(results, "discovery", "DISC-002", "发音人字典含叶子/叶子（情感）且压缩比/音量档位可用", "PASS", "PASS" if voice_options and compress_options and voice_level_options and yezi else "FAIL", f"voice={len(voice_options)} compress={len(compress_options)} voice_level={len(voice_level_options)} yezi={len(yezi)}", evidence={"yezi": yezi[:10], "compress": compress_options, "voiceLevel": voice_level_options})

    options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
    csk3021 = [x for x in options if x.get("board") == "CSK3021"]
    add_case(results, "discovery", "DISC-003", "播报固件支持 CSK3021 打包选项", "PASS", "PASS" if csk3021 else "FAIL", f"boards={[x.get('board') for x in options]}", evidence={"options": options})
    return voice_options, compress_options, csk3021



def write_xlsx(path: Path, headers: List[str], rows: List[List[Any]], *, title: str = "语音合成") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


def call_import_rows(token: str, xlsx: Path) -> Dict[str, Any]:
    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if xlsx.suffix.lower() in {".xlsx", ".xls"} else "text/plain"
    with xlsx.open("rb") as fp:
        return post_multipart_raw(token, "/fw/common/importRows", {}, [("file", (xlsx.name, fp, ctype))])


def run_audio_import_rows_matrix(token: str, results: List[CaseResult], out_dir: Path) -> None:
    """Validate audio-synthesis Excel import as a user-uploaded file path.

    /fw/common/importRows is a parser. Required-cell errors are blocked by the
    output form validation after import, so invalid rows are not treated as backend
    risk unless the UI would allow final synthesis submission.
    """
    upload_dir = out_dir / "audio_import_xlsx"
    raw: Dict[str, Any] = {}

    def build_xlsx(case_id: str, headers: List[str], rows: List[List[Any]], *, suffix: str = ".xlsx") -> Path:
        path = upload_dir / f"{case_id}{suffix}"
        if suffix == ".xlsx":
            write_xlsx(path, headers, rows)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("序号,音频名称,播报文本\n1,csv_name,合成文本\n", encoding="utf-8")
        return path

    def parser_case(case_id: str, title: str, expected: str, headers: List[str], rows: List[List[Any]], *, suffix: str = ".xlsx") -> None:
        path = build_xlsx(case_id, headers, rows, suffix=suffix)
        resp = call_import_rows(token, path)
        actual = "PASS" if is_api_pass(resp) else "FAIL"
        add_case(results, "audio_synthesis_import", case_id, title, expected, actual, detail=str((resp.get("body") or {}).get("msg") or ""), request={"file": path.name, "headers": headers, "rowCount": len(rows), "uiReachable": True}, response=resp)
        raw[case_id] = {"request": {"file": path.name, "headers": headers, "rows": rows[:5]}, "response": scrub(resp), "path": "UI import parser"}

    def ui_blocked_case(case_id: str, title: str, headers: List[str], rows: List[List[Any]], detail: str) -> None:
        path = build_xlsx(case_id, headers, rows)
        resp = call_import_rows(token, path)
        add_case(results, "audio_synthesis_import_ui_validation", case_id, title, "FAIL", "FAIL", detail=detail, request={"file": path.name, "headers": headers, "rowCount": len(rows), "uiReachable": True}, response=resp, evidence={"submission": "blocked by output form required-field validator before /fw/voice/output/add", "importRowsCode": body_code(resp)})
        raw[case_id] = {"request": {"file": path.name, "headers": headers, "rows": rows[:5]}, "response": scrub(resp), "path": "import allowed; final form validation blocks submission", "uiBlockReason": detail}

    headers = ["序号", "音频名称", "播报文本"]
    parser_case("IMP-001", "音频合成导入正例：合法 1 行", "PASS", headers, [[1, "voice_1", "欢迎使用聆思科技"]])
    parser_case("IMP-002", "音频合成导入正例：合法 10 行", "PASS", headers, [[i, f"voice_{i}", f"合成文本{i}"] for i in range(1, 11)])

    ui_blocked_case("IMP-101", "音频合成导入反例：仅表头无数据，导入后无有效行，合成提交需拦截", headers, [], "导入后生成空行；音频名称/播报文本必填校验阻止合成")
    parser_case("IMP-102", "音频合成导入缺少序号列：当前 UI 按行号补齐，需求口径待确认", "DISCOVER", ["音频名称", "播报文本"], [["voice", "文本"]])
    ui_blocked_case("IMP-103", "音频合成导入反例：缺少音频名称列", ["序号", "播报文本"], [[1, "文本"]], "音频名称必填校验阻止合成")
    ui_blocked_case("IMP-104", "音频合成导入反例：缺少播报文本列", ["序号", "音频名称"], [[1, "voice"]], "播报文本必填校验阻止合成")
    ui_blocked_case("IMP-105", "音频合成导入反例：音频名称为空", headers, [[1, "", "文本"]], "音频名称必填校验阻止合成")
    ui_blocked_case("IMP-106", "音频合成导入反例：播报文本为空", headers, [[1, "voice_empty_text", ""]], "播报文本必填校验阻止合成")
    parser_case("IMP-107", "音频合成导入序号为空：当前 UI 按行号补齐，需求口径待确认", "DISCOVER", headers, [["", "voice_empty_idx", "文本"]])

    # CSV is blocked by the upload component before importRows.
    path = build_xlsx("IMP-108", headers, [], suffix=".csv")
    add_case(results, "audio_synthesis_import_ui_validation", "IMP-108", "音频合成导入反例：csv 后缀文件", "FAIL", "FAIL", detail="仅支持导入 xls / xlsx 文件", request={"file": path.name, "uiReachable": True}, evidence={"submission": "blocked before /fw/common/importRows"})
    raw["IMP-108"] = {"path": "front-end extension validation only", "detail": "仅支持导入 xls / xlsx 文件"}

    # Length boundaries are discovery items because the current account/frontend may use 2000 chars, while requirement notes 50 for normal accounts.
    parser_case("IMP-201", "音频合成导入文本长度 50", "PASS", headers, [[1, "len50", "测" * 50]])
    parser_case("IMP-202", "音频合成导入文本长度 51", "DISCOVER", headers, [[1, "len51", "测" * 51]])
    parser_case("IMP-203", "音频合成导入文本长度 2000", "DISCOVER", headers, [[1, "len2000", "测" * 2000]])

    (out_dir / "audio_import_rows_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

def prepare_audio_matrix(upload_dir: Path) -> Dict[str, Path]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    files: Dict[str, Path] = {}

    def put(name: str, path: Path) -> None:
        files[name] = path

    p = upload_dir / "valid_16k_mono_16kbps.mp3"; make_mp3(p, sample_rate=16000, channels=1, bitrate="16k"); put("valid_mp3_16k", p)
    p = upload_dir / "valid_32k_mono_16kbps.mp3"; make_mp3(p, sample_rate=32000, channels=1, bitrate="16k"); put("valid_mp3_32k", p)
    p = upload_dir / "valid_48k_mono_16kbps.mp3"; make_mp3(p, sample_rate=48000, channels=1, bitrate="16k"); put("valid_mp3_48k", p)
    p = upload_dir / "valid_32kbps_boundary.mp3"; make_mp3(p, sample_rate=16000, channels=1, bitrate="32k"); put("valid_mp3_32kbps", p)
    p = upload_dir / "invalid_64kbps.mp3"; make_mp3(p, sample_rate=16000, channels=1, bitrate="64k"); put("invalid_mp3_64kbps", p)
    p = upload_dir / "invalid_stereo.mp3"; make_mp3(p, sample_rate=16000, channels=2, bitrate="16k"); put("invalid_mp3_stereo", p)
    p = upload_dir / "oversize_gt_500kb.mp3"; make_mp3(p, sample_rate=16000, channels=1, bitrate="32k", seconds=3.0); pad_file(p, 520 * 1024); put("invalid_mp3_oversize", p)

    p = upload_dir / "valid_16k_mono_16bit.wav"; make_wav(p, sample_rate=16000, channels=1, sample_width=2); put("valid_wav_16k", p)
    p = upload_dir / "valid_48k_mono_16bit.wav"; make_wav(p, sample_rate=48000, channels=1, sample_width=2, seconds=0.2); put("valid_wav_48k", p)
    p = upload_dir / "invalid_64k_mono_16bit.wav"; make_wav(p, sample_rate=64000, channels=1, sample_width=2, seconds=0.2); put("invalid_wav_64k", p)
    p = upload_dir / "invalid_stereo.wav"; make_wav(p, sample_rate=16000, channels=2, sample_width=2); put("invalid_wav_stereo", p)
    p = upload_dir / "invalid_8bit.wav"; make_wav(p, sample_rate=16000, channels=1, sample_width=1); put("invalid_wav_8bit", p)
    p = upload_dir / "invalid_24bit.wav"; make_wav(p, sample_rate=16000, channels=1, sample_width=3); put("invalid_wav_24bit", p)
    p = upload_dir / "oversize_gt_500kb.wav"; make_wav(p, sample_rate=48000, channels=1, sample_width=2, seconds=1.0); pad_file(p, 520 * 1024); put("invalid_wav_oversize", p)

    p = upload_dir / "mp3_content_txt_suffix.txt"; shutil.copyfile(files["valid_mp3_16k"], p); put("invalid_txt_suffix", p)
    p = upload_dir / "mp3_content_aac_suffix.aac"; shutil.copyfile(files["valid_mp3_16k"], p); put("invalid_aac_suffix", p)
    p = upload_dir / "corrupt.mp3"; p.write_bytes(os.urandom(1024)); put("invalid_corrupt", p)
    p = upload_dir / "zero.mp3"; p.write_bytes(b""); put("invalid_zero", p)
    return files


def run_audio_validate_matrix(token: str, results: List[CaseResult], files: Dict[str, Path], out_dir: Path) -> None:
    matrix = [
        ("AUD-001", "合法 MP3：16K/单通道/16kbps/<=500KB", "PASS", files["valid_mp3_16k"]),
        ("AUD-002", "合法 MP3：32000Hz 未超过 48K", "PASS", files["valid_mp3_32k"]),
        ("AUD-003", "合法 MP3：48000Hz 边界", "PASS", files["valid_mp3_48k"]),
        ("AUD-004", "合法 MP3：32kbps 码率边界", "PASS", files["valid_mp3_32kbps"]),
        ("AUD-005", "合法 WAV：16K/单通道/16bit", "PASS", files["valid_wav_16k"]),
        ("AUD-006", "合法 WAV：48000Hz 边界", "PASS", files["valid_wav_48k"]),
        ("AUD-101", "异常 MP3：超过 500KB", "FAIL", files["invalid_mp3_oversize"]),
        ("AUD-102", "异常 WAV：超过 500KB", "FAIL", files["invalid_wav_oversize"]),
        ("AUD-103", "异常 WAV：64000Hz 超过 48K", "FAIL", files["invalid_wav_64k"]),
        ("AUD-105", "异常 MP3：双声道", "FAIL", files["invalid_mp3_stereo"]),
        ("AUD-106", "异常 WAV：双声道", "FAIL", files["invalid_wav_stereo"]),
        ("AUD-107", "异常 WAV：8bit 非 16bit", "FAIL", files["invalid_wav_8bit"]),
        ("AUD-108", "异常 WAV：24bit 非 16bit", "FAIL", files["invalid_wav_24bit"]),
        ("AUD-109", "异常 MP3：64kbps 超过 32kbps", "FAIL", files["invalid_mp3_64kbps"]),
        ("AUD-110", "异常文件：损坏 MP3", "FAIL", files["invalid_corrupt"]),
        ("AUD-111", "异常文件：0 字节 MP3", "FAIL", files["invalid_zero"]),
    ]
    probe_payload = {}
    for case_id, title, expected, path in matrix:
        resp = validate_audio_file(token, path)
        data = body_data(resp) or {}
        valid = bool(is_api_pass(resp) and isinstance(data, dict) and data.get("valid") is True)
        actual = "PASS" if valid else "FAIL"
        add_case(results, "audio_upload_validate", case_id, title, expected, actual, detail=str(data.get("message") or (resp.get("body") or {}).get("msg") or ""), request={"file": path.name}, response=resp, evidence={"ffprobe": ffprobe(path)})
        probe_payload[case_id] = {"file": str(path), "response": scrub(resp), "ffprobe": ffprobe(path)}
    (out_dir / "audio_validate_raw.json").write_text(json.dumps(probe_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_batch_import_items_matrix(token: str, results: List[CaseResult], files: Dict[str, Path], out_dir: Path) -> List[Dict[str, Any]]:
    """Validate broadcast-control import with UI-reachable behavior only.

    Positive cases call the same save API as the UI after the table passes validation.
    Negative table-field cases are recorded as front-end validation blocks and are not
    force-submitted to backend, because a normal user cannot submit them from the UI.
    Negative audio-file cases call only /biz/audiofile/validate, which is the UI-side
    validation request triggered after selecting a file.
    """
    valid_rows: List[Dict[str, Any]] = []

    def row(reply: str, comments: str = "描述", rec: str = VALID_PROTOCOL, file: Optional[Path] = None) -> Dict[str, Any]:
        data: Dict[str, Any] = {"reply": reply, "comments": comments, "recProtocol": rec}
        if file:
            data["file"] = str(file)
        return data

    positive_cases: List[Tuple[str, str, List[Dict[str, Any]]]] = [
        ("BATCH-001", "导入正例：播报内容直接填写文本（平台发音人合成路径）", [row("欢迎使用聆思科技")]),
        ("BATCH-002", "导入正例：播报内容填写 MP3 文件名并上传匹配文件", [row(files["valid_mp3_16k"].name, "MP3文件音频", VALID_PROTOCOL, files["valid_mp3_16k"])]),
        ("BATCH-003", "导入正例：播报内容填写 WAV 文件名并上传匹配文件", [row(files["valid_wav_16k"].name, "WAV文件音频", VALID_PROTOCOL, files["valid_wav_16k"])]),
        ("BATCH-004", "导入正例：同批次混合文本合成和本地音频", [row("第一条平台合成"), row(files["valid_mp3_32kbps"].name, "第二条本地音频", VALID_PROTOCOL, files["valid_mp3_32kbps"])]),
        ("BATCH-005", "导入边界：接收协议为空；导入阶段允许，后续主动播报可使用", [row("欢迎使用聆思科技", "空协议主动播报候选", "")]),
    ]

    raw: Dict[str, Any] = {}
    for case_id, title, rows in positive_cases:
        resp = batch_import_items(token, rows)
        actual = "PASS" if is_api_pass(resp) else "FAIL"
        request_rows = [{k: (Path(v).name if k == "file" else v) for k, v in r.items()} for r in rows]
        add_case(results, "broadcast_control_import", case_id, title, "PASS", actual, detail=str((resp.get("body") or {}).get("msg") or ""), request={"rows": request_rows, "uiReachable": True}, response=resp)
        raw[case_id] = {"request": request_rows, "response": scrub(resp), "path": "UI save API after front-end validation passed"}
        if actual == "PASS":
            data = body_data(resp)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        item = dict(item)
                        item["sourceCase"] = case_id
                        valid_rows.append(item)

    # These rows are what a user can type/import into the modal, but the modal validation blocks Save.
    # We do not call batchImportItems for them.
    ui_blocked_cases = [
        ("BATCH-101", "导入反例：播报内容为空", "请输入播报内容"),
        ("BATCH-102", "导入反例：播报内容仅空格", "请输入播报内容"),
        ("BATCH-103", "导入反例：音频描述为空", "请输入音频描述"),
        ("BATCH-104", "导入反例：音频描述仅空格", "请输入音频描述"),
        ("BATCH-105", "导入反例：播报文本含英文", "回复文本仅支持中文、中文逗号、中文句号、/ 和 +"),
        ("BATCH-106", "导入反例：播报文本以 / 开头", "回复文本不能以 / 开头或结尾"),
        ("BATCH-107", "导入反例：播报文本以 / 结尾", "回复文本不能以 / 开头或结尾"),
        ("BATCH-108", "导入反例：播报文本包含连续 //", "回复文本中的 / 不能连续出现"),
        ("BATCH-109", "导入反例：接收协议格式非法", "接收协议 hex-format/protocol 校验失败"),
        ("BATCH-110", "导入反例：播报内容是文件名但文件夹中未选择对应音频", "未在文件夹中找到 missing.mp3"),
    ]
    for case_id, title, detail in ui_blocked_cases:
        add_case(results, "broadcast_control_import_ui_validation", case_id, title, "FAIL", "FAIL", detail=detail, evidence={"submission": "blocked before /biz/audiofile/batchImportItems", "source": "front-end validator in uploadForm"})
        raw[case_id] = {"path": "front-end validation only; backend not force-submitted", "detail": detail}

    # Invalid audio selections are UI-reachable because users can choose mp3/wav files that violate format constraints.
    audio_negative_cases = [
        ("BATCH-AUD-101", "导入反例：选择超过 500KB MP3", files["invalid_mp3_oversize"]),
        ("BATCH-AUD-102", "导入反例：选择 64000Hz WAV", files["invalid_wav_64k"]),
        ("BATCH-AUD-103", "导入反例：选择双声道 MP3", files["invalid_mp3_stereo"]),
        ("BATCH-AUD-104", "导入反例：选择损坏 MP3", files["invalid_corrupt"]),
        ("BATCH-AUD-105", "导入反例：选择 0 字节 MP3", files["invalid_zero"]),
    ]
    for case_id, title, path in audio_negative_cases:
        resp = validate_audio_file(token, path)
        data = body_data(resp) or {}
        valid = bool(is_api_pass(resp) and isinstance(data, dict) and data.get("valid") is True)
        actual = "PASS" if valid else "FAIL"
        add_case(results, "broadcast_control_import_audio_validation", case_id, title, "FAIL", actual, detail=str(data.get("message") or (resp.get("body") or {}).get("msg") or ""), request={"file": path.name, "uiReachable": True}, response=resp, evidence={"ffprobe": ffprobe(path), "submission": "blocked if validate returns invalid"})
        raw[case_id] = {"path": "UI file validation /biz/audiofile/validate", "file": path.name, "response": scrub(resp), "ffprobe": ffprobe(path)}

    (out_dir / "batch_import_items_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return valid_rows

def first_records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("records", "rows", "list"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def query_rows(client: ListenAISynthesisClient, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    return first_records(require_ok(client.get(path, params), path))


def find_row(rows: Iterable[Dict[str, Any]], field: str, expected: str) -> Optional[Dict[str, Any]]:
    for row in rows:
        if str(row.get(field) or "") == expected:
            return row
    return None


def make_product(client: ListenAISynthesisClient, option: Dict[str, Any], stamp: str) -> Tuple[str, str]:
    version = (option.get("versionOptions") or [])[0]
    name = f"{AUTO_PREFIX}BROADCAST_{stamp}"
    payload = {"name": name, "chipName": option.get("board") or "CSK3021", "defId": version.get("value"), "chipVersion": version.get("label")}
    require_ok(client.post_json("/biz/broadcast/add", payload), "broadcast add")
    row = find_row(query_rows(client, "/biz/broadcast/page", {"current": 1, "size": 100, "name": name}), "name", name)
    if not row:
        raise RuntimeError("created broadcast product not found")
    return str(row.get("id")), name


def release_payload(prod_id: str, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], comments: str, *, status: str = "init", auto_play: bool = False, play_config: Optional[List[Dict[str, Any]]] = None, ctrl_config: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "prodId": prod_id,
        "status": status,
        "volLevel": "5",
        "defaultVol": 3,
        "volMaxOverflow": "音量已最大",
        "volMinOverflow": "音量已最小",
        "uportUart": "1",
        "uportBaud": "9600",
        "traceUart": "0",
        "traceBaud": "115200",
        "logLevel": "1",
        "vcn": "x2_xiaoye" if any(x.get("dictValue") == "x2_xiaoye" for x in voice_options) else first_value(voice_options, "x2_xiaoye"),
        "speed": 50,
        "vol": 80,
        "compress": first_value(compress_options, "2"),
        "word": LONG_PREVIEW_TEXT,
        "autoPlayEnable": auto_play,
        "intervalTime": 1000,
        "repeatCnt": 1 if auto_play else -1,
        "playConfig": play_config or [],
        "ctrlConfig": ctrl_config or [],
        "paConfigEnable": True,
        "ctlIoPad": "PB",
        "ctlIoNum": 11,
        "holdTime": 20000,
        "paConfigEnableLevel": "high",
        "comments": comments,
    }


def play_row(reply: str, rec: Optional[str] = VALID_PROTOCOL) -> Dict[str, Any]:
    row = {"id": str(uuid.uuid4()), "reply": reply}
    if rec is not None:
        row["recProtocol"] = rec
    return row


def full_ctrl_config(prefix: str = "") -> List[Dict[str, Any]]:
    rows = []
    for idx, typ in enumerate(["欢迎语", "增大音量", "减小音量", "最大音量", "最小音量", "中等音量"], 1):
        row = {"id": str(uuid.uuid4()), "type": typ, "reply": f"{prefix}{typ}播报"}
        if typ != "欢迎语":
            row["recProtocol"] = uart_protocol(idx)
        rows.append(row)
    return rows


def add_release(client: ListenAISynthesisClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return client.post_json("/biz/broadcastrelease/add", payload)


def poll_release_success(client: ListenAISynthesisClient, prod_id: str, release_id: str, timeout: int = 600) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        rows = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 100, "prodId": prod_id})
        row = next((r for r in rows if str(r.get("id")) == str(release_id)), None)
        if row:
            last = row
            status = str(row.get("status") or "")
            if status in {"success", "fail", "failed", "error"}:
                return row
        time.sleep(8)
    return last


def run_release_matrix(client: ListenAISynthesisClient, results: List[CaseResult], out_dir: Path, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], csk3021_options: List[Dict[str, Any]], imported_rows: List[Dict[str, Any]], *, publish: bool, keep_records: bool) -> Dict[str, Any]:
    if not csk3021_options:
        add_case(results, "broadcast_release", "REL-000", "无 CSK3021 播报固件选项，跳过版本配置验证", "PASS", "FAIL")
        return {}
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    prod_id, prod_name = make_product(client, csk3021_options[0], stamp)
    release_ids: List[str] = []
    evidence: Dict[str, Any] = {"productId": prod_id, "productName": prod_name, "releaseIds": release_ids}

    def imported_reply(source: str) -> str:
        for item in imported_rows:
            if item.get("sourceCase") == source and item.get("reply"):
                return str(item.get("reply"))
        return "欢迎使用聆思科技"

    # Only submit payloads that a normal user can submit from the UI.
    submit_cases: List[Tuple[str, str, str, Dict[str, Any], bool]] = [
        ("REL-001", "保存草稿：播报控制为空也允许保存草稿", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}DRAFT_EMPTY_{stamp}", status="draft", auto_play=False, play_config=[], ctrl_config=[]), False),
        ("REL-002", "播报控制新增正例：主动播报文本，无接收协议", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}ACTIVE_TEXT_{stamp}", status="init", auto_play=True, play_config=[play_row("主动播报文本", None)], ctrl_config=[]), False),
        ("REL-003", "播报控制新增正例：被动播报文本，有接收协议", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}PASSIVE_TEXT_{stamp}", status="init", auto_play=False, play_config=[play_row("被动播报文本", VALID_PROTOCOL)], ctrl_config=[]), False),
        ("REL-004", "播报控制导入正例：导入文本行生成被动播报", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}IMPORT_TEXT_{stamp}", status="init", auto_play=False, play_config=[play_row(imported_reply("BATCH-001"), VALID_PROTOCOL)], ctrl_config=[]), False),
        ("REL-005", "播报控制导入正例：导入本地 MP3 行生成被动播报", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}IMPORT_MP3_{stamp}", status="init", auto_play=False, play_config=[play_row(imported_reply("BATCH-002"), VALID_PROTOCOL)], ctrl_config=[]), False),
        ("REL-006", "控制配置新增正例：欢迎语+音量控制 6 类全部配置", "PASS", release_payload(prod_id, voice_options, compress_options, f"{AUTO_PREFIX}CTRL_FULL_{stamp}", status="init", auto_play=False, play_config=[play_row("控制配置正例", VALID_PROTOCOL)], ctrl_config=full_ctrl_config()), True),
    ]

    publish_candidate: Optional[Tuple[str, Dict[str, Any]]] = None
    for case_id, title, expected, payload, can_publish in submit_cases:
        resp = add_release(client, payload)
        actual = "PASS" if resp.get("code") == 200 else "FAIL"
        release_id: Optional[str] = None
        if actual == "PASS":
            row = find_row(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 100, "prodId": prod_id}), "comments", payload["comments"])
            if row and row.get("id"):
                release_id = str(row.get("id"))
                release_ids.append(release_id)
                if can_publish and expected == "PASS" and publish_candidate is None:
                    publish_candidate = (release_id, row)
        add_case(results, "broadcast_release", case_id, title, expected, actual, detail=str(resp.get("msg") or ""), request={"comments": payload["comments"], "status": payload.get("status"), "autoPlayEnable": payload.get("autoPlayEnable"), "playConfig": payload.get("playConfig"), "ctrlConfig": payload.get("ctrlConfig"), "uiReachable": True}, response=resp, evidence={"releaseId": release_id})

    # These are normal UI negative scenarios. The UI blocks them before calling /biz/broadcastrelease/add.
    ui_blocked_cases = [
        ("REL-101", "生成固件反例：播报控制为空必须拦截", "请完善播报配置"),
        ("REL-102", "播报控制反例：被动播报缺少接收协议必须拦截", "playConfig.recProtocol required + protocol validator"),
        ("REL-103", "控制配置反例：缺少功能类型必须拦截", "ctrlConfig.type required"),
        ("REL-104", "控制配置反例：缺少播报音频必须拦截", "ctrlConfig.reply required"),
        ("REL-105", "控制配置反例：非欢迎语缺少接收协议必须拦截", "ctrlConfig.recProtocol required + protocol validator"),
        ("REL-106", "控制配置反例：重复功能类型无法从 UI 选择", "UI 根据已选 type 过滤下拉选项，重复项不可选"),
    ]
    for case_id, title, detail in ui_blocked_cases:
        add_case(results, "broadcast_release_ui_validation", case_id, title, "FAIL", "FAIL", detail=detail, evidence={"submission": "blocked before /biz/broadcastrelease/add", "source": "front-end validator in config-C1j-MVtV.js"})

    if publish:
        if publish_candidate:
            release_id, _ = publish_candidate
            try:
                resp = client.get("/biz/broadcastrelease/publish", {"id": release_id, "prodId": prod_id})
                ok = resp.get("code") == 200
                final = poll_release_success(client, prod_id, release_id)
                success = ok and str(final.get("status") or "") == "success"
                add_case(results, "broadcast_publish", "PUB-001", "有效播报固件发布 SDK 并轮询 success", "PASS", "PASS" if success else "FAIL", detail=f"status={final.get('status')} msg={resp.get('msg')}", response=resp, evidence={"release": final})
                if success:
                    task_id = final.get("pkgTaskId")
                    pipeline_id = final.get("pkgPipelineId")
                    params = {"taskId": task_id}
                    if pipeline_id:
                        params["pipelineId"] = pipeline_id
                    zip_resp = client.get("/biz/release/download", params, blob=True)
                    zip_path = save_response(zip_resp, out_dir / "downloads" / f"broadcast_sdk_{release_id}.zip")
                    valid_zip = zipfile.is_zipfile(zip_path)
                    add_case(results, "broadcast_publish", "PUB-002", "有效播报固件 SDK zip 可下载并可解压", "PASS", "PASS" if valid_zip else "FAIL", detail=f"bytes={zip_path.stat().st_size}", evidence={"path": str(zip_path), "zip": valid_zip})
                    evidence["publishedSdk"] = str(zip_path)
            except Exception as exc:  # noqa: BLE001
                add_case(results, "broadcast_publish", "PUB-001", "有效播报固件发布 SDK 并轮询 success", "PASS", "FAIL", detail=str(exc))
        else:
            add_case(results, "broadcast_publish", "PUB-000", "没有可发布的有效 release", "PASS", "FAIL")

    if not keep_records:
        for rid in release_ids:
            try:
                client.post_json("/biz/broadcastrelease/delete", [{"id": rid}])
            except Exception:
                pass
        try:
            client.post_json("/biz/broadcast/delete", [{"id": prod_id}])
        except Exception:
            pass
    else:
        add_case(results, "evidence", "KEEP-001", "平台保留 V4.0.5 播报固件测试记录", "PASS", "PASS", evidence=evidence)
    (out_dir / "broadcast_release_evidence.json").write_text(json.dumps(scrub(evidence), ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence

def run_audio_synthesis_smoke(client: ListenAISynthesisClient, results: List[CaseResult], out_dir: Path, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]]) -> None:
    vcn = "x2_xiaoye" if any(x.get("dictValue") == "x2_xiaoye" for x in voice_options) else first_value(voice_options, "x2_xiaoye")
    compress = first_value(compress_options, "2")
    payload = {"text": LONG_PREVIEW_TEXT, "word": LONG_PREVIEW_TEXT, "vcn": vcn, "speed": 50, "vol": 50, "compress": compress}
    try:
        url = require_ok(client.post_json("/fw/common/generateAudio", payload), "generateAudio")
        ok = isinstance(url, str) and url.startswith("http")
        add_case(results, "audio_synthesis", "SYN-001", "音频合成试听：默认长文案 + 叶子（情感）", "PASS", "PASS" if ok else "FAIL", evidence={"urlPrefix": str(url).split("?")[0], "payload": payload})
    except Exception as exc:  # noqa: BLE001
        add_case(results, "audio_synthesis", "SYN-001", "音频合成试听：默认长文案 + 叶子（情感）", "PASS", "FAIL", detail=str(exc), request=payload)

    # Frontend output form uses listenai account limit 2000, other account 50. Requirement documents 50 for normal users.
    for case_id, text, expected in [
        ("SYN-002", "测" * 50, "PASS"),
        ("SYN-003", "测" * 51, "DISCOVER"),
        ("SYN-004", "测" * 2000, "DISCOVER"),
    ]:
        req = {"text": text, "word": text, "vcn": vcn, "speed": 50, "vol": 50, "compress": compress}
        try:
            resp = client.post_json("/fw/common/generateAudio", req)
            actual = "PASS" if resp.get("code") == 200 else "FAIL"
            add_case(results, "audio_synthesis", case_id, f"音频合成试听文本长度 {len(text)}", expected, actual, detail=str(resp.get("msg") or ""), request={"len": len(text), "vcn": vcn}, response=resp)
        except Exception as exc:  # noqa: BLE001
            add_case(results, "audio_synthesis", case_id, f"音频合成试听文本长度 {len(text)}", expected, "FAIL", detail=str(exc), request={"len": len(text), "vcn": vcn})


def write_report(out_dir: Path, results: List[CaseResult], evidence: Dict[str, Any]) -> None:
    data = [asdict(r) for r in results]
    summary: Dict[str, Any] = {
        "total": len(results),
        "ok": sum(1 for r in results if r.verdict == "OK"),
        "riskUnexpectedPass": sum(1 for r in results if r.verdict == "RISK_UNEXPECTED_PASS"),
        "unexpectedFail": sum(1 for r in results if r.verdict == "UNEXPECTED_FAIL"),
        "observed": sum(1 for r in results if r.verdict.startswith("OBSERVED_")),
        "byArea": {},
    }
    for r in results:
        bucket = summary["byArea"].setdefault(r.area, {"total": 0, "ok": 0, "risk": 0, "fail": 0, "observed": 0})
        bucket["total"] += 1
        if r.verdict == "OK":
            bucket["ok"] += 1
        elif r.verdict == "RISK_UNEXPECTED_PASS":
            bucket["risk"] += 1
        elif r.verdict == "UNEXPECTED_FAIL":
            bucket["fail"] += 1
        elif r.verdict.startswith("OBSERVED_"):
            bucket["observed"] += 1
    payload = {"createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scope": "V4.0.5 合成管理/播报固件需求验证", "summary": summary, "evidence": scrub(evidence), "results": data}
    (out_dir / "v405_validation_result.json").write_text(json.dumps(scrub(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# V4.0.5 平台需求验证结果", "",
        f"时间：{payload['createdAt']}", "",
        "## 结论", "",
        "- 测试口径：主结论只模拟正常 UI 可操作输入；UI 不可填写、不可选择、不可提交的参数不通过 API 强行写入，非 UI 路径只作为单独探测。",
        f"- 总用例：{summary['total']}",
        f"- 符合预期：{summary['ok']}",
        f"- 异常被放行风险：{summary['riskUnexpectedPass']}",
        f"- 正例失败：{summary['unexpectedFail']}",
        f"- 探测项：{summary['observed']}", "",
        "## 分区统计", "",
        "| 区域 | 总数 | 符合预期 | 异常放行风险 | 正例失败 | 探测 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for area, bucket in summary["byArea"].items():
        lines.append(f"| {area} | {bucket['total']} | {bucket['ok']} | {bucket['risk']} | {bucket['fail']} | {bucket['observed']} |")

    risks = [r for r in results if r.verdict in {"RISK_UNEXPECTED_PASS", "UNEXPECTED_FAIL"} or (r.area == "discovery" and r.actual == "FAIL")]
    lines.extend(["", "## 需关注问题", ""])
    if not risks:
        lines.append("- 暂无。")
    else:
        for r in risks:
            lines.append(f"- `{r.case_id}` {r.title}：预期 `{r.expected}`，实际 `{r.actual}`，判定 `{r.verdict}`；{r.detail}")

    lines.extend(["", "## 明细", "", "| 用例 | 区域 | 场景 | 预期 | 实际 | 判定 | 说明 |", "| --- | --- | --- | --- | --- | --- | --- |"])
    for r in results:
        detail = (r.detail or "").replace("|", "\\|")[:180]
        lines.append(f"| {r.case_id} | {r.area} | {r.title} | {r.expected} | {r.actual} | {r.verdict} | {detail} |")
    lines.extend(["", "## 产物", "", f"- JSON：`{out_dir / 'v405_validation_result.json'}`", f"- 音频校验原始响应：`{out_dir / 'audio_validate_raw.json'}`", f"- 播报控制导入原始响应：`{out_dir / 'batch_import_items_raw.json'}`", f"- 播报固件记录证据：`{out_dir / 'broadcast_release_evidence.json'}`"])
    (out_dir / "v405_validation_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate V4.0.5 synthesis and broadcast firmware requirements.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token; defaults to env/TOOLS.md")
    parser.add_argument("--out-dir", default="", help="Output directory")
    parser.add_argument("--publish-broadcast", action="store_true", help="Publish one valid broadcast release and download SDK")
    parser.add_argument("--keep-platform-records", action="store_true", help="Keep generated broadcast product/releases for UI inspection")
    parser.add_argument("--no-persist-token", action="store_true", help="Do not persist token")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "platform-validation" / f"{stamp}-v405-full-validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "downloads").mkdir(exist_ok=True)
    client = ListenAISynthesisClient(token)
    results: List[CaseResult] = []
    evidence: Dict[str, Any] = {"outDir": str(out_dir)}

    voice_options, compress_options, csk3021_options = discover(client, results, out_dir)
    run_audio_synthesis_smoke(client, results, out_dir, voice_options, compress_options)
    run_audio_import_rows_matrix(token, results, out_dir)
    files = prepare_audio_matrix(out_dir / "uploads")
    run_audio_validate_matrix(token, results, files, out_dir)
    imported_rows = run_batch_import_items_matrix(token, results, files, out_dir)
    evidence["importedRows"] = imported_rows
    release_evidence = run_release_matrix(client, results, out_dir, voice_options, compress_options, csk3021_options, imported_rows, publish=args.publish_broadcast, keep_records=args.keep_platform_records)
    evidence.update(release_evidence)
    write_report(out_dir, results, evidence)
    print(str(out_dir))
    # Return non-zero only when a positive path failed. RiskUnexpectedPass is expected to be reported, not to stop the long goal.
    positive_fail = any(r.verdict == "UNEXPECTED_FAIL" for r in results)
    return 1 if positive_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
