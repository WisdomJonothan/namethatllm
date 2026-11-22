%%writefile pngy_residual.py
"""Mass-preserving sibling log-ratio refinement; operates on post-blend logits."""
import re
import numpy as np
from scipy.special import logsumexp, softmax
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MARKERS = (",", ".", ";", ":", "!", "?", "—", "–", "’", "“", "”",
           "'", '"', "(", ")", "[", "]", "*", "", "**", " - ", "  ")
FUNCTIONS = ("the", "and", "but", "which", "however", "therefore", "moreover", "because", "perhaps",
             "might", "would", "could", "should", "also", "although", "overall", "finally", "we", "you", "i")
WORDS = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

def features(texts):
    rows = []
    for value in texts:
        if not isinstance(value, str):
            value = str(value)
        counts = np.array([value.count(m) for m in MARKERS], dtype=np.float64)
        tokens = WORDS.findall(value)
        n, chars = max(1, len(tokens)), max(1, len(value))
        lengths = np.array([len(t) for t in tokens] or [0], dtype=float)
        lower = [t.lower() for t in tokens]
        shape = [np.log1p(len(tokens)), np.log1p(len(value)), chars/n,
                 len(set(lower))/n, lengths.mean(), lengths.std(),
                 sum(c.isupper() for c in value)/chars, sum(c.isdigit() for c in value)/chars,
                 sum(ord(c) > 127 for c in value)/chars, sum(c.isspace() for c in value)/chars]
        rows.append(np.r_[shape, counts/n, [lower.count(w)/n for w in FUNCTIONS]])
    return np.asarray(rows, dtype=np.float64)

def family_columns(labels):
    return [np.array([i for i, label in enumerate(labels) if label.startswith(f + "-")])
            for f in ("claude", "gpt", "gemini", "glm")]

def refine(log_probability, evidence, families, strength=0.10):
    z, style = np.asarray(log_probability, dtype=float), np.asarray(evidence, dtype=float)
    result = z.copy()
    for columns in families:
        if len(columns) < 2:
            continue
        p = softmax(z[:, columns], axis=1)
        q = np.maximum(style[:, columns], 1e-15)
        q /= q.sum(1, keepdims=True)
        top = np.partition(p, -2, axis=1)[:, -2:]
        gate = np.clip((0.65 - (top[:, 1] - top[:, 0])) / 0.65, 0, 1) * strength
        mixed = (1 - gate[:, None]) * p + gate[:, None] * q
        result[:, columns] = logsumexp(z[:, columns], axis=1, keepdims=True) + np.log(mixed)
    return result

class Residual:
    def __init__(self, labels):
        self.labels = list(labels)
        self.families = family_columns(labels)

    def fit(self, texts, target):
        x = features(texts)
        target = np.asarray(target)
        self.models = []
        for columns in self.families:
            if len(columns) < 2:
                continue
            selected = np.isin(target, np.asarray(self.labels)[columns])
            model = make_pipeline(StandardScaler(), LogisticRegression(
                C=0.03, max_iter=500, class_weight="balanced", random_state=166))
            model.fit(x[selected], target[selected])
            self.models.append((columns, model))
        return self

    def predict_log_probability(self, base, texts):
        x = features(texts)
        evidence = np.ones((len(x), len(self.labels)), dtype=float)
        for columns, model in self.models:
            p = model.predict_proba(x)
            for j, label in enumerate(model.classes_):
                evidence[:, self.labels.index(label)] = p[:, j]
        return refine(base, evidence, self.families)
