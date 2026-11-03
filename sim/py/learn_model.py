"""学習したモデル(JSONの木)で勝率を計算する。numpy なしで動く(ブラウザ用)"""
import json, math


def load(path):
    m = json.load(open(path))

    def predict(X):
        out = []
        trees = m["trees"]
        for x in X:
            s = m["base"]
            for t in trees:
                f, th, l, r, v, leaf = t["f"], t["t"], t["l"], t["r"], t["v"], t["leaf"]
                i = 0
                while not leaf[i]:
                    i = l[i] if x[f[i]] <= th[i] else r[i]
                s += v[i]
            out.append(1 / (1 + math.exp(-s)))
        return out
    return predict


def load_np(path):
    """numpy版(探索用に速い):全部の木を配列にして、候補をまとめて計算する"""
    import numpy as np
    m = json.load(open(path))
    T = len(m["trees"]); W = max(len(t["f"]) for t in m["trees"])
    F = np.zeros((T, W), np.int32); TH = np.zeros((T, W)); L = np.zeros((T, W), np.int32); R = np.zeros((T, W), np.int32)
    V = np.zeros((T, W)); LEAF = np.ones((T, W), bool)
    for i, t in enumerate(m["trees"]):
        n = len(t["f"])
        F[i, :n] = t["f"]; TH[i, :n] = t["t"]; L[i, :n] = t["l"]; R[i, :n] = t["r"]; V[i, :n] = t["v"]; LEAF[i, :n] = np.array(t["leaf"], bool)
    base = m["base"]; tid = np.arange(T)

    def predict(X):
        X = np.asarray(X, dtype=np.float64)
        k = X.shape[0]
        node = np.zeros((k, T), np.int32)
        rows = np.arange(k)[:, None]
        for _ in range(64):
            leaf = LEAF[tid, node]
            if leaf.all():
                break
            f = F[tid, node]
            go_left = X[rows, f] <= TH[tid, node]
            nxt = np.where(go_left, L[tid, node], R[tid, node])
            node = np.where(leaf, node, nxt)
        s = base + V[tid, node].sum(axis=1)
        return (1 / (1 + np.exp(-s))).tolist()
    return predict


def load_fast(path):
    """木をそのまま if 文のPythonコードに書き換えてから実行する(探索用。素直に辿るより数倍速い)"""
    m = json.load(open(path))
    lines = ["def _score(x):", f"    s = {m['base']!r}"]

    def emit(t, i, ind):
        if t["leaf"][i]:
            lines.append(" " * ind + f"s += {t['v'][i]!r}")
            return
        lines.append(" " * ind + f"if x[{t['f'][i]}] <= {t['t'][i]!r}:")
        emit(t, t["l"][i], ind + 4)
        lines.append(" " * ind + "else:")
        emit(t, t["r"][i], ind + 4)
    for t in m["trees"]:
        emit(t, 0, 4)
    lines.append("    return s")
    ns = {}
    exec(compile("\n".join(lines), path + "#fast", "exec"), ns)
    sc = ns["_score"]

    def predict(X):
        return [1 / (1 + math.exp(-sc(x))) for x in X]
    return predict
