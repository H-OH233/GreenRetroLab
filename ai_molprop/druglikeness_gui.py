# -*- coding: utf-8 -*-
"""
成药性评估工具 —— 图形界面版（tkinter）

用法：python druglikeness_gui.py
功能：
  1) 输入 SMILES 点"开始评估"，显示溶解度 + 12 项毒性风险报告；
  2) "示例分子"下拉框填入内置示例；
  3) "批量评估 CSV…"：选择一个含 smiles 列的 CSV → 选择保存位置 → 自动评估并写结果表。

说明：首次评估会加载 3 个模型（RF / GraphConv / Tox21），约需 30 秒~1 分钟；
本工具是教学演示，结果不能替代实验与监管评估。
"""
import os                                                  # 操作系统接口
import tkinter as tk                                       # GUI 基础库
from tkinter import ttk, filedialog, messagebox, scrolledtext  # GUI 组件

import predictor as dt                                     # 复用预测模块（整合后的统一实现）

FONT = ("Microsoft YaHei", 10)                             # 界面字体（中文友好）
FONT_MONO = ("Consolas", 10)                               # 结果区等宽字体


class App:
    """主窗口。"""

    def __init__(self, root):
        self.root = root                                   # 保存根窗口
        self.models = None                                 # 懒加载模型（首次评估时加载）
        self._build_ui()                                   # 搭建界面

    # ---------------- 界面搭建 ----------------
    def _build_ui(self):
        self.root.title("成药性评估工具（水溶性 + Tox21 毒性）")  # 窗口标题
        self.root.geometry("900x700")                       # 窗口大小
        # 顶部：SMILES 输入行
        top = ttk.Frame(self.root, padding=8)              # 顶部容器
        top.pack(fill="x")                                  # 水平铺满
        ttk.Label(top, text="SMILES:", font=FONT).pack(side="left")  # 标签
        self.entry = ttk.Entry(top, font=FONT)             # 输入框
        self.entry.pack(side="left", fill="x", expand=True, padx=6)  # 可拉伸
        self.entry.bind("<Return>", lambda e: self.evaluate())  # 回车触发评估
        ttk.Button(top, text="开始评估", command=self.evaluate).pack(side="left")  # 评估按钮
        # 第二行：示例与批量
        bar = ttk.Frame(self.root, padding=(8, 0, 8, 4))   # 工具栏
        bar.pack(fill="x")                                  # 水平铺满
        ttk.Label(bar, text="示例:", font=FONT).pack(side="left")  # 示例标签
        self.example_var = tk.StringVar(value="阿司匹林")    # 示例选择变量
        examples = {                                        # 示例字典：显示名->SMILES
            "阿司匹林": "CC(=O)Oc1ccccc1C(=O)O",             # 阿司匹林
            "对乙酰氨基酚": "CC(=O)Nc1ccc(O)cc1",            # 对乙酰氨基酚
            "咖啡因": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",        # 咖啡因
            "布洛芬": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",          # 布洛芬
            "苯": "c1ccccc1",                                # 苯
        }                                                    # 结束
        self.examples = examples                             # 保存
        cb = ttk.Combobox(bar, textvariable=self.example_var,  # 下拉框
                          values=list(examples), width=12, font=FONT)  # 选项
        cb.pack(side="left", padx=4)                         # 放置
        ttk.Button(bar, text="填入示例", command=self.fill_example).pack(side="left")  # 填入按钮
        ttk.Button(bar, text="清空", command=lambda: self.entry.delete(0, "end")).pack(side="left", padx=4)  # 清空
        ttk.Button(bar, text="批量评估 CSV…", command=self.batch_csv).pack(side="right")  # 批量按钮
        # 中部：输出区
        self.out = scrolledtext.ScrolledText(self.root, font=FONT_MONO,  # 滚动文本框
                                             wrap="word", state="disabled")  # 只读
        self.out.pack(fill="both", expand=True, padx=8, pady=(0, 8))  # 铺满剩余空间
        # 底部：状态栏
        self.status = ttk.Label(self.root, text="就绪。输入 SMILES 后点'开始评估'。",  # 状态栏
                                font=("Microsoft YaHei", 9), relief="sunken")  # 样式
        self.status.pack(fill="x")                           # 底栏

    # ---------------- 工具方法 ----------------
    def _log(self, text):
        """向输出区追加一行（自动滚动到底部）。"""
        self.out.configure(state="normal")                  # 临时可编辑
        self.out.insert("end", text + "\n")                 # 追加
        self.out.see("end")                                  # 滚动到底
        self.out.configure(state="disabled")                # 恢复只读

    def _ensure_models(self):
        """确保模型已加载（首次调用约 30s~1min）。"""
        if self.models is None:                              # 尚未加载
            self.status.configure(text="正在加载模型（首次约 30 秒~1 分钟），请稍候…")  # 状态提示
            self.root.update()                               # 刷新界面
            self.models = dt.load_models()                   # 加载（失败会抛异常）
            self.status.configure(text="模型加载完成。")       # 完成提示

    def fill_example(self):
        """把选中的示例 SMILES 填进输入框。"""
        name = self.example_var.get()                        # 选中的名字
        if name in self.examples:                            # 有效选项
            self.entry.delete(0, "end")                      # 清空输入框
            self.entry.insert(0, self.examples[name])        # 填入 SMILES

    def evaluate(self):
        """评估输入框中的 SMILES。"""
        smiles = self.entry.get().strip()                    # 取出并去空白
        if not smiles:                                       # 空输入
            messagebox.showwarning("提示", "请先输入 SMILES。")  # 弹窗
            return                                           # 返回
        try:                                                 # 捕获预测错误
            self._ensure_models()                            # 加载模型
            self._log("\n" + "=" * 66)                       # 分隔
            self.status.configure(text=f"评估中：{smiles} …")  # 状态
            self.root.update()                               # 刷新
            self._log(dt.build_report_text(self.models, smiles))  # 打印报告
            self.status.configure(text="评估完成。")           # 状态更新
        except Exception as e:                               # 出错
            messagebox.showerror("评估失败", str(e))          # 弹窗报错

    def batch_csv(self):
        """选择 CSV 批量评估。"""
        try:                                                 # 捕获错误
            self._ensure_models()                            # 加载模型
            in_path = filedialog.askopenfilename(            # 选择输入
                title="选择含 SMILES 的 CSV", filetypes=[("CSV", "*.csv")])  # 过滤类型
            if not in_path:                                  # 取消选择
                return                                       # 返回
            out_path = filedialog.asksaveasfilename(         # 选择输出
                title="保存评估结果", defaultextension=".csv",  # 默认扩展名
                initialfile=os.path.basename(in_path).replace(".csv", "_评估结果.csv"))  # 默认名
            if not out_path:                                 # 取消保存
                return                                       # 返回
            self.status.configure(text=f"批量评估中：{os.path.basename(in_path)} …")  # 状态
            self.root.update()                               # 刷新
            self._log(f"\n开始批量评估：{in_path}")            # 日志
            n, outp = dt.batch_csv(self.models, in_path, "smiles", out_path)  # 执行批量
            self._log(f"完成：{n} 行 -> {outp}")               # 日志
            self.status.configure(text=f"批量评估完成：{outp}")  # 状态
        except Exception as e:                               # 出错
            messagebox.showerror("批量评估失败", str(e))       # 弹窗报错


def main():
    root = tk.Tk()                                           # 创建根窗口
    App(root)                                                # 实例化应用
    root.mainloop()                                          # 进入事件循环


if __name__ == "__main__":
    main()                                                   # 启动 GUI
