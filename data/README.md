# 数据说明

| 文件 | 来源 | 许可 |
|---|---|---|
| `train.csv` | Kaggle 竞赛官方数据（Kaggle / Google LLC） | CC BY-SA 4.0 |
| `test.csv` | 同上（交互模式 3 行） | CC BY-SA 4.0 |
| `sample_submission.parquet` | 同上 | CC BY-SA 4.0 |
| `folds.csv` | 本项目生成（按 prompt 分组 5 折） | MIT |
| `eval_v2.csv` | 本项目从公开数据集派生 | 随上游 |

## eval_v2.csv 的构造

1. 取 `olegzholobov/name-that-llm-response-corpus` 的 `external_short_wide.parquet` 与
   `willrice/name-that-llm-openrouter-harvest` 的 `passages.csv`；
2. 只保留 40–250 词的段落；
3. **去重一**：文本规范化后 sha1 命中 open pool 或官方 train 即删除（删掉 9,758 行）；
4. **去重二**：prompt 与官方 train 有重叠则整组删除。

产物：3,350 行 / 550 个 prompt / 18 类齐全（`gemini-3.8-flash` 仅 42 条）。

## 重要限制

- open pool（`crtproduction/name-that-llm-open-pool`，11.5 万条 / 115MB）**没有放在本仓库**：它是公开数据集，可按名称下载；且它被 0.716 方案的 `comp_stack` 整份训练过，不能当验证集。
- `eval_v2` 与竞赛数据体裁不同（更长、Markdown 更多），**只能当回归哨兵，不能当 LB 代理**。详见 `docs/EXPERIMENTS.md` E8。
