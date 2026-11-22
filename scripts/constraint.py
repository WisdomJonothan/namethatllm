"""提交期使用的"组内去重"模块（自包含，单文件，可整段粘进 Kaggle notebook）。

核心思想：测试集里同一 prompt 会出现多个不同模型的回答。同一组内部，18 个模型
各答一次（训练集 149/150 组是完整的），所以组内标签天然近似一个排列。模型对
"同族兄弟"（gemini-3.8 vs 3.7、opus-4-8 vs opus-5）常常给出相同的预测，用
"组内不许重复"这个约束可以把它们掰开。

实测（训练集 OOF，基线 BA 0.3478）：
    完整组 18 条  → 0.4296 (+0.0818)
    随机抽 15 条  → 0.4017 (+0.0539)
    随机抽 12 条  → 0.3759 (+0.0281)
    随机抽 9 条   → 0.3577 (+0.0099)
温度 T=1 的 Sinkhorn 在所有残缺程度下都是最优或次优，所以只用一个超参。

用法：
    from constraint import rebuild_groups, apply_constraint
    groups = rebuild_groups(texts)          # 返回 list[np.ndarray]
    probs2 = apply_constraint(probs, groups)  # 返回同形状概率
"""
from __future__ import annotations

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

EPS = 1e-12


def rebuild_groups(texts, expected_group_size: int = 18, n_clusters: int | None = None,
                   max_features: int = 100_000, min_cluster_size: int = 3,
                   n_components: int = 128) -> list[np.ndarray]:
    """按写作风格把段落聚回 prompt 组（TF-IDF → SVD-128 → complete-linkage 余弦）。

    训练集 2699 条上 pairwise F1 0.974 / ARI 0.974（比直接对稀疏矩阵做 dense
    cosine 的 0.914 更好，因为 SVD 去噪）；4000 条约 1 分钟，内存 <100MB。
    """
    texts = [t if isinstance(t, str) and t.strip() else " " for t in texts]
    n = len(texts)
    if n_clusters is None:
        n_clusters = max(2, int(round(n / expected_group_size)))
    n_clusters = min(n_clusters, max(2, n // min_cluster_size))
    if n == 0:
        return []
    if n <= min_cluster_size:
        return [np.arange(n)]

    tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1,
                         max_features=max_features, sublinear_tf=True)
    X = tf.fit_transform(texts)
    comp = min(n_components, max(2, min(X.shape) - 1))
    Z = normalize(TruncatedSVD(n_components=comp, random_state=0).fit_transform(X))
    cl = AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="complete")
    labels = cl.fit_predict(Z)
    groups = [np.where(labels == g)[0] for g in range(n_clusters)]
    kept = [g for g in groups if len(g) >= min_cluster_size]
    # 小样本会被 min_cluster_size 全部过滤掉，退化为单组（防 min() 空列表崩溃）
    return kept if kept else [np.arange(n)]


def apply_constraint(probs: np.ndarray, groups: list[np.ndarray],
                     temperature: float = 1.0, iters: int = 30,
                     strength: float = 1.0,
                     target: str = "prior") -> np.ndarray:
    """在每组内部做 Sinkhorn 投影：行和 1，列和 = len(group)/n_labels。

    strength=0 时原样返回（方便做消融）；1 为完全投影。
    返回归一化后的概率，可直接 argmax。
    """
    probs = np.asarray(probs, dtype=np.float64)
    out = probs.copy()
    n_labels = probs.shape[1]
    logp = np.log(np.clip(probs, EPS, None))

    for g in groups:
        if len(g) < 2:
            continue
        sub = logp[g] / max(temperature, EPS)
        sub = sub - sub.max(axis=1, keepdims=True)
        P = np.exp(sub)
        P /= P.sum(axis=1, keepdims=True)
        if target == "prior":
            # 官方说明测试集 per-model 条数不等：列目标用"组内预测先验"与均匀的折中，
            # 这样在平衡/不平衡两种情况下都不吃亏（见 constraint_unbalanced.py）。
            w = 0.5 * P.mean(axis=0) + 0.5 / n_labels
            cap = len(g) * w / w.sum()
        else:
            cap = len(g) / n_labels
        for _ in range(iters):
            P /= (P.sum(axis=0, keepdims=True) / cap + EPS)
            P /= (P.sum(axis=1, keepdims=True) + EPS)
        P /= P.sum(axis=1, keepdims=True)
        if strength >= 1.0:
            out[g] = P
        else:
            mixed = (1.0 - strength) * probs[g] + strength * P
            out[g] = mixed / mixed.sum(axis=1, keepdims=True)
    return out


def balance_prior(probs: np.ndarray, prior: np.ndarray, lam: float = 0.0,
                  clip: float = 0.4) -> np.ndarray:
    """软先验校准：z = log p + λ·clip(log(π/ṁ), -clip, clip)。λ=0 时原样返回。"""
    if lam == 0.0:
        return probs
    m = probs.mean(axis=0)
    adj = lam * np.clip(np.log(np.maximum(prior, EPS) / np.maximum(m, EPS)), -clip, clip)
    z = np.log(np.clip(probs, EPS, None)) + adj
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


if __name__ == "__main__":
    # 自测：在训练集 OOF 上复现 0.4296
    import sys
    from pathlib import Path

    import pandas as pd
    from sklearn.metrics import balanced_accuracy_score

    probs_path = sys.argv[1] if len(sys.argv) > 1 else "results/oof_style_fine.npz"
    d = np.load(probs_path, allow_pickle=True)
    probs, y = d["probs"], d["y"]
    df = pd.read_csv("data/train.csv")
    base = balanced_accuracy_score(y, probs.argmax(1))
    groups = rebuild_groups(df.text.fillna(" ").astype(str).tolist())
    adj = apply_constraint(probs, groups, temperature=1.0)
    ba = balanced_accuracy_score(y, adj.argmax(1))
    print(f"groups={len(groups)}  naive={base:.4f}  constrained={ba:.4f}  ({ba - base:+.4f})")
    Path("results").mkdir(exist_ok=True)
