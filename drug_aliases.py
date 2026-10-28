# -*- coding: utf-8 -*-
"""
药物中文名 / 别名 / 商品名 / 俗名 解析（离线优先）
================================================================================
解决的问题：用户往往不写 SMILES，而是写中文名、别名、商品名甚至俗名——
「扑热息痛」「泰诺」「冰毒」「太空油」「盐酸二甲双胍」「阿司匹林肠溶片」。
本模块把这些写法规范化后查本地词库（data/drug_alias_table.json，SMILES 由
tools/build_alias_table.py 从 PubChem 取回并核验），命中即直接给出 SMILES，
不必联网；未命中则返回 None，由上层交给 ai-molprop 的在线解析（PubChem/Wikidata）。

规范化规则（按顺序）：
  1) 全角转半角、去掉空白与多余标点、统一括号与连接符
  2) 去掉剂型/规格词：片、胶囊、颗粒、注射液、口服液、缓释、分散片、软膏、乳膏、
     滴眼液、喷雾剂、栓、贴、凝胶、溶液、混悬液、糖浆、复方、针、丸、散 …
  3) 去掉盐型前缀/后缀（盐酸、硫酸、枸橼酸/柠檬酸、马来酸、富马酸、琥珀酸、酒石酸、
     甲磺酸、苯磺酸、对甲苯磺酸、乳酸、葡糖酸、磷酸、氢溴酸、醋酸/乙酸、硝酸、
     钠、钾、钙、镁、锌、铵、氯、溴、碘、葡甲胺、二乙胺、氨丁三醇、三乙胺 …）
  4) 去掉立体/来源前缀：左、右、消旋、外消旋、(R)-、(S)-、D-、L-、天然、合成
  5) 商品名/俗名/缩写直接查表
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent
TABLE_PATH = ROOT / "data" / "drug_alias_table.json"

# 剂型 / 规格 / 修饰词（会被剥离，并作为提示回传）
DOSAGE_WORDS = [
    "肠溶片", "缓释片", "控释片", "分散片", "咀嚼片", "泡腾片", "薄膜衣片", "糖衣片", "片剂", "片",
    "缓释胶囊", "胶囊", "软胶囊", "颗粒", "冲剂", "散剂", "散", "丸", "滴丸", "微丸",
    "注射液", "输液", "注射剂", "针剂", "针", "口服液", "口服溶液", "溶液", "混悬液", "糖浆",
    "软膏", "乳膏", "凝胶", "贴剂", "贴", "栓剂", "栓", "滴眼液", "滴鼻液", "喷雾剂", "气雾剂",
    "吸入剂", "鼻喷雾", "鼻喷剂", "复方", "缓释", "控释", "长效", "速释", "原料药", "药片",
]
# 盐型前缀（放在名称前面）
SALT_PREFIXES = [
    "盐酸", "氢氯酸", "硫酸", "磷酸", "硝酸", "氢溴酸", "氢碘酸", "枸橼酸", "柠檬酸", "马来酸",
    "富马酸", "琥珀酸", "酒石酸", "甲磺酸", "苯磺酸", "对甲苯磺酸", "乳酸", "葡糖酸", "葡萄糖酸",
    "醋酸", "乙酸", "草酸", "丙酸", "戊酸", "苯甲酸", "樟脑磺酸", "依西酸", "双羟萘酸", "泛酸",
    "葡甲胺", "氨丁三醇", "二乙胺", "三乙胺", "乙醇胺", "胆碱", "苄星", "普鲁卡因", "利多卡因",
]
# 盐型后缀（放在名称后面）：金属/无机离子
SALT_SUFFIXES = ["钠", "钾", "钙", "镁", "锌", "铵", "锂", "铝", "铋", "银", "铁",
                 "盐酸盐", "硫酸盐", "磷酸盐", "醋酸盐", "钠盐", "钾盐", "盐"]
STEREO_PREFIXES = ["左旋", "右旋", "左", "右", "消旋", "外消旋", "混旋",
                   "(R)-", "(S)-", "R-", "S-", "D-", "L-", "dl-", "DL-"]
NOISE_WORDS = ["原料药", "对照品", "标准品", "试剂", "分析纯", "化学纯", "药用级", "注射级"]

_TABLE: Optional[Dict[str, dict]] = None
_INDEX: Optional[Dict[str, tuple]] = None


# ---------------------------------------------------------------- 载入与索引

def load_table() -> Dict[str, dict]:
    """载入词库（整表以 PubChem 查询名为键）"""
    global _TABLE
    if _TABLE is None:
        try:
            _TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _TABLE = {}
    return _TABLE


def _norm(text: str) -> str:
    """基础规范化：全角→半角、去空白、统一标点"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))          # 全角字母数字/标点 → 半角
    text = text.replace("（", "(").replace("）", ")").replace("，", ",")
    text = text.replace("、", " ").replace("·", "").replace("．", ".")
    text = re.sub(r"[\s\u3000]+", "", text)                  # 去掉所有空白
    text = text.strip(" ,.()[]【】《》\"'")
    return text


def _strip_words(text: str, words: List[str]) -> tuple[str, List[str]]:
    """剥离词表中的词，返回 (剩余文本, 被剥离的词)"""
    removed = []
    for word in sorted(words, key=len, reverse=True):        # 长词优先，避免"片"先吃掉"分散片"
        if word and word in text:
            text = text.replace(word, "")
            removed.append(word)
    return text.strip(), removed


def normalize(text: str) -> dict:
    """
    把任意写法规范化为「候选核心名」列表（由精确到宽松），并记录剥离掉的信息。
    返回 {"candidates": [...], "dosage": [...], "salt": [...], "stereo": [...], "raw": ...}
    """
    raw = str(text or "")
    base = _norm(raw)
    candidates: List[str] = []
    dosage_removed: List[str] = []
    salt_removed: List[str] = []
    stereo_removed: List[str] = []

    if not base:
        return {"candidates": [], "dosage": [], "salt": [], "stereo": [], "raw": raw}

    candidates.append(base)                                  # ① 原样

    step = base
    step, removed = _strip_words(step, NOISE_WORDS)
    if removed:
        candidates.append(step)
    step, removed = _strip_words(step, DOSAGE_WORDS)
    dosage_removed += removed
    if removed:
        candidates.append(step)
    step, removed = _strip_words(step, SALT_PREFIXES + SALT_SUFFIXES)
    salt_removed += removed
    if removed:
        candidates.append(step)
    step, removed = _strip_words(step, ["盐"])
    if removed:
        candidates.append(step)
    step2 = step
    for prefix in STEREO_PREFIXES:
        if step2.startswith(prefix) and len(step2) > len(prefix):
            step2 = step2[len(prefix):]
            stereo_removed.append(prefix)
            break
    if step2 != step:
        candidates.append(step2)

    # 去重且保持顺序
    seen, ordered = set(), []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return {"candidates": ordered, "dosage": dosage_removed, "salt": salt_removed,
            "stereo": stereo_removed, "raw": raw}


def _as_list(value) -> List[str]:
    """词条字段既可能是字符串（主中文名）也可能是列表，统一成列表"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def build_index() -> Dict[str, tuple]:
    """构建「别名 → (表键, 命中类型)」索引"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    index: Dict[str, tuple] = {}
    for key, entry in load_table().items():
        names = []
        for field, kind in (("zh", "中文名"), ("zh_alt", "中文别名"), ("trade", "商品名/俗名"),
                            ("abbr", "缩写"), ("en", "英文名")):
            if field == "zh":
                for name in _as_list(entry.get("zh")) + _as_list(entry.get("zh_alt")):
                    names.append((name, kind if name in _as_list(entry.get("zh")) else "中文别名"))
            else:
                for name in _as_list(entry.get(field)):
                    names.append((name, kind))
        names.append((entry.get("pubchem_query", key), "英文名"))
        for name, kind in names:
            for variant in {_norm(name), _norm(name).lower()}:
                if variant:
                    index.setdefault(variant, (key, kind))
    _INDEX = index
    return index


# ---------------------------------------------------------------- 查询接口

def resolve(text: str, allow_salt_strip: bool = True) -> Optional[dict]:
    """
    解析用户输入。命中返回：
        {"key", "smiles", "zh", "en", "cid", "formula", "mw", "matched", "match_type",
         "notes": [...], "input", "normalized"}
    未命中返回 None（调用方应回退到在线解析）。
    """
    info = normalize(text)
    index = build_index()
    table = load_table()
    if not info["candidates"] or not table:
        return None

    matched_key = matched_alias = match_type = None
    for depth, candidate in enumerate(info["candidates"]):
        for probe in (candidate, candidate.lower()):
            if probe in index:
                matched_key, match_type = index[probe]
                matched_alias = candidate
                break
        if matched_key:
            if depth > 0 and ("盐" not in match_type):
                match_type = "%s（经规范化）" % match_type
            break

    if not matched_key:
        return None
    entry = table[matched_key]

    notes: List[str] = []
    if info["dosage"]:
        notes.append("输入含剂型/规格词“%s”，已忽略，按原料药分子处理" % "、".join(info["dosage"]))
    if info["salt"]:
        if allow_salt_strip:
            notes.append("输入含盐型/成盐离子“%s”，本次按**游离碱/母体**预测；"
                         "成盐通常提高溶解度，如需精确评估请直接给出盐的 SMILES"
                         % "、".join(info["salt"]))
        else:
            notes.append("输入含盐型“%s”" % "、".join(info["salt"]))
    if info["stereo"]:
        notes.append("输入含立体化学前缀“%s”，已按母体结构处理（立体异构体可能活性不同）"
                     % "、".join(info["stereo"]))
    if entry.get("note"):
        notes.append(entry["note"])
    if matched_alias != entry.get("zh"):
        notes.append("“%s”是 %s（%s）的%s" % (matched_alias, entry.get("zh"),
                                            entry.get("pubchem_query", ""), match_type or "别名"))

    return {
        "key": matched_key, "smiles": entry.get("smiles"), "zh": entry.get("zh"),
        "en": entry.get("en"), "trade": entry.get("trade"), "abbr": entry.get("abbr"),
        "cid": entry.get("cid"), "formula": entry.get("formula"), "mw": entry.get("mw"),
        "matched": matched_alias, "match_type": match_type, "notes": notes,
        "input": str(text), "normalized": info["candidates"],
    }


def suggest(text: str, limit: int = 5) -> List[str]:
    """未命中时给出相近词条建议（按字符重叠度）"""
    probe = _norm(text)
    if not probe:
        return []
    scored = []
    for name in build_index():
        if len(name) < 2:                                # 跳过单字索引，避免噪声
            continue
        overlap = len(set(probe) & set(name))
        if overlap >= 2:
            scored.append((overlap / max(len(probe), len(name)), name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:limit]]


def stats() -> dict:
    """词库规模统计（用于自检与报告）"""
    table = load_table()
    counts = {"entry": len(table),
              "zh": sum(len(_as_list(e.get("zh"))) + len(_as_list(e.get("zh_alt"))) for e in table.values()),
              "trade": sum(len(_as_list(e.get("trade"))) for e in table.values()),
              "abbr": sum(len(_as_list(e.get("abbr"))) for e in table.values()),
              "en": sum(len(_as_list(e.get("en"))) for e in table.values())}
    counts["index"] = len(build_index())
    return counts


if __name__ == "__main__":                                   # 手工试跑：python drug_aliases.py 泰诺 "盐酸二甲双胍"
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("词库统计:", stats())
    for arg in (sys.argv[1:] or ["泰诺", "扑热息痛", "盐酸二甲双胍", "阿司匹林肠溶片", "冰毒", "太空油", "速福达"]):
        result = resolve(arg)
        if result:
            print("\n%s → %s（%s）" % (arg, result["zh"], result["smiles"]))
            for note in result["notes"]:
                print("    · " + note)
        else:
            print("\n%s → 未命中；相近建议：%s" % (arg, "、".join(suggest(arg))))
