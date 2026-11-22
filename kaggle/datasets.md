# 数据集与模型清单

## 官方竞赛数据

| 文件 | 行数 | 说明 |
|---|---|---|
| `train.csv` | 2,699 | `id, prompt_id, text, model`，150 个 prompt × 18 模型 |
| `test.csv` | 3 | 交互模式只有 3 行；实际评测用隐藏集（约 4,000 条） |
| `sample_submission.parquet` | — | 输出格式 |

## 自建

| 名称 | 规模 | 说明 |
|---|---|---|
| `jonothanwu/ntl-eval-v2` | 3,350 行 / 550 prompt / 18 类 | 外部语料两道去重后重建，用于诊断跑批 |
| `jonothanwu/ntl-eval-independent` | 5,400 行 | **已被 open pool 污染，弃用**（comp_stack 训练时见过这些文本） |

外部语料来源：
- `olegzholobov/name-that-llm-response-corpus` → `external_short_wide.parquet`
- `willrice/name-that-llm-openrouter-harvest` → `passages.csv`
- `crtproduction/name-that-llm-open-pool` → `ntl_open_pool.parquet`（**被 comp_stack 整份训练，不能用于验证**）

## 诊断内核挂载的模型/数据集（26 个）

六读者基座 + LoRA 适配器（Qwen3-14B / Qwen3-8B / Llama-3.1-8B / Qwen2.5-14B 等）、GLM-5.3 IQ3_XXS 分层、GSQ 27B、style heads、SVC 预训练池、DeBERTa 两阶段模型、bitsandbytes wheel 等。完整清单见 `repro/areebbb_pull_raw.json` 与内核 Input 面板。
