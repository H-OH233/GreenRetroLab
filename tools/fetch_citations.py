# -*- coding: utf-8 -*-
"""
为「现代合成方法库」检索近期真实文献（tools/fetch_citations.py）
================================================================================
数据来源：Crossref REST API（https://api.crossref.org），**只记录真实存在的论文**，
每条都带 DOI 与来源，可自行核实。绝不手工编造引用。

做法：对 modern_methods.METHOD_LIBRARY 里每个方法的检索式做一次查询，
取近 N 年的 journal-article，按相关度取前若干条，落盘到 data/method_citations.json。

运行：
    python tools/fetch_citations.py                 # 增量补全（已有方法不重复查）
    python tools/fetch_citations.py --refresh       # 全部重新检索
    python tools/fetch_citations.py --years 8 --rows 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "method_citations.json"
UA = {"User-Agent": "GreenRetroLab/1.0 (mailto:noreply@example.com)"}
API = "https://api.crossref.org/works"

if hasattr(sys.stdout, "reconfigure"):          # 控制台按 UTF-8 输出，避免标题里的特殊字符导致 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """清洗 Crossref 返回的标题/期刊名：去 HTML 标记与实体、统一空白字符"""
    import html as _html
    text = _TAG.sub("", str(text or ""))
    text = _html.unescape(text)                     # 例如 "&amp;" → "&"
    text = text.replace("\xa0", " ").replace("\u2009", " ").replace("\u2013", "-")
    return re.sub(r"\s+", " ", text).strip()


def search(query: str, years: int, rows: int, retries: int = 3) -> list:
    params = {
        "query.bibliographic": query,
        "filter": "from-pub-date:%d-01-01,type:journal-article" % (datetime.now().year - years),
        "rows": str(rows),
        "select": "DOI,title,container-title,issued,author,URL,is-referenced-by-count",
        "sort": "relevance",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read().decode("utf-8"))["message"]["items"]
            papers = []
            for item in items:
                title = clean((item.get("title") or [""])[0])
                journal = clean((item.get("container-title") or [""])[0])
                year = None
                parts = (item.get("issued") or {}).get("date-parts") or [[None]]
                if parts and parts[0]:
                    year = parts[0][0]
                authors = item.get("author") or []
                first_author = ""
                if authors:
                    first_author = authors[0].get("family") or authors[0].get("name") or ""
                if not title or not item.get("DOI"):
                    continue
                papers.append({
                    "doi": item["DOI"], "title": title, "journal": journal,
                    "year": year, "first_author": first_author,
                    "cited_by": item.get("is-referenced-by-count"),
                    "url": item.get("URL") or ("https://doi.org/" + item["DOI"]),
                })
            return papers
        except Exception as exc:
            if attempt == retries - 1:
                print("     检索失败：%s" % exc)
            time.sleep(1.5 * (attempt + 1))
    return []


def rank_papers(papers: list, query: str, keep: int) -> list:
    """
    Crossref 的 relevance 排序常把"顺手用了该试剂"的论文排到前面（例如把 DMC 当 NMR 溶剂），
    这里按「标题/期刊与方法关键词的重合度 + 新近度 + 被引」重排，取前 keep 条。
    """
    terms = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", query) if len(t) > 3]
    this_year = datetime.now().year
    scored = []
    for paper in papers:
        blob = (paper.get("title", "") + " " + paper.get("journal", "")).lower()
        overlap = sum(1 for t in terms if t in blob)
        score = overlap * 2.0
        year = paper.get("year") or 0
        score += 1.0 if year >= this_year - 4 else (0.5 if year >= this_year - 7 else 0.0)
        if any(k in blob for k in ("review", "green", "sustainab", "continuous", "catal", "flow")):
            score += 0.7
        score += min(1.0, (paper.get("cited_by") or 0) / 200.0)
        scored.append((score, overlap, year, paper))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in scored[:keep]]


def main() -> int:
    parser = argparse.ArgumentParser("fetch_citations")
    parser.add_argument("--years", type=int, default=6, help="只取最近 N 年（默认 6）")
    parser.add_argument("--rows", type=int, default=3, help="每个方法取几条（默认 3）")
    parser.add_argument("--refresh", action="store_true", help="已有条目也重新检索")
    parser.add_argument("--sleep", type=float, default=0.6, help="请求间隔（Crossref 礼仪）")
    args = parser.parse_args()

    import modern_methods as mm

    payload = {"generated_at": None, "source": "Crossref REST API (https://api.crossref.org)",
               "filter": "journal-article, last %d years" % args.years, "methods": {}}
    if OUT.is_file():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            payload["methods"] = old.get("methods", {})
        except Exception:
            pass

    done = skipped = failed = 0
    for key, info in mm.METHOD_LIBRARY.items():
        query = info.get("query")
        if not query:
            continue
        if payload["methods"].get(key) and not args.refresh:
            skipped += 1
            continue
        print("  [%s] %s" % (key, query))
        papers = rank_papers(search(query, args.years, max(args.rows * 3, 8)), query, args.rows)
        time.sleep(args.sleep)
        if not papers:
            failed += 1
            print("     → 未取到")
            continue
        payload["methods"][key] = papers
        done += 1
        for paper in papers:
            print("     · %s (%s, %s)  doi:%s"
                  % (paper["title"][:74], paper["journal"][:28] or "-", paper["year"], paper["doi"]))

    payload["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(v) for v in payload["methods"].values())
    print("\n完成：新检索 %d 个方法（跳过 %d，失败 %d），共 %d 个方法 / %d 篇文献 → %s"
          % (done, skipped, failed, len(payload["methods"]), total, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
