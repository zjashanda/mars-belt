import argparse
import csv
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from listenai_auto_package import (
    ListenAIClient,
    apply_release_overrides,
    find_copied_release_id,
    poll_release_success,
    require_ok,
    wait_release_stable,
)
from listenai_batch_package_parameters import DEFAULT_CATALOG_JSON, load_json, verify_release
from listenai_packaging_rules import build_short_release_comment_from_selected
from listenai_shared_product_flow import package_release_for_existing_product
from listenai_task_support import PACKAGE_CACHE_ROOT, RUNTIME_ROOT, resolve_listenai_token


DEFAULT_SHARED_MANIFEST = str(
    RUNTIME_ROOT / "listenai_grouped_parameter_packages" / "heater-3021-zh-grouped-v1" / "manifest.json"
)
DEFAULT_TRIAL_ROOT = str(RUNTIME_ROOT / "listenai_grouped_parameter_packages" / "heater-3021-zh-advanced-combo-trials")
DEFAULT_PACKAGE_ROOT = str(PACKAGE_CACHE_ROOT / "heater-3021-zh-advanced-combo-trials")


DEFAULT_RELEASE_REGIST = {
    "registMode": "contLearn",
    "registType": "all",
    "wakeupRepeatCount": 2,
    "wakeupWordsMaxLimit": 5,
    "wakeupWordsMinLimit": 4,
    "wakeupSensitivity": "中",
    "wakeupRegistMaxLimit": 1,
    "wakeupRetryCount": 1,
    "commandRepeatCount": 2,
    "commandWordsMaxLimit": 6,
    "commandWordsMinLimit": 4,
    "commandSensitivity": "中",
    "commandRegistMaxLimit": 1,
    "commandRetryCount": 1,
    "reply": "我在",
    "replyMode": "主",
    "sndProtocol": "",
    "recProtocol": "",
}

DEFAULT_RELEASE_REGIST_CONFIG = {
    "common": {
        "label": "公用配置",
        "triggers": {
            "common": {
                "label": "公用配置",
                "stages": {
                    "stage_record": [
                        {"condition": "当录入成功时", "reply": "录入成功，请再说一次", "tags": ["record_success"]},
                        {"condition": "当录入失败时", "reply": "录入失败，请再说一次", "tags": ["record_failed"]},
                        {
                            "condition": "当录入语速过快或过慢时",
                            "reply": "检测到语速过快或过慢，请使用正常语速录入",
                            "tags": ["speed_error"],
                        },
                        {
                            "condition": "当录入字数超出上下限时",
                            "reply": "检测到字数超出有效范围，请换种说法",
                            "tags": ["length_error"],
                        },
                        {
                            "condition": "当前后两次录入内容不一致时",
                            "reply": "前后两次录入内容不一致，请再说一次",
                            "tags": ["simila_error"],
                        },
                        {
                            "condition": "当录入指令冲突时",
                            "reply": "检测到与默认指令冲突，请换种说法",
                            "tags": ["command_conflict"],
                        },
                        {"condition": "当学习模板已满时", "reply": "学习模板已达上限", "tags": ["template_full"]},
                    ],
                    "stage_result": [
                        {"condition": "当学习成功时", "reply": "学习成功", "tags": ["study_success"]},
                        {"condition": "当学习失败时", "reply": "学习失败", "tags": ["study_failed"]},
                        {"condition": "当学习完成时", "reply": "学习完成", "tags": ["study_complete"]},
                    ],
                    "stage_delete_result": [
                        {"condition": "当删除失败时", "reply": "删除失败", "tags": ["delete_failed"]}
                    ],
                    "stage_learn_exit": [
                        {"condition": "退出学习", "reply": "退出学习模式", "tags": ["algo", "STUDY_QUIT"]}
                    ],
                    "stage_delete_exit": [
                        {"condition": "退出删除", "reply": "退出删除模式", "tags": ["algo", "DELETE_REGISTERED_EXIT"]}
                    ],
                },
            }
        },
    },
    "contLearn": {
        "label": "连续学习",
        "triggers": {
            "learn_wakeup": {
                "label": "学习唤醒词",
                "stages": {
                    "stage_start": [
                        {
                            "condition": "当开始时提示",
                            "reply": "请在安静环境下，按提示学习唤醒词，请说唤醒词的指令",
                            "tags": ["algo", "study_wakeup_start", "VOICE_REGISTER_WAKE"],
                        }
                    ]
                },
            },
            "learn_command": {
                "label": "学习命令词",
                "stages": {
                    "stage_start": [
                        {
                            "condition": "当开始时提示",
                            "reply": "请在安静环境下，按提示学习命令词",
                            "tags": ["algo", "study_asr_start", "VOICE_REGISTER_CMD"],
                        }
                    ],
                    "stage_exception": [
                        {
                            "condition": "学习到最后一个命令时",
                            "reply": "当前已经是最后一条可学习命令词",
                            "tags": ["study_asr_over_tail"],
                        },
                        {
                            "condition": "没有上一条可用于重复学习时",
                            "reply": "当前已经是第一条可学习命令词",
                            "tags": ["study_asr_over_head"],
                        },
                    ],
                    "stage_learn_next": [
                        {"condition": "学习下一个", "reply": "学习下一个", "tags": ["algo", "STUDY_NEXT"]}
                    ],
                    "stage_relearn_prev": [
                        {"condition": "重新学习", "reply": "重新学习上一个", "tags": ["algo", "RELEARN"]}
                    ],
                    "stage_learn_command": [],
                },
            },
            "delete_wakeup": {
                "label": "删除唤醒词",
                "stages": {
                    "stage_process": [
                        {"condition": "当进入后进行删除确认时", "reply": "请再说一遍", "tags": ["study_wakeup_delete_start"]}
                    ],
                    "stage_delete_wakeup": [
                        {"condition": "", "reply": "唤醒词删除成功", "tags": ["algo", "study_wakeup_delete_success", "DELETE_REGISTERED_WAKE"]}
                    ],
                },
            },
            "delete_command": {
                "label": "删除命令词",
                "stages": {
                    "stage_process": [
                        {"condition": "当进入后进行删除确认时", "reply": "请再说一遍", "tags": ["study_asr_delete_start"]}
                    ],
                    "stage_delete_command": [
                        {"condition": "", "reply": "命令词删除成功", "tags": ["algo", "study_asr_delete_success", "DELETE_REGISTERED_CMD"]}
                    ],
                },
            },
            "delete_all": {
                "label": "全部删除",
                "stages": {
                    "stage_process": [
                        {"condition": "当进入后进行删除确认时", "reply": "请再说一遍", "tags": ["study_all_delete_start"]}
                    ],
                    "stage_delete_all": [
                        {"condition": "", "reply": "全部删除成功", "tags": ["algo", "study_all_delete_success", "DELETE_REGISTERED_ALL"]}
                    ],
                },
            },
        },
    },
    "specificLearn": {
        "label": "指定学习",
        "triggers": {
            "learn_wakeup": {
                "label": "学习唤醒词",
                "stages": {
                    "stage_start": [
                        {
                            "condition": "当开始时提示",
                            "reply": "请在安静环境下，按提示学习唤醒词，请说唤醒词的指令",
                            "tags": ["algo", "study_wakeup_start", "VOICE_REGISTER_WAKE"],
                        }
                    ]
                },
            },
            "delete_wakeup": {
                "label": "删除唤醒词",
                "stages": {
                    "stage_process": [
                        {"condition": "当进入后进行删除确认时", "reply": "请再说一遍", "tags": ["study_wakeup_delete_start"]}
                    ],
                    "stage_delete_wakeup": [
                        {"condition": "", "reply": "唤醒词删除成功", "tags": ["algo", "study_wakeup_delete_success", "DELETE_REGISTERED_WAKE"]}
                    ],
                },
            },
            "learn_all": {
                "label": "/",
                "stages": {
                    "stage_learn": [
                        {
                            "condition": "学习命令词",
                            "reply": "请在安静环境下，说出你想要学习的命令词",
                            "tags": ["algo", "study_asr_start", "VOICE_REGISTER_CMD"],
                        }
                    ],
                    "stage_delete": [
                        {
                            "condition": "删除命令词",
                            "reply": "请说你想要删除的命令词",
                            "tags": ["algo", "study_asr_delete_start", "DELETE_REGISTERED_CMD"],
                        }
                    ],
                    "stage_delete_all": [
                        {
                            "condition": "删除全部命令词",
                            "reply": "命令词学习数据已全部删除",
                            "tags": ["algo", "study_allasr_delete_success", "DELETE_ALL_REGISTERED_CMD"],
                        }
                    ],
                    "stage_exception": [
                        {
                            "condition": "当指定的指令不在已选择词条范围内时",
                            "reply": "该指令不支持学习或删除，请换个指令",
                            "tags": ["study_asr_oov"],
                        }
                    ],
                    "stage_learn_command": [],
                },
            },
            "delete_all": {
                "label": "全部删除",
                "stages": {
                    "stage_process": [
                        {"condition": "当进入后进行删除确认时", "reply": "请再说一遍", "tags": ["study_all_delete_start"]}
                    ],
                    "stage_delete_all": [
                        {"condition": "", "reply": "全部删除成功", "tags": ["algo", "study_all_delete_success", "DELETE_REGISTERED_ALL"]}
                    ],
                },
            },
        },
    },
}


DEFAULT_RELEASE_MULTI_WKE = {
    "common": [
        {"type": "query", "condition": "查询唤醒词", "reply": ""},
        {"type": "restore", "condition": "恢复默认唤醒词", "reply": ""},
        {"type": "switch", "condition": "切换唤醒词", "reply": "请说您想切换的唤醒词"},
    ],
    "wkelist": [],
}


def build_default_voice_reg_payload() -> Dict[str, Any]:
    return {
        "releaseRegist": deepcopy(DEFAULT_RELEASE_REGIST),
        "releaseRegistConfig": deepcopy(DEFAULT_RELEASE_REGIST_CONFIG),
    }


def normalize_voice_reg_learn_commands(commands: Optional[Sequence[str]]) -> List[str]:
    unique: List[str] = []
    for raw in commands or []:
        word = str(raw or "").strip()
        if word and word not in unique:
            unique.append(word)
    return unique


def extract_voice_reg_learn_commands(release_regist_config: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(release_regist_config, dict):
        return []

    commands: List[str] = []
    for path in [
        ("contLearn", "learn_command"),
        ("specificLearn", "learn_all"),
    ]:
        section = (
            release_regist_config.get(path[0], {})
            .get("triggers", {})
            .get(path[1], {})
            .get("stages", {})
            .get("stage_learn_command", [])
        )
        for item in section or []:
            if not isinstance(item, dict):
                continue
            word = str(item.get("condition") or "").strip()
            if word and word not in commands:
                commands.append(word)
    return commands


def select_voice_reg_learn_commands(
    release_algo_list: Optional[Sequence[Dict[str, Any]]],
    fallback: Optional[Sequence[str]] = None,
    limit: int = 3,
) -> List[str]:
    commands: List[str] = []
    for item in release_algo_list or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("intent") or "").strip()
        item_type = str(item.get("type") or "").strip()
        special_type = str(item.get("specialType") or item.get("special_type") or "").strip()
        if not word or item_type != "命令词" or special_type:
            continue
        if word in commands:
            continue
        commands.append(word)
        if len(commands) >= max(1, int(limit)):
            break
    return commands or normalize_voice_reg_learn_commands(fallback)


def build_voice_reg_stage_entries(learn_commands: Sequence[str], specific_mode: bool) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for word in normalize_voice_reg_learn_commands(learn_commands):
        if specific_mode:
            entries.append(
                {
                    "condition": word,
                    "reply": f"开始学习{word}，请在安静环境下说出新的指令",
                    "delReply": f"{word}的学习数据已经删除",
                    "tags": ["reg_commands"],
                }
            )
        else:
            entries.append(
                {
                    "condition": word,
                    "reply": f"请说{word}的相关指令",
                    "tags": ["reg_commands"],
                }
            )
    return entries


def apply_voice_reg_learn_commands(payload: Dict[str, Any], learn_commands: Sequence[str]) -> List[str]:
    selected = normalize_voice_reg_learn_commands(learn_commands)
    if not selected:
        return []

    release_regist_config = payload.setdefault("releaseRegistConfig", deepcopy(DEFAULT_RELEASE_REGIST_CONFIG))
    cont_stages = (
        release_regist_config.setdefault("contLearn", {})
        .setdefault("triggers", {})
        .setdefault("learn_command", {})
        .setdefault("stages", {})
    )
    specific_stages = (
        release_regist_config.setdefault("specificLearn", {})
        .setdefault("triggers", {})
        .setdefault("learn_all", {})
        .setdefault("stages", {})
    )
    cont_stages["stage_learn_command"] = build_voice_reg_stage_entries(selected, specific_mode=False)
    specific_stages["stage_learn_command"] = build_voice_reg_stage_entries(selected, specific_mode=True)
    return selected


def normalize_release_algo_list(release_algo_list: Any) -> List[Dict[str, Any]]:
    if isinstance(release_algo_list, list):
        return [dict(item) for item in release_algo_list if isinstance(item, dict)]
    if isinstance(release_algo_list, dict):
        nested = release_algo_list.get("releaseAlgoList")
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, dict)]
    return []


def extract_multi_wakeup_words(release_multi_wke: Optional[Dict[str, Any]]) -> List[str]:
    words: List[str] = []
    if not isinstance(release_multi_wke, dict):
        return words
    for item in release_multi_wke.get("wkelist") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("condition") or item.get("word") or "").strip()
        if word and word not in words:
            words.append(word)
    return words


def ensure_multi_wakeup_words_in_algo_payload(payload: Dict[str, Any]) -> List[str]:
    if not payload.get("multiWkeEnable"):
        return []

    wake_words = extract_multi_wakeup_words(payload.get("releaseMultiWke"))
    if not wake_words:
        return []

    release_algo_list = normalize_release_algo_list(payload.get("releaseAlgoList"))
    payload["releaseAlgoList"] = release_algo_list

    template: Optional[Dict[str, Any]] = None
    existing_words = set()
    next_idx = 0
    for item in release_algo_list:
        item_type = str(item.get("type") or "").strip()
        item_word = str(item.get("word") or item.get("intent") or "").strip()
        item_idx = item.get("idx")
        if isinstance(item_idx, int):
            next_idx = max(next_idx, item_idx)
        elif isinstance(item_idx, str) and item_idx.isdigit():
            next_idx = max(next_idx, int(item_idx))
        if item_type == "唤醒词":
            if template is None:
                template = deepcopy(item)
            if item_word:
                existing_words.add(item_word)

    if template is None:
        raise RuntimeError("multi wake packaging requires an existing wakeup entry in releaseAlgoList to clone")

    added_words: List[str] = []
    for word in wake_words:
        if word in existing_words:
            continue
        next_idx += 1
        cloned = deepcopy(template)
        cloned["id"] = ""
        cloned["releaseId"] = str(payload.get("releaseId") or cloned.get("releaseId") or "")
        cloned["pid"] = "0"
        cloned["idx"] = next_idx
        cloned["word"] = word
        cloned["extWord"] = None
        cloned["type"] = "唤醒词"
        cloned["recoId"] = ""
        cloned["recoExtWordStr"] = None
        cloned["asrFreeEnable"] = None
        cloned["relatedId"] = None
        cloned["relatedType"] = None
        cloned["pinyin"] = None
        cloned["deleteFlag"] = str(cloned.get("deleteFlag") or "NOT_DELETE")
        cloned["createTime"] = None
        cloned["createUser"] = None
        cloned["updateTime"] = None
        cloned["updateUser"] = None
        cloned["children"] = []
        release_algo_list.append(cloned)
        existing_words.add(word)
        added_words.append(word)

    return added_words


def build_specific_voice_reg_payload() -> Dict[str, Any]:
    payload = build_default_voice_reg_payload()
    release_regist = payload["releaseRegist"]
    release_regist.update(
        {
            "registMode": "specificLearn",
            "wakeupRepeatCount": 1,
            "wakeupWordsMinLimit": 3,
            "wakeupRetryCount": 0,
            "commandWordsMaxLimit": 10,
            "commandWordsMinLimit": 2,
            "commandRegistMaxLimit": 10,
            "commandRetryCount": 2,
            "reply": "好的",
        }
    )
    return payload


def build_default_multi_wke_payload() -> Dict[str, Any]:
    return {
        "releaseMultiWke": deepcopy(DEFAULT_RELEASE_MULTI_WKE),
    }


TRIALS = [
    {
        "name": "voice-only",
        "title": "voiceRegEnable",
        "ruleNote": "先验证 voiceRegEnable 单独置为 true 是否可打包，用来判断模板是否本身支持。",
        "overrides": {
            "voiceRegEnable": True,
        },
    },
    {
        "name": "voice-plus-multi",
        "title": "voiceRegEnable + multiWkeEnable",
        "ruleNote": "先验证两个布尔开关同时按二分法切到 true 是否可打包。",
        "overrides": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
        },
    },
    {
        "name": "multi-loop",
        "title": "multiWkeEnable + multiWkeMode(loop)",
        "ruleNote": "多唤醒先保留当前已成功的 loop 组合，作为对照组。",
        "overrides": {
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
        },
    },
    {
        "name": "voice-default-objects",
        "title": "voiceRegEnable + releaseRegist(default) + releaseRegistConfig(default)",
        "ruleNote": "补齐前端默认自学习对象，验证 500 是否由 releaseRegist / releaseRegistConfig 缺失导致。",
        "overrides": {
            "voiceRegEnable": True,
            **build_default_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "releaseRegist": "default-template",
            "releaseRegistConfig": "default-template",
        },
    },
    {
        "name": "voice-default-loop-objects",
        "title": "voiceRegEnable + multiWkeEnable + multiWkeMode(loop) + default voice objects",
        "ruleNote": "在默认自学习对象基础上叠加多唤醒 loop，对比 voiceRegEnable 与 multiWke 的共同影响。",
        "overrides": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            **build_default_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            "releaseRegist": "default-template",
            "releaseRegistConfig": "default-template",
        },
    },
    {
        "name": "voice-specific-objects",
        "title": "voiceRegEnable + specificLearn structured defaults",
        "ruleNote": "把自学习切到 specificLearn，并对多字段使用边界值/中值组合，继续验证结构对象是否可被打包链路接受。",
        "overrides": {
            "voiceRegEnable": True,
            **build_specific_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "releaseRegist.registMode": "specificLearn",
            "releaseRegist.wakeupRepeatCount": 1,
            "releaseRegist.commandWordsMaxLimit": 10,
            "releaseRegist.commandWordsMinLimit": 2,
            "releaseRegist.commandRegistMaxLimit": 10,
            "releaseRegist.commandRetryCount": 2,
            "releaseRegist.reply": "好的",
        },
    },
    {
        "name": "voice-default-unified",
        "title": "voiceRegEnable + default voice objects via algoUnifiedSave",
        "ruleNote": "改走前端真实使用的 algoUnifiedSave 接口，再次验证默认自学习对象是否可打包。",
        "save_mode": "algo_unified",
        "overrides": {
            "voiceRegEnable": True,
            **build_default_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "saveMode": "algoUnifiedSave",
            "releaseRegist": "default-template",
            "releaseRegistConfig": "default-template",
        },
    },
    {
        "name": "voice-default-loop-unified",
        "title": "voiceRegEnable + multiWkeEnable + loop via algoUnifiedSave",
        "ruleNote": "在 algoUnifiedSave 路径下叠加 multiWke loop，验证 voiceReg 与 multiWke 共存是否可打包。",
        "save_mode": "algo_unified",
        "overrides": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            **build_default_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            "saveMode": "algoUnifiedSave",
            "releaseRegist": "default-template",
            "releaseRegistConfig": "default-template",
        },
    },
    {
        "name": "voice-specific-unified",
        "title": "voiceRegEnable + specificLearn structured defaults via algoUnifiedSave",
        "ruleNote": "沿用 specificLearn 的多变量组合，但改走 algoUnifiedSave，排查是否是保存链路差异导致 500。",
        "save_mode": "algo_unified",
        "overrides": {
            "voiceRegEnable": True,
            **build_specific_voice_reg_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "saveMode": "algoUnifiedSave",
            "releaseRegist.registMode": "specificLearn",
            "releaseRegist.wakeupRepeatCount": 1,
            "releaseRegist.commandWordsMaxLimit": 10,
            "releaseRegist.commandWordsMinLimit": 2,
            "releaseRegist.commandRegistMaxLimit": 10,
            "releaseRegist.commandRetryCount": 2,
            "releaseRegist.reply": "好的",
        },
    },
    {
        "name": "multi-loop-unified-default-multi",
        "title": "multiWkeEnable + loop + default releaseMultiWke via algoUnifiedSave",
        "ruleNote": "单独验证 algoUnifiedSave 在多唤醒场景下是否需要显式 releaseMultiWke 负载。",
        "save_mode": "algo_unified",
        "overrides": {
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            **build_default_multi_wke_payload(),
        },
        "comment_payload": {
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            "saveMode": "algoUnifiedSave",
            "releaseMultiWke": "default-template",
        },
    },
    {
        "name": "voice-default-loop-unified-default-multi",
        "title": "voiceRegEnable + loop + default voice/multi objects via algoUnifiedSave",
        "ruleNote": "同时补齐 voiceReg 和 multiWke 两侧默认结构，继续验证双特性组合是否能通过保存与打包。",
        "save_mode": "algo_unified",
        "overrides": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            **build_default_voice_reg_payload(),
            **build_default_multi_wke_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            "saveMode": "algoUnifiedSave",
            "releaseRegist": "default-template",
            "releaseRegistConfig": "default-template",
            "releaseMultiWke": "default-template",
        },
    },
    {
        "name": "voice-specific-loop-unified-default-multi",
        "title": "voiceRegEnable + specificLearn + loop + default multi payload",
        "ruleNote": "把 specificLearn 的边界/中值组合与默认 multiWke 结构一起提交，验证是否存在模式相关冲突。",
        "save_mode": "algo_unified",
        "overrides": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            **build_specific_voice_reg_payload(),
            **build_default_multi_wke_payload(),
        },
        "comment_payload": {
            "voiceRegEnable": True,
            "multiWkeEnable": True,
            "multiWkeMode": "loop",
            "saveMode": "algoUnifiedSave",
            "releaseRegist.registMode": "specificLearn",
            "releaseRegist.wakeupRepeatCount": 1,
            "releaseRegist.commandWordsMaxLimit": 10,
            "releaseRegist.commandWordsMinLimit": 2,
            "releaseRegist.commandRegistMaxLimit": 10,
            "releaseRegist.commandRetryCount": 2,
            "releaseRegist.reply": "好的",
            "releaseMultiWke": "default-template",
        },
    },
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "index",
                "name",
                "title",
                "status",
                "releaseId",
                "releaseVersion",
                "downloadedFirmwarePath",
                "verifyOk",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "index": row.get("index"),
                    "name": row.get("name"),
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "releaseId": ((row.get("summary") or {}).get("releaseId") if row.get("summary") else ""),
                    "releaseVersion": ((row.get("summary") or {}).get("releaseVersion") if row.get("summary") else ""),
                    "downloadedFirmwarePath": row.get("downloadedFirmwarePath", ""),
                    "verifyOk": row.get("verifyOk", False),
                    "error": row.get("error", ""),
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Try advanced feature combos under the existing grouped product.")
    parser.add_argument("--token", default=os.environ.get("LISTENAI_TOKEN", ""), help="ListenAI token")
    parser.add_argument("--shared-manifest", default=DEFAULT_SHARED_MANIFEST, help="Existing grouped manifest with shared product metadata")
    parser.add_argument("--catalog-json", default=DEFAULT_CATALOG_JSON, help="Parameter catalog json with featureMap metadata")
    parser.add_argument("--trial-root", default=DEFAULT_TRIAL_ROOT, help="Result directory for combo trial metadata")
    parser.add_argument("--package-root", default=DEFAULT_PACKAGE_ROOT, help="Download directory for combo firmware zips")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Per-package timeout")
    parser.add_argument("--continue-on-success", action="store_true", help="Continue trying all combos even after the first success")
    return parser


def choose_download_name(index: int, trial_name: str, release_version: str) -> str:
    safe_version = (release_version or "package").replace(":", "-").replace(".", "-").replace("/", "-")
    return f"{index:02d}_{trial_name}_{safe_version}.zip"


def load_existing_rows(summary_json: Path) -> Dict[str, Dict[str, Any]]:
    if not summary_json.exists():
        return {}
    data = json.loads(summary_json.read_text(encoding="utf-8"))
    return {str(row.get("name") or ""): row for row in data.get("trials") or [] if row.get("name")}


def write_summary(summary_json: Path, shared: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    summary_json.write_text(
        json.dumps(
            {
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "sharedProduct": shared,
                "trials": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    save_csv(summary_json.with_suffix(".csv"), rows)


def build_feature_toggle(feature_map: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cmds": feature_map.get("main_cmd") or feature_map.get("e2e_cmd") or feature_map.get("dec_cmd") or "Unsupported",
        "denoise": feature_map.get("denoise") or "Unsupported",
        "free_cmd": feature_map.get("free_cmd") or "Unsupported",
        "muti_intent": feature_map.get("muti_intent") or "Unsupported",
        "multi_wakeup": feature_map.get("multi_wakeup") or "Unsupported",
        "voice_regist": feature_map.get("voice_regist") or "Unsupported",
        "echo_cancellation": feature_map.get("echo_cancellation") or "Unsupported",
        "main_cmd": feature_map.get("main_cmd") or "Unsupported",
        "e2e_cmd": feature_map.get("e2e_cmd") or "Unsupported",
        "dec_cmd": feature_map.get("dec_cmd") or "Unsupported",
    }


def split_algo_overrides(release_overrides: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    algo_keys = {
        "sensitivity",
        "voiceRegEnable",
        "multiWkeEnable",
        "multiWkeMode",
        "algoViewMode",
        "releaseAlgoList",
        "releaseRegist",
        "releaseRegistConfig",
        "releaseMultiWke",
        "studyRegCommands",
    }
    base_overrides: Dict[str, Any] = {}
    algo_overrides: Dict[str, Any] = {}
    for key, value in release_overrides.items():
        if key in algo_keys:
            algo_overrides[key] = value
        else:
            base_overrides[key] = value
    return {"base": base_overrides, "algo": algo_overrides}


def package_release_with_algo_unified(
    client: ListenAIClient,
    product_detail: Dict[str, Any],
    source_release_id: str,
    timeout_sec: int,
    release_overrides: Dict[str, Any],
    feature_toggle: Dict[str, Any],
    study_reg_commands: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    require_ok(client.get("/fw/release/copy", params={"id": source_release_id}), "copy release")

    source_release = require_ok(
        client.get("/fw/release/detail", params={"id": source_release_id}),
        "source release detail",
    ).get("data") or {}
    source_prod_id = str(source_release.get("prodId") or "")
    if not source_prod_id:
        raise RuntimeError("source release missing prodId")

    release_id = find_copied_release_id(
        client,
        prod_id=source_prod_id,
        source_release_id=source_release_id,
    )
    release_detail = require_ok(
        client.get("/fw/release/detail", params={"id": release_id}),
        "copied release detail",
    ).get("data") or {}
    release_detail["prodId"] = str(product_detail["id"])
    release_detail["status"] = "ready"
    for key in [
        "createTime",
        "createUser",
        "updateTime",
        "updateUser",
        "pkgLogs",
        "pkgUrl",
        "pkgSDKUrl",
        "pkgPipelineId",
        "pkgTaskId",
        "deleteFlag",
        "version",
    ]:
        release_detail.pop(key, None)

    override_groups = split_algo_overrides(release_overrides)
    release_detail = apply_release_overrides(release_detail, override_groups["base"])
    require_ok(client.post_json("/fw/release/edit", release_detail), "rebind copied release")

    algo_data = require_ok(client.get("/fw/release/getAlgoData", params={"id": release_id}), "algo data").get("data") or []
    resolved_overrides = deepcopy(release_overrides)
    resolved_voice_reg_learn_commands: List[str] = []
    algo_payload: Dict[str, Any] = {
        "language": str(product_detail["language"]),
        "configId": str(product_detail["defId"]),
        "releaseId": str(release_id),
        "sensitivity": override_groups["algo"].get("sensitivity", release_detail.get("sensitivity")),
        "voiceRegEnable": override_groups["algo"].get("voiceRegEnable", release_detail.get("voiceRegEnable")),
        "multiWkeEnable": override_groups["algo"].get("multiWkeEnable", release_detail.get("multiWkeEnable")),
        "multiWkeMode": override_groups["algo"].get("multiWkeMode", release_detail.get("multiWkeMode")),
        "algoViewMode": override_groups["algo"].get("algoViewMode", release_detail.get("algoViewMode")),
        "releaseAlgoList": deepcopy(normalize_release_algo_list(override_groups["algo"].get("releaseAlgoList", algo_data))),
        "featureToggle": feature_toggle,
    }
    for key in ["releaseRegist", "releaseRegistConfig", "releaseMultiWke"]:
        if key in override_groups["algo"]:
            algo_payload[key] = deepcopy(override_groups["algo"][key])

    if algo_payload.get("voiceRegEnable") and ("releaseRegist" in algo_payload or "releaseRegistConfig" in algo_payload):
        explicit_commands = normalize_voice_reg_learn_commands(
            study_reg_commands if study_reg_commands is not None else override_groups["algo"].get("studyRegCommands")
        )
        if explicit_commands:
            selected_commands = explicit_commands
        else:
            selected_commands = select_voice_reg_learn_commands(
                algo_payload.get("releaseAlgoList") or [],
                fallback=extract_voice_reg_learn_commands(algo_payload.get("releaseRegistConfig")),
            )
        if selected_commands:
            if "releaseRegist" not in algo_payload:
                algo_payload["releaseRegist"] = deepcopy(DEFAULT_RELEASE_REGIST)
            if "releaseRegistConfig" not in algo_payload:
                algo_payload["releaseRegistConfig"] = deepcopy(DEFAULT_RELEASE_REGIST_CONFIG)
            resolved_voice_reg_learn_commands = apply_voice_reg_learn_commands(algo_payload, selected_commands)
            resolved_overrides["releaseRegist"] = deepcopy(algo_payload["releaseRegist"])
            resolved_overrides["releaseRegistConfig"] = deepcopy(algo_payload["releaseRegistConfig"])

    resolved_multi_wakeup_words = ensure_multi_wakeup_words_in_algo_payload(algo_payload)
    if resolved_multi_wakeup_words or "releaseAlgoList" in override_groups["algo"]:
        resolved_overrides["releaseAlgoList"] = deepcopy(algo_payload["releaseAlgoList"])

    require_ok(client.post_json("/fw/release/algoUnifiedSave", algo_payload), "algo unified save")
    expected_scalar_keys = {
        "timeout",
        "volLevel",
        "defaultVol",
        "volMaxOverflow",
        "volMinOverflow",
        "uportUart",
        "uportBaud",
        "traceUart",
        "traceBaud",
        "logLevel",
        "wakeWordSave",
        "volSave",
        "vcn",
        "speed",
        "vol",
        "compress",
        "word",
        "paConfigEnable",
        "voiceRegEnable",
        "multiWkeEnable",
        "multiWkeMode",
        "algoViewMode",
        "sensitivity",
    }
    expected_seed = {
        key: release_detail.get(key)
        for key in expected_scalar_keys
        if key in release_detail
    }
    expected_fields = apply_release_overrides(
        expected_seed,
        {
            key: value
            for key, value in resolved_overrides.items()
            if key in expected_scalar_keys and not isinstance(value, (dict, list))
        },
    )
    wait_release_stable(client, release_id, expected_fields)

    package_params = {
        "id": release_id,
        "categoryName": str(product_detail["type"]),
        "mark": str(product_detail["chipModule"]),
        "scene": str(product_detail["scene"]),
        "productName": str(product_detail["name"]),
        "language": str(product_detail["language"]),
        "configId": str(product_detail["defId"]),
    }
    require_ok(client.get("/fw/release/package", params=package_params), "package")

    final_release = poll_release_success(client, release_id, timeout_sec=timeout_sec)
    return {
        "productId": str(product_detail["id"]),
        "productName": str(product_detail["name"]),
        "releaseId": str(release_id),
        "releaseVersion": final_release.get("version"),
        "status": final_release.get("status"),
        "pkgTaskId": final_release.get("pkgTaskId"),
        "pkgPipelineId": final_release.get("pkgPipelineId"),
        "pkgUrl": final_release.get("pkgUrl"),
        "pkgSDKUrl": final_release.get("pkgSDKUrl"),
        "appliedOverrides": resolved_overrides,
        "resolvedVoiceRegLearnCommands": resolved_voice_reg_learn_commands,
        "resolvedMultiWakeupWords": resolved_multi_wakeup_words,
    }


def main() -> int:
    args = build_parser().parse_args()
    args.token = resolve_listenai_token(args.token, persist=True)
    if not args.token:
        raise RuntimeError("Missing token. Use --token or set LISTENAI_TOKEN.")

    catalog = load_json(args.catalog_json)
    feature_toggle = build_feature_toggle(dict(catalog.get("featureMap") or {}))
    shared_manifest = json.loads(Path(args.shared_manifest).read_text(encoding="utf-8"))
    shared = shared_manifest.get("sharedProduct") or {}
    product_id = str(shared.get("productId") or "")
    if not product_id:
        raise RuntimeError("Shared product id missing in shared manifest.")

    trial_root = Path(args.trial_root)
    package_root = Path(args.package_root)
    ensure_dir(trial_root)
    ensure_dir(package_root)

    summary_json = trial_root / "advanced_combo_trials.json"
    existing_rows = load_existing_rows(summary_json)

    client = ListenAIClient(token=args.token, timeout=max(60, args.timeout_sec))
    product_detail = require_ok(client.get("/biz/prod/detail", params={"id": product_id}), "shared product detail").get("data") or {}

    rows: List[Dict[str, Any]] = []
    for index, trial in enumerate(TRIALS, start=1):
        existing_row = existing_rows.get(trial["name"])
        if existing_row and existing_row.get("status") in {"success", "verify_failed", "failed"}:
            cached_row = dict(existing_row)
            cached_row["index"] = index
            cached_row["name"] = trial["name"]
            cached_row["title"] = trial["title"]
            cached_row["ruleNote"] = trial["ruleNote"]
            rows.append(cached_row)
            continue

        row: Dict[str, Any] = {
            "index": index,
            "name": trial["name"],
            "title": trial["title"],
            "ruleNote": trial["ruleNote"],
            "status": "running",
            "overrides": deepcopy(trial["overrides"]),
            "summary": None,
            "releaseCheck": [],
            "verifyOk": False,
            "downloadedFirmwarePath": "",
            "algoRegData": {},
            "multiWkeData": {},
            "error": "",
        }
        release_overrides = deepcopy(trial["overrides"])
        comment_payload = dict(trial.get("comment_payload") or trial["overrides"])
        release_overrides["comments"] = build_short_release_comment_from_selected(
            dict(shared_manifest.get("selectedMeta") or {}),
            comment_payload,
        )
        row["comments"] = release_overrides["comments"]
        result_json_path = trial_root / f"{index:02d}_{trial['name']}.json"
        row["resultJsonPath"] = str(result_json_path.resolve())
        final_release: Dict[str, Any] = {}
        algo_reg_data: Dict[str, Any] = {}
        multi_wke_data: Dict[str, Any] = {}

        try:
            if trial.get("save_mode") == "algo_unified":
                summary = package_release_with_algo_unified(
                    client=client,
                    product_detail=product_detail,
                    source_release_id=str(shared_manifest["sourceReleaseId"]),
                    timeout_sec=args.timeout_sec,
                    release_overrides=release_overrides,
                    feature_toggle=feature_toggle,
                )
            else:
                summary = package_release_for_existing_product(
                    client=client,
                    product_detail=product_detail,
                    source_release_id=str(shared_manifest["sourceReleaseId"]),
                    timeout_sec=args.timeout_sec,
                    release_overrides=release_overrides,
                )
            final_release = require_ok(client.get("/fw/release/detail", params={"id": summary["releaseId"]}), "release detail").get("data") or {}
            if trial.get("save_mode") == "algo_unified":
                algo_reg_data = require_ok(client.get("/fw/release/getAlgoRegData", params={"id": summary["releaseId"]}), "algo reg data").get("data") or {}
                multi_wke_data = require_ok(client.get("/fw/release/getMultiWkeData", params={"id": summary["releaseId"]}), "multi wake data").get("data") or {}
            verify_ok, checks = verify_release(final_release, release_overrides)
            downloaded = client.download(
                str(summary["pkgUrl"]),
                str(package_root / choose_download_name(index, trial["name"], str(summary.get("releaseVersion") or ""))),
            )
            row["status"] = "success" if verify_ok else "verify_failed"
            row["summary"] = summary
            row["releaseCheck"] = checks
            row["verifyOk"] = verify_ok
            row["downloadedFirmwarePath"] = downloaded
            row["algoRegData"] = algo_reg_data
            row["multiWkeData"] = multi_wke_data
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = str(exc)

        result_json_path.write_text(
            json.dumps(
                {
                    "generatedAt": datetime.now().isoformat(timespec="seconds"),
                    "sharedProduct": shared,
                    "trial": row,
                    "finalRelease": final_release,
                    "algoRegData": algo_reg_data,
                    "multiWkeData": multi_wke_data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        rows.append(row)
        write_summary(summary_json, shared, rows)

    success_count = sum(1 for row in rows if row.get("status") == "success")
    verify_failed_count = sum(1 for row in rows if row.get("status") == "verify_failed")
    failed_count = sum(1 for row in rows if row.get("status") == "failed")
    print(f"summary_json: {summary_json}")
    print(f"package_root: {package_root.resolve()}")
    print(f"success={success_count} verify_failed={verify_failed_count} failed={failed_count}")
    return 0 if success_count > 0 and verify_failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
