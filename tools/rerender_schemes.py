# -*- coding: utf-8 -*-
"""
只重画、不重算：用已保存的结果 JSON 重新生成论文式路线流程图
==============================================================

用途：调整了 ``scheme_render.py`` 的版式/文案后，不必重跑模型与逆合成，
直接对已有的报告 JSON 重新出图（秒级完成）。

覆盖三种产物：
  1. 网页运行目录 ``reports/web/<run_id>/result.json`` → 每条路线 PNG + 图版
  2. 命令行单分子 ``reports/**/*_report.json``          → 每条路线 PNG + 图版
  3. 命令行批量目录 ``reports/**/batch_summary.json``   → batch_routes_scheme.png

用法：
  python tools/rerender_schemes.py                     # 处理 reports 下所有结果
  python tools/rerender_schemes.py --run b55a1f36e89c  # 只处理某次网页运行
  python tools/rerender_schemes.py --reports-dir D:/somewhere/reports
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mol_report as mr            # noqa: E402  复用 safe_filename
import scheme_render as scheme     # noqa: E402

REPORT_ROOT = ROOT / "reports"


def hints_from_audit(audit: Optional[dict]) -> Dict[int, str]:
    """modern_audit[route] -> {步号: 建议条件}"""
    out: Dict[int, str] = {}
    for adv in ((audit or {}).get("audit") or {}).get("advice", []) or []:
        short = (adv.get("modern") or "").split("；")[0].strip()
        text = "%s：%s" % (adv.get("zh") or "建议", short) if short else (adv.get("zh") or "")
        for st in (adv.get("steps") or []):
            out[st] = text
    return out


def combined(route: dict, audit_by_route: Dict[str, dict]) -> float:
    audit = audit_by_route.get(str(route.get("index", route.get("route_index")))) or {}
    return 0.65 * float(route.get("score") or route.get("green_score") or 0) + \
        0.35 * float(audit.get("score") or 0)


def render_set(routes: List[dict], ranked: List[dict], target: str, label: str,
               out_dir: Path, stem: str, hints_by_route: Dict[str, Dict[int, str]],
               extras: Dict[int, str], sep: str = "_") -> Dict[int, str]:
    """画：每个展示路线的单图 + 一张图版；返回 {路线号: 文件名}
    ``sep`` 为文件名的连接符：命令行是 ``_route1_scheme.png``，网页端是 ``route1_scheme.png``。"""
    files: Dict[int, str] = {}
    for r in routes:
        idx = r.get("index", r.get("route_index"))
        path = out_dir / ("%s%sroute%s_scheme.png" % (stem, sep, idx))
        if scheme.save_route_scheme(r, path, target_smiles=target, target_name=label,
                                    condition_hints=hints_by_route.get(str(idx)) or {}):
            files[idx] = path.name
    sheet_routes = list(ranked[:4])
    best = extras.get("best")
    if best is not None and all(best.get("index") != r.get("index") for r in sheet_routes):
        sheet_routes.append(best)
    scheme.render_route_sheet(sheet_routes[:5], out_dir / ("%s%sroutes_scheme.png" % (stem, sep)),
                              max_routes=5, target_smiles=target, target_name=label,
                              subtitle="按绿色评分排序；结构式表示分子，箭头上下为模板条件与建议条件")
    return files


def do_web_run(run_dir: Path) -> int:
    result = run_dir / "result.json"
    if not result.is_file():
        return 0
    data = json.loads(result.read_text(encoding="utf-8"))
    molecules = data.get("molecules") or []
    done = 0
    for i, item in enumerate(molecules, start=1):
        routes = ((item.get("retro") or {}).get("routes")) or []
        if not routes:
            continue
        label = item.get("label") or ("mol%d" % i)
        target = item.get("canonical_smiles") or ""
        stem = ("%02d_" % i if len(molecules) > 1 else "") + mr.safe_filename(str(label))
        audit_by_route = {str(s.get("route")): s for s in ((item.get("modern") or {}).get("scores") or [])}
        # 逐路线建议条件：modern.advice[].occurrences = [[路线号, 步号], ...]
        hints_by_route: Dict[str, Dict[int, str]] = {}
        for adv in ((item.get("modern") or {}).get("advice") or []):
            short = (adv.get("modern") or "").split("；")[0].strip()
            text = "%s：%s" % (adv.get("zh") or "建议", short) if short else (adv.get("zh") or "")
            for occ in (adv.get("occurrences") or []):
                if len(occ) == 2:
                    hints_by_route.setdefault(str(occ[0]), {})[occ[1]] = text
        best = (item.get("green") or {}).get("recommended")
        files = render_set(routes[:4], routes, target, str(label), run_dir, stem,
                           hints_by_route, {"best": best}, sep="")
        # 回填 scheme 文件名，保持前端不用重跑也能显示
        for group in (item.get("retro"), item.get("green")):
            if not group:
                continue
            for r in (group.get("routes") or []) + ([group["recommended"]] if group.get("recommended") else []):
                r["scheme"] = files.get(r.get("index"), r.get("scheme") or "")
        done += len(files)
    if done:
        result.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return done


def do_report_json(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    assessments = data.get("green_assessment") or []
    if not assessments:
        return 0
    stem = path.name[:-len("_report.json")]
    target = data.get("canonical_smiles") or ""
    label = data.get("name") or stem
    audit = {str(k): v for k, v in (data.get("modern_audit") or {}).items()}
    plaus = {str(k): v for k, v in (data.get("plausibility") or {}).items()}
    routes = sorted(assessments, key=lambda r: r.get("green_score") or 0, reverse=True)
    for r in routes:                                   # 把可行性判定挂回路线对象，供图上标注
        pl = plaus.get(str(r.get("route_index")))
        if pl:
            r["plausibility"] = pl
            for s in (r.get("steps") or []):
                s["plaus_flags"] = [h["zh"] for h in
                                    ((pl.get("step_flags") or {}).get(str(s.get("step"))) or [])]
    clean = [r for r in routes if not r.get("precursor_suspicious")] or routes

    def _rank(r):
        pl = plaus.get(str(r.get("route_index"))) or {}
        tier = {"通过": 0, "需确认": 1, "不推荐": 2}.get(pl.get("verdict"), 1)
        return tier, -combined(r, audit)

    best = min(clean, key=_rank) if clean else None
    hints_by_route = {str(r.get("route_index")): hints_from_audit(audit.get(str(r.get("route_index"))))
                      for r in routes}
    # 与 mol_report.py 保持一致：搜索序前 3 条 + 绿色优先若干条 + 综合推荐，最多 6 条
    shown = list(assessments[:3])
    reserve = 1 if best is not None else 0
    for r in routes:
        if len(shown) >= 6 - reserve:
            break
        if all(r.get("route_index") != s.get("route_index") for s in shown):
            shown.append(r)
    if best is not None and all(best.get("route_index") != s.get("route_index") for s in shown):
        shown.append(best)
    shown = shown[:6]
    # 先清掉旧的流程图（命名规则曾变动过，避免留下重复文件）
    for stale in path.parent.glob("%s*route*_scheme.png" % stem):
        stale.unlink(missing_ok=True)
    for stale in path.parent.glob("%s*routes_scheme.png" % stem):
        stale.unlink(missing_ok=True)
    files = render_set(shown, routes, target, str(label), path.parent, stem,
                       hints_by_route, {"best": best}, sep="_")
    return len(files)


def do_batch_dir(batch_json: Path) -> int:
    """批量目录：用各分子报告 JSON 里的推荐路线拼一张批量流程图版"""
    items = []
    for report in sorted(batch_json.parent.glob("*_report.json")):
        data = json.loads(report.read_text(encoding="utf-8"))
        assessments = data.get("green_assessment") or []
        if not assessments:
            continue
        audit = {str(k): v for k, v in (data.get("modern_audit") or {}).items()}
        plaus = {str(k): v for k, v in (data.get("plausibility") or {}).items()}
        routes = sorted(assessments, key=lambda r: r.get("green_score") or 0, reverse=True)
        for r in routes:
            pl = plaus.get(str(r.get("route_index")))
            if pl:
                r["plausibility"] = pl
        clean = [r for r in routes if not r.get("precursor_suspicious")] or routes
        if not clean:
            continue

        def _rank(r):
            pl = plaus.get(str(r.get("route_index"))) or {}
            tier = {"通过": 0, "需确认": 1, "不推荐": 2}.get(pl.get("verdict"), 1)
            return tier, -combined(r, audit)

        pick = min(clean, key=_rank)
        pick["condition_hints"] = hints_from_audit(audit.get(str(pick.get("route_index"))))
        items.append({"label": data.get("name"), "smiles": data.get("canonical_smiles"), "route": pick})
    if not items:
        return 0
    out = batch_json.parent / "batch_routes_scheme.png"
    scheme.render_batch_sheet(items, out, max_rows=6)
    return len(items)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("rerender_schemes", description="用已保存的结果重画路线流程图")
    ap.add_argument("--reports-dir", default=str(REPORT_ROOT))
    ap.add_argument("--run", default=None, help="只处理某次网页运行 id")
    args = ap.parse_args(argv)
    reports = Path(args.reports_dir)
    total = 0

    web_root = reports / "web"
    if web_root.is_dir():
        for run_dir in sorted(web_root.iterdir()):
            if not run_dir.is_dir() or run_dir.name.startswith("_"):
                continue
            if args.run and run_dir.name != args.run:
                continue
            n = do_web_run(run_dir)
            if n:
                print("网页运行 %s：重画 %d 张" % (run_dir.name, n))
            total += n

    for report in sorted(reports.rglob("*_report.json")):
        if web_root in report.parents:
            continue
        n = do_report_json(report)
        if n:
            print("命令报告 %s：重画 %d 张" % (report.relative_to(reports), n))
        total += n

    for summary in sorted(reports.rglob("batch_summary.json")):
        if web_root in summary.parents:
            continue
        n = do_batch_dir(summary)
        if n:
            print("批量目录 %s：推荐路线 %d 条" % (summary.parent.relative_to(reports), n))
        total += n

    print("完成：共重画 %d 张流程图" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
