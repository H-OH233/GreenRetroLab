# -*- coding: utf-8 -*-
"""
构建「反应先例库」（检索式适用域）
==================================

输入：USPTO 反应数据（parquet，HF 镜像上可下载，见 fetch_reaction_data 提示）
输出：``data/reaction_precedents.npz``，包含

* ``fps_diff``   ：反应差异指纹（产物 Morgan XOR 原料 Morgan），uint8 (n, 256) = 2048 bit
* ``fps_prod``   ：产物 Morgan 指纹，uint8 (n, 256)
* ``bits_diff`` / ``bits_prod``：每行置位数（算 Tanimoto 用）
* ``products`` / ``reactants``：SMILES（用于把最近先例展示给人看）
* ``source``     ：来源标记（uspto50k / uspto_full）
* ``classes``    ：USPTO-50k 的反应类别（若可用）

用途：对模板逆合成给出的某一步，检索真实反应库里最相似的先例——
相似度低 = 这一步「超出模板适用域」，即模板把它泛化到了训练时没见过的底物类型上。

用法：
  python tools/build_precedent_index.py --parquet D:/data/uspto50k_train.parquet \
      --parquet D:/data/uspto_full_train.parquet --out data/reaction_precedents.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rdkit import Chem, RDLogger                      # noqa: E402
from rdkit.Chem import AllChem                        # noqa: E402

RDLogger.DisableLog("rdApp.*")

FP_BITS = 2048
FP_RADIUS = 2
FP_BYTES = FP_BITS // 8


def _morgan_bits(mol) -> Optional[np.ndarray]:
    """Morgan 指纹 → 打包成 uint8 位串"""
    if mol is None:
        return None
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, FP_RADIUS, nBits=FP_BITS)
    arr = np.zeros((FP_BYTES,), dtype=np.uint8)
    for bit in fp.GetOnBits():
        arr[bit // 8] |= np.uint8(1 << (bit % 8))
    return arr


def _clean(smiles: str):
    """去原子映射、返回规范 SMILES；空串/解析失败/零原子一律视为无效"""
    if not smiles or not str(smiles).strip():
        return None, None
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None or mol.GetNumAtoms() == 0:
        return None, None
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None, None
    return mol, Chem.MolToSmiles(mol)


def _split_rxn(rxn_smiles: str) -> Tuple[Optional[str], Optional[str], str]:
    """拆成 (原料, 产物, 试剂)；兼容 'A>B>C'（含试剂）与 'A>B'（两段）两种格式"""
    if not rxn_smiles:
        return None, None, ""
    parts = [p.strip() for p in str(rxn_smiles).split(">")]
    if len(parts) >= 3:
        reactants, reagents, product = parts[0], parts[1], parts[-1]
    elif len(parts) == 2:
        reactants, reagents, product = parts[0], "", parts[1]
    else:
        return None, None, ""
    if not product:
        return None, None, ""
    return reactants, product, reagents


def iter_rows(parquet: Path):
    import pandas as pd
    df = pd.read_parquet(parquet)
    cols = {c.lower(): c for c in df.columns}
    prod_col = next((cols[k] for k in ("prod_smiles", "product", "products") if k in cols), None)
    rxn_col = next((cols[k] for k in ("rxn_smiles", "reaction", "reactions") if k in cols), None)
    cls_col = next((cols[k] for k in ("class", "rxn_class") if k in cols), None)
    if rxn_col is None and prod_col is None:
        raise SystemExit("找不到反应列：%s" % list(df.columns))
    print("列：rxn=%s prod=%s class=%s 行数=%d" % (rxn_col, prod_col, cls_col, len(df)))
    for rxn_smiles, prod_smiles, cls in zip(
            df[rxn_col] if rxn_col else [None] * len(df),
            df[prod_col] if prod_col else [None] * len(df),
            df[cls_col] if cls_col else [""] * len(df)):
        reactants, product, reagents = _split_rxn(rxn_smiles)
        if not product and prod_smiles:
            product = str(prod_smiles)
        yield reactants or "", product or "", str(cls), reagents


def build(parquets: List[Path], out_path: Path, max_per_source: int = 0) -> dict:
    diffs: List[np.ndarray] = []
    prods: List[np.ndarray] = []
    products: List[str] = []
    reactants: List[str] = []
    reagents: List[str] = []
    sources: List[str] = []
    classes: List[str] = []
    seen = set()
    stats: Dict[str, int] = {}

    for parquet in parquets:
        if not parquet.is_file():
            print("跳过（不存在）：%s" % parquet)
            continue
        tag = parquet.stem
        kept = skipped = dup = 0
        for reac_smi, prod_smi, cls, reag_smi in iter_rows(parquet):
            if max_per_source and kept >= max_per_source:
                break
            r_mol, r_can = _clean(reac_smi) if reac_smi else (None, None)
            p_mol, p_can = _clean(prod_smi)
            if p_mol is None:
                skipped += 1
                continue
            if r_mol is None:                     # 没有原料：算作单组分（自己），仍可入库
                r_can = ""
                r_bits = np.zeros((FP_BYTES,), dtype=np.uint8)
            else:
                r_bits = _morgan_bits(r_mol)
                if r_bits is None:
                    skipped += 1
                    continue
            key = (p_can, r_can)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            p_bits = _morgan_bits(p_mol)
            if p_bits is None:
                skipped += 1
                continue
            diffs.append(np.bitwise_xor(p_bits, r_bits))
            prods.append(p_bits)
            products.append(p_can)
            reactants.append(r_can)
            reagents.append(reag_smi[:120])
            sources.append(tag)
            classes.append(cls)
            kept += 1
        stats[tag] = kept
        print("%-28s 入库 %6d 条（跳过 %d，去重 %d）" % (parquet.name, kept, skipped, dup))

    if not diffs:
        raise SystemExit("没有可入库的反应")

    fps_diff = np.stack(diffs)
    fps_prod = np.stack(prods)
    lut = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    bits_diff = lut[fps_diff].sum(axis=1).astype(np.uint16)
    bits_prod = lut[fps_prod].sum(axis=1).astype(np.uint16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        fps_diff=fps_diff, fps_prod=fps_prod,
        bits_diff=bits_diff, bits_prod=bits_prod,
        products=np.array(products, dtype=object),
        reactants=np.array(reactants, dtype=object),
        reagents=np.array(reagents, dtype=object),
        sources=np.array(sources, dtype=object),
        classes=np.array(classes, dtype=object),
        meta=np.array([FP_BITS, FP_RADIUS], dtype=np.int32),
    )
    size_mb = out_path.stat().st_size / 1048576
    print("写出 %s：%d 条反应，%.1f MB" % (out_path, len(products), size_mb))
    return {"count": len(products), "size_mb": round(size_mb, 1), "sources": stats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("build_precedent_index", description="构建反应先例库（kNN 适用域）")
    ap.add_argument("--parquet", action="append", required=True, help="可多次传入")
    ap.add_argument("--out", default=str(ROOT / "data" / "reaction_precedents.npz"))
    ap.add_argument("--max-per-source", type=int, default=0, help="每个来源最多入库多少条（0=不限）")
    args = ap.parse_args(argv)
    build([Path(p) for p in args.parquet], Path(args.out), args.max_per_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
