#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import List, Tuple

IDIOMS = [
    '春风化雨','行云流水','一帆风顺','如鱼得水','心想事成','万象更新','花好月圆','四海升平','吉星高照','福星高照',
    '安居乐业','安然无恙','百川归海','百花齐放','百事顺遂','冰清玉洁','不疾不徐','不骄不躁','不言而喻','乘风破浪',
    '大展宏图','得心应手','耳目一新','繁花似锦','风调雨顺','扶摇直上','高山流水','光风霁月','和风细雨','厚积薄发',
    '欢天喜地','豁然开朗','见贤思齐','渐入佳境','锦上添花','井然有序','举重若轻','开门见喜','康庄大道','柳暗花明',
    '龙腾四海','马到成功','满堂生辉','美意延年','妙语连珠','明月清风','宁静致远','鹏程万里','平步青云','其乐融融',
    '千祥云集','沁人心脾','清风明月','情真意切','秋水长天','群贤毕至','人和业兴','日新月异','如沐春风','山高水长',
    '赏心悦目','神采飞扬','时和岁丰','水到渠成','松风水月','岁月静好','泰然自若','天朗气清','万里同风','温故知新',
    '无远弗届','熙熙攘攘','闲庭信步','相得益彰','祥云瑞气','心平气和','欣欣向荣','行稳致远','虚怀若谷','雅俗共赏',
    '一团和气','一语中的','怡然自得','意气风发','游目骋怀','鱼跃龙门','云程发轫','云舒霞卷','芝兰玉树','竹报平安',
    '珠联璧合','壮志凌云','左右逢源','春和景明','风和日丽','海晏河清','吉祥如意','金声玉振','景星庆云','兰亭雅集'
]

ASSISTS = ['请','帮我','麻烦','劳烦','给我','替我','现在','马上','立刻','尽快','可否','能否','试着','直接','迅速']
TONES = ['一下','一遍','一点','吧','呀','呢','啦','即可','就行','就好','可好','好吗','行吗','试试','安排']
PRODUCT_ACTIONS = ['打开','关闭','切换','设为','调到','启动','停止','开启','恢复','进入']
PRODUCT_TARGETS = ['暖风','取暖','摇头','低档','中档','高档','睡眠','定时','童锁','工作']


def checksum_for(protocol: str) -> str:
    parts = [int(x, 16) for x in protocol.split()]
    parts[-1] = sum(parts[:-1]) & 0xFF
    return ' '.join(f'{x:02X}' for x in parts)


def next_protocol(seed: int) -> Tuple[str, str]:
    hi = (seed >> 8) & 0xFF
    lo = seed & 0xFF
    snd = checksum_for(f'A5 FA 00 81 {hi:02X} {lo:02X} 00')
    rec = checksum_for(f'A5 FA 00 82 {hi:02X} {lo:02X} 00')
    return snd, rec


def build_product_terms(count: int) -> List[Tuple[str, str]]:
    pairs = []
    seen = set()
    suffixes = ['篇', '卷', '章', '令', '诀', '式', '调', '格', '路', '境']
    i = 0
    while len(pairs) < count:
        action = PRODUCT_ACTIONS[i % len(PRODUCT_ACTIONS)]
        target = PRODUCT_TARGETS[(i // len(PRODUCT_ACTIONS)) % len(PRODUCT_TARGETS)]
        tone = TONES[(i // (len(PRODUCT_ACTIONS) * len(PRODUCT_TARGETS))) % len(TONES)]
        assist = ASSISTS[(i // len(PRODUCT_TARGETS)) % len(ASSISTS)]
        suffix = suffixes[(i // (len(PRODUCT_ACTIONS) * len(PRODUCT_TARGETS) * len(TONES))) % len(suffixes)]
        word = f'{action}{target}' if i < len(PRODUCT_ACTIONS) * len(PRODUCT_TARGETS) else f'{action}{target}{suffix}'
        ext = f'{assist}{action}{target}{tone}' if suffix == '篇' else f'{assist}{action}{target}{suffix}{tone}'
        pair = (word, ext)
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
        i += 1
    return pairs


def build_free_terms(count: int) -> List[Tuple[str, str]]:
    pairs = []
    seen = set()
    suffixes = ['篇', '卷', '章', '令', '诀', '式', '境', '调', '格', '路']
    i = 0
    while len(pairs) < count:
        base = IDIOMS[i % len(IDIOMS)]
        assist = ASSISTS[(i // len(IDIOMS)) % len(ASSISTS)]
        tone = TONES[(i // (len(IDIOMS) * len(ASSISTS))) % len(TONES)]
        suffix = suffixes[(i // (len(IDIOMS) * len(ASSISTS) * len(TONES))) % len(suffixes)]
        word = base if i < len(IDIOMS) else f'{base}{suffix}'
        ext = f'{assist}{base}{tone}' if suffix == '篇' else f'{assist}{base}{suffix}{tone}'
        pair = (word, ext)
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
        i += 1
    return pairs


def make_item(idx: int, release_id: str, word: str, ext_word: str, proto_seed: int) -> dict:
    snd, rec = next_protocol(proto_seed)
    return {
        'id': '',
        'releaseId': release_id,
        'pid': '0',
        'idx': idx,
        'word': word,
        'extWord': ext_word,
        'type': '命令词',
        'reply': f'好的/{word}已执行',
        'replyMode': '主',
        'sndProtocol': snd,
        'recProtocol': rec,
        'recoId': '',
        'recoExtWordStr': None,
        'asrFreeEnable': None,
        'relatedId': None,
        'relatedType': None,
        'pinyin': None,
        'deleteFlag': 'NOT_DELETE',
        'createTime': None,
        'createUser': None,
        'updateTime': None,
        'updateUser': None,
        'children': [],
    }


def dedupe_pair(word: str, ext_word: str, used_words: set[str], used_ext: set[str]) -> Tuple[str, str]:
    if word not in used_words and ext_word not in used_ext:
        return word, ext_word
    word_candidate = word
    ext_candidate = ext_word
    counter = 1
    while word_candidate in used_words or ext_candidate in used_ext:
        word_candidate = f'{word}令' * counter
        ext_candidate = f'{ext_word}呀' * counter
        counter += 1
    return word_candidate, ext_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate unique Chinese releaseAlgoList payloads.')
    parser.add_argument('--base-json', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--append-count', type=int, required=True)
    parser.add_argument('--proto-start', type=lambda x: int(x, 0), default=0x0083)
    args = parser.parse_args()

    base = json.loads(Path(args.base_json).read_text(encoding='utf-8'))
    if not isinstance(base, list) or not base:
        raise SystemExit('base-json must be a non-empty JSON list')

    release_id = str(base[0].get('releaseId') or '')
    next_idx = max(int(item.get('idx') or 0) for item in base) + 1
    pairs = build_product_terms(args.append_count) if args.append_count <= 50 else build_free_terms(args.append_count)

    used_words = {str(item.get('word') or '') for item in base}
    used_ext = {str(item.get('extWord') or '') for item in base}
    used_proto = {str(item.get('sndProtocol') or '') for item in base} | {str(item.get('recProtocol') or '') for item in base}

    payload = list(base)
    seed = args.proto_start
    for word, ext in pairs:
        word, ext = dedupe_pair(word, ext, used_words, used_ext)
        snd, rec = next_protocol(seed)
        while snd in used_proto or rec in used_proto:
            seed += 1
            snd, rec = next_protocol(seed)
        item = make_item(next_idx, release_id, word, ext, seed)
        payload.append(item)
        used_words.add(word)
        used_ext.add(ext)
        used_proto.add(snd)
        used_proto.add(rec)
        next_idx += 1
        seed += 1

    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(Path(args.output))
    print(len(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
