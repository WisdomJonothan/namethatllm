import numpy as np
from sklearn.linear_model import LogisticRegression
d = np.load("results/emb_train.npz", allow_pickle=True)
X = d["X"].astype(np.float64); y_raw = d["y"]
labels = sorted(set(y_raw.tolist())); L = {l: i for i, l in enumerate(labels)}
y = np.array([L[v] for v in y_raw])
print("X", X.shape, "norm mean", float(np.linalg.norm(X, axis=1).mean()))
# in-sample linear head
clf = LogisticRegression(C=1.0, max_iter=2000).fit(X, y)
print("in-sample acc %.4f" % float((clf.predict(X) == y).mean()))
# nearest centroid (cosine) in-sample
cent = np.stack([X[y == c].mean(0) for c in range(len(labels))])
cent /= np.linalg.norm(cent, axis=1, keepdims=True)
sim = X @ cent.T
print("centroid acc %.4f" % float((sim.argmax(1) == y).mean()))
# in-sample top2 coverage
print("centroid top2 %.4f" % float(np.isin(y, np.argsort(-sim, 1)[:, :2]).mean()))
# TF-IDF reference on the same rows
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import make_pipeline
from sklearn.metrics import confusion_matrix
df = pd.read_csv("data/train.csv")
tf = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), min_df=2, sublinear_tf=True,
                                   max_features=120000), LogisticRegression(C=4, max_iter=2000))
tf.fit(df.text.fillna(""), y)
p = tf.predict(df.text.fillna(""))
rec = [float((p[y==c]==c).mean()) for c in range(len(labels))]
print("tfidf in-sample BA %.4f acc %.4f" % (np.mean(rec), float((p==y).mean())))
