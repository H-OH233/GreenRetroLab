# -*- coding: utf-8 -*-
"""
构建「药物中文名 / 别名 / 商品名 / 俗名」词库（tools/build_alias_table.py）
================================================================================
设计原则（很重要）：
  * 别名、商品名、俗名、缩写由人工整理（这是本词库的价值所在）；
  * **SMILES 一律从 PubChem 现取并核验**，不由人手写，避免结构记错；
  * 取回后校验分子式/分子量是否存在、能否被 RDKit 解析，并记录 CID 便于溯源；
  * 结果写入 data/drug_alias_table.json，供 drug_aliases.py 离线查询。

运行（需要联网）：
    python tools/build_alias_table.py            # 全量构建
    python tools/build_alias_table.py --only 阿司匹林,布洛芬
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "drug_alias_table.json"
UA = {"User-Agent": "GreenRetroLab/1.0 (local research tool)"}

# =====================================================================
# 词条表：(中文主名, [中文别名], [英文名/通用名], [商品名/俗名], [缩写], PubChem 查询名, 备注)
# 说明：商品名与俗名为人工整理、尽力而为；不要把它们当作权威药典数据。
# =====================================================================
ENTRIES = [
    # ---------------- A. 常用小分子药物 ----------------
    ("阿司匹林", ["乙酰水杨酸", "醋柳酸", "阿斯匹林"], ["aspirin", "acetylsalicylic acid"],
     ["拜阿司匹灵", "巴米尔", "阿司匹林肠溶片"], ["ASA"], "aspirin", ""),
    ("对乙酰氨基酚", ["扑热息痛", "醋氨酚", "乙酰氨基酚"], ["paracetamol", "acetaminophen"],
     ["泰诺", "泰诺林", "百服宁", "必理通"], ["APAP"], "acetaminophen", ""),
    ("布洛芬", ["异丁苯丙酸"], ["ibuprofen"], ["芬必得", "美林", "布洛芬缓释胶囊"], [], "ibuprofen", ""),
    ("萘普生", ["甲氧萘丙酸"], ["naproxen"], ["萘普生钠"], [], "naproxen", ""),
    ("双氯芬酸", ["二氯芬酸"], ["diclofenac"], ["扶他林", "英太青", "双氯芬酸钠"], [], "diclofenac", ""),
    ("塞来昔布", [], ["celecoxib"], ["西乐葆"], ["CBX"], "celecoxib", ""),
    ("阿莫西林", ["羟氨苄青霉素"], ["amoxicillin"], ["阿莫仙", "再林"], [], "amoxicillin", "青霉素类抗生素"),
    ("头孢氨苄", ["苯甘孢霉素"], ["cephalexin"], ["先锋IV号", "先锋4号"], [], "cephalexin", ""),
    ("左氧氟沙星", ["可乐必妥"], ["levofloxacin"], ["可乐必妥", "利复星"], [], "levofloxacin", "喹诺酮类"),
    ("阿奇霉素", [], ["azithromycin"], ["希舒美", "泰力特"], [], "azithromycin", "大环内酯类"),
    ("甲硝唑", ["灭滴灵", "灭滴唑"], ["metronidazole"], ["灭滴灵"], [], "metronidazole", ""),
    ("奥美拉唑", [], ["omeprazole"], ["洛赛克"], [], "omeprazole", "质子泵抑制剂"),
    ("二甲双胍", ["盐酸二甲双胍"], ["metformin"], ["格华止", "美迪康"], [], "metformin", "常见盐型：盐酸盐"),
    ("格列本脲", ["优降糖", "格列苯脲"], ["glibenclamide", "glyburide"], ["优降糖"], [], "glyburide", ""),
    ("硝苯地平", [], ["nifedipine"], ["心痛定", "拜新同"], [], "nifedipine", "钙通道阻滞剂"),
    ("氨氯地平", ["苯磺酸氨氯地平"], ["amlodipine"], ["络活喜", "压氏达"], [], "amlodipine", "常见盐型：苯磺酸盐"),
    ("缬沙坦", [], ["valsartan"], ["代文"], [], "valsartan", "沙坦类降压药"),
    ("阿托伐他汀", ["阿伐他汀"], ["atorvastatin"], ["立普妥", "阿乐"], [], "atorvastatin", "他汀类"),
    ("辛伐他汀", ["舒降之"], ["simvastatin"], ["舒降之"], [], "simvastatin", "他汀类"),
    ("华法林", ["华法林钠", "苄丙酮香豆素钠"], ["warfarin"], ["芬兰产华法林"], [], "warfarin", "抗凝药，治疗窗窄"),

    # ---------------- B. 神经/精神科药物 ----------------
    ("地西泮", ["安定", "苯甲二氮䓬"], ["diazepam"], ["安定"], [], "diazepam", "苯二氮䓬类，管制药品"),
    ("阿普唑仑", ["佳静安定", "佳乐定"], ["alprazolam"], ["佳静安定"], [], "alprazolam", "苯二氮䓬类，管制药品"),
    ("氯硝西泮", ["氯硝安定"], ["clonazepam"], ["氯硝安定"], [], "clonazepam", "苯二氮䓬类，管制药品"),
    ("佐匹克隆", [], ["zopiclone"], ["忆梦返"], [], "zopiclone", "镇静催眠，管制药品"),
    ("舍曲林", ["盐酸舍曲林"], ["sertraline"], ["左洛复"], [], "sertraline", "SSRI 抗抑郁药"),
    ("氟西汀", ["盐酸氟西汀"], ["fluoxetine"], ["百优解", "优克"], [], "fluoxetine", "SSRI"),
    ("帕罗西汀", ["盐酸帕罗西汀"], ["paroxetine"], ["赛乐特"], [], "paroxetine", "SSRI"),
    ("文拉法辛", ["盐酸文拉法辛"], ["venlafaxine"], ["怡诺思"], [], "venlafaxine", "SNRI"),
    ("喹硫平", ["富马酸喹硫平"], ["quetiapine"], ["思瑞康"], [], "quetiapine", "非典型抗精神病药"),
    ("奥氮平", [], ["olanzapine"], ["再普乐"], [], "olanzapine", "非典型抗精神病药"),
    ("卡马西平", ["酰胺咪嗪"], ["carbamazepine"], ["得理多"], [], "carbamazepine", "抗癫痫"),
    ("丙戊酸", ["丙戊酸钠", "德巴金"], ["valproic acid", "valproate"], ["德巴金"], [], "valproic acid", "抗癫痫"),
    ("左乙拉西坦", [], ["levetiracetam"], ["开浦兰"], [], "levetiracetam", "抗癫痫"),
    ("多奈哌齐", ["盐酸多奈哌齐"], ["donepezil"], ["安理申"], [], "donepezil", "阿尔茨海默病"),
    ("美金刚", ["盐酸美金刚"], ["memantine"], ["易倍申"], [], "memantine", "阿尔茨海默病"),
    ("加巴喷丁", ["盐酸加巴喷丁"], ["gabapentin"], ["迭力"], [], "gabapentin", "抗癫痫/神经痛，存在滥用报告"),
    ("普瑞巴林", [], ["pregabalin"], ["乐瑞卡"], [], "pregabalin", "管制药品，存在滥用报告"),
    ("托吡酯", ["妥泰"], ["topiramate"], ["妥泰"], [], "topiramate", "抗癫痫/偏头痛"),

    # ---------------- C. 镇痛药与滥用物质（毒理评估重点） ----------------
    ("吗啡", ["盐酸吗啡"], ["morphine"], [], [], "morphine", "麻醉药品，强管制"),
    ("可待因", ["甲基吗啡", "磷酸可待因"], ["codeine"], [], [], "codeine", "麻醉药品，管制"),
    ("羟考酮", ["盐酸羟考酮"], ["oxycodone"], ["奥施康定"], [], "oxycodone", "麻醉药品，管制"),
    ("芬太尼", ["枸橼酸芬太尼"], ["fentanyl"], ["多瑞吉"], [], "fentanyl", "麻醉药品；类似物非法滥用严重"),
    ("曲马多", ["盐酸曲马多"], ["tramadol"], ["奇曼丁"], [], "tramadol", "管制药品"),
    ("氯胺酮", ["K粉", "K他命", "克他命", "盐酸氯胺酮"], ["ketamine"], ["K粉"], [], "ketamine", "第一类精神药品，滥用严重"),
    ("甲基苯丙胺", ["冰毒", "甲基安非他命", "去氧麻黄碱"], ["methamphetamine"], ["冰毒", "ice"], ["MA", "METH"],
     "methamphetamine", "毒品，中国列管"),
    ("苯丙胺", ["安非他命"], ["amphetamine"], ["安非他命"], [], "amphetamine", "毒品，列管"),
    ("亚甲二氧基甲基苯丙胺", ["摇头丸", "摇头丸(MDMA)", "Ecstasy", "迷魂药"], ["MDMA", "ecstasy"],
     ["摇头丸"], ["MDMA"], "MDMA", "毒品，列管"),
    ("γ-羟丁酸", ["听话水", "神仙水", "液体摇头丸", "迷奸水"], ["gamma-hydroxybutyric acid", "GHB"],
     ["听话水"], ["GHB"], "4-hydroxybutanoic acid", "毒品/迷药，列管"),
    ("依托咪酯", ["太空油", "上头电子烟", "烟弹"], ["etomidate"], ["太空油"], [], "etomidate",
     "静脉麻醉药；2023 年起中国列管为第二类精神药品，常被掺入电子烟油"),
    ("赛拉嗪", ["甲苯噻嗪", "tranq", "zombie drug"], ["xylazine"], ["tranq dope"], [], "xylazine",
     "兽用镇静剂；近年常见于芬太尼掺假（美国已列管）"),
    ("右美沙芬", ["氢溴酸右美沙芬", "DXM", "美沙芬"], ["dextromethorphan"], ["惠菲宁"], ["DXM"],
     "dextromethorphan", "OTC 止咳药，过量可致幻，存在滥用"),
    ("溴唑仑", [], ["bromazolam"], [], [], "bromazolam", "设计型苯二氮䓬（新型精神活性物质）"),
    ("卡痛", ["帽蕊柱", "美丽帽柱木", "kratom"], ["mitragynine"], [], [], "mitragynine",
     "卡痛叶主要活性成分，多国管制"),
    ("一氧化二氮", ["笑气", "氧化亚氮"], ["nitrous oxide"], ["笑气"], ["N2O"], "nitrous oxide",
     "吸入滥用致神经损伤，中国已列管"),
    ("大麻二酚", ["CBD"], ["cannabidiol"], [], ["CBD"], "cannabidiol", ""),
    ("四氢大麻酚", ["THC"], ["delta-9-tetrahydrocannabinol"], [], ["THC"], "dronabinol", "毒品成分"),
    ("尼古丁", ["烟碱"], ["nicotine"], [], [], "nicotine", ""),

    # ---------------- D. 2019 年后的新药 / 热点药物 ----------------
    ("奈玛特韦", ["尼马瑞韦", "帕罗韦德主要成分"], ["nirmatrelvir"], ["Paxlovid 主药", "帕罗韦德", "派罗韦德", "帕克斯洛维德"], [], "nirmatrelvir", "抗新冠 3CL 蛋白酶抑制剂"),
    ("利托那韦", [], ["ritonavir"], ["Norvir"], [], "ritonavir", "常作药代动力学增强剂"),
    ("莫诺拉韦", ["莫努匹韦", "EIDD-2801"], ["molnupiravir"], ["利卓瑞"], [], "molnupiravir", "抗新冠核苷类似物"),
    ("瑞德西韦", ["伦地西韦"], ["remdesivir"], ["Veklury"], [], "remdesivir", "抗病毒核苷类似物"),
    ("阿兹夫定", [], ["azvudine"], ["阿兹夫定片"], [], "azvudine", "国产抗新冠/抗 HIV 核苷类似物"),
    ("先诺特韦", [], ["simnotrelvir"], ["先诺欣主要成分"], [], "simnotrelvir", "国产抗新冠 3CL 抑制剂"),
    ("巴洛沙韦", ["玛巴洛沙韦", "速福达"], ["baloxavir marboxil", "baloxavir"], ["速福达"], [], "baloxavir marboxil", "抗流感"),
    ("奥司他韦", ["磷酸奥司他韦", "达菲"], ["oseltamivir"], ["达菲"], [], "oseltamivir", "抗流感神经氨酸酶抑制剂"),
    ("司美格鲁肽", ["诺和泰", "诺和盈", "索马鲁肽"], ["semaglutide"], ["诺和泰", "诺和盈"], [],
     "semaglutide", "GLP-1 多肽（分子量大，模板逆合成不适用）"),
    ("替尔泊肽", ["穆峰达"], ["tirzepatide"], ["穆峰达"], [], "tirzepatide", "GIP/GLP-1 双激动剂多肽"),
    ("利拉鲁肽", ["诺和力"], ["liraglutide"], ["诺和力"], [], "liraglutide", "GLP-1 多肽"),
    ("奥希替尼", ["泰瑞沙", "AZD9291"], ["osimertinib"], ["泰瑞沙"], [], "osimertinib", "第三代 EGFR 抑制剂"),
    ("伊布替尼", ["亿珂", "依鲁替尼"], ["ibrutinib"], ["亿珂"], [], "ibrutinib", "BTK 抑制剂"),
    ("来那度胺", ["雷利度胺", "瑞复美"], ["lenalidomide"], ["瑞复美"], [], "lenalidomide", "免疫调节剂"),
    ("恩杂鲁胺", ["安可坦", "恩扎卢胺"], ["enzalutamide"], ["安可坦"], [], "enzalutamide", "雄激素受体拮抗剂"),
    ("阿帕他胺", ["安森珂"], ["apalutamide"], ["安森珂"], [], "apalutamide", ""),
    ("维奈克拉", ["维奈托克", "唯可来"], ["venetoclax"], ["唯可来"], [], "venetoclax", "BCL-2 抑制剂"),
    ("索托拉西布", ["AMG 510"], ["sotorasib"], [], [], "sotorasib", "KRAS G12C 抑制剂"),
    ("玛伐凯泰", [], ["mavacamten"], ["Camzyos"], [], "mavacamten", "心肌肌球蛋白抑制剂"),
    ("非奈利酮", ["可申达"], ["finerenone"], ["可申达"], [], "finerenone", "非甾体 MRA"),
    ("达格列净", ["安达唐"], ["dapagliflozin"], ["安达唐"], [], "dapagliflozin", "SGLT2 抑制剂"),
    ("恩格列净", ["欧唐静"], ["empagliflozin"], ["欧唐静"], [], "empagliflozin", "SGLT2 抑制剂"),
    ("西格列汀", ["捷诺维", "磷酸西格列汀"], ["sitagliptin"], ["捷诺维"], [], "sitagliptin",
     "DPP-4 抑制剂；其转氨酶催化手性胺路线是绿色合成的经典案例"),
    ("依折麦布", ["益适纯"], ["ezetimibe"], ["益适纯"], [], "ezetimibe", "胆固醇吸收抑制剂"),
    ("巴瑞替尼", ["艾乐明"], ["baricitinib"], ["艾乐明"], [], "baricitinib", "JAK 抑制剂"),
    ("托法替布", ["尚杰", "枸橼酸托法替布"], ["tofacitinib"], ["尚杰"], [], "tofacitinib", "JAK 抑制剂"),
    ("乌帕替尼", ["瑞福"], ["upadacitinib"], ["瑞福"], [], "upadacitinib", "JAK1 抑制剂"),
    ("艾司氯胺酮", ["艾司氯胺酮鼻喷雾", "速开朗"], ["esketamine"], ["速开朗"], [], "esketamine", "抗抑郁，管制药品"),

    # ---------------- E. 常见溶剂 / 试剂（中文名） ----------------
    ("乙醇", ["酒精"], ["ethanol", "ethyl alcohol"], [], ["EtOH"], "ethanol", ""),
    ("甲醇", ["木醇", "木精"], ["methanol", "methyl alcohol"], [], ["MeOH"], "methanol", "有毒，慎用"),
    ("二甲基亚砜", ["DMSO"], ["dimethyl sulfoxide"], [], ["DMSO"], "dimethyl sulfoxide", ""),
    ("N,N-二甲基甲酰胺", ["二甲基甲酰胺", "DMF"], ["N,N-dimethylformamide"], [], ["DMF"],
     "dimethylformamide", "生殖毒性，绿色替代：DMAc/NMP 或直接无溶剂"),
    ("四氢呋喃", ["THF"], ["tetrahydrofuran"], [], ["THF"], "tetrahydrofuran", ""),
    ("二氯甲烷", ["DCM", "亚甲基氯"], ["dichloromethane"], [], ["DCM"], "dichloromethane", "卤代溶剂，逐步淘汰"),
    ("乙腈", ["甲基氰", "ACN"], ["acetonitrile"], [], ["ACN", "MeCN"], "acetonitrile", ""),
    ("甲苯", [], ["toluene"], [], [], "toluene", ""),
    ("吡啶", [], ["pyridine"], [], [], "pyridine", ""),
    ("三乙胺", ["TEA"], ["triethylamine"], [], ["TEA"], "triethylamine", ""),
    ("碳酸二甲酯", ["DMC"], ["dimethyl carbonate"], [], ["DMC"], "dimethyl carbonate",
     "绿色甲基化/羰基化试剂，可替代硫酸二甲酯与光气"),
    ("硫酸二甲酯", ["二甲基硫酸酯", "DMS"], ["dimethyl sulfate"], [], ["DMS"], "dimethyl sulfate",
     "致癌物，应优先用碳酸二甲酯/碘甲烷替代"),
    ("乙酸乙酯", ["醋酸乙酯", "EA"], ["ethyl acetate"], [], ["EtOAc"], "ethyl acetate", ""),
    ("丙酮", [], ["acetone"], [], [], "acetone", ""),
    ("正己烷", [], ["n-hexane", "hexane"], [], [], "hexane", "神经毒性，可用庚烷替代"),
    ("乙醚", ["二乙醚", "醚"], ["diethyl ether"], [], ["Et2O"], "diethyl ether", "易爆"),
    ("水", [], ["water"], [], [], "water", "最绿色溶剂"),
    ("二氧化碳", [], ["carbon dioxide"], [], ["CO2"], "carbon dioxide", "绿色 C1 源"),

    # ---------------- F. 其他常见分子 ----------------
    ("咖啡因", ["1,3,7-三甲基黄嘌呤"], ["caffeine"], [], [], "caffeine", ""),
    ("茶碱", ["1,3-二甲基黄嘌呤"], ["theophylline"], [], [], "theophylline", ""),
    ("可可碱", ["3,7-二甲基黄嘌呤"], ["theobromine"], [], [], "theobromine", ""),
    ("维生素C", ["抗坏血酸", "维C"], ["ascorbic acid", "vitamin C"], [], ["VC"], "ascorbic acid", ""),
    ("维生素E", ["α-生育酚"], ["alpha-tocopherol", "vitamin E"], [], [], "alpha-tocopherol", ""),
    ("叶酸", ["维生素B9"], ["folic acid"], [], [], "folic acid", ""),
    ("柠檬酸", ["枸橼酸"], ["citric acid"], [], [], "citric acid", ""),
    ("尿素", ["脲", "碳酰胺"], ["urea"], [], [], "urea", ""),
    ("葡萄糖", ["右旋糖", "血糖"], ["glucose", "D-glucose"], [], [], "glucose", ""),
    ("地塞米松", ["氟美松", "德沙美松"], ["dexamethasone"], ["氟美松"], [], "dexamethasone", "糖皮质激素"),
    ("氢化可的松", ["皮质醇"], ["hydrocortisone", "cortisol"], [], [], "hydrocortisone", ""),
    ("沙丁胺醇", ["舒喘灵", "硫酸沙丁胺醇"], ["salbutamol", "albuterol"], ["万托林"], [], "salbutamol", "支气管扩张剂"),
    ("氯雷他定", ["开瑞坦"], ["loratadine"], ["开瑞坦"], [], "loratadine", "抗组胺药"),
    ("西替利嗪", ["仙特明"], ["cetirizine"], ["仙特明"], [], "cetirizine", "抗组胺药"),
    ("奥美拉唑镁", [], ["omeprazole magnesium"], [], [], "omeprazole magnesium", "盐型示例"),
]


def fetch_pubchem(name: str, retries: int = 3) -> dict | None:
    """从 PubChem 取 SMILES / 分子式 / 分子量 / CID（只按英文名查询）"""
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/%s/property/"
           "CanonicalSMILES,IsomericSMILES,MolecularFormula,MolecularWeight/JSON"
           % urllib.parse.quote(name))
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as resp:
                props = json.loads(resp.read().decode("utf-8"))["PropertyTable"]["Properties"][0]
            return {
                "cid": props.get("CID"),
                "smiles": props.get("CanonicalSMILES") or props.get("SMILES"),
                "isomeric": props.get("IsomericSMILES"),
                "formula": props.get("MolecularFormula"),
                "mw": props.get("MolecularWeight"),
            }
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return None


def verify(entry: dict) -> tuple[bool, str]:
    """用 RDKit 复核 SMILES 可解析，并回报分子量与 PubChem 的一致性"""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors
    except Exception:
        return True, "（未安装 RDKit，跳过复核）"
    mol = Chem.MolFromSmiles(entry["smiles"] or "")
    if mol is None:
        return False, "SMILES 无法被 RDKit 解析"
    mw = Descriptors.MolWt(mol)
    try:
        ref = float(entry.get("mw") or 0)
    except Exception:
        ref = 0.0
    if ref and abs(mw - ref) > max(0.6, ref * 0.005):
        return False, "分子量与 PubChem 不一致（%.2f vs %s）" % (mw, entry.get("mw"))
    entry["rdkit_mw"] = round(mw, 2)
    return True, "OK (MW %.2f)" % mw


def main() -> int:
    parser = argparse.ArgumentParser("build_alias_table")
    parser.add_argument("--only", default=None, help="只处理这些中文主名（逗号分隔）")
    parser.add_argument("--sleep", type=float, default=0.25, help="每次请求间隔，别把 PubChem 打爆")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    table: dict = {}
    if OUT.is_file():
        table = json.loads(OUT.read_text(encoding="utf-8"))
    problems, ok, skipped = [], 0, 0

    for zh, zh_alt, en, trade, abbr, query, note in ENTRIES:
        if only and zh not in only:
            continue
        key = query.lower()
        info = fetch_pubchem(query)
        time.sleep(args.sleep)
        if not info or not info.get("smiles"):
            problems.append((zh, query, "PubChem 未取到"))
            skipped += 1
            print("  [!] %-24s %-28s 取不到" % (zh, query))
            continue
        entry = {
            "zh": zh, "zh_alt": zh_alt, "en": en, "trade": trade, "abbr": abbr,
            "pubchem_query": query, "note": note,
            "cid": info["cid"], "smiles": info["smiles"], "isomeric": info.get("isomeric"),
            "formula": info.get("formula"), "mw": info["mw"],
        }
        good, message = verify(entry)
        if not good:
            problems.append((zh, query, message))
            print("  [!] %-24s %-28s %s" % (zh, query, message))
            continue
        table[key] = entry
        ok += 1
        print("  [ok] %-24s CID=%-9s %-14s %s" % (zh, entry["cid"], entry["formula"], message))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n完成：成功 %d 条，失败 %d 条 → %s" % (ok, skipped, OUT))
    if problems:
        print("需要人工处理：")
        for zh, query, why in problems:
            print("  - %s（%s）：%s" % (zh, query, why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
