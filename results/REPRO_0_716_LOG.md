# Name That LLM — 公开方案 0.716 复现记录

任务：用作者发布的代码与权重，原样复现推理与提交，取得可作对照的真实 public LB 成绩。
固定参考：`areebbb/0-716-fingerprints-sibling-specialis`，scriptVersionId **372211652**，作者记录 **0.716**。

## 1. 版本与附件核对

| 项 | 值 | 依据 |
|---|---|---|
| 源码版本 | version 24（`currentVersionNumberNullable=24`） | `/api/v1/kernels/pull` 元数据 |
| 版本对应 | 页面 oembed 中 `scriptVersionId=372211652`，不带参数即该版 | 传 `?scriptVersionId=1` 返回 404，说明默认页=固定版 |
| 源码大小 | 1,011,759 字符，49 cells（25 code） | pull 结果 |
| competition 输入 | `name-that-llm` | pull metadata |
| dataset 输入 | 26 个（GLM-5.3 IQ3_XXS 分片 A/B、GLM-5.3-Flash IQ3_XXS、Qwen3-8B/9B、NTL Ours Qwen3-14B LoRA、NTL Spectral Style Heads、NTL SVC Prefit Pool、NTL Two-Stage Encoders、Qwen2.5-14B、Llama-3.1-8B-base、UltraBERT-380M 等） | pull metadata |
| kernel 输入 | 11 个，其中 1 个为 `deleted/140855160`（推送时剔除，故副本 10 个） | pull metadata |
| model 输入 | 9 个（Qwen 3 ×4、Qwen2.5-14b、llama-3.1-8b-base 等） | pull metadata |
| 环境 | `gcr.io/kaggle-images/python@sha256:0717e81990be591f8c5f98f033d29ab23e9ffde057e5b3a6bd4371f93969e962` | pull metadata |
| GPU / 网络 | `enableGpu=true`、`enableInternet=false` | pull metadata |

副本：`jonothanwu/ntl-repro-0-716-areebbb-v372211652`（private），源码字节未改，
仅改 slug / 标题 / 私有标志，并剔除 `deleted/140855160` 这一个失效 kernel 输入。

## 2. 代码路径（决定"短测试"含义）

- cell 9：`FAST_COMMIT = os.getenv('KAGGLE_IS_COMPETITION_RERUN') is None and len(test.csv) < 50`；
  `RUN_LIMIT_S = 7h`。
- cell 35：`if not FAST_COMMIT: run(comp_stack.py)`
- cell 37：`if FAST_COMMIT:` 写全 `gpt-5.5` 的 dummy `submission.parquet` + `core_votes` smoke
  （svc4 / tfidf / distort，只校验形状与有限值）；`else:` 才真正跑 `infer.py`。
- cell 39：断言 `submission` 行数、id 集合与 test.csv 一致，列 == `['id','model']`。

→ 交互运行与普通 commit 都走 FAST_COMMIT（**不加载权重、不算真实预测**）；
  只有竞赛评分重跑（`KAGGLE_IS_COMPETITION_RERUN` 存在、隐藏测试 >50 行）才会跑完整推理。

## 3. 运行记录

| 项 | 值 |
|---|---|
| Version 1（Save & Run All）提交时间 | 2026-11-22 01:42（本地） |
| Version 1 状态 | 长时间 `Starting...`（输入体量大：26 dataset + 9 model，需拉取/挂载数十 GB） |
| Version 2（Quick Save） | 7.5 s 跑完，`files=[]`，不产出 submission.parquet |
| 交互会话 | Run All 8 分钟跑完，`/kaggle/working` ~1 MiB；离开编辑页会销毁会话，输出丢失 |

## 4. 待填（出分后）

| 源码版本 | 附件版本 | 必要改动 | 提交 ID | 实际 public 分 | 运行时间 | GLM 覆盖 | 是否回退 |
|---|---|---|---|---|---|---|---|
| v24 / 372211652 | 26 ds + 10 kn + 9 md | 剔除 deleted kernel；slug 改名 | 待填 | 待填 | 待填 | 待填 | 待填 |
