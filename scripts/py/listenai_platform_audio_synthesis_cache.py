#!/usr/bin/env python3
"""Create/reuse platform audio-synthesis test assets for runtime tests.

This script intentionally uses 合成管理 -> 音频合成 records
(`/fw/voice/*`) instead of the lightweight `/fw/common/generateAudio` preview
endpoint, because firmware runtime tests need platform-visible build evidence
and reusable downloaded audio artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests
import urllib3

from listenai_task_support import resolve_listenai_token

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend"
DEFAULT_EN_SPEAKERS = [
    "x4_EnUs_Luna_assist",
    "x4_EnUs_Gavin_assist",
    "x4_EnUk_Lydia_edu",
    "x4_lingxiaoying_en",
]
DEFAULT_ZH_SPEAKERS = [
    "x2_xiaoye",
    "x2_yezi",
    "x4_yezi",
    "x_nannan",
]


class Client:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"token": token})

    def get(self, path: str, params: Dict[str, Any] | None = None, *, blob: bool = False) -> Any:
        resp = self.session.get(BASE_URL + path, params=params or {}, timeout=180, stream=blob)
        resp.raise_for_status()
        if blob:
            return resp
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(f"{path} failed: code={data.get('code')} msg={data.get('msg')}")
        return data.get("data")

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        resp = self.session.post(
            BASE_URL + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(f"{path} failed: code={data.get('code')} msg={data.get('msg')}")
        return data.get("data")


def safe_audio_name(text: str, language: str) -> str:
    text = str(text or "").strip()
    if language == "en":
        name = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    else:
        common_zh = {
            "小聆小聆": "xiao_ling_xiao_ling",
            "小优小优": "xiao_you_xiao_you",
            "小乐小乐": "xiao_le_xiao_le",
            "打开风扇": "da_kai_feng_shan",
            "关闭风扇": "guan_bi_feng_shan",
            "查询状态": "cha_xun_zhuang_tai",
            "增大音量": "zeng_da_yin_liang",
            "减小音量": "jian_xiao_yin_liang",
            "最大音量": "zui_da_yin_liang",
            "最小音量": "zui_xiao_yin_liang",
            "中等音量": "zhong_deng_yin_liang",
            "退出识别": "tui_chu_shi_bie",
            "学习命令词": "xue_xi_ming_ling_ci",
            "删除命令词": "shan_chu_ming_ling_ci",
            "查询唤醒词": "cha_xun_huan_xing_ci",
            "切换唤醒词": "qie_huan_huan_xing_ci",
            "切换到小优小优": "qie_huan_dao_xiao_you_xiao_you",
            "切换到小乐小乐": "qie_huan_dao_xiao_le_xiao_le",
            "恢复默认唤醒词": "hui_fu_mo_ren_huan_xing_ci",
        }
        name = common_zh.get(text)
        if not name:
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
            name = f"zh_{digest}"
    return (name or "audio")[:80]


def children_by_value(nodes: Sequence[Dict[str, Any]], value: str) -> List[Dict[str, Any]]:
    for node in nodes:
        if node.get("dictValue") == value or node.get("dictLabel") == value:
            return [x for x in node.get("children") or [] if isinstance(x, dict)]
        found = children_by_value([x for x in node.get("children") or [] if isinstance(x, dict)], value)
        if found:
            return found
    return []


def query_rows(client: Client, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = client.get(path, params)
    if isinstance(data, dict):
        for key in ("records", "rows", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def is_english_speaker(label: str, value: str) -> bool:
    return bool(re.search(r"(英语|英文|English|EnUs|EnUk|_en\b|en_)", f"{label} {value}", re.I))


def choose_speaker(dict_tree: List[Dict[str, Any]], language: str, requested: str = "") -> Dict[str, str]:
    voices = children_by_value(dict_tree, "voice")
    candidates: List[Dict[str, str]] = []
    for item in voices:
        label = str(item.get("dictLabel") or "")
        value = str(item.get("dictValue") or "")
        english = is_english_speaker(label, value)
        if language == "en" and english:
            candidates.append({"label": label, "value": value})
        elif language == "zh" and not english and not re.search(r"(日语|韩语|JaJp|KoKr)", f"{label} {value}", re.I):
            candidates.append({"label": label, "value": value})
    if requested:
        hit = next((item for item in candidates if item["value"] == requested or item["label"] == requested), None)
        if not hit:
            raise RuntimeError(f"requested speaker is not a valid {language} speaker option: {requested}")
        return hit
    preferred = DEFAULT_EN_SPEAKERS if language == "en" else DEFAULT_ZH_SPEAKERS
    for value in preferred:
        hit = next((item for item in candidates if item["value"] == value), None)
        if hit:
            return hit
    if candidates:
        return candidates[0]
    raise RuntimeError(f"no {language} speaker found in platform dictionary")


def audio_candidates(text: str, out_dir: Path, language: str) -> List[Path]:
    name = safe_audio_name(text, language)
    return [out_dir / f"{name}.mp3", out_dir / f"{name}.wav"]


def all_cached(texts: Sequence[str], out_dir: Path, language: str) -> bool:
    return all(any(p.exists() and p.stat().st_size > 0 for p in audio_candidates(text, out_dir, language)) for text in texts)


def create_audio_synthesis(
    client: Client,
    texts: Sequence[str],
    out_dir: Path,
    speaker: Dict[str, str],
    *,
    project_prefix: str,
    language: str,
    work_dir: Path,
) -> Dict[str, Any]:
    dict_tree = client.get("/dev/dict/tree")
    compress_options = children_by_value(dict_tree, "compress")
    compress = str((compress_options[0].get("dictValue") if compress_options else "") or "2")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"{project_prefix}_{stamp}"
    client.post("/fw/voice/add", {"projectName": project_name, "comments": "Mars-Belt runtime audio asset; keep for reuse"})
    project = next((row for row in query_rows(client, "/fw/voice/page", {"current": 1, "size": 50, "projectName": project_name}) if row.get("projectName") == project_name), None)
    if not project:
        raise RuntimeError("created audio synthesis project not found")
    project_id = str(project.get("id") or "")
    rows = [{"idx": idx + 1, "filename": safe_audio_name(text, language), "text": text} for idx, text in enumerate(texts)]
    comments = f"{project_prefix}_OUTPUT_{stamp}"
    # Current UI sends params as an object. Older API tolerated a JSON string,
    # but the restored endpoint now returns 415 for that legacy shape.
    payload = {
        "relatedId": project_id,
        "vcn": speaker["value"],
        "speed": "50",
        "vol": "50",
        "compress": compress,
        "comments": comments,
        "params": {"rows": rows},
    }
    client.post("/fw/voice/output/add", payload)
    output = None
    for _ in range(90):
        output = next((row for row in query_rows(client, "/fw/voice/output/page", {"current": 1, "size": 50, "relatedId": project_id}) if row.get("comments") == comments), None)
        if output and str(output.get("status") or "") == "normal" and output.get("fileId"):
            break
        time.sleep(2)
    if not output or str(output.get("status") or "") != "normal" or not output.get("fileId"):
        raise RuntimeError(f"audio synthesis output did not finish normally: {output}")

    extract_dir = work_dir / "extracted"
    work_dir.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / f"{comments}.zip"
    zip_path.write_bytes(client.get("/dev/file/download", {"id": output.get("fileId")}, blob=True).content)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    items: List[Dict[str, Any]] = []
    for audio_file in sorted(extract_dir.rglob("*")):
        if not audio_file.is_file() or audio_file.suffix.lower() not in {".mp3", ".wav"}:
            continue
        target = out_dir / audio_file.name
        shutil.copy2(audio_file, target)
        items.append({
            "file": target.name,
            "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    if not items:
        raise RuntimeError("audio synthesis zip contains no mp3/wav files")
    return {
        "projectName": project_name,
        "projectId": project_id,
        "outputId": str(output.get("id") or ""),
        "fileId": str(output.get("fileId") or ""),
        "zipPath": str(zip_path),
        "items": items,
        "platformRecordsKept": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reuse or create official platform audio-synthesis test assets.")
    parser.add_argument("--text", action="append", required=True, help="Text to synthesize. Repeatable.")
    parser.add_argument("--language", choices=["zh", "en"], default="en")
    parser.add_argument("--suite", default="", help="Logical asset suite name under assets/audio/platform_synthesis/<language>/")
    parser.add_argument("--out-dir", default="", help="Override asset directory. Default: assets/audio/platform_synthesis/<language>/<suite>.")
    parser.add_argument("--speaker", default="", help="Speaker value/label. Default picks a platform speaker matching --language.")
    parser.add_argument("--project-prefix", default="EN_AUDIO_SYNTH_3021_BASE")
    parser.add_argument("--work-dir", default="", help="Temporary build/download directory. Default: artifacts/audio-synthesis-assets/<timestamp>.")
    parser.add_argument("--force-synthesize", action="store_true", help="Create a new platform audio-synthesis record even if cache exists.")
    args = parser.parse_args()

    suite = args.suite or ("3021_fan_base" if args.language == "en" else "3021_common_base")
    if args.language == "zh" and args.project_prefix == "EN_AUDIO_SYNTH_3021_BASE":
        args.project_prefix = "ZH_AUDIO_SYNTH_3021_BASE"
    out_dir = Path(args.out_dir or Path("assets/audio/platform_synthesis") / args.language / suite)
    work_dir = Path(args.work_dir or Path("artifacts/audio-synthesis-assets") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    token = resolve_listenai_token()
    client = Client(token)
    dict_tree = client.get("/dev/dict/tree")
    speaker = choose_speaker(dict_tree, args.language, args.speaker)
    created: Dict[str, Any] | None = None
    if args.force_synthesize or not all_cached(args.text, out_dir, args.language):
        created = create_audio_synthesis(client, args.text, out_dir, speaker, project_prefix=args.project_prefix, language=args.language, work_dir=work_dir)
    items = []
    for text in args.text:
        candidates = audio_candidates(text, out_dir, args.language)
        path = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
        if not path:
            raise RuntimeError(f"cache missing after synthesis: {text}")
        items.append({"text": text, "file": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {
        "assetType": "platform-audio-synthesis-test-asset",
        "language": "英文" if args.language == "en" else "中文",
        "languageCode": args.language,
        "suite": suite,
        "speaker": speaker,
        "source": "合成管理/音频合成正式输出，不是 generateAudio 试听接口",
        "usage": "固件运行态验证优先复用；缺失时使用本脚本重新走音频合成构建。该目录是测试资产，需要随 skill git 同步，不是 git 忽略缓存。",
        "createdBuild": created,
        "items": items,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outDir": str(out_dir), "speaker": speaker, "created": bool(created), "items": items}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
