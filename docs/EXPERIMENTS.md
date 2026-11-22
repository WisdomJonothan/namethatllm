# 实验结论汇总

按"假设 → 做法 → 结果 → 结论"记录，避免重复踩坑。所有数字都可追溯：脚本在 `scripts/`，输出在 `results/`。

---

## E1 TF-IDF 基线

| 项 | 值 |
|---|---|
| 做法 | char_wb 3–5 + word 1–2 TF-IDF，LogisticRegression(C=2)，5 折按 prompt 分组 |
| OOF | **0.3015**（各折 0.3019 / 0.3020 / 0.2963 / 0.3037 / 0.3037，SD 0.0027） |
| 耗时 | 79 秒，约 97K 特征 |

最低召回：`glm-5.3-flash` 0.107、`claude-fable-5` 0.107、`gpt-5.6-sol` 0.173。
最强混淆：`gemini-3.8-flash ↔ 3.7-flash`（50/43）、`glm-5.3 → claude-opus-5`（37）、`gpt-5.6-sol → 5.6-luna`（35）、`gpt-5.5 → kimi-k3`（31）。

脚本：`baseline_tfidf.py`；输出：`results/baseline_tfidf.json`、`results/oof_tfidf.npz`。

## E2 加手工风格特征

48 维排版/结构特征（em dash、弯引号、分号、Markdown 标记、大小写密度、句长方差、TTR、空行比例…），StandardScaler 后与 TF-IDF 拼接。

| C | OOF BA |
|---|---|
| 1 | 0.3152 |
| 2 | 0.3274 |
| 4 | 0.3367 |
| 8 | 0.3441 |
| **16** | **0.3478** |

比纯 n-gram +0.046，追平社区公开的 0.3515。最差类仍是 `claude-fable-5` 0.114、`glm-5.3` 0.16。

脚本：`baseline_style.py`；输出：`results/baseline_style.json`、`results/oof_style_fine.npz`。

## E3 prompt 聚类重建（transductive）

假设：测试集里同 prompt 的 18 段共享主题，可以把它们聚回组，再在组内做分配。

| 方法 | pairwise F1 | ARI |
|---|---|---|
| 阈值法（connected components） | 0.801（最好 thr=0.20，632 簇） | — |
| 层次聚类 average linkage | 0.810 | — |
| **层次聚类 complete linkage，n=150** | **0.914** | **0.913** |

在真标签上模拟组内重分配（中位组大小 18）：

| 配置 | BA |
|---|---|
| 直接 argmax（基线） | 0.3015 |
| 重建簇 + greedy | 0.3241（+0.0226） |
| **重建簇 + Hungarian** | **0.3875（+0.0860）** |
| 真簇 + greedy | 0.3330 |
| 真簇 + Hungarian | 0.4038（+0.1023，上限） |

用风格特征 OOF 重做：重建簇 + Hungarian **0.4316（+0.0838）**，上限 0.4486。救回 `gemini-3.8-flash` 0.253→0.480、`claude-fable-5` 0.114→0.295、`gpt-5.6-luna` 0.253→0.420。

**但公开实测（别人做的）是 −0.009~−0.017** —— 说明这个增益来自"我们知道有 18 类配对结构"这一先验，而隐藏测试每组只有 1–3 段，这个前提不成立。**结论：不做。**

脚本：`reconstruct_prompts.py`、`transductive_sim.py`。

> 教训：我一度给真类概率加常数做敏感性 sweep，那是标签泄漏，数字从 0.47 虚高到 0.71，已删。

## E4 A3 零方差伪影规则 → 否定

假设：某些模型有独一无二的排版怪癖，可以写成硬规则直接排除。

做法：47 个特征在官方 train 2,699 行上按模型统计。

结果：**没有任何一条**满足 `p_own ≥ 0.50 且其他 17 个 ≤ 0.10`（最强 gap 仅 0.04，`colon_space → kimi-k3`）；反向的"某模型从不使用"硬 veto **也一条都没有**。纯排版特征训 LR 在 eval_v2 上 BA 仅 0.1716。

**结论：硬规则路线放弃。** 公开那几条手写规则要么是软调参，要么作用在更细粒度的模式上。

脚本：`artifact_rules.py`；输出：`results/artifact_rules.json`、`results/artifact_veto.csv`。

## E5 A1 prompt 固定效应 → 负结果

假设：把同组 18 行的特征减去组均值，抹掉主题、只留风格。

结果（eval_v2 上，C=0.5/2/8）：

| 处理 | BA |
|---|---|
| 原始特征 | 0.1617 / 0.1636 / 0.1556 |
| 组内中心化 | 0.1429 / 0.1436 / 0.1424 |
| Δ | **−0.0188 / −0.0200 / −0.0132** |

accuracy 反而从 0.144 升到 0.173 —— 被推向多数类，正是 BA 不想要的。

**结论：不做。**

脚本：`prompt_fe_probe.py`。

## E6 A4 BA 最优决策层 → 小正信号

理论：balanced accuracy 的 Bayes 规则是 `argmax p(x|y) = argmax p(y|x) / p_train(y)`，而 Sinkhorn 是把预测边缘拉向测试先验，两者目标不同。

弱模型实测：

| 做法 | BA | Δ |
|---|---|---|
| 直接 argmax | 0.1716 | — |
| 纯先验校正 | 0.1716 | **+0.0000** |
| 在 train 上拟合 18 个类别偏置 | 0.1772 | **+0.0056** |
| 在测试集上拟合（交叉拟合，不可部署） | 0.2159 | +0.0443 |

**结论：值得在真集成上试，零 GPU 成本；但先验校正本身没用，需要拟合偏置。**

脚本：`logit_adjust.py`。

## E7 集成在独立集上的表现（诊断跑批）

配置：六读者 + GSQ 27B + native，comp 通道因 bug 缺失，测试集换成 eval_v2（3,350 行 / 550 prompt / 18 类齐）。

| 指标 | 值 |
|---|---|
| balanced accuracy | **0.4783** |
| accuracy | 0.4904 |
| 最终 logits 的 argmax BA | 0.5017 |

逐类召回：

| 类 | recall | 类 | recall |
|---|---|---|---|
| claude-opus-5 | 0.959 | qwen3.8-max | 0.348 |
| claude-opus-4-6 | 0.907 | gpt-5.6-terra | 0.325 |
| gpt-6-astra | 0.829 | glm-5.3-flash | 0.272 |
| gpt-5.6-luna | 0.798 | gpt-5.6-sol | 0.254 |
| claude-sonnet-5 | 0.727 | claude-opus-4-8 | 0.498 |
| gpt-5.5 | 0.597 | gemini-3.8-flash | **0.048** |
| gemini-3.1-pro-preview | 0.573 | kimi-k3 | **0.038** |
| glm-5.3 | 0.485 | claude-fable-5 | **0.020** |

混淆去向（真 → 预测）：`gemini-3.7-flash → 3.8-flash` 110、`gpt-5.6-sol → luna` 110、`kimi-k3 → glm-5.3` 95、`gpt-5.6-terra → luna` 94、`claude-fable-5 → glm-5.3` 67。

**结论：域外塌缩是真实弱点，三个类基本为 0；这也是 A4/A6 类决策层校正最该救的地方。**

## E8 独立集的分布差异（为什么 0.478 ≠ 0.716）

| | 官方 train | eval_v2 |
|---|---|---|
| 词数中位数 | 101 | 134（塌缩类到 193） |
| 含 `**粗体**` | 0% | 56–69% |
| 含括号 | 1–5% | 32–48% |

同一 TF-IDF 模型：train 的 prompt 分组 OOF **0.348**，eval_v2 **0.146**，eval_v2 内部 CV **0.663**。

按体裁筛选子集（≤160 词、无 Markdown）后 BA 从 0.164 降到 0.139 —— 说明差异不只是表层格式。

**结论：eval_v2 只能当"回归哨兵"，不能当 LB 代理。任何改动必须在官方 train 的 prompt 分组 OOF 上符号一致才算证据。**

## E9 最后一层后处理是域外负贡献

最终 `final-logits.npz` 的 argmax 在 eval_v2 上 0.5017，而写进 `submission` 的预测 0.4783，两者只有 95% 行一致 —— 约 5% 的行被最后一步改掉了，净损 0.023。

**结论：这套后处理对竞赛分布过拟合。域内可能是 +0.012，域外是负的。**

## E10 已证伪的公开方向（调研得来，非本地实测）

| 方向 | 结果 |
|---|---|
| 测试期 prompt 聚类 + Hungarian | −0.009 ~ −0.017 |
| 更强骨干 / 加成员 | ±0.004 |
| 额外 33k 公开行训练 | OOF 反降 |
| GLM-4-9B 参考似然 | −0.002 |
| 同码重跑噪声 | **±0.01**（六次 0.682–0.694） |
