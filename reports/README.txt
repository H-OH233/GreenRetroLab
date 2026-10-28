本目录存放评估报告（程序自动生成，可随时清空）。

单个分子会生成 4 类文件：
  <分子>_report.txt          完整文本报告（八节：分子信息/溶解度/毒性/逆合成/绿色评分+可行性复核/时代性审阅/结论/产物）
  <分子>_report.json         机器可读的全量数据
  <分子>_properties.png      性质面板图（溶解度 + Tox21 12 终点 + 附加毒性面板）
  <分子>_green_routes.png    绿色路线评分对比图（斜纹柱 = 疑似同系物拆解的伪路线）
  <分子>_routes_scheme.png   候选路线流程图版（论文式结构式流程图）
  <分子>_routeN_scheme.png   单条路线的结构式流程图（分子画成结构式，箭头上下为模板条件与建议条件）

批量模式（一次评估多个分子）额外生成：
  batch_summary.csv          汇总表（Excel 可直接打开）
  batch_summary.json         汇总数据
  batch_overview.png         总览气泡图：横轴溶解度、纵轴最佳绿色路线评分、
                             颜色=毒性高风险数、点大小=分子量
  batch_routes_scheme.png    各分子推荐路线的结构式流程图版（论文附图样式）

中间文件（排查问题用，可删）：
  _properties_raw.json / _properties_raw.jsonl   性质预测原始结果
  _molprop_bridge.log                            桥接子进程日志
  _bridge_inputs.txt                             批量输入临时文件
