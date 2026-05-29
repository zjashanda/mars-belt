#!/usr/bin/env python3
"""Full-chain validation for ListenAI 合成管理.

The suite covers feature-level flows for 音频合成 and 播报合成:
manual fields, uploads, imports, downloads, edit/query/detail/delete, and SDK publish.
All write records use AUTO_TEST_* names. By default records are cleaned; use
--keep-platform-records only when a human needs to inspect generated platform rows.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
import wave
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
import urllib3
from openpyxl import Workbook

from listenai_task_support import ARTIFACTS_ROOT, resolve_listenai_token

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"
AUTO_PREFIX = "AUTO_TEST_"


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class ListenAISynthesisClient:
    def __init__(self, token: str, timeout: int = 120) -> None:
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"token": token})
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return BASE_URL + path

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, *, blob: bool = False) -> Any:
        response = self.session.get(self._url(path), params=params or {}, timeout=self.timeout)
        response.raise_for_status()
        if blob:
            return response
        return response.json()

    def post_json(self, path: str, payload: Any) -> Dict[str, Any]:
        response = self.session.post(
            self._url(path),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def post_multipart(self, path: str, *, data: Dict[str, Any], files: List[Tuple[str, Tuple[str, Any, str]]]) -> Dict[str, Any]:
        response = self.session.post(self._url(path), data=data, files=files, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_url(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> requests.Response:
        response = self.session.get(url, headers=headers or {}, timeout=self.timeout, stream=True)
        response.raise_for_status()
        return response


def sanitize_text(text: Any) -> str:
    value = str(text or "")
    value = re.sub(r"([：:=])([A-Za-z0-9_\-]{24,})", r"\1<redacted>", value)
    value = re.sub(r"(token\s+无效[:：]?)\s*[^\s]+", r"\1 <redacted>", value, flags=re.IGNORECASE)
    return value


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {"token", "authorization"}:
                output[key] = "<redacted>"
            elif isinstance(item, str) and len(item) > 400:
                output[key] = item[:400] + f"...<len={len(item)}>"
            else:
                output[key] = scrub(item)
        return output
    if isinstance(value, list):
        return [scrub(item) for item in value[:80]] + ([f"<truncated total={len(value)}>"] if len(value) > 80 else [])
    return value


def require_ok(result: Dict[str, Any], label: str) -> Any:
    if result.get("code") != 200:
        raise RuntimeError(f"{label} failed: code={result.get('code')} msg={sanitize_text(result.get('msg'))}")
    return result.get("data")


def first_records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("records", "rows", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def find_row(rows: List[Dict[str, Any]], field: str, expected: str) -> Optional[Dict[str, Any]]:
    for row in rows:
        if str(row.get(field) or "") == expected:
            return row
    return None


def query_rows(client: ListenAISynthesisClient, path: str, params: Dict[str, Any], label: str) -> List[Dict[str, Any]]:
    return first_records(require_ok(client.get(path, params), label))


def query_first_by_field(client: ListenAISynthesisClient, path: str, params: Dict[str, Any], field: str, expected: str) -> Optional[Dict[str, Any]]:
    return find_row(query_rows(client, path, params, f"query {path}"), field, expected)


def dict_children(dict_tree: List[Dict[str, Any]], value: str) -> List[Dict[str, Any]]:
    for item in dict_tree:
        if item.get("dictValue") == value or str(item.get("dictLabel")) == value:
            return [child for child in (item.get("children") or []) if isinstance(child, dict)]
    return []


def first_value(options: List[Dict[str, Any]], fallback: str) -> str:
    if options:
        value = options[0].get("dictValue") or options[0].get("value")
        if value is not None:
            return str(value)
    return fallback


def find_menu_titles(menu: List[Dict[str, Any]], titles: set[str]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for node in nodes:
            title = str(node.get("title") or node.get("name") or "")
            if title in titles:
                found[title] = node
            children = node.get("children")
            if isinstance(children, list):
                walk(children)

    walk(menu)
    return found


def add_step(steps: List[StepResult], name: str, status: str, detail: str = "", data: Optional[Dict[str, Any]] = None) -> None:
    steps.append(StepResult(name, status, sanitize_text(detail), scrub(data or {})))


def delete_records(client: ListenAISynthesisClient, path: str, ids: List[str], steps: List[StepResult], label: str) -> None:
    ids = [str(item_id) for item_id in ids if item_id]
    if not ids:
        return
    try:
        require_ok(client.post_json(path, [{"id": item_id} for item_id in ids]), f"delete {label}")
        add_step(steps, f"cleanup {label}", "PASS", f"deleted {len(ids)} record(s)", {"ids": ids})
    except Exception as exc:  # noqa: BLE001
        add_step(steps, f"cleanup {label}", "FAIL", str(exc), {"ids": ids})


def create_test_wav(path: Path, seconds: float = 0.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    samples = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * samples)


def create_test_mp3(path: Path, seconds: float = 0.8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
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
            ],
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to create batch-import mp3 test audio") from exc


def write_voice_import_xlsx(path: Path, rows: List[Tuple[int, str, str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "语音合成"
    ws.append(["序号", "音频名称", "播报文本"])
    for row in rows:
        ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_broadcast_import_xlsx(path: Path, rows: List[Tuple[str, str, str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "播报映射"
    ws.append(["播报内容", "音频描述", "接收协议"])
    for row in rows:
        ws.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def save_response(response: requests.Response, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    if path.stat().st_size <= 0:
        raise RuntimeError(f"downloaded file is empty: {path}")
    return path


def assert_zip(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"downloaded file is not a zip: {path}")


def assert_audio_file(path: Path) -> None:
    prefix = path.read_bytes()[:16]
    if not (prefix.startswith(b"RIFF") or prefix.startswith(b"ID3") or prefix.startswith(b"\xff")):
        raise RuntimeError(f"downloaded file is not recognized audio: {path}")


def poll_output_status(client: ListenAISynthesisClient, related_id: str, output_id: str, timeout_s: int = 120) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        rows = query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": related_id}, "voice output page poll")
        for row in rows:
            if str(row.get("id")) == str(output_id):
                last = row
                status = str(row.get("status") or "")
                if status == "normal" and row.get("fileId"):
                    return row
                if status not in {"pending", "draft", ""} and status != "normal":
                    return row
        time.sleep(3)
    return last


def poll_broadcast_release(client: ListenAISynthesisClient, prod_id: str, release_id: str, timeout_s: int = 300) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        rows = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": prod_id}, "broadcast release poll")
        for row in rows:
            if str(row.get("id")) == str(release_id):
                last = row
                if str(row.get("status") or "") in {"success", "failed"}:
                    return row
        time.sleep(5)
    return last


def cleanup_auto_test_records(client: ListenAISynthesisClient, steps: List[StepResult], phase: str) -> Dict[str, int]:
    counts = {"voiceProjects": 0, "voiceOutputs": 0, "broadcastProducts": 0, "broadcastReleases": 0, "audioFiles": 0}

    voice_projects = [row for row in query_rows(client, "/fw/voice/page", {"current": 1, "size": 200}, "cleanup voice page") if str(row.get("projectName") or "").startswith(AUTO_PREFIX)]
    for project in voice_projects:
        pid = str(project.get("id") or "")
        outputs = query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 200, "relatedId": pid}, "cleanup voice outputs")
        output_ids = [str(row.get("id")) for row in outputs if row.get("id")]
        counts["voiceOutputs"] += len(output_ids)
        delete_records(client, "/fw/voice/output/delete", output_ids, steps, f"{phase} stale voice output")
    counts["voiceProjects"] = len(voice_projects)
    delete_records(client, "/fw/voice/delete", [str(row.get("id")) for row in voice_projects], steps, f"{phase} stale voice project")

    broadcast_products = [row for row in query_rows(client, "/biz/broadcast/page", {"current": 1, "size": 200}, "cleanup broadcast page") if str(row.get("name") or "").startswith(AUTO_PREFIX)]
    for product in broadcast_products:
        prod_id = str(product.get("id") or "")
        releases = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 200, "prodId": prod_id}, "cleanup broadcast releases")
        release_ids = [str(row.get("id")) for row in releases if row.get("id")]
        counts["broadcastReleases"] += len(release_ids)
        delete_records(client, "/biz/broadcastrelease/delete", release_ids, steps, f"{phase} stale broadcast release")
    counts["broadcastProducts"] = len(broadcast_products)
    delete_records(client, "/biz/broadcast/delete", [str(row.get("id")) for row in broadcast_products], steps, f"{phase} stale broadcast product")

    audio_files = [row for row in query_rows(client, "/biz/audiofile/page", {"current": 1, "size": 200}, "cleanup audio files") if str(row.get("comments") or "").startswith(AUTO_PREFIX) or str(row.get("fileName") or "").startswith(AUTO_PREFIX)]
    counts["audioFiles"] = len(audio_files)
    delete_records(client, "/biz/audiofile/delete", [str(row.get("id")) for row in audio_files], steps, f"{phase} stale custom audio")

    detail = ", ".join(f"{key}={value}" for key, value in counts.items())
    add_step(steps, f"{phase} AUTO_TEST cleanup scan", "PASS", detail, counts)
    return counts


def collect_auto_test_records(client: ListenAISynthesisClient) -> Dict[str, Any]:
    voice_projects = [row for row in query_rows(client, "/fw/voice/page", {"current": 1, "size": 200}, "collect voice page") if str(row.get("projectName") or "").startswith(AUTO_PREFIX)]
    voice_outputs: List[Dict[str, Any]] = []
    for project in voice_projects:
        pid = str(project.get("id") or "")
        voice_outputs.extend(query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 200, "relatedId": pid}, "collect voice outputs"))

    broadcast_products = [row for row in query_rows(client, "/biz/broadcast/page", {"current": 1, "size": 200}, "collect broadcast page") if str(row.get("name") or "").startswith(AUTO_PREFIX)]
    broadcast_releases: List[Dict[str, Any]] = []
    for product in broadcast_products:
        prod_id = str(product.get("id") or "")
        broadcast_releases.extend(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 200, "prodId": prod_id}, "collect broadcast releases"))

    audio_files = [row for row in query_rows(client, "/biz/audiofile/page", {"current": 1, "size": 200}, "collect audio files") if str(row.get("comments") or "").startswith(AUTO_PREFIX) or str(row.get("fileName") or "").startswith(AUTO_PREFIX)]
    return {
        "counts": {
            "voiceProjects": len(voice_projects),
            "voiceOutputs": len(voice_outputs),
            "broadcastProducts": len(broadcast_products),
            "broadcastReleases": len(broadcast_releases),
            "audioFiles": len(audio_files),
        },
        "voiceProjects": voice_projects,
        "voiceOutputs": voice_outputs,
        "broadcastProducts": broadcast_products,
        "broadcastReleases": broadcast_releases,
        "audioFiles": audio_files,
    }


def validate_readonly(client: ListenAISynthesisClient, steps: List[StepResult], out_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    menu = require_ok(client.get("/sys/userCenter/loginMenu"), "login menu")
    found = find_menu_titles(menu, {"合成管理", "音频合成", "播报合成"})
    missing = sorted({"合成管理", "音频合成", "播报合成"} - set(found))
    add_step(steps, "menu discovery", "PASS" if not missing else "FAIL", "found synthesis menus" if not missing else f"missing {missing}", {"found": found})

    dict_tree = require_ok(client.get("/dev/dict/tree"), "dict tree")
    voice_options = dict_children(dict_tree, "voice")
    compress_options = dict_children(dict_tree, "compress")
    add_step(steps, "dictionary discovery", "PASS" if voice_options and compress_options else "FAIL", f"voice={len(voice_options)} compress={len(compress_options)}")

    audio_projects = require_ok(client.get("/fw/voice/page", {"current": 1, "size": 10}), "audio synthesis page")
    add_step(steps, "audio synthesis page", "PASS", f"total={audio_projects.get('total') if isinstance(audio_projects, dict) else 'unknown'}")

    template = client.get("/fw/common/download/template", {"sourceFile": "语音合成文本导入模板.xlsx", "aliasName": "语音合成文本导入模板.xlsx"}, blob=True)
    template_path = save_response(template, out_dir / "downloads" / "语音合成文本导入模板.xlsx")
    add_step(steps, "audio synthesis template download", "PASS", f"bytes={template_path.stat().st_size}", {"path": str(template_path)})

    broadcast_options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
    boards = [str(item.get("board") or item.get("mark") or "") for item in broadcast_options if isinstance(item, dict)]
    add_step(steps, "broadcast options", "PASS" if boards else "FAIL", ",".join(boards), {"options": broadcast_options})

    broadcast_page = require_ok(client.get("/biz/broadcast/page", {"current": 1, "size": 10}), "broadcast page")
    add_step(steps, "broadcast product page", "PASS", f"total={broadcast_page.get('total') if isinstance(broadcast_page, dict) else 'unknown'}")

    audiofile_options = require_ok(client.get("/biz/audiofile/options"), "audiofile options")
    add_step(steps, "custom audio options", "PASS", f"count={len(audiofile_options) if isinstance(audiofile_options, list) else 'unknown'}")
    return dict_tree, voice_options, compress_options


def validate_preview_audio(client: ListenAISynthesisClient, steps: List[StepResult], voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]]) -> str:
    payload = {
        "text": "合成管理自动化试听测试",
        "word": "合成管理自动化试听测试",
        "vcn": first_value(voice_options, "x2_xiaoye"),
        "speed": 50,
        "vol": 70,
        "compress": first_value(compress_options, "2"),
    }
    url = require_ok(client.post_json("/fw/common/generateAudio", payload), "generate audio")
    if not isinstance(url, str) or not url.startswith("http"):
        raise RuntimeError(f"generateAudio returned invalid url: {url!r}")
    response = requests.get(url, headers={"Range": "bytes=0-64", "User-Agent": "Mars-Belt/1.0"}, timeout=30, verify=False)
    response.raise_for_status()
    content_type = response.headers.get("content-type") or ""
    if "audio" not in content_type and not response.content.startswith((b"ID3", b"\xff")):
        raise RuntimeError(f"generated audio url is not audio: {content_type}")
    add_step(steps, "preview generateAudio manual fields", "PASS", f"contentType={content_type}", {"urlPrefix": url.split("?")[0], "payload": payload})
    return url


def validate_custom_audio_features(client: ListenAISynthesisClient, steps: List[StepResult], out_dir: Path, name_suffix: str, *, keep_records: bool = False) -> List[Dict[str, Any]]:
    uploaded_ids: List[str] = []
    import_rows: List[Dict[str, Any]] = []
    try:
        comments = f"{AUTO_PREFIX}AUDIO_UPLOAD_{name_suffix}"
        wav_path = out_dir / "uploads" / f"{comments}.wav"
        create_test_wav(wav_path)
        with wav_path.open("rb") as fp:
            require_ok(
                client.post_multipart(
                    "/biz/audiofile/batchUpload",
                    data={"items[0].comments": comments},
                    files=[("items[0].file", (wav_path.name, fp, "audio/wav"))],
                ),
                "audio batch upload",
            )
        row = query_first_by_field(client, "/biz/audiofile/page", {"current": 1, "size": 50, "comments": comments}, "comments", comments)
        if not row:
            raise RuntimeError("uploaded custom audio not found")
        audio_id = str(row.get("id") or "")
        uploaded_ids.append(audio_id)
        add_step(steps, "custom audio batchUpload/query", "PASS", f"id={audio_id} size={row.get('sizeInfo')}", {"id": audio_id, "fileName": row.get("fileName")})

        edited_comments = f"{comments}_EDITED"
        require_ok(client.post_json("/biz/audiofile/edit", {"id": audio_id, "comments": edited_comments, "fileId": row.get("fileId")}), "audio edit comments")
        edited = query_first_by_field(client, "/biz/audiofile/page", {"current": 1, "size": 50, "comments": edited_comments}, "comments", edited_comments)
        if not edited:
            raise RuntimeError("custom audio edit was not reflected in page query")
        add_step(steps, "custom audio edit inline comments", "PASS", f"comments={edited_comments}", {"id": audio_id})

        download_url = str(edited.get("downloadPath") or "")
        if not download_url:
            raise RuntimeError("custom audio downloadPath is empty")
        downloaded = save_response(client.get_url(download_url), out_dir / "downloads" / f"{edited_comments}.audio")
        assert_audio_file(downloaded)
        add_step(steps, "custom audio download/play source", "PASS", f"bytes={downloaded.stat().st_size}", {"path": str(downloaded)})

        import_comments = f"{AUTO_PREFIX}AUDIO_IMPORT_{name_suffix}"
        import_mp3 = out_dir / "uploads" / f"{import_comments}.mp3"
        create_test_mp3(import_mp3, seconds=0.8)
        import_xlsx = out_dir / "uploads" / f"{import_comments}.xlsx"
        write_broadcast_import_xlsx(import_xlsx, [(f"{AUTO_PREFIX}BATCH_REPLY_{name_suffix}", import_comments, "A5 FA 00 81 08 00 2B FB")])
        handles = []
        try:
            mp3_fp = import_mp3.open("rb")
            xlsx_fp = import_xlsx.open("rb")
            handles.extend([mp3_fp, xlsx_fp])
            result = require_ok(
                client.post_multipart(
                    "/biz/audiofile/batchImport",
                    data={},
                    files=[
                        ("files", (import_mp3.name, mp3_fp, "audio/mpeg")),
                        ("files", (import_xlsx.name, xlsx_fp, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
                    ],
                ),
                "audio batch import",
            )
        finally:
            for handle in handles:
                handle.close()
        if not isinstance(result, list) or not result:
            raise RuntimeError(f"audio batchImport returned no rows: {result!r}")
        import_rows = [row for row in result if isinstance(row, dict)]
        add_step(steps, "custom audio folder batchImport", "PASS", f"rows={len(import_rows)}", {"rows": import_rows})
        return import_rows
    finally:
        if keep_records:
            add_step(steps, "retain custom audio", "PASS", f"kept {len(uploaded_ids)} uploaded record(s)", {"ids": uploaded_ids})
        else:
            delete_records(client, "/biz/audiofile/delete", uploaded_ids, steps, "custom audio")
            # batchImport normally returns mapping rows only. If backend persisted files, remove them too.
            leftovers = [row for row in query_rows(client, "/biz/audiofile/page", {"current": 1, "size": 100}, "custom audio cleanup page") if str(row.get("comments") or "").startswith(f"{AUTO_PREFIX}AUDIO_IMPORT_{name_suffix}")]
            delete_records(client, "/biz/audiofile/delete", [str(row.get("id")) for row in leftovers], steps, "custom audio import leftovers")


def validate_audio_synthesis_features(client: ListenAISynthesisClient, steps: List[StepResult], out_dir: Path, name_suffix: str, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], *, keep_records: bool = False) -> None:
    project_id = ""
    output_ids: List[str] = []
    try:
        project_name = f"{AUTO_PREFIX}SYNTH_{name_suffix}"
        require_ok(client.post_json("/fw/voice/add", {"projectName": project_name, "comments": "create by Mars-Belt full validation"}), "voice project add")
        project = query_first_by_field(client, "/fw/voice/page", {"current": 1, "size": 50, "projectName": project_name}, "projectName", project_name)
        if not project:
            raise RuntimeError("created voice project not found")
        project_id = str(project.get("id") or "")
        add_step(steps, "audio project add/page query", "PASS", f"id={project_id}", {"id": project_id, "projectName": project_name})

        detail = require_ok(client.get("/fw/voice/detail", {"id": project_id}), "voice project detail")
        add_step(steps, "audio project detail", "PASS", f"projectName={detail.get('projectName')}", {"detail": detail})

        edited_name = f"{project_name}_EDIT"
        require_ok(client.post_json("/fw/voice/edit", {"id": project_id, "projectName": edited_name, "comments": "edited by Mars-Belt"}), "voice project edit")
        edited = query_first_by_field(client, "/fw/voice/page", {"current": 1, "size": 50, "projectName": edited_name}, "projectName", edited_name)
        if not edited:
            raise RuntimeError("edited voice project not found")
        add_step(steps, "audio project edit/page query", "PASS", f"projectName={edited_name}", {"id": project_id})

        import_xlsx = out_dir / "uploads" / f"{AUTO_PREFIX}VOICE_IMPORT_{name_suffix}.xlsx"
        write_voice_import_xlsx(
            import_xlsx,
            [
                (1, f"voice_import_1_{name_suffix}", "音频合成导入第一条"),
                (2, f"voice_import_2_{name_suffix}", "音频合成导入第二条"),
            ],
        )
        with import_xlsx.open("rb") as fp:
            imported = require_ok(
                client.post_multipart(
                    "/fw/common/importRows",
                    data={},
                    files=[("file", (import_xlsx.name, fp, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
                ),
                "voice import rows",
            )
        if not isinstance(imported, list) or len(imported) < 2:
            raise RuntimeError(f"importRows did not return expected rows: {imported!r}")
        add_step(steps, "audio output Excel importRows", "PASS", f"rows={len(imported)}", {"rows": imported})

        base_payload = {
            "relatedId": project_id,
            "vcn": first_value(voice_options, "x2_xiaoye"),
            "speed": "50",
            "vol": "50",
            "compress": first_value(compress_options, "2"),
            "comments": f"{AUTO_PREFIX}VOICE_OUTPUT_{name_suffix}",
            "params": json.dumps({"rows": [{"idx": 1, "filename": f"manual_{name_suffix}", "text": "手动填写音频合成文本"}]}, ensure_ascii=False),
        }

        draft_payload = dict(base_payload)
        draft_payload["comments"] = f"{AUTO_PREFIX}VOICE_DRAFT_{name_suffix}"
        require_ok(client.post_json("/fw/voice/output/draft", draft_payload), "voice output draft")
        draft_rows = query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": project_id}, "voice output draft page")
        draft = find_row(draft_rows, "comments", draft_payload["comments"])
        if not draft:
            raise RuntimeError("saved draft not found")
        draft_id = str(draft.get("id") or "")
        output_ids.append(draft_id)
        add_step(steps, "audio output save draft/query", "PASS", f"id={draft_id} status={draft.get('status')}", {"id": draft_id})

        draft_detail = require_ok(client.get("/fw/voice/output/detail", {"id": draft_id}), "voice output detail")
        add_step(steps, "audio output detail", "PASS", f"id={draft_id}", {"detail": draft_detail})

        draft_edit_comments = f"{draft_payload['comments']}_COMMENTS"
        require_ok(client.post_json("/fw/voice/output/comments", {"id": draft_id, "comments": draft_edit_comments}), "voice output comments")
        add_step(steps, "audio output inline comments edit", "PASS", f"comments={draft_edit_comments}", {"id": draft_id})

        synth_from_draft = dict(base_payload)
        synth_from_draft["id"] = draft_id
        synth_from_draft["comments"] = f"{AUTO_PREFIX}VOICE_DRAFT_SYNTH_{name_suffix}"
        require_ok(client.post_json("/fw/voice/output/edit", synth_from_draft), "voice output edit synth")
        draft_final = poll_output_status(client, project_id, draft_id)
        if str(draft_final.get("status") or "") != "normal" or not draft_final.get("fileId"):
            raise RuntimeError(f"draft synth did not become normal with fileId: {draft_final}")
        add_step(steps, "audio output draft edit to synth", "PASS", f"status={draft_final.get('status')} fileId={draft_final.get('fileId')}", {"id": draft_id})

        manual_payload = dict(base_payload)
        manual_payload["comments"] = f"{AUTO_PREFIX}VOICE_OUTPUT_MANUAL_{name_suffix}"
        manual_payload["speed"] = "80"
        manual_payload["vol"] = "30"
        manual_payload["params"] = json.dumps({"rows": [{"idx": idx + 1, "filename": row.get("filename") or f"import_{idx+1}", "text": row.get("text") or "导入文本"} for idx, row in enumerate(imported)]}, ensure_ascii=False)
        require_ok(client.post_json("/fw/voice/output/add", manual_payload), "voice output add")
        manual_rows = query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": project_id}, "voice output add page")
        manual = find_row(manual_rows, "comments", manual_payload["comments"])
        if not manual:
            raise RuntimeError("manual voice output not found")
        manual_id = str(manual.get("id") or "")
        output_ids.append(manual_id)
        manual_final = poll_output_status(client, project_id, manual_id)
        if str(manual_final.get("status") or "") != "normal" or not manual_final.get("fileId"):
            raise RuntimeError(f"manual output did not become normal with fileId: {manual_final}")
        add_step(steps, "audio output manual add/imported rows synth", "PASS", f"id={manual_id} status={manual_final.get('status')}", {"id": manual_id, "fileId": manual_final.get("fileId")})

        zip_response = client.get("/dev/file/download", {"id": manual_final.get("fileId")}, blob=True)
        zip_path = save_response(zip_response, out_dir / "downloads" / f"voice_output_{manual_id}.zip")
        assert_zip(zip_path)
        add_step(steps, "audio output zip download", "PASS", f"bytes={zip_path.stat().st_size}", {"path": str(zip_path)})
    finally:
        if keep_records:
            add_step(steps, "retain voice synthesis records", "PASS", f"projectId={project_id} outputIds={output_ids}", {"projectId": project_id, "outputIds": output_ids})
        else:
            delete_records(client, "/fw/voice/output/delete", output_ids, steps, "voice output")
            delete_records(client, "/fw/voice/delete", [project_id], steps, "voice project")


def build_broadcast_release_payload(prod_id: str, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], name_suffix: str, *, auto_play: bool, comments: str, play_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if play_rows:
        play_config = [
            {
                "id": str(uuid.uuid4()),
                "reply": str(row.get("reply") or f"导入播报_{idx}"),
                **({"recProtocol": str(row.get("recProtocol"))} if not auto_play and row.get("recProtocol") else {}),
            }
            for idx, row in enumerate(play_rows, 1)
        ]
    else:
        play_config = [{"id": str(uuid.uuid4()), "reply": f"{AUTO_PREFIX}播报内容_{name_suffix}"}]
        if not auto_play:
            play_config[0]["recProtocol"] = "A5 FA 00 81 08 00 2B FB"

    ctrl_types = ["欢迎语", "增大音量", "减小音量", "最大音量", "最小音量", "中等音量"]
    ctrl_config = []
    for idx, ctrl_type in enumerate(ctrl_types):
        row = {"id": str(uuid.uuid4()), "type": ctrl_type, "reply": f"{ctrl_type}播报"}
        if ctrl_type != "欢迎语":
            row["recProtocol"] = f"A5 FA 00 81 08 0{idx} 2B FB"
        ctrl_config.append(row)

    return {
        "prodId": prod_id,
        "volLevel": "5",
        "defaultVol": 3,
        "volMaxOverflow": "音量已最大",
        "volMinOverflow": "音量已最小",
        "uportUart": "1",
        "uportBaud": "9600",
        "traceUart": "0",
        "traceBaud": "115200",
        "logLevel": "1",
        "vcn": first_value(voice_options, "x2_xiaoye"),
        "speed": 60,
        "vol": 80,
        "compress": first_value(compress_options, "2"),
        "word": "欢迎使用聆思科技语音方案",
        "autoPlayEnable": auto_play,
        "intervalTime": 1000,
        "repeatCnt": 1 if auto_play else -1,
        "playConfig": play_config,
        "ctrlConfig": ctrl_config,
        "paConfigEnable": True,
        "ctlIoPad": "PB",
        "ctlIoNum": 11,
        "holdTime": 20000,
        "paConfigEnableLevel": "high",
        "comments": comments,
    }


def validate_broadcast_features(client: ListenAISynthesisClient, steps: List[StepResult], out_dir: Path, name_suffix: str, voice_options: List[Dict[str, Any]], compress_options: List[Dict[str, Any]], imported_play_rows: List[Dict[str, Any]], *, publish: bool, keep_records: bool = False) -> None:
    product_id = ""
    release_ids: List[str] = []
    try:
        options = require_ok(client.get("/biz/broadcast/options"), "broadcast options")
        option = next((item for item in options if item.get("board") == "CSK3021"), None) or (options[0] if options else None)
        if not option:
            raise RuntimeError("no broadcast board options")
        version_options = option.get("versionOptions") or []
        if not version_options:
            raise RuntimeError(f"no version options for {option.get('board')}")
        version = version_options[0]

        product_name = f"{AUTO_PREFIX}BROADCAST_{name_suffix}"
        product_payload = {
            "name": product_name,
            "chipName": option.get("board") or option.get("mark") or "CSK3021",
            "defId": version.get("value"),
            "chipVersion": version.get("label"),
        }
        require_ok(client.post_json("/biz/broadcast/add", product_payload), "broadcast product add")
        product = query_first_by_field(client, "/biz/broadcast/page", {"current": 1, "size": 50, "name": product_name}, "name", product_name)
        if not product:
            raise RuntimeError("created broadcast product not found")
        product_id = str(product.get("id") or "")
        add_step(steps, "broadcast product add/page query", "PASS", f"id={product_id} chip={product_payload['chipName']}", {"id": product_id})

        detail = require_ok(client.get("/biz/broadcast/detail", {"id": product_id}), "broadcast product detail")
        add_step(steps, "broadcast product detail", "PASS", f"name={detail.get('name')}", {"detail": detail})

        edited_name = f"{product_name}_EDIT"
        edit_payload = dict(product_payload)
        edit_payload["id"] = product_id
        edit_payload["name"] = edited_name
        require_ok(client.post_json("/biz/broadcast/edit", edit_payload), "broadcast product edit")
        edited = query_first_by_field(client, "/biz/broadcast/page", {"current": 1, "size": 50, "name": edited_name}, "name", edited_name)
        if not edited:
            raise RuntimeError("edited broadcast product not found")
        add_step(steps, "broadcast product edit/page query", "PASS", f"name={edited_name}", {"id": product_id})

        auto_comments = f"{AUTO_PREFIX}BROADCAST_RELEASE_AUTO_{name_suffix}"
        auto_payload = build_broadcast_release_payload(product_id, voice_options, compress_options, name_suffix, auto_play=True, comments=auto_comments)
        require_ok(client.post_json("/biz/broadcastrelease/add", auto_payload), "broadcast release auto add")
        auto_release = find_row(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": product_id}, "broadcast release page"), "comments", auto_comments)
        if not auto_release:
            raise RuntimeError("auto broadcast release not found")
        auto_release_id = str(auto_release.get("id") or "")
        release_ids.append(auto_release_id)
        add_step(steps, "broadcast release autoPlay manual fields add/query", "PASS", f"id={auto_release_id} status={auto_release.get('status')}", {"id": auto_release_id})

        release_detail = require_ok(client.get("/biz/broadcastrelease/detail", {"id": auto_release_id}), "broadcast release detail")
        add_step(steps, "broadcast release detail", "PASS", f"id={auto_release_id}", {"detail": release_detail})

        if publish:
            require_ok(client.get("/biz/broadcastrelease/publish", {"id": auto_release_id, "prodId": product_id}), "broadcast release publish")
            final_record = poll_broadcast_release(client, product_id, auto_release_id)
            if str(final_record.get("status") or "") != "success":
                raise RuntimeError(f"broadcast publish did not finish successfully: {final_record}")
            add_step(steps, "broadcast release publish/poll", "PASS", "status=success", {"id": auto_release_id, "pkgTaskId": final_record.get("pkgTaskId"), "pkgPipelineId": final_record.get("pkgPipelineId")})

            task_id = final_record.get("pkgTaskId")
            pipeline_id = final_record.get("pkgPipelineId")
            if not task_id:
                raise RuntimeError(f"published release has no pkgTaskId: {final_record}")
            download_params = {"taskId": task_id}
            if pipeline_id:
                download_params["pipelineId"] = pipeline_id
            sdk_response = client.get("/biz/release/download", download_params, blob=True)
            sdk_path = save_response(sdk_response, out_dir / "downloads" / f"broadcast_sdk_{auto_release_id}.zip")
            assert_zip(sdk_path)
            add_step(steps, "broadcast SDK download", "PASS", f"bytes={sdk_path.stat().st_size}", {"path": str(sdk_path)})

        manual_comments = f"{AUTO_PREFIX}BROADCAST_RELEASE_MANUAL_{name_suffix}"
        manual_payload = build_broadcast_release_payload(product_id, voice_options, compress_options, name_suffix, auto_play=False, comments=manual_comments, play_rows=imported_play_rows)
        require_ok(client.post_json("/biz/broadcastrelease/add", manual_payload), "broadcast release manual add")
        manual_release = find_row(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": product_id}, "broadcast release page manual"), "comments", manual_comments)
        if not manual_release:
            raise RuntimeError("manual broadcast release not found")
        manual_release_id = str(manual_release.get("id") or "")
        release_ids.append(manual_release_id)
        add_step(steps, "broadcast release protocol/manual rows add/query", "PASS", f"id={manual_release_id} status={manual_release.get('status')}", {"id": manual_release_id, "playRows": imported_play_rows})

        edited_payload = dict(manual_payload)
        edited_payload["id"] = manual_release_id
        edited_payload["comments"] = f"{manual_comments}_EDIT"
        edited_payload["intervalTime"] = 1500
        edited_payload["repeatCnt"] = 2
        require_ok(client.post_json("/biz/broadcastrelease/edit", edited_payload), "broadcast release edit")
        edited_release = find_row(query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": product_id}, "broadcast release page edited"), "comments", edited_payload["comments"])
        if not edited_release:
            raise RuntimeError("edited broadcast release not found")
        add_step(steps, "broadcast release edit/page query", "PASS", f"comments={edited_payload['comments']}", {"id": manual_release_id})

        before_ids = set(release_ids)
        require_ok(client.get("/biz/broadcastrelease/duplicate", {"id": manual_release_id}), "broadcast release duplicate")
        duplicated_rows = query_rows(client, "/biz/broadcastrelease/page", {"current": 1, "size": 50, "prodId": product_id}, "broadcast release page duplicate")
        duplicate_ids = [str(row.get("id")) for row in duplicated_rows if row.get("id") and str(row.get("id")) not in before_ids]
        if not duplicate_ids:
            raise RuntimeError("duplicate broadcast release did not create a new record")
        release_ids.extend(duplicate_ids)
        add_step(steps, "broadcast release duplicate", "PASS", f"duplicateIds={duplicate_ids}", {"ids": duplicate_ids})
    finally:
        if keep_records:
            add_step(steps, "retain broadcast records", "PASS", f"productId={product_id} releaseIds={release_ids}", {"productId": product_id, "releaseIds": release_ids})
        else:
            delete_records(client, "/biz/broadcastrelease/delete", release_ids, steps, "broadcast release")
            delete_records(client, "/biz/broadcast/delete", [product_id], steps, "broadcast product")


def verify_no_auto_test_leftovers(client: ListenAISynthesisClient, steps: List[StepResult]) -> None:
    counts = {
        "voiceProjects": len([row for row in query_rows(client, "/fw/voice/page", {"current": 1, "size": 200}, "leftover voice page") if str(row.get("projectName") or "").startswith(AUTO_PREFIX)]),
        "broadcastProducts": len([row for row in query_rows(client, "/biz/broadcast/page", {"current": 1, "size": 200}, "leftover broadcast page") if str(row.get("name") or "").startswith(AUTO_PREFIX)]),
        "audioFiles": len([row for row in query_rows(client, "/biz/audiofile/page", {"current": 1, "size": 200}, "leftover audio page") if str(row.get("comments") or "").startswith(AUTO_PREFIX) or str(row.get("fileName") or "").startswith(AUTO_PREFIX)]),
    }
    status = "PASS" if all(value == 0 for value in counts.values()) else "FAIL"
    add_step(steps, "final AUTO_TEST leftover scan", status, ", ".join(f"{k}={v}" for k, v in counts.items()), counts)


def verify_retained_auto_test_records(client: ListenAISynthesisClient, steps: List[StepResult]) -> None:
    inventory = collect_auto_test_records(client)
    counts = inventory["counts"]
    required = counts["voiceProjects"] > 0 and counts["voiceOutputs"] > 0 and counts["broadcastProducts"] > 0 and counts["broadcastReleases"] > 0
    add_step(
        steps,
        "retained AUTO_TEST platform records scan",
        "PASS" if required else "FAIL",
        ", ".join(f"{key}={value}" for key, value in counts.items()),
        inventory,
    )


def run_section(name: str, steps: List[StepResult], func: Callable[[], Any]) -> Any:
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        add_step(steps, name, "FAIL", str(exc))
        return None


def write_reports(out_dir: Path, steps: List[StepResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total": len(steps),
        "pass": sum(1 for step in steps if step.status == "PASS"),
        "fail": sum(1 for step in steps if step.status == "FAIL"),
    }
    payload = {
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "baseUrl": BASE_URL,
        "scope": "合成管理全功能链路：音频合成 + 播报合成 + 自定义音频",
        "summary": summary,
        "steps": [step.__dict__ for step in steps],
    }
    (out_dir / "synthesis_validation_result.json").write_text(json.dumps(scrub(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    keep_records = any(step.name == "final cleanup skipped for manual inspection" and step.status == "PASS" for step in steps)
    coverage = (
        "- 覆盖：下载、上传、Excel 导入、手填字段、创建、查询、详情、编辑、产物合成、SDK 发布、zip 下载、复制，并保留平台记录供人工复核。"
        if keep_records
        else "- 覆盖：下载、上传、Excel 导入、手填字段、创建、查询、详情、编辑、产物合成、SDK 发布、zip 下载、复制、删除和清理复核。"
    )
    lines = ["# 合成管理全功能链路验证结果", "", f"时间：{payload['createdAt']}", "", "## 结论", ""]
    if summary["fail"] == 0:
        lines.append(f"- 结果：PASS，{summary['pass']}/{summary['total']} 个功能点通过。")
    else:
        lines.append(f"- 结果：FAIL，PASS {summary['pass']}/{summary['total']}，失败 {summary['fail']} 项。")
    lines.extend([
        coverage,
        "",
        "## 功能点明细",
        "",
        "| 功能点 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ])
    for step in steps:
        lines.append(f"| {step.name} | {step.status} | {sanitize_text(step.detail).replace('|', '\\|')} |")
    lines.extend([
        "",
        "## 产物",
        "",
        f"- JSON：`{out_dir / 'synthesis_validation_result.json'}`",
        f"- 下载目录：`{out_dir / 'downloads'}`",
        f"- 上传构造目录：`{out_dir / 'uploads'}`",
    ])
    (out_dir / "synthesis_validation_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate ListenAI synthesis-management APIs.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token; defaults to env/local config")
    parser.add_argument("--out-dir", default="", help="Output directory")
    parser.add_argument("--skip-writes", action="store_true", help="Only run readonly and preview checks")
    parser.add_argument("--publish-broadcast", action="store_true", help="Trigger broadcast SDK generation and poll for success")
    parser.add_argument("--keep-platform-records", action="store_true", help="Do not clean generated AUTO_TEST rows; use only for manual UI inspection")
    parser.add_argument("--no-persist-token", action="store_true", help="Do not write token back to TOOLS.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = resolve_listenai_token(args.token, persist=not args.no_persist_token)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ARTIFACTS_ROOT / "synthesis-validation" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    client = ListenAISynthesisClient(token)
    steps: List[StepResult] = []
    name_suffix = datetime.now().strftime("%Y%m%d%H%M%S")

    context: Dict[str, Any] = {"voice_options": [], "compress_options": [], "imported_play_rows": []}

    run_section("initial AUTO_TEST cleanup", steps, lambda: cleanup_auto_test_records(client, steps, "initial"))

    def readonly() -> None:
        _, voice_options, compress_options = validate_readonly(client, steps, out_dir)
        context["voice_options"] = voice_options
        context["compress_options"] = compress_options

    run_section("readonly discovery", steps, readonly)

    voice_options = context.get("voice_options") or []
    compress_options = context.get("compress_options") or []
    run_section("preview audio", steps, lambda: validate_preview_audio(client, steps, voice_options, compress_options))

    if not args.skip_writes:
        rows = run_section("custom audio feature flow", steps, lambda: validate_custom_audio_features(client, steps, out_dir, name_suffix, keep_records=args.keep_platform_records))
        context["imported_play_rows"] = rows or []
        run_section("audio synthesis full feature flow", steps, lambda: validate_audio_synthesis_features(client, steps, out_dir, name_suffix, voice_options, compress_options, keep_records=args.keep_platform_records))
        run_section("broadcast synthesis full feature flow", steps, lambda: validate_broadcast_features(client, steps, out_dir, name_suffix, voice_options, compress_options, context["imported_play_rows"], publish=args.publish_broadcast, keep_records=args.keep_platform_records))

    if args.keep_platform_records:
        add_step(steps, "final cleanup skipped for manual inspection", "PASS", "AUTO_TEST records intentionally retained on platform")
        run_section("retained record verification", steps, lambda: verify_retained_auto_test_records(client, steps))
    else:
        run_section("final cleanup", steps, lambda: cleanup_auto_test_records(client, steps, "final"))
        run_section("leftover verification", steps, lambda: verify_no_auto_test_leftovers(client, steps))

    write_reports(out_dir, steps)
    print(str(out_dir))
    return 0 if all(step.status == "PASS" for step in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
