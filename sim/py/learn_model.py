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
