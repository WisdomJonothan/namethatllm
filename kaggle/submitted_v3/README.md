# v3 版本的 Output 留档

| 文件 | 大小 | 说明 |
|---|---|---|
| `submission.parquet` | 1.7 KB | **本地产出，只有 3 行** |
| `v1_smoke.log` | 189 KB | 那次运行的日志 |

## 重要说明

这个 `submission.parquet` **不是被评分的那个文件**。

交互模式下 `test.csv` 只有 3 行，notebook 检测到「行数 < 50 且非 rerun」就走 `FAST_COMMIT` 分支，只对这 3 行做预测并写出 parquet —— 它的作用是验证「挂载 + 加载 + 写盘」链路通不通。

真正被评分的预测由 Kaggle 在**隐藏测试集**上重跑整个 notebook 时现场产生，不对外提供，只在排行榜上留下一个分数（public 0.716）。

所以这里留档的价值是：证明端到端链路可跑（`FAST_COMMIT True`、471.8s、372.98 kB 输出），以及那份 smoke 日志可供排查。
