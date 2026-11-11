"""自己学習AI その2(2026/11/08):メインフェイズの判断を学ぶ
- 払うかどうか選べるコスト(杏のドロー、神木、小田桐、東郷、純黒、G3の起動、エリザベスをソウルへ など)
- 山札から何を手札に加えるか(モルガナ、千枝)
やり方はガードの自己学習(learn_ai.py)と同じ:
1. データ作り:判断の場面で一定の確率でランダムに選び、その時の盤面・選んだ手の特徴と、最後に勝ったかを記録
2. 学習:learn_train.py で勝率を当てるモデルを作る
3. 使う:環境変数 LEARN_MAIN にモデルを指定すると、pengine の decide / choose_card がモデルで選ぶ
"""
import random
from pengine import PersonaPlayer, PGame

DECKS = ["P3", "P3P", "P4", "P5"]
KINDS = ["cb_draw", "cb_retire", "cb_recall", "cb_discard_opp", "cb_self_5000", "maiko", "eb1_lock", "eb1_zero", "eb3_retire",
         "eb2_cc1", "cb1_ec3", "togo", "sae", "morgana", "chie", "act1", "regalis", "drop_call_eb", "drop_call_cb", "eliz_soul", "bebe_crit", "boost_soul", "end_soul", "end_soul_ec", "skill_use"]


def state_features(p):
    o = p.opp
    h = p.hand
    _n = lambda x: x if isinstance(x, (int, float)) else len(x)
    tr = lambda t: sum(1 for c in h if c.kind == "trigger" and c.trig == t)
    units = p.units()
    oc = getattr(o, "circles", None)
    f = [1.0 if p.key == k else 0.0 for k in DECKS] + [1.0 if getattr(o, "key", "") == k else 0.0 for k in DECKS]
    f += [p.turn, 1.0 if getattr(p, "in_turn", True) else 0.0, 1.0 if p.persona_this_turn else 0.0,
          p.damage, o.damage, p.cb_avail(), len(p.soul), p.energy, getattr(o, "energy", 0), _n(getattr(o, "soul", [])),
          len(h), tr("crit"), tr("draw"), tr("front"), tr("heal"), tr("over"),
          sum(1 for c in h if c.kind == "sentinel"), sum(1 for c in h if p.is_g3(c)),
          sum(1 for c in h if c.kind == "unit" and c.grade == 1), sum(1 for c in h if c.kind == "unit" and c.grade == 2 and not p.is_g3(c)),
          len(units), sum(1 for k in ("FL", "FR") if p.circles[k]), sum(1 for u in units if u.gauge),
          p.v_grade, o.v_grade, len(p.vgauge), len(p.deck), _n(o.hand), _n(getattr(o, "deck", [])),
          sum(1 for k in ("FL", "FR") if (oc.get(k) if isinstance(oc, dict) else 0)),
          sum(1 for k in ("BL", "BC", "BR") if (oc.get(k) if isinstance(oc, dict) else 0)),
          sum(p.arcana.values()), sum(1 for v in p.arcana.values() if v >= 3),
          1.0 if getattr(p, "act1_turn", -1) == p.turn else 0.0,
          sum(1 for c in p.drop if c.kind == "unit" and c.grade in (1, 2)),
          1.0 if getattr(p, "regalis_used", False) else 0.0]
    return f


def option_features(p, kind, card, yes, extra=None):
    f = [1.0 if kind == k else 0.0 for k in KINDS] + [1.0 if yes else 0.0]
    if card is not None:
        f += [card.grade, 1.0 if p.is_g3(card) else 0.0, 1.0 if card.kind == "sentinel" else 0.0, 1.0 if card.kind == "trigger" else 0.0,
              1.0 if card.persona else 0.0, p.unit_score(card) / 10000 if card.kind == "unit" else 0.0,
              (p.arcana[p.arc(card)] if p.arc(card) else 0),
              sum(1 for c in p.hand if c.name == card.name), sum(1 for u in p.units() if u.card.name == card.name)]
    else:
        f += [0.0] * 9
    e = list(extra or ())
    f += (e + [0.0] * 3)[:3]
    import learn_ai
    f += learn_ai.opp_extras(p)       # 2026/11/10:相手の環境デッキの種類(古いモデルは見ない)
    return f


class MainLearnPlayer(PersonaPlayer):
    MODE = "explore"
    EXPLORE_P = 0.2
    MODEL = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.records = []
        self.xrng = random.Random()

    def _rec(self, x):
        self.records.append(x)

    def decide(self, kind, card=None, default=True, extra=None):
        sf = state_features(self)
        if self.xrng.random() < self.EXPLORE_P:
            yes = self.xrng.random() < 0.5
            self._rec(sf + option_features(self, kind, card, yes, extra))
            return yes
        if self.MODEL is not None:
            pr = self.MODEL([sf + option_features(self, kind, card, y, extra) for y in (False, True)])
            return pr[1] > pr[0]
        return default

    def choose_card(self, kind, cands, default):
        sf = state_features(self)
        uniq = list({c.name: c for c in cands}.values())
        if self.xrng.random() < self.EXPLORE_P:
            c = self.xrng.choice(uniq)
            self._rec(sf + option_features(self, kind, c, True))
            return c
        if self.MODEL is not None:
            pr = self.MODEL([sf + option_features(self, kind, c, True) for c in uniq])
            return uniq[max(range(len(uniq)), key=lambda i: pr[i])]
        return default


class MainLearnGame(PGame):
    def make(self, spec, log):
        if spec[0] == "mlearn":
            _, key, recipe, attrs = spec
            p = MainLearnPlayer(key, recipe, self.rng, log)
            for k, v in attrs.items():
                setattr(p, k, v)
            return p
        return super().make(spec, log)
