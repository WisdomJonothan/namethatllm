# Name That LLM — 复现、诊断与改进实验

Kaggle 竞赛 [Name That LLM](https://www.kaggle.com/competitions/name-that-llm)（Identify the model behind the text）的完整工作留痕：从零调研、复现公开 0.716 方案、真实提交验证、独立验证集构建、以及一轮系统的证伪实验。

本仓库同时包含**对话记录**（`docs/conversations/`）、**实验脚本与结果**（`scripts/`、`results/`）、**Kaggle 侧运行产物**（`runs/`、`kaggle/`）。

---

## 1. 竞赛事实

| 项 | 值 |
|---|---|
| 任务 | 给定 40–250 词英文段落，判断 18 个前沿 LLM 中哪一个写的 |
| 18 类 | `gpt-5.5`/`gpt-5.6-luna`/`gpt-5.6-sol`/`gpt-5.6-terra`/`gpt-6-astra`、`gemini-3.1-pro-preview`/`3.6-flash`/`3.7-flash`/`3.8-flash`、`claude-fable-5`/`opus-4-6`/`opus-4-8`/`opus-5`/`sonnet-5`、`qwen3.8-max`、`glm-5.3`/`glm-5.3-flash`、`kimi-k3` |
| 指标 | balanced accuracy（18 类宏平均召回，每类等权 1/18） |
| 数据 | train 2,699 条 / 150 个 prompt（每组基本 18 条，`claude-fable-5` 仅 149）；隐藏测试约 4,000 条，prompt 与训练不重叠 |
| 赛制 | Code Competition：只能提交 notebook，无网络重跑，CPU/GPU 各 ≤9 小时，输出 `submission.parquet` |
| 提交 | 每天 5 次，最终保留 2 个；public 40% / private 60% |
| 规模 | 6,600+ 队；金牌前 10，银牌前 5%（约 331），铜牌前 10%（约 663） |
| 奖金 | $50,000（前 10 名各 $5,000） |

## 2. 我们做了什么（按时间）

| 阶段 | 内容 | 结果 |
|---|---|---|
| 调研 | 赛制、规则澄清、公开前沿扫描 | 公开 notebook 最高 public **0.719**；0.717/0.716/0.715 全为同源 fork |
| 复现 | 拉取 `areebbb` v24 内核（0.716），修复挂载与路径，推成自己的版本 | `FAST_COMMIT` 471.8s 跑通，产出 `submission.parquet` |
| 真实提交 | 用隐藏测试集重跑完整流程（7 小时预算） | **public 0.716，当时排名 #269**，落进银牌区 |
| 独立验证集 | 外部语料去重后重建 3,350 行 / 550 prompt / 18 类 | `data/eval_v2.csv`，内部 prompting CV 0.663 |
| 诊断跑批 | 把测试集换成 eval_v2，跑完整六读者 + GSQ + native | 4h25m 完成，集成 BA **0.4783** |
| 证伪实验 | A1 prompt 固定效应、A3 零方差伪影、A4 BA 最优决策层 | 前两个负结果，A4 小正信号 +0.0056（弱模型） |
| GPT 三兄弟实验 | 同题配对专家与等预算对照 | 专家 +0.0056 被简单分差对照 +0.0202 追平，方向关闭 |
| stage3 诊断跑批 | 打开 GLM 似然阶段，落盘成员 LP 与逐 token 证据 | 6.6h 完成；成员对账 3.4e-6；**定位到 GLM 融合算法缺陷** |

## 3. 关键结论

**3.1 单次分数不可信。** 同码重跑噪声实测 ±0.01（公开 notebook 六次 0.682–0.694）。任何 <0.01 的提升都不能算证据，必须成对提交或 k=2 抽样 OOF。

**3.2 0.716 已在公开前沿。** 金牌线 0.751 需要 +0.035；公开知识内未发现可达路径。已证伪的方向：测试期 prompt 聚类 + Hungarian（−0.009~−0.017）、加骨干/加成员（±0.004）、额外 33k 公开行（OOF 反降）。

**3.3 独立集的分布差异是硬约束。** 同一 TF-IDF 模型：官方 train 的 prompt 分组 OOF 0.348，在 eval_v2 上只有 0.146，而 eval_v2 内部 CV 有 0.663。外部语料是「长文 + Markdown」体裁（词数中位 134 vs 101，含粗体 56–69% vs 0%），不能当作 LB 代理。

**3.4 集成在域外的塌缩是真实弱点。** eval_v2 上 `claude-fable-5` 召回 0.020、`kimi-k3` 0.038、`gemini-3.8-flash` 0.048，而 `claude-opus-5` 0.959。混淆去向集中在 `glm-5.3` / `claude-opus-4-6`。

**3.5 最后一层后处理对域外是负贡献。** 最终 `final-logits.npz` 的 argmax 在 eval_v2 上 0.5017，写进 parquet 的预测 0.4783 —— 差 0.023、约 5% 的行被改掉。说明这套后处理是对竞赛分布过拟合的。

**3.6 GLM 似然阶段的分数没被兑现，问题在融合算法。** 出厂版 `argmax(zl + w·ll)` 把 GLM 的 18 类意见直接加进全部 logits、与其余 16 类竞争，判错时会抢走别的类的质量；在同一读者底座（BA 0.48515）上它只带来 −0.0005。改成"保留该对总概率、只在两版之间重分"后，同一底座上是 **+0.0240**（理想判别上限 +0.0398），真 GLM 行的判定准确率从 0.311 升到 0.503。自似然本身能分这两版（AUC 0.99、准确率 91%），所以缺的不是评分器而是融合算术。详见 `docs/EXPERIMENTS.md` E12。

## 4. 已知缺陷

- **comp 通道整段丢失**：`comp_stack.py` 的 `find_checkpoint()` 按 `config.json` 的 `model_type`+`hidden_size` 选基座，**不校验权重文件**；挂载顺序变化后命中只有 config 的空壳目录 → `OSError: no file named model.safetensors`。兜底逻辑 `COMP SKIPPED` 让流程继续，但 `comp_w` 无从验证。
- **中间态未落盘**：`infer.py` 只在特定 `write()` 调用时覆盖 `final-logits.npz`，各阶段概率矩阵 P 不保存，导致 comp_w / sinkhorn / ρ 无法离线精确重放。stage3 补上了成员分项 LP 与 Apex 前预测，但 GSQ / native 两段的 P 仍未保存。
- **GLM 阶段的融合算术有缺陷**：见 3.6，出厂版把意见当加性 logit，未做质量保持。

## 5. 目录

```
data/            官方数据 + 自建独立集 eval_v2（见 data/README.md 的许可与来源）
docs/            实验结论、时间线、对话记录
  conversations/ 本项目相关的 DSH 会话 Markdown 导出（含索引）
kaggle/          Kaggle 侧资料：内核源码、数据集清单、提交记录
repro/           复现用 notebook（0.716 原版 + 诊断版）
results/         全部实验输出（json / npz / md）
runs/diag1/      诊断跑批产物：diag_lp.npy、final-logits.npz、diag_pred.parquet
runs/diag3/      stage3 产物：final-logits / Apex 前预测 / 两款 GLM 的逐文本特征与报告
scripts/         实验脚本（建集、五折、基线、聚类、重放、推送、轮询）
tools/           辅助工具（会话导出）
logs/            内核状态轮询日志
```

## 6. 复现

```bash
# 1. 官方数据（需已接受竞赛规则，见 scripts/kaggle_fetch.py）
python scripts/kaggle_fetch.py comp name-that-llm --all

# 2. 五折（按 prompt 分组，训练/验证 prompt 交集为 0）
python scripts/make_folds.py

# 3. TF-IDF 基线（OOF 0.3015；加风格特征 0.3478）
python scripts/baseline_tfidf.py
python scripts/baseline_style.py --fine

# 4. 独立验证集（外部语料两道去重）
python scripts/build_eval_v2.py

# 5. 诊断跑批（Kaggle 侧，4.5 小时）
python scripts/make_diag_notebook.py
python scripts/kaggle_push_diag.py
```

## 7. 许可

- 代码：MIT
- 竞赛数据：CC BY-SA 4.0（Kaggle / Google LLC 提供）
- `data/eval_v2.csv` 由公开数据集派生，遵守其上游许可，见 `data/README.md`
