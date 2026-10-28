# -*- coding: utf-8 -*-
"""
data_loader.py —— 数据加载模块

统一封装本项目用到的 DeepChem 数据集加载：
  * load_delaney()        : Delaney(ESOL) 水溶性回归数据集（featurizer / splitter 可选）
  * load_tox21()          : Tox21 多任务毒性数据集（默认 ECFP + scaffold）
  * delaney_smiles_list() : 按行序返回 ESOL 全部 SMILES（用于结构筛选实验）
  * make_substruct_mask() : 用 RDKit HasSubstructMatch 判断分子是否含某子结构（如苯环）

数据缓存目录：默认 <项目根>/.dcdata（可用环境变量 DEEPCHEM_DATA_DIR 覆盖）；
首次运行若原始 CSV 不存在会自动下载到该目录。
"""
import os                                                  # 操作系统接口
import urllib.request                                      # 下载原始 CSV（兜底）
import warnings                                            # 警告模块
warnings.filterwarnings("ignore", message=".*please use MorganGenerator.*")  # 屏蔽 rdkit 弃用刷屏

import numpy as np                                         # 数值计算
import pandas as pd                                        # 读取原始 CSV
import deepchem as dc                                      # DeepChem
from rdkit import Chem                                     # RDKit 分子工具

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))  # 项目根目录（本文件所在目录）

# 数据缓存目录：优先取环境变量，否则用项目内 .dcdata
DATA_DIR = os.environ.get("DEEPCHEM_DATA_DIR",             # 已有环境变量
                          os.path.join(PROJECT_ROOT, ".dcdata"))  # 默认项目内缓存
os.makedirs(DATA_DIR, exist_ok=True)                       # 确保目录存在
os.environ["DEEPCHEM_DATA_DIR"] = DATA_DIR                 # 让 deepchem 使用同一目录

DELANEY_CSV = os.path.join(DATA_DIR, "delaney-processed.csv")  # ESOL 原始数据文件
DELANEY_Y_COL = "measured log solubility in mols per litre"   # 回归目标列名
DELANEY_URL = (                                            # deepchem 官方数据源（loader 内部也会用）
    "https://deepchemdata.s3-us-west-1.amazonaws.com/datasets/delaney-processed.csv")  # 地址
BENZENE_SMARTS = "c1ccccc1"                                # 苯环 SMARTS 子结构模板


def set_seed(seed=42):
    """重置随机种子：让随机/scaffold 划分结果可复现（deepchem 内部随机数用 numpy）。"""
    np.random.seed(seed)                                   # numpy 种子


def load_delaney(featurizer="ECFP", splitter="random", transform_y=True):
    """加载 Delaney(ESOL) 水溶性数据集。

    参数:
        featurizer : 'ECFP'(指纹, 供随机森林) 或 'GraphConv'(分子图, 供图卷积)
        splitter   : 'random' 或 'scaffold'（骨架划分，更接近真实难度）
        transform_y: True=对 logS 做均值/方差标准化（深度学习更稳）；False=保留原始单位
    返回:
        (tasks, (train, valid, test), transformers)
    """
    set_seed(42)                                           # 划分前固定种子
    transformers = ["normalization"] if transform_y else []  # 是否套标准化器
    tasks, datasets, transformers_obj = dc.molnet.load_delaney(  # deepchem 内置加载
        featurizer=featurizer,                             # 特征类型
        splitter=splitter,                                 # 划分方式
        transformers=transformers,                         # 变换器（可为空列表）
    )
    return tasks, datasets, transformers_obj                # 返回结果


def load_tox21(featurizer="ECFP", splitter="scaffold"):
    """加载 Tox21 毒性数据集（12 个二分类毒性终点，标注允许缺失）。

    返回:
        (tasks, (train, valid, test), transformers)
    """
    set_seed(42)                                           # 划分前固定种子
    return dc.molnet.load_tox21(                           # deepchem 内置加载
        featurizer=featurizer,                             # 特征
        splitter=splitter,                                 # 划分
    )                                                      # 直接返回三元组


def ensure_delaney_csv():
    """确保 ESOL 原始 CSV 存在（缺则下载），返回文件路径。"""
    if not os.path.exists(DELANEY_CSV):                    # 本地没有
        print(f"正在下载 ESOL 数据集 -> {DELANEY_CSV} ...")  # 提示
        req = urllib.request.Request(DELANEY_URL, headers={"User-Agent": "Mozilla/5.0"})  # 请求
        with urllib.request.urlopen(req, timeout=120) as r, open(DELANEY_CSV, "wb") as f:  # 流式写
            f.write(r.read())                              # 写入文件
    return DELANEY_CSV                                     # 返回路径


def delaney_smiles_list():
    """按数据集行序返回 ESOL 全部 SMILES 列表（与 dc 数据集 ids=行号 对应）。"""
    ensure_delaney_csv()                                   # 确保文件在
    df = pd.read_csv(DELANEY_CSV)                          # 读表
    return df["smiles"].astype(str).tolist()               # 返回 smiles 列表


def delaney_targets_list():
    """按行序返回 ESOL 全部真实 logS（原始单位）。"""
    ensure_delaney_csv()                                   # 确保文件在
    df = pd.read_csv(DELANEY_CSV)                          # 读表
    return df[DELANEY_Y_COL].astype(float).tolist()        # 目标列


def make_substruct_mask(smiles_list, smarts=BENZENE_SMARTS):
    """用 RDKit HasSubstructMatch 逐个判断 smiles_list 是否含 smarts 子结构。
    返回等长布尔列表；SMILES 解析失败视为不含。"""
    pattern = Chem.MolFromSmarts(smarts)                   # 编译子结构模板
    mask = []                                              # 结果列表
    for smi in smiles_list:                                # 逐个分子
        mol = Chem.MolFromSmiles(smi)                      # 解析 SMILES
        if mol is None:                                    # 解析失败
            mask.append(False)                             # 记 False
            continue                                       # 下一个
        mask.append(mol.HasSubstructMatch(pattern))        # 子结构匹配结果
    return np.asarray(mask, dtype=bool)                    # 转布尔数组
