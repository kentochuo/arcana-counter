"""自己学習AI(2026/11/03):盤面と候補から「この手を選んだら何%勝てるか」を学んで、ガードを選ぶ

1. データ作り(mode="explore"):ふだんのAI同士の対戦で、ガードの場面ごとに一定の確率で候補をランダムに選び、
   その時の盤面・候補の特徴と、最後に勝ったかどうかを記録する。
2. 学習:記録から勝率を当てるモデルを作る(learn_train.py)。
3. 使う(mode="model"):ガードの場面で、候補ごとにモデルで勝率を見積もり、一番高い手を選ぶ。
"""
import random
from pengine import PersonaPlayer, PGame

INF = 10 ** 9
DECKS = ["P3", "P3P", "P4", "P5"]


def guard_options(p, atk, items, need):
    """候補:ノーガード / ちょうど止める / トリガー1枚分・2枚分多め / 守護者 / いつもの判断"""
    need0 = atk.power - p.vg_power() + 1
    out = [("take", ())]
    for ex in ((0, 10000, 20000) if atk.drives else (0,)):
        cost, gs = p.knap(items, need0 + ex)
        if cost < INF:
            out.append(("guard", tuple(sorted(gs))))
    if p.sentinel_cost() < INF:
        out.append(("sentinel", ()))
    d = PersonaPlayer.base_choose_guard(p, atk, items, need)
    dd = (d[0], tuple(sorted(d[1] or [])))
    if dd[0] == "take":
        dd = ("take", ())
    if dd[0] == "sentinel":
        dd = ("sentinel", ())
    out.append(dd)
    uniq = []
    for o in out:
        if o not in uniq:
            uniq.append(o)
    return uniq, dd


def state_features(p, atk):
    o = p.opp
    h = p.hand
    _n = lambda x: x if isinstance(x, (int, float)) else len(x)   # 環境デッキ(旧エンジン)の相手は枚数だけ持つことがある
    tr = lambda t: sum(1 for c in h if c.kind == "trigger" and c.trig == t)
    units = p.units()
    rem = getattr(p, "cur_rem", 1)
    need0 = atk.power - p.vg_power() + 1
    f = [1.0 if p.key == k else 0.0 for k in DECKS] + [1.0 if getattr(o, "key", "") == k else 0.0 for k in DECKS]
    f += [o.turn, p.damage, o.damage, p.cb_avail(), len(p.soul), p.energy, getattr(o, "energy", 0), _n(getattr(o, "soul", [])),
          len(h), tr("crit"), tr("draw"), tr("front"), tr("heal"), tr("over"),
          sum(1 for c in h if c.kind == "sentinel"), sum(1 for c in h if p.is_g3(c)),
          sum(1 for c in h if c.kind == "unit" and c.grade == 1), sum(1 for c in h if c.kind == "unit" and c.grade == 2 and not p.is_g3(c)),
          len(units), sum(1 for k in ("FL", "FR") if p.circles[k]), sum(1 for u in units if u.gauge),
          p.v_grade, o.v_grade, len(p.vgauge), len(p.deck), _n(o.hand), _n(getattr(o, "deck", [])),
          sum(1 for k in ("FL", "FR") if (o.circles.get(k) if isinstance(getattr(o, "circles", None), dict) else 0)), sum(p.arcana.values()), sum(1 for v in p.arcana.values() if v >= 3),
          need0 / 10000, atk.crit, atk.drives, 1.0 if atk.is_v else 0.0, rem]
    return f


def option_features(p, atk, items, opt):
    need0 = atk.power - p.vg_power() + 1
    kind = opt[0]
    if kind == "guard":
        its = [items[i] for i in opt[1]]
        hand_used = sum(1 for it in its if it[0] == "H")
        ic = sum(1 for it in its if it[0] == "I")
        shield = sum(it[2] for it in its)
        trig = sum(1 for it in its if it[0] == "H" and it[1].kind == "trigger")
        unit_used = hand_used - trig
        over = sum(1 for it in its if it[0] == "H" and it[1].kind == "trigger" and it[1].trig == "over")
        margin = (shield - need0) / 10000
        hit = 0.0
    elif kind == "sentinel":
        s = next(c for c in p.hand if c.kind == "sentinel")
        free = p.arcana[p.arc(s)] >= 2
        hand_used, ic, shield, trig, unit_used, over = (1 if free else 2), 0, 0, 0, 0, 0
        margin, hit = 5.0, 0.0
    else:
        hand_used = ic = shield = trig = unit_used = over = 0
        margin = -need0 / 10000
        hit = 1.0
    return [1.0 if kind == "take" else 0.0, 1.0 if kind == "guard" else 0.0, 1.0 if kind == "sentinel" else 0.0,
            hand_used, ic, shield / 10000, trig, unit_used, over, margin, hit,
            len(p.hand) - hand_used, p.damage + (atk.crit if hit else 0)]




class LearnPlayer(PersonaPlayer):
    MODE = "default"        # "explore":データ作り / "model":学習したモデルで選ぶ
    EXPLORE_P = 0.25
    MIN_STAKE = 2           # 受けた後のダメージがこの点数以上になる場面だけ(序盤はふつうの判断)
    MODEL = None            # predict(list of feature lists) -> list of 勝率

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.records = []
        self.xrng = random.Random()

    def choose_guard(self, atk, items, need):
        if self.MODE == "default" or self.damage + atk.crit < self.MIN_STAKE:
            return super().choose_guard(atk, items, need)
        opts, default = guard_options(self, atk, items, need)
        if len(opts) <= 1:
            return default[0], list(default[1])
        sf = state_features(self, atk)
        if self.MODE == "explore":
            if self.xrng.random() < self.EXPLORE_P:
                pick = self.xrng.choice(opts)
                self.records.append(sf + option_features(self, atk, items, pick))
            elif self.MODEL is not None:            # 2周目以降:探らない場面は学習済みモデルの判断で進める
                X = [sf + option_features(self, atk, items, o) for o in opts]
                pr = self.MODEL(X)
                pick = opts[max(range(len(opts)), key=lambda i: pr[i])]
            else:
                pick = default
            return pick[0], list(pick[1])
        X = [sf + option_features(self, atk, items, o) for o in opts]
        pr = self.MODEL(X)
        bi = max(range(len(opts)), key=lambda i: pr[i] + (self.DEFAULT_BONUS if opts[i] == default else 0))
        pick = opts[bi]
        return pick[0], list(pick[1])

    DEFAULT_BONUS = 0.0


class LearnGame(PGame):
    def make(self, spec, log):
        if spec[0] == "learn":
            _, key, recipe, attrs = spec
            p = LearnPlayer(key, recipe, self.rng, log)
            for k, v in attrs.items():
                setattr(p, k, v)
            return p
        return super().make(spec, log)
