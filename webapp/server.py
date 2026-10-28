# -*- coding: utf-8 -*-
"""
GreenRetroLab 网页前端 —— 本地 HTTP 服务（只用标准库，不引入额外依赖）
================================================================================
运行环境：与 mol_report.py 相同（aizynthfinder 环境，Python 3.11）
    python webapp/server.py [--port 8766] [--no-browser]

设计要点：
  * 只监听 127.0.0.1，所有计算都在本机完成，分子结构与结果不出机器（页面里也如实写明）；
  * 评估任务放到后台线程执行，前端轮询 /api/job/<id> 拿进度，避免长请求被浏览器超时掐断；
  * 复用 mol_report.py / green_route_assessment.py 的全部算法，不重复实现；
  * 批量输入（多行）时走桥接的批量模式，模型只加载一次。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent          # 包根目录
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webapp"))

import mol_report as mr                                # noqa: E402  复用主程序全部算法
import green_route_assessment as gra                   # noqa: E402
import drug_aliases as alias                           # noqa: E402  中文名/别名/商品名解析
import modern_methods as modern                        # noqa: E402  时代性审阅
import scheme_render as scheme                         # noqa: E402  论文式反应流程图（结构式 + 箭头条件）
import plausibility as plaus                           # noqa: E402  可行性复核（结构级规则 + 过滤器概率 + 科学分）
import precedent as prec                               # noqa: E402  反应先例检索（kNN 适用域）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPORT_ROOT = ROOT / "reports" / "web"                 # 网页端产物目录
DEFAULT_PORT = 8766

JOBS: dict = {}                                        # job_id -> 任务状态
JOBS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()                            # 同一时刻只跑一个流水线（都很吃 CPU）
HISTORY: list = []                                     # 会话历史（最近若干次）
HISTORY_MAX = 30


# ============================================================ 小工具

def structure_svg(smiles: str, width: int = 460, height: int = 300) -> str:
    """用 RDKit 画分子结构 SVG（内联进页面，不依赖外部服务）"""
    try:
        from rdkit import Chem
        from rdkit.Chem.Draw import rdMolDraw2D

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return ""
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        opts = drawer.drawOptions()
        opts.clearBackground = False                   # 透明底，贴合页面纸色
        opts.bondLineWidth = 2
        opts.minFontSize = 13
        opts.maxFontSize = 16
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
        svg = re.sub(r"<\?xml[^>]*\?>", "", svg)       # 去掉 xml 声明，便于内联
        svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg)
        return svg.strip()
    except Exception:
        return ""


def short(text, limit: int = 46) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _looks_like_smiles(text: str) -> bool:
    """判断输入是否已经是 SMILES（能解析就当 SMILES，否则按名称去别名词库查）"""
    try:
        from rdkit import Chem
        return Chem.MolFromSmiles(str(text)) is not None
    except Exception:
        return False


def step_hints(modern_info: dict | None) -> dict:
    """从时代性审阅结果里抽「步号 → 建议条件」，供流程图写在箭头下方"""
    hints: dict = {}
    for adv in ((modern_info or {}).get("audit") or {}).get("advice", []) or []:
        short = (adv.get("modern") or "").split("；")[0].strip()
        text = "%s：%s" % (adv.get("zh") or "建议", short) if short else (adv.get("zh") or "")
        for st in (adv.get("steps") or []):
            hints[st] = text
    return hints


def molimg_cache() -> Path:
    d = REPORT_ROOT / "_molimg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_mol_image(smiles: str, w: int, h: int) -> bytes | None:
    """结构式缩略图（按 SMILES+尺寸做磁盘缓存，网页表格里替代 SMILES 文本）"""
    import hashlib
    key = hashlib.sha1(("%s|%d|%d" % (smiles, w, h)).encode("utf-8")).hexdigest()[:20]
    path = molimg_cache() / ("%s.png" % key)
    if path.is_file():
        return path.read_bytes()
    data = scheme.thumbnail(smiles, w, h)
    if data:
        try:
            path.write_bytes(data)
        except Exception:
            pass
    return data


def route_payload(assessment, modern_info: dict | None = None) -> dict:
    """把 RouteAssessment 转成前端好用的结构（可附带时代性审阅结果）"""
    payload = {
        "index": assessment.route_index,
        "score": assessment.green_score,
        "grade": assessment.grade,
        "grade_label": assessment.grade_label,
        "steps": assessment.n_steps,
        "solved": assessment.is_solved,
        "atom_economy": assessment.atom_economy,
        "safe": assessment.n_low_risk,
        "hazardous": assessment.n_hazardous,
        "highly_hazardous": assessment.n_highly_hazardous,
        "suspicious": assessment.precursor_suspicious,
        "precursor_note": assessment.precursor_note,
        "min_occurrence": assessment.min_template_occurrence,
        "modern_score": (modern_info or {}).get("score"),
        "modern_dated_steps": (modern_info or {}).get("dated_steps"),
        "modern_reasons": (modern_info or {}).get("reasons") or [],
        "dated_labels": sorted({h["label"] for h in ((modern_info or {}).get("audit") or {}).get("dated", [])}),
        "step_detail": [
            {
                "step": s["step"],
                "reactants": s["reactants"],
                "product": s["product"],
                "ae": s["atom_economy"],
                "template": s.get("template_code"),
                "occurrence": s.get("template_occurrence"),
                "probability": s.get("policy_probability"),
                "feasibility": s.get("feasibility"),          # 可行性复核：内置过滤器概率
                "plaus_flags": s.get("plaus_flags") or [],    # 命中的结构级规则
                "precedent_sim": s.get("precedent_sim"),      # 真实反应库最近先例相似度
                "precedent": s.get("precedent_example"),      # 最近先例（原料/产物/试剂）
            }
            for s in assessment.steps
        ],
        "materials": [
            {
                "smiles": m["smiles"],
                "name": m["name"],
                "caption": scheme.mol_caption(m["smiles"], m.get("name") or ""),
                "level": m["level"],
                "h_codes": m["h_codes"],
                "statements": m.get("statements", {}),
                "whitelist": m["is_common_safe"],
                "tags": m.get("tags", []),
            }
            for m in assessment.materials
        ],
        "scheme": "",                       # 由 run_one 填：论文式路线流程图文件名
        "plausibility": None,               # 由 run_one 填：可行性复核结果
    }
    return payload


# ============================================================ 评估流水线

def run_one(text: str, record: dict, opts: dict, out_dir: Path, config_path: Path,
            emit, index: int = 1, total: int = 1, batch: bool = False,
            alias_info: dict | None = None) -> dict:
    """单个分子的完整评估（性质结果由上游批量桥接一次性给出）"""
    props = (record or {}).get("properties") or {}
    props_error = "" if props else ((record or {}).get("error") or "性质预测未完成")

    canonical = props.get("canonical_smiles") or (alias_info or {}).get("smiles")
    if not canonical:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(text)
        canonical = Chem.MolToSmiles(mol) if mol is not None else None
    label = (alias_info or {}).get("zh") or props.get("resolve_english") or text
    item = {
        "input": text, "label": short(label, 40), "canonical_smiles": canonical,
        "alias_info": alias_info or {},
        "properties_error": props_error, "descriptors": {}, "properties": props,
        "structure_svg": "", "retro": None, "green": None, "modern": None, "conclusion": [],
        "figures": {}, "report_text": "", "summary": {},
    }
    if not canonical:
        item["summary"] = {"error": "无法解析为分子"}
        return item

    item["descriptors"] = mr.molecule_descriptors(canonical)
    item["structure_svg"] = structure_svg(canonical)

    # ---- 逆合成 + 绿色评分 + 时代性审阅 ----
    stats, assessments = None, []
    modern_audit: dict = {}
    plaus_map: dict = {}
    too_big = bool(item["descriptors"].get("mw") and item["descriptors"]["mw"] > opts.get("max_mw", 1200))
    if opts.get("skip_retro"):
        pass
    elif too_big:
        emit("  分子量 %.0f 超过阈值，模板逆合成不适用，已跳过" % item["descriptors"]["mw"], "warn")
        item["properties_error"] = item.get("properties_error") or "分子量过大，已跳过逆合成"
    else:
        emit("逆合成搜索：%s" % canonical)
        finder, trees, stats = mr.run_retrosynthesis(
            canonical, config_path, opts.get("iterations"), opts.get("time_limit"),
            opts.get("routes", 3))
        emit("  搜索完成：节点 %s，路线 %s，已打通 %s，用时 %.1f 秒"
             % (stats.get("number_of_nodes"), stats.get("number_of_routes"),
                stats.get("number_of_solved_routes"), stats.get("wall_time")))
        assessments = [gra.assess_route(t, i + 1, allow_network=not opts.get("offline"))
                       for i, t in enumerate(trees)]
        for a in assessments:                              # 时代性审阅（逐路线）
            occurrences = [s.get("template_occurrence") for s in a.steps]
            modern_audit[a.route_index] = modern.contemporary_score(a.steps, a.n_steps, occurrences)
        dated_routes = [k for k, v in modern_audit.items() if v.get("dated_steps")]
        emit("  路线评估完成：%d 条路线（%d 条疑似同系物拆解）；方法学审阅：%d 条路线含过时做法"
             % (len(assessments), sum(1 for a in assessments if a.precursor_suspicious),
                len(dated_routes)))
        # 可行性复核：① 反应先例检索（kNN 适用域）② 内置过滤器概率 ③ 结构级规则 → 科学分
        try:
            prec_index = prec.load(ROOT / "data" / "reaction_precedents.npz")
            for a in assessments:
                probs = plaus.filter_probabilities(finder, a.steps)
                prec_info = prec.assess_route(a.steps, prec_index) if prec_index else None
                res = plaus.assess_route(
                    a.steps, filter_probs=probs,
                    precursor_suspicious=a.precursor_suspicious,
                    template_occurrences=[s.get("template_occurrence") for s in a.steps],
                    precedent_info=prec_info)
                plaus_map[a.route_index] = res
                a.plausibility = res
                for s in a.steps:
                    s["feasibility"] = probs.get(s.get("step"))
                    s["plaus_flags"] = plaus.step_flag_labels(res, s.get("step"))
                    st = ((prec_info or {}).get("steps") or {}).get(s.get("step")) or {}
                    s["precedent_sim"] = st.get("sim_max")
                    s["precedent_example"] = (st.get("examples") or [None])[0]
            blocked = sum(1 for v in plaus_map.values() if v["verdict"] == plaus.VERDICT_BLOCK)
            checked = sum(1 for v in plaus_map.values() if v["verdict"] == plaus.VERDICT_CHECK)
            emit("  可行性复核：%d 条不推荐、%d 条需确认、%d 条通过（先例库 %s 条真实反应）"
                 % (blocked, checked, len(assessments) - blocked - checked,
                    ("%d" % prec_index.n) if prec_index else "未建立"),
                 "warn" if blocked else "info")
        except Exception as exc:
            emit("  可行性复核跳过：%s" % exc, "warn")

    # ---- 图表与文本报告 ----
    stem = ("%02d_" % index if batch else "") + mr.safe_filename(str(label))
    prop_png = out_dir / ("%s_properties.png" % stem)
    green_png = out_dir / ("%s_green.png" % stem)
    if props:
        if mr.plot_property_panel(props, item["descriptors"], prop_png, str(label)):
            item["figures"]["properties"] = prop_png.name
    if assessments:
        gra.plot_scores(assessments, green_png)
        item["figures"]["green"] = green_png.name

    # 排序与推荐（流程图与绿色评分都用到，故提前计算）
    ranked = sorted(assessments, key=lambda a: a.green_score, reverse=True)
    clean = [a for a in ranked if not a.precursor_suspicious]

    def combined(item_route):
        audit = modern_audit.get(item_route.route_index) or {}
        return plaus.final_score(plaus_map.get(item_route.route_index), item_route.green_score,
                                 audit.get("score"))

    def rank(item_route):
        return plaus.rank_key(plaus_map.get(item_route.route_index), combined(item_route))

    pool = clean or ranked
    best = min(pool, key=rank) if pool else None
    best_green = max(pool, key=lambda a: a.green_score) if pool else None

    # ---- 论文式路线流程图（结构式 + 箭头条件）----
    scheme_files: dict = {}
    if assessments:
        shown = ranked[:4]
        if best is not None and all(best.route_index != a.route_index for a in shown):
            shown.append(best)
        for a in shown:
            p = out_dir / ("%sroute%d_scheme.png" % (stem, a.route_index))
            if scheme.save_route_scheme(a, p, target_smiles=canonical, target_name=str(label),
                                        condition_hints=step_hints(modern_audit.get(a.route_index))):
                scheme_files[a.route_index] = p.name
        sheet_name = "%sroutes_scheme.png" % stem
        if scheme.render_route_sheet(shown[:5], out_dir / sheet_name, max_routes=5,
                                     target_smiles=canonical, target_name=str(label),
                                     subtitle="按绿色评分排序；结构式表示分子，箭头上下为模板条件与建议条件"):
            item["figures"]["scheme"] = sheet_name

    artifacts = {k: str(out_dir / v) for k, v in
                 (("性质面板", item["figures"].get("properties", "")),
                  ("绿色评分", item["figures"].get("green", "")),
                  ("路线流程图", item["figures"].get("scheme", ""))) if v}
    for idx in sorted(scheme_files):
        artifacts["路线 %s 流程图" % idx] = str(out_dir / scheme_files[idx])
    item["report_text"] = mr.render_report(str(label), text, props, props_error,
                                           item["descriptors"], stats, assessments,
                                           bool(opts.get("offline")), artifacts,
                                           alias_info=alias_info, modern_audit=modern_audit,
                                           scheme_files={k: str(out_dir / v) for k, v in scheme_files.items()},
                                           plaus_map=plaus_map)

    # ---- 前端用的结构化结果 ----
    item["retro"] = {
        "stats": stats,
        "routes": [route_payload(a, modern_audit.get(a.route_index)) for a in ranked],
    } if stats else None
    item["green"] = {
        "ranked": [route_payload(a, modern_audit.get(a.route_index)) for a in ranked],
        "recommended": route_payload(best, modern_audit.get(best.route_index)) if best else None,
        "recommended_is_suspicious": bool(best and best.precursor_suspicious),
        "suspicious_count": sum(1 for a in ranked if a.precursor_suspicious),
        "green_best_index": best_green.route_index if best_green else None,
    } if ranked else None
    for group in (item.get("retro"), item.get("green")):
        if not group:
            continue
        for r in (group.get("routes") or []) + ([group["recommended"]] if group.get("recommended") else []):
            r["scheme"] = scheme_files.get(r.get("index"), "")
            r["plausibility"] = plaus_map.get(r.get("index"))
    item["plausibility"] = {
        "routes": [{"route": r.route_index,
                    "verdict": (plaus_map.get(r.route_index) or {}).get("verdict"),
                    "score": (plaus_map.get(r.route_index) or {}).get("score"),
                    "science_score": (plaus_map.get(r.route_index) or {}).get("science_score"),
                    "min_sim": (plaus_map.get(r.route_index) or {}).get("min_sim"),
                    "min_filter": (plaus_map.get(r.route_index) or {}).get("min_filter"),
                    "reasons": (plaus_map.get(r.route_index) or {}).get("reasons") or [],
                    "blocked": (plaus_map.get(r.route_index) or {}).get("blocked") or [],
                    "cautions": (plaus_map.get(r.route_index) or {}).get("cautions") or []}
                   for r in ranked],
        "recommended_verdict": (plaus_map.get(best.route_index) or {}).get("verdict") if best else None,
        "recommended_science": (plaus_map.get(best.route_index) or {}).get("science_score") if best else None,
        "blocked_routes": sorted(k for k, v in plaus_map.items() if v.get("verdict") == plaus.VERDICT_BLOCK),
        "green_best_verdict": (plaus_map.get(best_green.route_index) or {}).get("verdict") if best_green else None,
        "index_size": (prec.load() .n if prec.load() else None),
    } if plaus_map else None
    # 前端只做逆合成讨论：性质/绿色图表与完整文本报告留在磁盘，不进前端载荷
    item["figures"] = {k: v for k, v in (item.get("figures") or {}).items() if k == "scheme"}

    # 时代性区块：评分表 + 跨路线合并后的替代建议（带真实文献 DOI）
    if modern_audit:
        merged = modern.merge_advice(modern_audit, limit=8)
        for entry in merged:
            entry["citations"] = modern.citations_for(entry.get("key"), limit=2)
            entry["occurrence_text"] = "、".join("路线%d第%s步" % (r, s) for r, s in entry["occurrences"])
        item["modern"] = {
            "scores": [{"route": r.route_index,
                        "score": (modern_audit.get(r.route_index) or {}).get("score"),
                        "dated_steps": (modern_audit.get(r.route_index) or {}).get("dated_steps"),
                        "reasons": (modern_audit.get(r.route_index) or {}).get("reasons") or []}
                       for r in ranked],
            "advice": merged,
            "flagged_routes": sorted(k for k, v in modern_audit.items() if v.get("dated_steps")),
            "citation_stats": modern.citation_summary(),
        }
    item["conclusion"] = mr.synthesize_conclusion(props, props_error, assessments, modern_audit,
                                                  plaus_map, routes_only=True)

    extra = (props.get("extra_tox") or {}).get("items") or []
    herg = next((i.get("prob") for i in extra if "hERG" in str(i.get("cn", ""))), None)
    ld50 = next((i.get("ld50_mg_kg") for i in extra if i.get("kind") == "regression"), None)
    item["summary"] = {
        "mean_logS": props.get("mean_logS"), "sol_class": props.get("sol_class"),
        "tox_high": props.get("high"), "tox_suspect": props.get("suspect"),
        "herg": herg, "ld50": ld50,
        "best_score": best.green_score if best else None,
        "best_grade": best.grade if best else None,
        "best_steps": best.n_steps if best else None,
        "best_ae": best.atom_economy if best else None,
        "hazardous": best.n_hazardous if best else None,
        "routes_total": stats.get("number_of_routes") if stats else None,
        "routes_solved": stats.get("number_of_solved_routes") if stats else None,
        # 时代性字段必须放在这里（summary 是整体重建的，写在前面会被覆盖）
        "modern_score": (modern_audit.get(best.route_index) or {}).get("score") if best else None,
        "dated_steps": (modern_audit.get(best.route_index) or {}).get("dated_steps") if best else None,
    }
    return item


def pipeline(job: dict) -> None:
    """后台线程：跑完整条流水线，把进度追加到 job['log']"""
    def emit(line: str, kind: str = "info") -> None:
        job["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": line})

    opts = job["options"]
    targets = job["targets"]
    batch = len(targets) > 1
    out_dir = REPORT_ROOT / job["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    try:
        data_dir = mr.DEFAULT_DATA_DIR
        mr.configure_paths(data_dir)
        config_path = mr.prepare_config(data_dir, refresh=opts.get("refresh_config", False))
        if not config_path.is_file():
            raise RuntimeError("找不到配置文件：%s（请先运行 --doctor 自检）" % config_path)

        emit("样本 %d 个，输出目录 %s" % (len(targets), out_dir))
        emit("初始化：程序目录 %s" % mr.ROOT)

        # ---- 中文名/别名/商品名/俗名解析（离线优先；命中就不必联网解析名称）----
        alias_infos = []
        for text in targets:
            info = None
            if not _looks_like_smiles(text):
                try:
                    info = alias.resolve(text)
                except Exception:
                    info = None
            alias_infos.append(info)
        hit = sum(1 for info in alias_infos if info)
        if hit:
            emit("别名词库命中 %d/%d：%s" % (hit, len(targets), "、".join(
                "%s→%s" % (targets[i], alias_infos[i]["zh"]) for i in range(len(targets))
                if alias_infos[i])))
        unresolved = [targets[i] for i in range(len(targets))
                      if not alias_infos[i] and not _looks_like_smiles(targets[i])]
        if unresolved:
            emit("未命中本地词库，交给 ai-molprop 在线解析：%s" % "、".join(unresolved), "warn")
        bridge_targets = [(alias_infos[i] or {}).get("smiles") or targets[i]
                          for i in range(len(targets))]

        # ---- 一次性启动性质预测桥接（批量时模型只加载一次）----
        props_json = out_dir / ("_props.jsonl" if batch else "_props.json")
        bridge_log = out_dir / "_bridge.log"
        records: list = [{} for _ in targets]
        if opts.get("skip_props"):
            emit("已跳过性质预测（--skip-props）", "warn")
        else:
            pyexe = opts.get("molprop_python") or mr.DEFAULT_MOLPROP_PYTHON
            if not pyexe or not Path(pyexe).exists():
                raise RuntimeError("找不到 ai-molprop 环境的 python，请先运行 install\\INSTALL.bat")
            emit("性质预测：加载模型（一次性，约 20~35 秒）…")
            proc = mr.start_property_bridge(bridge_targets, props_json, bridge_log,
                                            python_exe=pyexe,
                                            bridge=opts.get("bridge") or mr.DEFAULT_BRIDGE,
                                            workdir=opts.get("molprop_dir") or mr.DEFAULT_MOLPROP_DIR)
            for idx, rec in enumerate(mr.iter_bridge_records(proc, props_json, batch=batch)):
                if idx < len(records):
                    records[idx] = rec
                props = rec.get("properties") or {}
                if props:
                    emit("  %s → 分子已解析（性质在后台计算，前端不展示）" % short(rec.get("input"), 28))
                else:
                    emit("  %s → 预测失败：%s" % (short(rec.get("input"), 28), rec.get("error")), "warn")
            try:
                proc.wait(timeout=60)
            except Exception:
                pass

        # ---- 逐个分子：逆合成 + 绿色评分 + 报告 ----
        molecules = []
        for idx, text in enumerate(targets, start=1):
            emit("评估分子 %d/%d：%s" % (idx, len(targets), text))
            molecules.append(run_one(text, records[idx - 1] if idx - 1 < len(records) else {},
                                     opts, out_dir, config_path, emit,
                                     index=idx, total=len(targets), batch=batch,
                                     alias_info=alias_infos[idx - 1]))

        # ---- 批量汇总 ----
        batch_png = None
        if batch:
            rows = []
            for item in molecules:
                s = item["summary"]
                rows.append({"label": item["label"], "input": item["input"],
                             "mean_logS": s.get("mean_logS"), "best_green_score": s.get("best_score"),
                             "tox_high": s.get("tox_high"), "mw": item["descriptors"].get("mw")})
            overview = out_dir / "batch_overview.png"
            if mr.plot_batch_overview(rows, overview):
                batch_png = overview.name
            emit("批量总览图已生成")

        job["result"] = {
            "run_id": job["id"],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "batch": batch,
            "elapsed": round(time.time() - started, 1),
            "log": job["log"],                           # 带上日志，便于用 ?run= 重新打开时还原
            "engine": {
                "data_dir": str(mr.DEFAULT_DATA_DIR),
                "config": str(config_path),
                "molprop_python": opts.get("molprop_python") or mr.DEFAULT_MOLPROP_PYTHON,
                "iteration_limit": opts.get("iterations"),
                "routes": opts.get("routes"),
            },
            "molecules": molecules,
            "batch_overview": batch_png,
        }
        job["state"] = "done"
        emit("完成，用时 %.1f 秒" % (time.time() - started), "ok")
        # 结果落盘：这样服务重启后 ?run= 链接与历史记录依然可用
        try:
            (out_dir / "result.json").write_text(
                json.dumps(job["result"], ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            emit("结果落盘失败（不影响查看）：%s" % exc, "warn")
        with JOBS_LOCK:
            HISTORY.insert(0, {
                "run_id": job["id"], "created_at": job["result"]["created_at"],
                "batch": batch,
                # 前端只做逆合成讨论：历史里也只带路线相关指标（科学分/可行性/路线数）
                "items": [{"label": m["label"], "input": m["input"],
                           "science": (m.get("plausibility") or {}).get("recommended_science"),
                           "verdict": (m.get("plausibility") or {}).get("recommended_verdict"),
                           "routes": (m["summary"] or {}).get("routes_solved")} for m in molecules],
            })
            del HISTORY[HISTORY_MAX:]
    except Exception as exc:
        job["state"] = "error"
        job["error"] = "%s: %s" % (type(exc).__name__, exc)
        emit("失败：%s" % job["error"], "err")
        traceback.print_exc()
    finally:
        job["finished"] = time.time()


def start_job(texts: list, options: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "log": [], "result": None, "error": None,
           "targets": texts, "options": options, "created": time.time(), "finished": None}

    def wrapper() -> None:
        with RUN_LOCK:                                  # 串行执行，避免两个重任务互相抢 CPU
            job["state"] = "running"
            pipeline(job)

    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=wrapper, daemon=True).start()
    return job_id


def load_saved_result(run_id: str) -> dict | None:
    """从磁盘读取某次运行的结果（服务重启后依然可用）"""
    if not re.fullmatch(r"[0-9a-f]{6,32}", run_id or ""):
        return None
    path = REPORT_ROOT / run_id / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def rebuild_history() -> None:
    """启动时从 reports/web/*/result.json 重建会话历史（按时间倒序）"""
    entries = []
    if REPORT_ROOT.is_dir():
        for child in REPORT_ROOT.iterdir():
            result = load_saved_result(child.name)
            if not result or not result.get("molecules"):
                continue
            entries.append((result.get("created_at", ""), {
                "run_id": result["run_id"], "created_at": result.get("created_at", ""),
                "batch": result.get("batch", False),
                "items": [{"label": m.get("label"), "input": m.get("input"),
                           "science": (m.get("plausibility") or {}).get("recommended_science"),
                           "verdict": (m.get("plausibility") or {}).get("recommended_verdict"),
                           "routes": ((m.get("summary") or {}).get("routes_solved"))}
                          for m in result["molecules"]],
            }))
    entries.sort(key=lambda kv: kv[0], reverse=True)
    HISTORY.extend([item for _, item in entries[:HISTORY_MAX]])


# ============================================================ HTTP 服务

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".png": "image/png",
        ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8",
        ".ico": "image/x-icon", ".woff2": "font/woff2"}


class Handler(BaseHTTPRequestHandler):
    server_version = "GreenRetroLab"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):                   # 安静一点，别刷控制台
        if os.environ.get("GREENRETRO_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- 输出助手 ----------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json({"error": "not found", "path": str(path)}, 404)
            return
        self._send(200, path.read_bytes(), MIME.get(path.suffix.lower(), "application/octet-stream"))

    # ---------- 路由 ----------
    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            self._file(STATIC_DIR / "index.html")
        elif path.startswith("/static/"):
            target = (STATIC_DIR / path[len("/static/"):]).resolve()
            if STATIC_DIR.resolve() in target.parents or target.parent == STATIC_DIR.resolve():
                self._file(target)
            else:
                self._json({"error": "forbidden"}, 403)
        elif path.startswith("/files/"):
            parts = path[len("/files/"):].split("/", 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{6,32}", parts[0]):
                self._file(REPORT_ROOT / parts[0] / parts[1])
            else:
                self._json({"error": "bad path"}, 400)
        elif path == "/molimg":
            # 结构式缩略图：用画出来的结构替代 SMILES 文本
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            smiles = (q.get("smiles") or [""])[0]
            try:
                w = max(60, min(int((q.get("w") or ["168"])[0]), 600))
                h = max(44, min(int((q.get("h") or ["108"])[0]), 400))
            except ValueError:
                w, h = 168, 108
            data = cached_mol_image(smiles, w, h) if smiles else None
            if data:
                self._send(200, data, "image/png")
            else:
                self._json({"error": "cannot draw molecule"}, 404)
        elif path == "/api/history":
            with JOBS_LOCK:
                self._json({"history": HISTORY})
        elif path.startswith("/api/job/"):
            job = JOBS.get(path.rsplit("/", 1)[-1])
            if not job:
                self._json({"error": "no such job"}, 404)
                return
            self._json({"id": job["id"], "state": job["state"], "log": job["log"],
                        "error": job["error"], "result": job["result"]})
        elif path.startswith("/api/result/"):
            run_id = path.rsplit("/", 1)[-1]
            job = JOBS.get(run_id)
            result = (job or {}).get("result") or load_saved_result(run_id)
            if not result:
                self._json({"error": "no such result"}, 404)
                return
            self._json(result)
        elif path == "/api/health":
            self._json({"ok": True, "root": str(ROOT), "data_dir": str(mr.DEFAULT_DATA_DIR),
                        "molprop_python": mr.DEFAULT_MOLPROP_PYTHON,
                        "molprop_found": bool(mr.DEFAULT_MOLPROP_PYTHON
                                              and Path(mr.DEFAULT_MOLPROP_PYTHON).exists())})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._json({"error": "invalid json"}, 400)
            return

        if path == "/api/run":
            raw = str(payload.get("input") or "")
            targets = [line.strip() for line in raw.splitlines() if line.strip()]
            targets = targets[:12]                       # 一次最多 12 个，避免误粘长文件
            if not targets:
                self._json({"error": "请输入至少一个分子"}, 400)
                return
            options = {
                "routes": max(1, min(int(payload.get("routes") or 3), 12)),
                "iterations": max(10, min(int(payload.get("iterations") or 200), 2000)),
                "time_limit": int(payload.get("time_limit") or 300),
                "offline": bool(payload.get("offline")),
                "skip_props": bool(payload.get("skip_props")),
                "skip_retro": bool(payload.get("skip_retro")),
            }
            self._json({"job_id": start_job(targets, options)})
        elif path == "/api/doctor":
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            code = 1
            try:
                with redirect_stdout(buf):
                    code = mr.main(["--doctor"])
            except SystemExit as exc:
                code = int(exc.code or 0)
            self._json({"code": code, "text": buf.getvalue()})
        else:
            self._json({"error": "not found"}, 404)


def main() -> int:
    parser = argparse.ArgumentParser("server", description="GreenRetroLab 网页前端")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    rebuild_history()                                    # 从磁盘恢复历史记录
    url = "http://%s:%d/" % (args.host, args.port)
    print("=" * 78)
    print("GreenRetroLab 网页前端")
    print("  地址      : %s" % url)
    print("  程序目录  : %s" % ROOT)
    print("  数据目录  : %s" % mr.DEFAULT_DATA_DIR)
    print("  molprop   : %s" % (mr.DEFAULT_MOLPROP_PYTHON or "（未找到，性质预测将不可用）"))
    print("  产物目录  : %s" % REPORT_ROOT)
    print("  只监听本机回环地址；所有计算都在本机完成，分子结构不会离开这台机器。")
    print("  Ctrl+C 停止服务")
    print("=" * 78)
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
