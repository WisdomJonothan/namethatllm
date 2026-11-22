# A / B / C 阶段结果（2026-11-21）

## 已完成

| 项 | 状态 | 结果 |
|---|---|---|
| 竞赛数据 | ✅ | `data/train.csv` 2,699 行 / 150 prompt / 18 模型；`test.csv` 交互模式 3 行 |
| B 五折切分 | ✅ | `data/folds.csv`，prompt 级分组，每折 30 prompt、18 类齐、训练/验证 prompt 交集 = 0 |
| C TF-IDF 基线 | ✅ | OOF **BA = 0.3015**（各折 0.2963–0.3037，SD 0.0027） |
| C+ 风格特征 | ✅ | OOF **BA = 0.3478**（C=16，48 维法证特征，较纯 n-gram +0.0463） |
| transductive 杠杆 | ✅ | 分组可重建（pairwise F1 **0.914**），去重后 BA 0.3015 → **0.3875** |

## 关键发现 1：同族混淆在真实数据上成立

基线前 8 名混淆对：

```
gemini-3.8-flash -> gemini-3.7-flash   50
gemini-3.7-flash -> gemini-3.8-flash   43
glm-5.3          -> claude-opus-5      37
gpt-5.6-sol      -> gpt-5.6-luna       35
gpt-5.5          -> kimi-k3            31
claude-fable-5   -> claude-opus-5      30
gemini-3.7-flash -> gemini-3.6-flash   29
gemini-3.6-flash -> gemini-3.7-flash   29
```

除 `gpt-5.5 -> kimi-k3` 一条外，全部是"同族兄弟"或"同价位强模型"。这支持"先分 lab、再分家族"的两级结构。

## 关键发现 2：差距确实在风格特征，门槛已达成

社区同类 CPU 基线 CV 0.3515 / LB 0.339，纯 n-gram 只有 0.3015。加上 48 维法证风格特征（标点指纹、Markdown 习惯、大小写/数字密度、句长方差、列表结构）后：

| C | OOF BA |
|---|---|
| 1 | 0.3152 |
| 2 | 0.3274 |
| 4 | 0.3367 |
| 8 | 0.3441 |
| **16** | **0.3478** |

+0.0463，基本追平社区 0.3515 的水平。**C 阶段门槛 0.33 已达成**（0.3478），8B 模型必须明显超过 0.348 才有存在意义。

## 关键发现 3：transductive 去重是真的，而且幅度远超预期

用训练集模拟（prompt_id 当未知）：

| 方案 | BA | Δ |
|---|---|---|
| naive argmax | 0.3015 | — |
| 重建组 + greedy 去重 | 0.3241 | +0.0226 |
| **重建组 + Hungarian 去重** | **0.3875** | **+0.0860** |
| 真实组 + greedy | 0.3330 | +0.0315 |
| 真实组 + Hungarian（上限） | 0.4038 | +0.1023 |

- 分组重建：`AgglomerativeClustering(n_clusters=150, metric="cosine", linkage="complete")`，pairwise **F1 0.914 / ARI 0.913**；阈值法（connected components）完全不行（最好 F1 0.801，且阈值取 0.25 以上就碎成孤立点）。
用更强的风格特征基线（0.3478）重跑，增益几乎没缩水：clustered+greedy 0.3641、**clustered+Hungarian 0.4316（+0.0838）**、oracle+Hungarian 0.4486（+0.1008）。说明这不是"弱模型不会预测某类"造成的假象。

- 被救回的类很分散，不是只救稀有类：

```
gemini-3.8-flash   0.253 -> 0.480  (+0.227)
claude-fable-5     0.114 -> 0.295  (+0.181)
gpt-5.6-luna       0.253 -> 0.420  (+0.167)
claude-opus-4-8    0.220 -> 0.367  (+0.147)
gpt-5.6-terra      0.173 -> 0.313  (+0.140)
gemini-3.6-flash   0.380 -> 0.500  (+0.120)
```

**仍要打折**：0.35 的基线离 0.62 还远，`Hungarian` 强行把 18 个标签各发一次的做法在高精度区间收益会单调下降（BA→1 时增益→0）。真实增量等 8B OOF 出来用同一个脚本重测。

## 两个必须在最终 notebook 里处理的不确定性

1. **测试集分组大小未知**。训练集 149/150 是 18 条一组，但评价页明说"每类条数不等"，说明测试集并非每组都 18 条。
   → 不能硬套 `linear_sum_assignment(18×18)`，要用**软约束**：组内重复标签的概率做惩罚（`p[c] -= β·count(c)`，β 用 OOF 调），或在 `min(len(group), 18)` 的窗口内做受限分配。
2. **测试集 prompt 数与 4,000 条的比例未知**。聚类的 `n_clusters` 只能估（≈ n/18）。若真实分组不规整，聚类质心数量要按相似度分布自适应选，而不是写死 150。

## 关键发现 4：不完整的组也能救（决定提交版用哪种约束）

测试集每组条数不固定，所以用训练集群做「随机抽 k 条、只在这 k 条上施加约束」的模拟：

| 每组条数 | 最优方法 | BA | 增量 |
|---|---|---|---|
| 18（完整） | Sinkhorn T=1 | 0.4296 | +0.0818 |
| 15 | Sinkhorn T=1 | 0.4017 | +0.0539 |
| 12 | Sinkhorn T=1 | 0.3759 | +0.0281 |
| 9 | Hungarian | 0.3591 | +0.0113 |

两点结论：

1. **硬分配不是最优**。完整组上 Hungarian 0.4250 反而低于 T=1 的 Sinkhorn 0.4296，残缺时退化更快（k=12：0.3749 vs 0.3759，k=9 两者接近）。
2. **T=1 在所有 k 下都最优或次优**，所以不需要为测试集另调超参。

已封装成 `scripts/constraint.py`（`rebuild_groups` + `apply_constraint` + `balance_prior`，单文件可直接粘进 notebook）。生产版（TF-IDF → SVD-128 → complete/cosine，Sinkhorn T=1）在 OOF 上自测 `0.3478 → 0.4434`（+0.0956），oracle 分组 0.4460，聚类损失 0.0026；训练集 2699 条的 pairwise F1 0.974（比直接 dense cosine 的 0.914 更高）。复现：`python scripts/sim_production.py --probs results/oof_style_fine.npz`。

## 下一步

1. 服务器通了之后：A 阶段 P100 探针（`notebooks/a_stage_probe.ipynb`）。
2. 给基线加风格特征，把 C 阶段抬到 ≥0.33，确认门槛口径。
3. 等 8B 的 OOF 出来，用同一份 `transductive_sim.py` 重测这个杠杆在 BA≈0.62 时的真实增量，再决定软约束的 β 和是否写进提交 notebook。

## 复现

```bash
python scripts/kaggle_fetch.py comp name-that-llm --all
python scripts/make_folds.py --train data/train.csv
python scripts/baseline_tfidf.py
python scripts/reconstruct_prompts.py --train data/train.csv
python scripts/transductive_sim.py
```

## 关键发现 5：约束策略的稳健性（组内条数不等时）

官方说明"测试集 per-model 条数不等、public/private 混合比例不同"，所以用
`scripts/constraint_unbalanced.py` 在 OOF 上模拟"组内标签构成不等于 18 类各一条"：

| 场景 | naive | Sinkhorn(均匀列目标) | Sinkhorn(预测先验列目标) | Hungarian |
|---|---|---|---|---|
| alpha=10（几乎均匀） | 0.3406 | 0.3735 | **0.3771** | 0.2829 |
| alpha=3 | 0.3410 | 0.3723 | **0.3810** | 0.2708 |
| alpha=1 + 20% 缺类 | 0.3362 | 0.3487 | **0.3666** | 0.2295 |
| alpha=0.5（严重不等） | 0.3417 | 0.3406 | **0.3619** | 0.2141 |

结论：
- **Hungarian（硬指派）绝不能用于提交**：组内条数一旦不等，掉 0.12–0.13。
- 均匀列目标的 Sinkhorn 在严重不等时增益归零（0.3406 vs naive 0.3417），但不会掉分。
- 先验列目标在四种场景下都最好，但**在训练集这种平衡组上反而更差**（0.4137 vs 0.4460，-0.032）。
  所以只有拿到"测试组严重不等"的证据时才切换到 `target="prior"`；默认用均匀列目标。

## 端到端验证（服务器，非 Kaggle）

`scripts/predict.py`（与提交 notebook 同一套代码）在 fold0 验证集 540 条上：
4-bit 加载 14s、推理 71s，**raw BA 0.4185 → 约束后 0.4556（+0.0370）**，重建 30 组、改动 106 条预测。
比训练内 eval 的 0.4259 略低，原因是 padding/bs 不同；**约束增益随模型变强而缩小**
（0.3478 时 +0.0956，0.4259 时 +0.037），不要拿旧增益外推。

训练侧对照（外部数据 9,000 条，5 倍样本）：
- ext0（fold0）epoch1 BA **0.4074**（clean0 同 epoch 只有 0.2759；但后者一步只训 135 步）
- ext1（fold1）epoch1 BA **0.4175**
第二个 epoch 结果待补。
