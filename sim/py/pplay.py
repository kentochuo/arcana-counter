"""対戦練習用:自分(人)が操作する PersonaPlayer と、ブラウザから呼ぶ1手ずつの進行

進め方(リプレイ方式)
  step(cfg, answers) を呼ぶたびに、同じ乱数の種で最初から対戦をやり直し、
  answers に入っている人の選択を順に使う。答えが足りなくなった所(次に人が決める所)で止まり、
  その時の盤面と質問を返す。1手戻すのは answers の最後を消すだけでできる。
"""
from __future__ import annotations

import json
import random

import pengine
from pengine import PersonaPlayer, Attack, short_name, EX_JP
from pcards import C, GAUGES, VG_GAUGES
from precipes_final import FINAL


class NeedInput(Exception):
    def __init__(self, q):
        super().__init__("need input")
        self.q = q


class GameOver(Exception):
    def __init__(self, winner):
        super().__init__("game over")
        self.winner = winner


def cname(c):
    return short_name(c)


class HumanPlayer(PersonaPlayer):
    USE_ESOUL = False          # エリザベスのソウル起動は人が選ぶ
    GEN_DRAW_MIN = 99          # エネルギージェネレーターも人が選ぶ(メインフェイズの選択肢)

    def __init__(self, deck_key, recipe, rng, log, answers):
        self.answers = answers
        self.qi = 0
        self._defer_mull = True
        super().__init__(deck_key, recipe, rng, log)
        self._defer_mull = False
        self.KEEP_G3 = 1

    def L(self, *a):
        if self.log is not None:
            self.log.append(f"[自分 T{self.turn}] " + " ".join(str(x) for x in a))

    # ------------------------------------------------------------ 質問
    def ask(self, kind, title, options, multi=False, desc="", minn=None, maxn=None, extra=None):
        """options: [{id, label, no(カード番号 任意), sub(補足 任意)}]"""
        hint = self.__dict__.pop("_hint", None)
        if self.qi < len(self.answers):
            a = self.answers[self.qi]
            self.qi += 1
            return a
        if getattr(self, "autopilot_ok", False):
            # 先読みの試行中:人の答えが尽きたら、ここからはふだんのAIの判断で動かす
            self.go_auto()
            return self.ask(kind, title, options, multi, desc, minn, maxn, extra)
        ai = None
        if getattr(self, "SHOW_AI", True):
            try:
                ai = hint() if callable(hint) else (hint or self.ai_hint(kind, options, multi, extra or {}))
                if ai:
                    ids = [str(o["id"]) for o in options]
                    ai = dict(ids=[str(x) for x in ai[0] if str(x) in ids], why=ai[1])
                    if not ai["ids"] and not multi:
                        ai = None
            except Exception as ex:          # おすすめの計算に失敗しても対戦は止めない
                ai = None
        raise NeedInput(dict(kind=kind, title=title, desc=desc, options=options, multi=multi,
                             min=minn if minn is not None else (0 if multi else 1),
                             max=maxn if maxn is not None else (len(options) if multi else 1),
                             extra=extra or {}, ai=ai))

    # ------------------------------------------------------------ 「AIならこれを選ぶ」(ふつうのAIの判断を写して計算する)
    def hint(self, fn):
        """次の質問のおすすめ(計算は質問を出す時だけ行う)"""
        self._hint = fn

    def shadow(self):
        """盤面をコピーして、自分側をふだんのAIにしたもの(おすすめの計算用。本物の盤面は変えない)"""
        import copy as _copy
        pc = _copy.deepcopy(self)
        pc.answers = []; pc.qi = 0; pc.log = []
        pc.opp.log = []
        pc.go_auto()
        return pc

    NMS = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右", "V": "ヴァンガード"}

    def ai_spot(self, c, ids):
        """ふだんのAIがこのカードを置く場所"""
        g = self.v_grade
        if c.grade >= 2:
            bc = self.circles["BC"]
            if self.series in ("P4", "P5") and g >= 3 and self.free_front() is None and (bc is None or "team" not in bc.card.tags or bc.card.grade < 2):
                k = "BC"
            else:
                k = self.free_front() or min(("FL", "FR"), key=lambda x: self.unit_score(self.circles[x].card) if self.circles[x] else -1)
        else:
            bce = self.circles["BC"] and self.circles["BC"].card.d["extra"].get("elizabeth")
            if c.d["extra"].get("elizabeth") and self.key in ("P3", "P3P") and g >= 3 and not bce:
                k = "BC"
            else:
                k = self.free_back() or "BL"
        return k if k in ids else (ids[0] if ids else None)

    def sim_main(self):
        """ふだんのAIが今のメインフェイズで最初にすること"""
        pc = self.shadow()
        ev = pc.log
        orig_call, orig_act1 = pc.call, pc.act1
        def call(card, circle, from_where="hand", opp=None, in_battle=False):
            ev.append(("CALL", card, circle, from_where))
            return orig_call(card, circle, from_where, opp, in_battle)
        used = self.act1_turn == self.turn
        def act1(o):
            if used:                 # このターンはもう使った(AIのメインフェイズは1回しか起動しない前提なので、ここで止める)
                return
            ev.append(("ACT1",))
            return orig_act1(o)
        pc.call, pc.act1 = call, act1
        hand_before = list(self.hand)
        pc.main_phase(pc.opp)
        self._ai_spots = {}
        first = None
        for e in ev:
            if isinstance(e, tuple) and e[0] == "CALL" and e[3] == "hand":
                self._ai_spots.setdefault(e[1].d["no"], e[2])
        for e in ev:
            if isinstance(e, tuple):
                if e[0] == "ACT1":
                    first = (["act1"], "G3の【起】で山札からアタッカーを2体コールします(最初に使う)"); break
                if e[0] == "CALL" and e[3] == "hand":
                    i = next((j for j, c in enumerate(hand_before) if c is e[1]), None)
                    if i is not None:
                        first = ([f"call:{i}"], f"{cname(e[1])}を{self.NMS.get(e[2], e[2])}にコールします"); break
                if e[0] == "CALL" and e[3] == "drop":
                    if e[1].d["no"] == "015":
                        first = (["nanako"], "菜々子をドロップからコールします"); break
                    j = next((j for j, c in enumerate(self.drop) if c is e[1]), None)
                    if j is not None:
                        first = ([f"dropcall:{j}"], f"{cname(e[1])}をドロップからコールします"); break
            elif isinstance(e, str):
                t = e.split("] ", 1)[-1].strip()
                if t.startswith("エリザベスをソウルへ:エネルギー"):
                    first = (["eliz:ec"], "エリザベスをソウルに置いてエネルギー+3"); break
                if t.startswith("エリザベスをソウルへ:カウンター"):
                    first = (["eliz:cc"], "エリザベスをソウルに置いてカウンターチャージ1"); break
                if t.startswith("覚醒"):
                    for k, u in self.circles.items():
                        pu = pc.circles.get(k)
                        if u and pu and u.gauge != pu.gauge:
                            first = ([f"awaken:{k}"], f"{cname(u.card)}を覚醒させます"); break
                    if first:
                        break
        return first or (["end"], "AIならこれ以上は何もせずバトルへ進みます(手札を守りに残す)")

    def ai_hint(self, kind, options, multi, extra):
        ids = [str(o["id"]) for o in options]
        o = self.opp
        if kind == "mulligan":
            back, g3k, sk = [], 0, 0
            for i, c in enumerate(self.hand):
                if c.kind == "unit" and c.grade in (1, 2) and not self.is_g3(c):
                    continue
                if self.is_g3(c) and g3k < 1:
                    g3k += 1; continue
                if c.kind == "sentinel" and sk < 1:
                    sk += 1; continue
                back.append(str(i))
            return back, "G3を1枚・G1とG2のユニット・守護者1枚を残し、トリガーと2枚目以降のG3を戻します"
        if kind == "ride_cost":
            pref = [i for i, c in enumerate(self.hand) if c.d["extra"].get("discard_search") in ("persona_ability", "velvet")]
            cand = pref or list(range(len(self.hand)))
            i = min(cand, key=lambda j: self.card_value(self.hand[j]))
            c = self.hand[i]
            why = "捨てると山札の上7枚から代わりを持ってこられる" if pref else "手札の中で一番いらないカード"
            return [str(i)], f"{cname(c)}を捨てます({why})"
        if kind in ("persona_ride", "marie"):
            return ["yes"], "ペルソナライドは毎ターン行います(前列+10000、1枚引く)"
        if kind == "main":
            return self.sim_main()
        if kind == "call_spot":
            no = (extra or {}).get("card")
            sp = getattr(self, "_ai_spots", {}).get(no)
            c = next((x for x in self.hand + self.drop + self.deck if x.d["no"] == no), None)
            if sp not in ids and c is not None:
                sp = self.ai_spot(c, [x for x in ids if x != "cancel"])
            return ([sp], f"{self.NMS.get(sp, sp)}に置きます") if sp in ids else None
        if kind == "attack":
            order = [sp for sp in ("FL", "V", "FR", "BC") if sp in ids]
            if order:
                sp = order[0]
                if len(order) == 1:
                    why = "残りはこのユニットだけです"
                else:
                    why = " → ".join(self.NMS[x] for x in order) + "の順にアタックします"
                    if sp != "V" and "V" in order:
                        why += "(ヴァンガードのドライブチェックで出たトリガーを、後のアタックに乗せるため)"
                return [sp], why
            return ["end"], "アタックできるユニットはもういません"
        if kind == "boost":
            return ["yes"], "ブーストできる時はブーストします"
        if kind == "eliz_bc":
            return ["yes"], "エネルギーが2あれば使います(クリティカル+1)"
        if kind == "ryuji":
            return ["yes"], "相手の前列を退却させてからスタンドして、もう一度アタックします"
        if kind == "pharos":
            ok = len(self.hand) <= 6 and len(self.deck) > self.DECK_SAFE
            return (["yes"], "手札が6枚以下なら引きます") if ok else (["no"], "手札が多い・山札が少ないので置きません")
        if kind == "sent_cost":
            i = min(range(len(self.hand)), key=lambda j: self.guard_cost(self.hand[j]))
            return [str(i)], f"{cname(self.hand[i])}を捨てます(一番価値の低いカード)"
        if kind == "over_call":
            g2 = [(j, c) for j, c in enumerate(self.hand) if c.kind == "unit" and c.grade == 2 and not self.is_g3(c) and str(j) in ids]
            if g2 and self.free_front():
                j, c = max(g2, key=lambda jc: self.unit_score(jc[1])); return [str(j)], f"{cname(c)}を前列にコールします"
            g1 = [(j, c) for j, c in enumerate(self.hand) if c.kind == "unit" and c.grade == 1 and str(j) in ids]
            if g1 and self.free_back():
                j, c = max(g1, key=lambda jc: self.back_score(jc[1], self.free_back())); return [str(j)], f"{cname(c)}を後列にコールします"
            return ["none"], "コールしません"
        return None


    def go_auto(self):
        self.__class__ = AutoHuman
        for k in ("USE_ESOUL", "GEN_DRAW_MIN"):
            setattr(self, k, getattr(PersonaPlayer, k))
        for k, v in pengine.TUNE.get(self.key, {}).items():
            if k == "GUARD_TABLE":
                v = {int(a): b for a, b in v.items()}
            elif k in ("RES", "EARLY_G1_SECOND") and v is not None:
                v = tuple(v)
            elif k == "EARLY":
                v = {int(a): tuple(b) for a, b in v.items()}
            setattr(self, k, v)

    def opt_card(self, c, id_, sub=""):
        return dict(id=id_, label=cname(c), no=c.d["no"], sub=sub)

    # ------------------------------------------------------------ マリガン
    def mulligan(self):
        if self._defer_mull:
            return
        opts = [self.opt_card(c, i) for i, c in enumerate(self.hand)]
        back = self.ask("mulligan", "マリガン:山札に戻すカードを選んでください(0枚でもOK)", opts, multi=True,
                        desc="選んだカードを山札に戻してシャッフルし、同じ枚数を引き直します。")
        back = sorted(set(int(i) for i in back))
        ret = [self.hand[i] for i in back]
        self.hand = [c for i, c in enumerate(self.hand) if i not in back]
        self.deck += ret
        self.rng.shuffle(self.deck)
        self.draw(len(ret))
        self.L(f"マリガン:{len(ret)}枚戻した")

    # ------------------------------------------------------------ ライド
    def ride_phase(self, first, o):
        if self.v_grade < 3:
            nxt = C[self.ride_line[self.v_grade + 1].d["no"]]["name"] if False else f"G{self.v_grade + 1}"
            if self.hand:
                opts = [self.opt_card(c, i) for i, c in enumerate(self.hand)]
                i = int(self.ask("ride_cost", f"ライド({nxt}主人公へ):コストで手札から捨てるカードを選んでください", opts,
                                 desc="エリザベスを捨てると山札の上7枚からペルソナを持つカード、イゴールを捨てるとベルベットルームの住人を探します。"))
                disc = self.hand.pop(i)
                self.drop.append(disc)
                if disc.d["extra"].get("discard_search"):
                    kind = disc.d["extra"]["discard_search"]
                    if kind == "persona_ability":
                        self.pick_top7(f"{cname(disc)}:山札の上7枚からペルソナ能力を持つカードを1枚まで手札に加えます", lambda x: bool(x.persona), score=self.unit_score)
                    else:
                        self.pick_top7(f"{cname(disc)}:山札の上7枚からベルベットルームの住人を1枚まで手札に加えます", lambda x: "velvet" in x.tags, score=lambda x: 1 if x.no == "024" else 0)
            else:
                disc = None
            self.soul.append(self.vg)
            self.v_grade += 1
            self.vg = self.ride_line[self.v_grade]
            self.L(f"ライド G{self.v_grade}(コスト:{self.short(disc) if disc else 'なし'})")
            if self.v_grade == 1:
                if not first:
                    self.draw(1)
                self.draw(1); self.team_token = True
                self.vgauge = [pengine.VG_BASE[self.series]]
                self.L("  G1主人公:1ドロー、チームトークン設置" + ("(後攻G0の1ドローも)" if not first else ""))
            elif self.v_grade == 2:
                self.place_marker(self.arc(self.vg))
                need_g3 = not any(self.is_g3(h) for h in self.hand)
                k = self.pick_top7("G2主人公:山札の上7枚からユニットを1枚まで手札に加えます" + ("" if need_g3 else "(G3は手札にあるので選べません)"),
                                   lambda x: x.kind == "unit" and (need_g3 or not self.is_g3(x)),
                                   score=lambda x: 99999 if (need_g3 and self.is_g3(x)) else self.unit_score(x) + (5000 if x.grade == 2 else 0))
                self.L("  G2主人公:山札7枚から", self.short(k) if k else "なし")
            elif self.v_grade == 3:
                self.place_marker(self.arc(self.vg))
                if self.series == "P3":
                    self.place_marker("愚者")
        else:
            g3s = [c for c in self.hand if self.is_g3(c)]
            if g3s:
                a = self.ask("persona_ride", "ペルソナライドしますか?", [
                    dict(id="yes", label="ペルソナライドする", sub="手札の主人公G3にライド:1枚引き、前列+10000"),
                    dict(id="no", label="しない")])
                if a == "yes":
                    g3 = g3s[0]
                    self.hand.remove(g3)
                    self.soul.append(self.vg)
                    self.vg = g3
                    self.draw(1)
                    self.persona_this_turn = True
                    self.persona_ever = True
                    self.place_marker(self.arc(g3))
                    if self.series == "P3":
                        self.place_marker("愚者")
                    self.L("ペルソナライド")


    def ask_spot(self, c, why="", allow=("FL", "FR", "BL", "BC", "BR"), only_empty=False):
        sopt = []
        for k in allow:
            u = self.circles[k]
            if only_empty and u is not None:
                continue
            nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}[k]
            sopt.append(dict(id=k, label=nm, sub=("空き" if u is None else f"{cname(u.card)}を退却させて置く")))
        if not sopt:
            return None
        return self.ask("call_spot", f"{cname(c)}をどこにコールしますか?" + (f"({why})" if why else ""), sopt, extra=dict(card=c.d["no"]))

    # ------------------------------------------------------------ メインフェイズ
    def callable_units(self):
        g = self.v_grade
        return [(i, c) for i, c in enumerate(self.hand) if c.kind in ("unit", "sentinel") and 0 <= c.grade <= g and not self.is_g3(c)]

    def main_phase(self, o):
        g = self.v_grade
        # マリー(永劫3つ)
        if not self.persona_this_turn and not self.marie_pr_used and g >= 3 and len(self.vgauge) >= 2 and self.arcana.get("永劫", 0) >= 3 \
                and any(u and u.card.d["no"] == "016" for u in self.circles.values()):
            if self.ask("marie", "マリー:自身をバインドしてペルソナライドを発動しますか?",
                        [dict(id="yes", label="発動する"), dict(id="no", label="しない")]) == "yes":
                self.marie_persona()
        while True:
            opts = []
            # G3の【起】1
            cost = max(0, 1 - self.cb_discount)
            if g >= 3 and self.act1_turn != self.turn and self.cb_avail() >= cost:
                opts.append(dict(id="act1", label=f"G3の【起】:CB{cost}で山札の上7枚から2体コール",
                                 sub="チームのユニット1体+協力者/ベルベットルームの住人1体。中央後列のユニットがアタックできるようになる"))
            # コール
            if g >= 1 and not (self.first and self.turn == 1 and False):
                for i, c in self.callable_units():
                    opts.append(dict(id=f"call:{i}", label=f"コール:{cname(c)}", no=c.d["no"], sub=f"G{c.grade} パワー{c.power}"))
            # 前後の入れ替え
            for col in (("FL", "BL"), ("FR", "BR")):
                f, b = col
                fu, bu = self.circles[f], self.circles[b]
                if fu or bu:
                    opts.append(dict(id=f"swap:{f}", label=f"{'左' if f == 'FL' else '右'}列の前後を入れ替え",
                                     sub=f"{cname(fu.card) if fu else '空'} ⇔ {cname(bu.card) if bu else '空'}"))
            # エリザベス(手札):ソウルに置いてCC1かEC3
            if g >= 2 and self.eliz_soul_turn != self.turn:
                el = [c for c in self.hand if c.d["extra"].get("elizabeth")]
                if el:
                    opts.append(dict(id="eliz:ec", label="エリザベス(手札)をソウルへ:エネルギー+3", no=el[0].d["no"]))
                    if len(self.dmg_zone) - self.cb_avail() >= 1:
                        opts.append(dict(id="eliz:cc", label="エリザベス(手札)をソウルへ:カウンターチャージ1", no=el[0].d["no"]))
            # P3:覚醒
            if self.series == "P3" and g >= 3 and o.v_grade >= 3 and getattr(self, "awaken_turn", -1) != self.turn:   # S.E.E.S.の【起】【ターン1回】
                for k, u in self.circles.items():
                    if u and u.gauge and "evolve" in GAUGES[u.gauge]:
                        nd = sum(1 for c in self.drop if c.name == u.card.name); ns = sum(1 for c in self.soul if c.name == u.card.name)
                        if nd or ns:
                            opts.append(dict(id=f"awaken:{k}", label=f"覚醒:{cname(u.card)}({u.gauge}→{GAUGES[u.gauge]['evolve']})",
                                             no=u.card.d["no"], sub=f"S.E.E.S.の【起】(ターン1回):同名カードをバインド(ドロップ{nd}枚・ソウル{ns}枚)"))
            # 菜々子(正義3つ)
            if g >= 3 and self.key == "P4" and self.arcana["正義"] >= 3 and len(self.soul) >= 1:
                nn = [x for x in self.drop if x.d["no"] == "015"]
                kuma = any(u and u.card.d["no"] == "040" for u in self.circles.values())
                same = any(u and u.card.d["no"] == "015" for u in self.circles.values())
                if nn and kuma and not same and self.free_back():
                    opts.append(dict(id="nanako", label="菜々子:SB1でドロップからコール", no="015"))
            # ドロップから出られるユニット
            if g >= 2:
                for j, c in enumerate(self.drop):
                    ex = c.d["extra"].get("drop_call")
                    if not ex or not self.works(c):
                        continue
                    if any(u and u.card.name == c.name for u in self.circles.values()):
                        continue
                    kind, n = ex
                    if (kind == "EB" and self.energy >= n) or (kind == "CB" and self.cb_avail() >= 1):
                        opts.append(dict(id=f"dropcall:{j}", label=f"ドロップからコール:{cname(c)}({kind}{n if kind == 'EB' else 1})", no=c.d["no"]))
            if self.energy >= 7 and getattr(self, "gen_turn", -1) != self.turn and self.deck:
                opts.append(dict(id="gen", label="エネルギージェネレーター:EB7で1枚引く", sub="ターン1回"))
            opts.append(dict(id="end", label="メインフェイズを終わる(バトルへ)" if not (self.first and self.turn == 1) else "メインフェイズを終わる(先攻1ターン目はバトルなし)"))
            a = self.ask("main", "メインフェイズ:行動を選んでください", opts)
            if a == "end":
                break
            kind, _, arg = a.partition(":")
            if kind == "act1":
                if self.pay_cb(cost):
                    self.cb_discount = 0
                    self.act1_turn = self.turn
                    self.act1(o)
            elif kind == "call":
                c = self.hand[int(arg)]
                spots = ["FL", "FR", "BL", "BC", "BR"]
                sopt = []
                for k in spots:
                    u = self.circles[k]
                    nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}[k]
                    sopt.append(dict(id=k, label=nm, sub=("空き" if u is None else f"{cname(u.card)}を退却させて置く")))
                sopt.append(dict(id="cancel", label="やめる"))
                k = self.ask("call_spot", f"{cname(c)}をどこにコールしますか?", sopt, extra=dict(card=c.d["no"]))
                if k == "cancel":
                    continue
                self.hand.remove(c)
                self.call(c, k, "hand", o)
            elif kind == "gen":
                if self.pay_eb(7):
                    self.gen_turn = self.turn; self.draw(1); self.L("  エネルギージェネレーター:EB7で1枚引く")
            elif kind == "swap":
                f = arg; b = "BL" if f == "FL" else "BR"
                self.circles[f], self.circles[b] = self.circles[b], self.circles[f]
                self.L("前後を入れ替え", f, "⇔", b)
            elif kind == "eliz":
                el = [c for c in self.hand if c.d["extra"].get("elizabeth")][0]
                self.hand.remove(el); self.soul.append(el); self.eliz_soul_turn = self.turn
                if arg == "ec":
                    self.ec(3); self.L("エリザベスをソウルへ:エネルギーチャージ3")
                else:
                    self.cc(1); self.L("エリザベスをソウルへ:カウンターチャージ1")
            elif kind == "awaken":
                u = self.circles[arg]
                fd = next((c for c in self.drop if c.name == u.card.name), None)
                fs = next((c for c in self.soul if c.name == u.card.name), None)
                if fd and fs:
                    self.hint((["drop"], "ソウルはスキルのコストに使うので、ドロップのカードをバインドします"))
                    w = self.ask("eff_pick", f"覚醒:{cname(u.card)}のコストでバインドするカードを選んでください",
                                 [dict(id="drop", label="ドロップの同名カード", no=fd.d["no"]), dict(id="soul", label="ソウルの同名カード", no=fs.d["no"], sub=f"ソウルが{len(self.soul)}→{len(self.soul) - 1}枚に減る")])
                    c = fd if w == "drop" else fs
                else:
                    c = fd or fs
                (self.soul if c in self.soul else self.drop).remove(c)
                self.awaken_turn = self.turn
                self.bind.append(c)
                self.awakened.add(u.card.name)
                u.gauge = GAUGES[u.gauge]["evolve"]
                self.L("覚醒", u.card, "→", u.gauge)
            elif kind == "nanako":
                nn = [x for x in self.drop if x.d["no"] == "015"]
                spot = self.ask_spot(nn[0], "菜々子", only_empty=True) if nn else None
                if nn and spot and self.pay_sb(1):
                    self.drop.remove(nn[0]); self.call(nn[0], spot, "drop", o); self.L("  菜々子:ドロップからコール")
            elif kind == "dropcall":
                c = self.drop[int(arg)]
                spot = self.ask_spot(c, "ドロップから")
                if spot is None:
                    continue
                k2, n = c.d["extra"]["drop_call"]
                if k2 == "EB" and self.pay_eb(n):
                    self.drop.remove(c); self.call(c, spot, "drop", o)
                elif k2 == "CB" and self.pay_cb(1):
                    self.drop.remove(c); self.call(c, spot, "drop", o)
                    if self.deck:
                        self.soul.append(self.deck.pop())
            if self.deck_empty_check():
                return
        # G3の【起】2(ペルソナライドしたターン:コストなしなので自動で使う)
        if g >= 3 and self.persona_this_turn:
            if self.series == "P3":
                self.p3_power = 1000 * sum(self.arcana.values())
                self.L(f"G3の【起】:ヴァンガード+{self.p3_power}(アルカナマーカー×1000)")
                if len(self.vgauge) == 2 and self.arcana["審判"] >= 3:
                    self.vgauge.append("メサイア"); self.L("ペルソナ:メサイア")
            elif self.series == "P4":
                self.p4_drive = True; self.L("G3の【起】:ヴァンガード+5000・ドライブ+1")
            elif self.series == "P5":
                self.p5_bc_drive = True; self.L("G3の【起】:中央後列のペルソナゲージ持ちがヴァンガードにアタックした時ドライブチェック")


    # ------------------------------------------------------------ G3の【起】1(山札の上7枚から2体コール)
    def act1(self, o):
        top = list(reversed(self.deck[-7:]))          # 山札の上から順に
        names = "、".join(cname(c) for c in top)
        team = [c for c in top if "team" in c.tags and c.kind in ("unit", "sentinel")]
        sec_tag = lambda c: c.kind in ("unit", "sentinel") and ("velvet" in c.tags or ("kyoryoku" in c.tags and self.works(c)))
        picks = []
        if team:
            opts = [self.opt_card(c, f"t{top.index(c)}", f"G{c.grade} パワー{c.power}") for c in team] + [dict(id="none", label="選ばない")]
            onb = {u.card.name for u in self.units()}
            tm = [c for c in team if c.kind == "unit" and not self.is_g3(c)]
            tm = [c for c in tm if c.name not in onb] or tm
            if tm:
                b = max(tm, key=self.unit_score)
                self.hint(([f"t{top.index(b)}"], f"{cname(b)}(盤面にいない名前で、アタッカーとして一番強いユニット)"))
            a = self.ask("act1_pick", "G3の【起】:チームのユニットを1枚まで選んでください", opts, desc=f"山札の上7枚:{names}")
            if a != "none":
                picks.append(top[int(a[1:])])
        sec = [c for c in top if sec_tag(c) and c not in picks]
        if sec:
            opts = [self.opt_card(c, f"t{top.index(c)}", f"G{c.grade} パワー{c.power}") for c in sec] + [dict(id="none", label="選ばない")]
            bce = self.circles["BC"] and self.circles["BC"].card.d["extra"].get("elizabeth")
            def s2(c):
                if c.d["extra"].get("elizabeth") and self.key in ("P3", "P3P"):
                    return 50 if not bce else 5
                if getattr(self, "focus_th", False) and "タナトス" not in self.vgauge and c.no in ("010", "064"):
                    return 60 if (c.no == "010" and self.arcana["死神"] >= 3) else 30
                return 10 + (5 if c.on_call else 0)
            b = max(sec, key=s2)
            self.hint(([f"t{top.index(b)}"], f"{cname(b)}" + ("(中央後列のアタッカー)" if b.d["extra"].get("elizabeth") and self.key in ("P3", "P3P") else "(登場時の効果を持つカードを優先)")))
            a = self.ask("act1_pick", "G3の【起】:ベルベットルームの住人か協力者を1枚まで選んでください", opts, desc=f"山札の上7枚:{names}")
            if a != "none":
                picks.append(top[int(a[1:])])
        for c in picks:
            self.deck.remove(c)
        self.rng.shuffle(self.deck)
        used = set()
        for c in picks:
            sopt = []
            for k in ("FL", "FR", "BL", "BC", "BR"):
                if k in used:
                    continue
                u = self.circles[k]
                nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}[k]
                sopt.append(dict(id=k, label=nm, sub=("空き" if u is None else f"{cname(u.card)}を退却させて置く")))
            k = self.ask("call_spot", f"{cname(c)}をどこにコールしますか?(G3の【起】)", sopt, extra=dict(card=c.d["no"]))
            used.add(k)
            self.L("G3の起動でコール", c, "→", k)
            self.call(c, k, "deck", o)


    # ------------------------------------------------------------ 登場時効果のうち、自分で選ぶもの
    MANUAL_EFF = ("takemi", "togo", "morgana", "cb_draw", "yukiko", "eb1_lock", "sae", "chie_search", "nanako", "marie", "fushimi")
    ONCE_EFF = ("takemi", "togo", "eb1_lock", "sae", "nanako", "marie", "fushimi")

    def yes_no(self, title, yes, no="使わない", desc="", sub="", ai=None):
        if ai is not None:
            self.hint(([("yes" if ai[0] else "no")], ai[1]))
        return self.ask("eff_yn", title, [dict(id="yes", label=yes, sub=sub), dict(id="no", label=no)], desc=desc) == "yes"

    def pick_top7(self, title, pred, desc="", score=None):
        """山札の上7枚を見て、条件に合うカードを1枚まで選ぶ(選んだカードを返す。山札はシャッフル)"""
        top = list(reversed(self.deck[-7:]))
        names = "、".join(cname(c) for c in top) or "なし"
        cands = [c for c in top if pred(c)]
        if not cands:
            self.rng.shuffle(self.deck); self.L("  山札の上7枚に対象なし"); return None
        seen, opts = [], []
        for c in cands:
            if c.d["no"] not in seen:
                seen.append(c.d["no"]); opts.append(self.opt_card(c, f"t{top.index(c)}", f"G{c.grade}" + (" 守護者" if c.kind == "sentinel" else "")))
        opts.append(dict(id="none", label="選ばない"))
        if score is not None:
            best = max(cands, key=score)
            self.hint(([f"t{top.index(best)}"], f"{cname(best)}(ふだんのAIの評価が一番高いカード)"))
        k = self.ask("eff_pick", title, opts, desc=(desc + " " if desc else "") + f"山札の上7枚:{names}")
        if k == "none":
            self.rng.shuffle(self.deck); return None
        c = top[int(k[1:])]
        self.deck.remove(c); self.rng.shuffle(self.deck); self.hand.append(c)
        self.L("  手札に加えた:", cname(c))
        return c

    def lock_pick(self, opp):
        cands = [k for k in ("FL", "FR", "BL", "BC", "BR") if opp.circles.get(k) and not opp.circles[k].locked]
        if not cands:
            return False
        nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}
        fr = [k for k in cands if k in ("FL", "FR")] or cands
        best = max(fr, key=lambda k: (opp.circles[k].rest, opp.circles[k].card.power))
        self.hint(([best], "アタックしてレストしている前列(次のターンにスタンドできなくなる)を優先します"))
        opts = [dict(id=k, label=f"{nm[k]} {cname(opp.circles[k].card)}", no=opp.circles[k].card.d["no"],
                     sub=("レスト中:次のターンもスタンドできない" if opp.circles[k].rest else "スタンド中:インターセプトできなくなる")) for k in cands]
        k = self.ask("eff_pick", "相手のリアガードを1枚選んでください(次の相手のターン終了時まで、インターセプトできずスタンドもできない)", opts)
        x = opp.circles[k]
        x.locked = True; x.lock_rest = x.rest
        opp.L("  ロックされた:", opp.short(x.card), "(レスト中→次のターンもスタンドできない)" if x.rest else "(スタンド中→インターセプトだけできない)")
        return True

    def effect(self, eff, u, circle, opp, in_battle):
        if eff not in self.MANUAL_EFF:
            return super().effect(eff, u, circle, opp, in_battle)
        name = u.card.name
        self.L(f"  登場時効果[{eff}] {self.short(u.card)}")
        if eff in self.ONCE_EFF:
            if name in self.once:
                return
            self.once.add(name)
        a = self.arcana[self.arc(u.card)] if self.arc(u.card) else 0
        sn = cname(u.card)
        if eff == "cb_draw":
            use = self.cb_avail() >= 2 or (self.cb_avail() >= 1 and self.act1_turn == self.turn)
            if self.cb_avail() >= 1 and self.yes_no(f"{sn}:カウンターブラスト1で1枚引きますか?", "CB1で1枚引く", sub=f"表のダメージ {self.cb_avail()}枚",
                                                   ai=(use, "表のダメージが2枚以上(G3の【起】の分を残せる)なら引きます" if use else "表のダメージが1枚なので、G3の【起】のために残します")):
                self.pay_cb(1); self.draw(1)
            return
        if eff == "yukiko":
            others = [(k, x) for k, x in self.circles.items() if x and x is not u and "team" in x.card.tags and x.card.kind == "unit" and x.card.name != u.card.name]
            if not others:
                return
            nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}
            opts = [dict(id=k, label=f"{nm[k]} {cname(x.card)}", no=x.card.d["no"]) for k, x in others]
            bad = [k for k, x in others if x.locked or x.doomed]
            tk = bad[0] if bad else min(others, key=lambda kx: self.unit_score(kx[1].card))[0]
            self.hint(([tk], "ロックされた・弱いユニットに与えます(次のターンに役立たないユニットを手札1枚に替える)"))
            k = self.ask("eff_pick", f"{sn}:ユニットを1枚選び、『ターン終了時、このユニットを退却させて1枚引く』を与えます", opts,
                         desc="与えたユニットは、ターン終了時に退却させて1枚引くかどうかを選べます。")
            self.circles[k].end_draw_retire = True
            self.L("  雪子:", cname(self.circles[k].card), "に「ターン終了時、退却して1枚引く」を与えた")
            return
        if eff == "eb1_lock":
            if opp and self.energy >= 1 and any(opp.circles.get(k) for k in ("FL", "FR", "BL", "BC", "BR")) \
                    and self.yes_no(f"{sn}:エネルギー1で相手のリアガード1枚をロックしますか?", "EB1でロックする", sub=f"エネルギー {self.energy}",
                                    ai=(self.energy >= 3, "エネルギーが3以上あればロックします" if self.energy >= 3 else "エネルギーが少ないのでスキル用に残します")):
                self.pay_eb(1); self.lock_pick(opp)
                if a >= 3 and u.card.no == "093":
                    self.ec(2)
            return
        if eff == "sae":
            if self.cb_avail() < 1:
                return
            want_g3 = a >= 3
            what = "主人公のG3" if want_g3 else "明智 吾郎"
            if not self.yes_no(f"{sn}:カウンターブラスト1で山札から{what}を探しますか?", "CB1で探す", sub=f"表のダメージ {self.cb_avail()}枚",
                               ai=(self.cb_avail() >= 2, "表のダメージが2枚以上なら探します" if self.cb_avail() >= 2 else "表のダメージが1枚なので残します")):
                return
            k = self.search_deck(lambda x: (self.is_g3(x) if want_g3 else x.no == "020"), lambda x: 0)
            self.pay_cb(1)
            if k:
                self.hand.append(k); self.L("  冴:", cname(k), "を手札に")
            return
        if eff == "chie_search":
            need_sent = not any(x.kind == "sentinel" for x in self.hand)
            need_g3 = not any(self.is_g3(x) for x in self.hand)
            def csc(x):
                if self.is_g3(x): return 2_000_000 if need_g3 else -2_000_000
                if x.kind == "sentinel": return 1_000_000 if need_sent else -1_000_000
                return self.unit_score(x)
            c = self.pick_top7(f"{sn}:山札の上7枚から〈自称特別捜査隊〉を1枚まで手札に加えます",
                               lambda x: "team" in x.tags and x.kind in ("unit", "sentinel") and x.name != name,
                               desc="主人公のG3や守護者(りせ)も選べます。", score=csc)
            if not c:
                return
            if self.cb_avail() >= 1 and self.yes_no(f"{sn}:カウンターブラスト1しますか?", "CB1する", no="しない(手札1枚をソウルに置く)", sub=f"表のダメージ {self.cb_avail()}枚",
                                                   ai=(self.cb_avail() >= 2, "表のダメージが2枚以上ならCB1" if self.cb_avail() >= 2 else "CBを残して手札をソウルへ")):
                self.pay_cb(1)
            elif self.hand:
                opts = [self.opt_card(x, j) for j, x in enumerate(self.hand)]
                jj = min(range(len(self.hand)), key=lambda q: self.card_value(self.hand[q]))
                self.hint(([str(jj)], f"{cname(self.hand[jj])}(手札の中で一番いらないカード)"))
                j = int(self.ask("eff_soul", f"{sn}:手札から1枚選んでソウルに置いてください", opts))
                x = self.hand.pop(j); self.soul.append(x); self.L("  千枝:", cname(x), "をソウルへ")
            return
        if eff == "nanako":
            self.pick_top7(f"{sn}:山札の上7枚から「主人公」「花村 陽介」「クマ」のユニットを1枚まで手札に加えます",
                           lambda x: x.kind == "unit" and (self.is_g3(x) or x.no in ("012", "040")),
                           score=lambda x: 2 if self.is_g3(x) and not any(self.is_g3(h) for h in self.hand) else (1 if x.no == "012" else 0))
            return
        if eff == "marie":
            gs = [(k, x) for k, x in self.circles.items() if x and x.gauge]
            if not gs:
                return
            nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}
            opts = [dict(id=k, label=f"{nm[k]} {cname(x.card)}", no=x.card.d["no"], sub=f"「{self.arc(x.card)}」{self.arcana[self.arc(x.card)]}つ → +1") for k, x in gs]
            bk = max(gs, key=lambda kx: self.arcana[self.arc(kx[1].card)] if self.arcana[self.arc(kx[1].card)] < 3 else -1)[0]
            self.hint(([bk], "3つに一番近いアルカナを増やします(3つで転生)"))
            k = self.ask("eff_pick", f"{sn}:ペルソナゲージを持つリアガードを1枚選び、そのアルカナのマーカーを1つ置きます", opts)
            self.place_marker(self.arc(self.circles[k].card))
            return
        if eff == "fushimi":
            if len(self.hand) < 1:
                return
            triple = a >= 3 and self.v_grade >= 3
            opts = [self.opt_card(x, j) for j, x in enumerate(self.hand)] + [dict(id="none", label="使わない")]
            if len(self.hand) >= 2:
                jj = min(range(len(self.hand)), key=lambda q: self.card_value(self.hand[q]))
                self.hint(([str(jj)], f"{cname(self.hand[jj])}をソウルへ(一番いらないカード)"))
            else:
                self.hint((["none"], "手札が1枚なので使いません"))
            j = self.ask("eff_soul", f"{sn}:手札を1枚ソウルに置いて" + ("ヴァンガードをトリプルドライブにしますか?(正義3つ)" if triple else "1枚引きますか?"), opts)
            if j == "none":
                return
            x = self.hand.pop(int(j)); self.soul.append(x); self.L("  伏見:", cname(x), "をソウルへ")
            if triple:
                self.triple = True; self.L("伏見:トリプルドライブ")
            else:
                self.draw(1)
            return
        if eff == "morgana":
            top = list(reversed(self.deck[-5:]))
            names = "、".join(cname(c) for c in top)
            cands = [c for c in top if "team" in c.tags and c.kind in ("unit", "sentinel") and c.name != name]
            if not cands:
                self.rng.shuffle(self.deck); self.L("  モルガナ:山札の上5枚に対象なし"); return
            opts = [self.opt_card(c, f"t{top.index(c)}", f"G{c.grade}" + (" 守護者" if c.kind == "sentinel" else "")) for c in cands] + [dict(id="none", label="選ばない")]
            ns = not any(c.kind == "sentinel" for c in self.hand); ng = not any(self.is_g3(c) for c in self.hand)
            b = max(cands, key=lambda x: (2_000_000 if ng else -2_000_000) if self.is_g3(x) else ((1_000_000 if ns else -1_000_000) if x.kind == "sentinel" else self.unit_score(x)))
            self.hint(([f"t{top.index(b)}"], f"{cname(b)}" + ("(手札にG3がない)" if self.is_g3(b) else "(手札に守護者がない)" if b.kind == "sentinel" else "")))
            k = self.ask("eff_pick", "モルガナ:山札の上5枚から〈心の怪盗団〉を1枚まで手札に加えます", opts, desc=f"山札の上5枚:{names}。加えたら手札を1枚ソウルに置きます。")
            if k == "none":
                self.rng.shuffle(self.deck); return
            c = top[int(k[1:])]
            self.deck.remove(c); self.rng.shuffle(self.deck); self.hand.append(c)
            self.L("  モルガナ:", cname(c), "を手札に")
            opts = [self.opt_card(x, j) for j, x in enumerate(self.hand)]
            jj = min(range(len(self.hand)), key=lambda q: self.card_value(self.hand[q]))
            self.hint(([str(jj)], f"{cname(self.hand[jj])}(一番いらないカード)"))
            j = int(self.ask("eff_soul", "モルガナ:手札から1枚選んでソウルに置いてください", opts))
            x = self.hand.pop(j); self.soul.append(x); self.L("  モルガナ:", cname(x), "をソウルへ")
        elif eff == "takemi":
            cands = [k for k in self.drop if "team" in k.tags and k.kind == "unit" and k.grade <= self.v_grade]
            if not cands or not (a >= 3 or len(self.soul) >= 1):
                return
            seen = []
            opts = []
            for j, k in enumerate(self.drop):
                if k in cands and k.d["no"] not in seen:
                    seen.append(k.d["no"]); opts.append(self.opt_card(k, f"d{j}", f"G{k.grade} パワー{k.power}"))
            opts.append(dict(id="none", label="使わない"))
            spot0 = self.free_front() or (self.free_back() if cands and cands[0].grade == 1 else None)
            if spot0 and (a >= 3 or len(self.soul) >= 2):
                b = max(cands, key=self.unit_score)
                j = next(j for j, k in enumerate(self.drop) if k.d["no"] == b.d["no"])
                self.hint(([f"d{j}"], f"{cname(b)}(一番強いユニット)を空いているサークルに"))
            else:
                self.hint((["none"], "空いているサークルがない・ソウルが少ないので使いません"))
            pick = self.ask("eff_pick", "武見:ドロップから〈心の怪盗団〉をコールしますか?" + ("(死神3つ:コストなし)" if a >= 3 else "(SB1)"), opts)
            if pick == "none":
                return
            k = self.drop[int(pick[1:])]
            spot = self.ask_spot(k, "武見")
            if spot is None or not (a >= 3 or self.pay_sb(1)):
                return
            self.drop.remove(k); self.call(k, spot, "drop", opp)
        elif eff == "togo":
            g2 = [(k, x) for k, x in self.circles.items() if x and x.card.grade == 2 and "team" in x.card.tags and x.card.kind == "unit"]
            if not g2:
                return
            opts = [dict(id=k, label=f"{cname(x.card)}をソウルへ", no=x.card.d["no"]) for k, x in g2] + [dict(id="none", label="使わない")]
            k = self.ask("eff_pick", "一二三:(R)のG2〈心の怪盗団〉を1枚ソウルに置いて、山札から別名のG2をコールしますか?", opts)
            if k == "none":
                return
            x = self.circles[k]
            cands = [c for c in self.deck if c.grade == 2 and "team" in c.tags and c.kind == "unit" and c.name != x.card.name and not c.d["extra"].get("x")]
            seen, opts = [], []
            for c in cands:
                if c.d["no"] not in seen:
                    seen.append(c.d["no"]); opts.append(self.opt_card(c, c.d["no"], f"パワー{c.power}"))
            self.soul.append(x.card); self.circles[k] = None
            self.L("  一二三:", cname(x.card), "をソウルへ")
            if not opts:
                self.rng.shuffle(self.deck); return
            no = self.ask("eff_pick", "一二三:山札からコールするG2を選んでください", opts)
            tgt = next(c for c in self.deck if c.d["no"] == no)
            self.deck.remove(tgt); self.rng.shuffle(self.deck)
            spot = self.ask_spot(tgt, "一二三") or k
            nu = self.call(tgt, spot, "deck", opp)
            if a >= 3:
                nu.bonus += 5000

    def end_phase(self, o):
        """雪子が与えた『ターン終了時、退却させて1枚引く』は使うかどうかを選ぶ"""
        nm = {"FL": "前列左", "FR": "前列右", "BL": "後列左", "BC": "中央後列", "BR": "後列右"}
        for k, u in list(self.circles.items()):
            if u and u.end_draw_retire and not u.doomed:
                if not self.yes_no(f"ターン終了時:{nm[k]}の{cname(u.card)}を退却させて1枚引きますか?(雪子の効果)", "退却させて1枚引く", no="残す",
                                   desc=f"手札 {len(self.hand)}枚・山札 {len(self.deck)}枚", ai=(True, "ふだんのAIはいつも退却させて1枚引きます")):
                    u.end_draw_retire = False
        return super().end_phase(o)

    def _recarm_target(self):
        """リカームを使えるか(対象と空きサークルがあるか)だけ判定。対象と場所は使う時に選ぶ"""
        spot = self.free_front() or self.free_back()
        if not spot:
            return None
        names = {u.card.name for u in self.units()}
        ks = [k for k in self.drop if "team" in k.tags and k.kind == "unit" and k.name not in names and not self.is_g3(k) and k.grade <= self.v_grade]
        return (ks[0], spot) if ks else None

    def recarm_manual(self, opp):
        names = {u.card.name for u in self.units()}
        seen, opts = [], []
        for j, k in enumerate(self.drop):
            if "team" in k.tags and k.kind == "unit" and k.name not in names and not self.is_g3(k) and k.grade <= self.v_grade and k.d["no"] not in seen:
                seen.append(k.d["no"]); opts.append(self.opt_card(k, f"d{j}", f"G{k.grade} パワー{k.power}"))
        if not opts:
            return
        pick = self.ask("eff_pick", "リカーム:ドロップからコールするユニットを選んでください", opts)
        k = self.drop[int(pick[1:])]
        spot = self.ask_spot(k, "リカーム:ユニットのいないサークル", only_empty=True)
        if spot:
            self.drop.remove(k); self.call(k, spot, "drop", opp, in_battle=True)

    # ------------------------------------------------------------ スキル(アタック時)
    def skill_options(self, gauges_skills, opp, vg=False):
        out = []
        for gname, kind, n, pw, ex in gauges_skills:
            if ex == "persona_only" and not self.persona_this_turn:
                continue
            if ex in ("g3only", "sweep_g3") and opp.v_grade < 3:
                continue
            if ex == "g3bonus" and opp.v_grade >= 3:
                pw += 5000
            if ex == "persona_bonus" and self.persona_this_turn:
                pw += 5000
            if kind == "EB" and self.energy < n: continue
            if kind == "SB" and len(self.soul) < n: continue
            if kind not in ("EB", "SB", "EC", "free_retire"): continue
            if ex == "recarm" and not self._recarm_target(): continue
            if ex == "hamaon" and (self.v_grade < 3 or not opp.low_grade_rg()): continue
            if ex == "recycle" and not any(c.kind == "trigger" and c.trig == "crit" for c in self.drop): continue
            cost = {"EB": f"EB{n}", "SB": f"SB{n}", "EC": "コストなし", "free_retire": "ターン終了時に退却"}[kind]
            eff = []
            if pw: eff.append(f"+{pw}")
            if EX_JP.get(ex, ex): eff.append(EX_JP.get(ex, ex))
            if ex == "ec_grade" or kind == "EC": eff.append(f"エネルギー+{self.v_grade}")
            out.append((gname, kind, n, pw, ex, f"{gname}:{cost} {' '.join(eff)}".strip()))
        return out

    def _apply_skill(self, u, opp, atk, kind, n, pw, ex, gname):
        if kind == "EB":
            self.pay_eb(n)
        elif kind == "SB":
            self.pay_sb(n)
        elif kind == "EC":
            self.ec(self.v_grade)
        elif kind == "free_retire" and u is not None:
            u.end_retire = True
        atk.power += pw
        self.L(f"  スキル({gname}) {kind}{n}" + (f" +{pw}" if pw else "") + (f" {EX_JP.get(ex, ex)}" if EX_JP.get(ex, ex) else ""))
        if ex == "lock":
            opp.lock_one()
        elif ex in ("sweep", "sweep_g3"):
            atk.sweep = True
        elif ex == "tarunda":
            base = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[opp.v_grade]
            opp.v_temp -= max(0, base - 8000)
        elif ex == "hamaon":
            opp.retire_low_grade()
        elif ex == "recarm":
            self.recarm_manual(opp)
        elif ex == "recycle":
            c = next(c for c in self.drop if c.kind == "trigger" and c.trig == "crit")
            self.drop.remove(c); self.deck.append(c); self.rng.shuffle(self.deck)
        if ex == "crit":
            atk.crit += 1; atk.crit_ability = True
        elif ex == "CC1":
            self.cc(1)
        elif ex == "retire":
            opp.retire_one()

    def skill_hint(self, opts, key, atk, remaining):
        """ふだんのAIが使うスキル(盤面のコピーでAIに選ばせて、同じものを探す)"""
        import copy as _copy, re as _re
        pc = self.shadow()
        ac = _copy.deepcopy(atk)
        if key == "V":
            pc.use_vg_skill(pc.opp, ac, list(remaining or []))
        else:
            ac.attacker = pc.circles[key]
            pc.use_skill(pc.circles[key], pc.opp, ac)
        line = next((l for l in pc.log if isinstance(l, str) and "スキル(" in l), None)
        if not line:
            return ["none"], "条件が合わないので使いません(エネルギーやソウルを温存)"
        m = _re.search(r"スキル\((.+?)\) ([A-Za-z_]+?)(\d+)", line)
        if not m:
            return None
        kind, n = m.group(2), int(m.group(3))
        txt = line.split(") ", 1)[-1]
        for i, o in enumerate(opts):
            if o[1] == kind and o[2] == n and (not EX_JP.get(o[4]) or EX_JP.get(o[4]) in txt):
                return [str(i)], f"{o[5]}(相手のダメージ・残りのエネルギーやソウルから、一番効果が大きいもの)"
        return None

    def use_skill(self, u, opp, atk):
        if not u.gauge:
            return
        sk = [(u.gauge,) + tuple(s) for s in GAUGES[u.gauge]["skills"]]
        for bname, bd in GAUGES.items():
            if bd.get("evolve") == u.gauge:
                sk += [(bname,) + tuple(s) for s in bd["skills"]]
        opts = self.skill_options(sk, opp)
        if not opts:
            return
        key = next((k for k, x in self.circles.items() if x is u), None)
        self.hint(lambda: self.skill_hint(opts, key, atk, None))
        a = self.ask("skill", f"{cname(u.card)}のアタック:ペルソナの〔スキル〕を1つ選んで使います",
                     [dict(id=str(i), label=o[5]) for i, o in enumerate(opts)] + [dict(id="none", label="使わない")])
        if a == "none":
            return
        gname, kind, n, pw, ex, _ = opts[int(a)]
        self._apply_skill(u, opp, atk, kind, n, pw, ex, gname)

    def use_vg_skill(self, opp, atk, remaining):
        if not self.vgauge:
            return
        sk = []
        for gname in self.vgauge:
            sk += [(gname,) + tuple(s) for s in VG_GAUGES[gname]["skills"]]
        opts = self.skill_options(sk, opp, vg=True)
        if not opts:
            return
        self.hint(lambda: self.skill_hint(opts, "V", atk, remaining))
        a = self.ask("skill", "ヴァンガードのアタック:ペルソナの〔スキル〕を1つ選んで使います",
                     [dict(id=str(i), label=o[5]) for i, o in enumerate(opts)] + [dict(id="none", label="使わない")])
        if a == "none":
            return
        gname, kind, n, pw, ex, _ = opts[int(a)]
        self._apply_skill(None, opp, atk, kind, n, pw, ex, gname)

    # ------------------------------------------------------------ バトル
    def battle(self, opp, game):
        self.restand = set()
        pb = 10000 if self.persona_this_turn else 0
        self.kasumi_used = False
        circ = self.circles
        g = self.v_grade
        bc = circ["BC"]
        bc_attack = False
        if bc and g >= 3 and self.act1_turn == self.turn:
            if self.series == "P3":
                ok = bc.card.d["extra"].get("elizabeth") or "team" in bc.card.tags
            else:
                ok = "team" in bc.card.tags and bc.card.kind == "unit"
            bc_attack = bool(ok) and bc.card.kind == "unit"
        self.pend = {}
        booster_of = {"FL": "BL", "V": None if bc_attack else "BC", "FR": "BR", "BC": None}

        def boost_power(b):
            if not b:
                return 0
            bu = circ[b]
            if bu and bu.card.grade <= 1 and bu.card.kind == "unit" and not bu.rest:
                p = self.unit_power(bu, opp, b) if b != "BC" else bu.card.power + bu.bonus
                if bu.card.no == "010" and self.series == "P3" and self.arcana["死神"] >= 3:
                    p += 5000
                return p
            return 0

        def attackers():
            out = []
            if not self.v_rest:
                out.append("V")
            for k in ("FL", "FR"):
                if circ[k] and not circ[k].rest:
                    out.append(k)
            if bc_attack and circ["BC"] and not circ["BC"].rest:
                out.append("BC")
            return out

        def est_power(spot):
            b = booster_of[spot]
            if spot == "V":
                p = self.vg_power() + pb + self.p3_power + (5000 if self.p4_drive else 0) + sum(VG_GAUGES[x]["flat"] for x in self.vgauge)
            else:
                u = circ[spot]
                p = self.unit_power(u, opp, spot) + (pb if spot in ("FL", "FR") else 0) + (10000 if spot == "BC" and pb else 0)
            return p + self.pend.get(spot, (0, 0))[0], b, boost_power(b)

        while True:
            av = attackers()
            if not av:
                break
            opts = []
            nm = {"V": "ヴァンガード", "FL": "前列左", "FR": "前列右", "BC": "中央後列"}
            for sp in av:
                p, b, bp = est_power(sp)
                unit = self.vg if sp == "V" else circ[sp].card
                sub = f"パワー{p}" + (f"(ブーストで{p + bp})" if bp else "")
                opts.append(dict(id=sp, label=f"{nm[sp]}:{cname(unit)}でアタック", no=unit.d["no"], sub=sub))
            opts.append(dict(id="end", label="バトルを終わる"))
            spot = self.ask("attack", f"アタックするユニットを選んでください(相手ヴァンガード {opp.vg_power()})", opts)
            if spot == "end":
                break
            power, b, bp = est_power(spot)
            # アタックする相手:ヴァンガードか、相手の前列のリアガード
            target = "V"
            orgs = [k for k in ("FL", "FR") if opp.circles.get(k)]
            if orgs:
                full = power + bp
                vp = opp.vg_power()
                topts = [dict(id="V", label=f"ヴァンガード:{cname(opp.vg)}", no=opp.vg.d["no"], sub=f"パワー{vp}" + ("" if full >= vp else "(ブーストしても届かない)"))]
                rgp = {}
                for k in orgs:
                    u0 = opp.circles[k]; rgp[k] = opp.unit_power(u0, self, k)
                    topts.append(dict(id=k, label=f"{nm[k]}のリアガード:{cname(u0.card)}", no=u0.card.d["no"],
                                      sub=f"パワー{rgp[k]}" + ("(ヒットすると退却)" if full >= rgp[k] else "(届かない)")))
                reach = [k for k in orgs if full >= rgp[k]]
                if full < vp and reach:
                    best = max(reach, key=lambda k: (opp.unit_score(opp.circles[k].card), rgp[k]))
                    self.hint(([best], "ヴァンガードには届かないので、届くリアガードを退却させて相手のアタッカーを減らします"))
                else:
                    self.hint((["V"], "ヴァンガードにアタックしてダメージを与えます" if full >= vp else "どこにも届きませんが、ドライブチェックのトリガー次第でヴァンガードに届きます"))
                target = self.ask("target", f"{nm[spot]}でアタックする相手を選んでください", topts)
            power -= self.pend.get(spot, (0, 0))[0]
            boosted = False
            if bp:
                bu = circ[b]
                if self.ask("boost", f"{nm[spot]}を{cname(bu.card)}でブーストしますか?(+{bp})",
                            [dict(id="yes", label=f"ブーストする(+{bp})", no=bu.card.d["no"]), dict(id="no", label="しない")]) == "yes":
                    boosted = True
                    power += bp
            remaining = [(s, None) for s in attackers() if s != spot]
            if spot == "V":
                crit = 2 if self.v_crit2 else 1
                drives = 1 if g < 3 else (3 if (self.p4_drive or self.triple) else 2)
                atk = Attack(power, crit, drives, True, crit_ability=self.v_crit2)
                self.use_vg_skill(opp, atk, remaining)
            else:
                u = circ[spot]
                crit, drives = 1, 0
                if spot == "BC" and self.p5_bc_drive and u.gauge:
                    drives = 1
                atk = Attack(power, crit, drives, False, attacker=u)
                if u.card.d["extra"].get("ryuji") and self.works(u.card) and u.attacks == 0 and g >= 3 \
                        and opp.rg_targets() and len(self.soul) >= 1:
                    if self.ask("ryuji", "竜司:相手の前列リアガードにアタックして、SB1でスタンドしますか?",
                                [dict(id="yes", label="リアガードにアタック"), dict(id="no", label="ヴァンガードにアタック")]) == "yes":
                        self.use_skill(u, opp, atk)
                        idx, rp = max(opp.rg_targets(), key=lambda t: t[1])
                        u.attacks += 1
                        if atk.power >= rp:
                            opp.retire_front(idx)
                            self.L("竜司がリアガードを退却")
                        u.rest = True
                        if boosted:
                            circ[b].rest = True
                        if self.pay_sb(1):
                            u.rest = False
                            self.L("竜司スタンド")
                        continue
                if spot == "BC" and u.card.d["extra"].get("elizabeth") and self.persona_this_turn and self.energy >= 2:
                    if self.ask("eliz_bc", "エリザベス:EB2でパワー+9999・クリティカル+1しますか?",
                                [dict(id="yes", label="使う(EB2)"), dict(id="no", label="使わない")]) == "yes":
                        self.pay_eb(2); atk.power += 9999; atk.crit += 1; atk.crit_ability = True
                else:
                    self.use_skill(u, opp, atk)
                u.attacks += 1
                atk.second = u.attacks >= 2
            atk.power += self.pend.get(spot, (0, 0))[0]
            atk.crit += self.pend.get(spot, (0, 0))[1]
            self.pend.pop(spot, None)
            if spot == "V":
                self.v_rest = True
            elif circ[spot]:
                circ[spot].rest = True
            if boosted and circ[b]:
                circ[b].rest = True
            self.cur_spot = spot
            if target != "V" and opp.circles.get(target) and getattr(atk, "sweep", False):
                self.L("  前列すべてとバトル:アタックはヴァンガードと前列のリアガードすべてが対象になる")
                target = "V"
            if target != "V" and opp.circles.get(target):
                res = self.resolve_rg(opp, atk, remaining, self.pend, target)
            else:
                res = self.resolve(opp, atk, remaining, self.pend, [], 0)
            opp.gzone = []
            if res is not None:
                return res
            if spot in self.restand and spot != "V" and circ[spot]:
                circ[spot].rest = False; self.restand.discard(spot)
                self.L("  オーバートリガーの効果でスタンド")
            if boosted and circ[b] and circ[b].card.no == "010" and self.series == "P3":
                if len(self.deck) > 0 and self.ask("pharos", "ファルロス:ソウルに置いて1枚引きますか?",
                                                   [dict(id="yes", label="ソウルに置いて1枚引く", no="010"), dict(id="no", label="置かない")]) == "yes":
                    self.soul.append(circ[b].card); circ[b] = None; self.draw(1)
                    self.L("  ファルロス:ソウルに置いて1枚引く")
            if spot == "V" and getattr(self, "hayase_turn", -1) == self.turn:
                self.sc(1); self.ec(1)
            if self.deckout or not self.deck:
                self.deck_empty_check()
                return opp
        return None

    def resolve_rg(self, opp, atk, remaining, pend, k):
        """相手のリアガードへのアタック(相手AIはリアガードへのアタックをガードしない)。ヒットしたら退却"""
        u = opp.circles[k]
        self.L("アタック", "V" if atk.is_v else atk.attacker.card, atk.power, f"ドライブ{atk.drives}", "→ 相手のリアガード", cname(u.card))
        for _ in range(atk.drives):
            c = self.take_top()
            if c is None:
                return opp
            self.hand.append(c)
            self.L("  ドライブチェック:", self.short(c))
            if c.kind == "trigger":
                self.apply_trigger(opp, c, atk, remaining, pend, [], 0)
            if self.deckout:
                self.deck_empty_check()
                return opp
        tp = opp.unit_power(u, self, k)
        if atk.power >= tp and opp.circles.get(k) is u:
            opp.drop.append(u.card); opp.circles[k] = None
            self.L(f"  → 最終パワー{atk.power if atk.power < 10**7 else '1億+'} 相手のリアガード{tp} ヒット:{cname(u.card)}を退却")
        else:
            self.L(f"  → 最終パワー{atk.power} 相手のリアガード{tp} 届かず")
        return None

    def trigger_target(self, title, include_current=True):
        nm = {"V": "ヴァンガード", "FL": "前列左", "FR": "前列右", "BC": "中央後列", "BL": "後列左", "BR": "後列右"}
        opts = [dict(id="V", label=f"ヴァンガード:{cname(self.vg)}", no=self.vg.d["no"])]
        for k in ("FL", "FR", "BC", "BL", "BR"):
            u = self.circles[k]
            if u:
                st = "アタック済" if u.rest else "未アタック"
                opts.append(dict(id=k, label=f"{nm[k]}:{cname(u.card)}", no=u.card.d["no"], sub=st))
        cur = getattr(self, "cur_spot", "V")
        for o in opts:
            if o["id"] == cur:
                o["sub"] = "今アタック中"
        rem = [x for x in ("FL", "V", "FR", "BC") if x in getattr(self, "_trig_rem", [])]
        now = self.opp.damage >= 4 or not rem
        nxt = rem[0] if rem else None
        if "クリティカル+1" in title:
            tgt = cur if (now or self.opp.damage >= 3 or nxt is None) else nxt
        else:
            tgt = cur if (now or nxt is None) else nxt
        why = ("今のアタックに乗せます" + ("(相手が4点以上・最後のアタック)" if now else "(相手が3点以上なのでクリティカルは今)")) if tgt == cur else f"まだアタックしていない{self.NMS.get(tgt, tgt)}に乗せて、次のアタックを通しやすくします"
        self.hint(([tgt], why))
        return self.ask("trigger", title, opts)

    def apply_trigger(self, opp, c, atk, remaining, pend, plan, idx):
        self._trig_rem = [s0 for s0, _ in remaining]
        t = c.trig
        cur = getattr(self, "cur_spot", "V")

        def give(sp, p=0, k=0):
            if sp == cur:
                atk.power += p; atk.crit += k
            else:
                a, b = pend.get(sp, (0, 0)); pend[sp] = (a + p, b + k)
                if sp != "V" and self.circles.get(sp):
                    self.circles[sp].bonus += 0
            if p:
                if sp == "V":
                    self.v_disp_tb = getattr(self, "v_disp_tb", 0) + p
                elif self.circles.get(sp):
                    self.circles[sp].disp_tb += p
        if t == "front":
            atk.power += 10000 if cur in ("V", "FL", "FR") else 0
            for s in ("V", "FL", "FR"):
                if s != cur:
                    a, b = pend.get(s, (0, 0)); pend[s] = (a + 10000, b)
            self.L("  フロントトリガー:前列すべて+10000")
        elif t == "over":
            self.hand.remove(c)
            self.draw(1)
            atk.power += 100_000_000
            self.L("  オーバートリガー:+1億、1枚引く")
            cands = [k for k in ("FL", "FR", "BC") if self.circles[k] and not self.circles[k].rest]
            if cands:
                opts = [dict(id=k, label=cname(self.circles[k].card), no=self.circles[k].card.d["no"]) for k in cands] + [dict(id="none", label="選ばない")]
                rem = [x for x in ("FL", "FR", "BC") if x in self._trig_rem and x in cands]
                self.hint(([rem[0]], "次にアタックするリアガードをスタンドさせて、もう一度アタックします") if rem else (["none"], "1枚引いて手札からコールします"))
                k = self.ask("over", "オーバートリガー:アタックしたバトル終了時にスタンドするリアガードを選んでください", opts)
                if k != "none":
                    self.restand.add(k)
            else:
                k = "none"
            if k == "none":
                # 選ばなかったら、1枚引き、手札からユニットカードを1枚まで(R)にコール
                self.draw(1)
                cc = [(j, c) for j, c in enumerate(self.hand) if c.kind == "unit" and 1 <= c.grade <= self.v_grade and not self.is_g3(c)]
                spots_ok = [k2 for k2 in ("FL", "FR", "BL", "BC", "BR") if self.circles[k2] is None]
                if cc and spots_ok:
                    opts2 = [self.opt_card(c, j) for j, c in cc] + [dict(id="none", label="コールしない")]
                    j = self.ask("over_call", "オーバートリガー:1枚引きました。手札からユニットを1枚(R)にコールできます", opts2)
                    if j != "none":
                        c = self.hand[int(j)]
                        spot = self.ask_spot(c, "オーバートリガー", only_empty=True)
                        self.hand.remove(c); self.call(c, spot, "hand", opp, in_battle=True)
        else:
            nmj = {"crit": "クリティカル", "draw": "ドロー", "heal": "ヒール"}[t]
            if t == "crit":
                sp = self.trigger_target("クリティカルトリガー:クリティカル+1を与えるユニットを選んでください")
                give(sp, 0, 1)
                sp = self.trigger_target("クリティカルトリガー:パワー+10000を与えるユニットを選んでください")
                give(sp, 10000, 0)
            else:
                sp = self.trigger_target(f"{nmj}トリガー:パワー+10000を与えるユニットを選んでください")
                give(sp, 10000, 0)
            if t == "draw":
                self.draw(1)
            elif t == "heal":
                if self.damage > 0 and self.damage >= opp.damage:
                    self.drop.append(self.dmg_zone.pop()[0]); self.L("  ヒール:1点回復")

    # ------------------------------------------------------------ ガード
    def guard_hint(self, atk, attacker, defense):
        """ふだんのAI(自己学習したガードの判断)ならどうガードするか"""
        items = PersonaPlayer.guard_items(self, atk)
        self.cur_rem = self.remaining_attacks(attacker, atk)
        hit = self.damage + atk.crit
        lethal = hit >= 6
        passes = atk.drives if lethal else (min(self.PASS5, atk.drives) if hit >= 5 and atk.drives else 0)
        need = atk.power - defense + 1 + passes * 10000
        need0 = atk.power - defense + 1
        try:
            import learn_ai
            if hit >= 2:
                opts, default = learn_ai.guard_options(self, atk, items, need)
                if len(opts) > 1:
                    sf = learn_ai.state_features(self, atk)
                    pr = guard_model()([sf + learn_ai.option_features(self, atk, items, o) for o in opts])
                    ch, gs = opts[max(range(len(opts)), key=lambda i: pr[i])]
                else:
                    ch, gs = default
            else:
                ch, gs = PersonaPlayer.base_choose_guard(self, atk, items, need)
        except Exception:
            ch, gs = PersonaPlayer.base_choose_guard(self, atk, items, need)
        if ch == "take":
            return [], f"ノーガード(受けると{min(hit, 6)}点。手札を次のアタックに残す)"
        if ch == "sentinel":
            i = next(i for i, c in enumerate(self.hand) if c.kind == "sentinel")
            return [f"S{i}"], "守護者で完全ガードします"
        ids, tot = [], 0
        for idx in gs:
            it = items[idx]
            tot += it[2]
            if it[0] == "H":
                i = next((i for i, c in enumerate(self.hand) if c is it[1]), None)
                if i is not None:
                    ids.append(f"H{i}")
            else:
                ids.append(f"I{it[1]}")
        mg = tot - need0
        extra = "(トリガー2枚分多め)" if mg >= 20000 else "(トリガー1枚分多め)" if mg >= 10000 else "(ちょうど止める)"
        return ids, f"シールド{tot}でガード{extra}"

    def _defend(self, atk, attacker):
        self.gzone = []
        defense = self.vg_power()
        if atk.power < defense:
            return 0
        # かすみ
        if self.key == "P5" and not self.kasumi_used and self.v_grade >= 3 and atk.is_v:
            ks = [c for c in self.drop if c.d["extra"].get("kasumi")]
            if ks:
                use = self.damage >= 3 or self.damage + atk.crit >= 6
                self.hint((["yes"], "ダメージ3点以上なのでかすみで受けます") if use else (["no"], "ダメージ2点以下なので温存します"))
                if self.ask("kasumi", f"かすみ:ドロップからバインドしてヴァンガード+10000しますか?(相手{atk.power} / 自分{defense})",
                            [dict(id="yes", label="バインドする(+10000)", no="052"), dict(id="no", label="しない")]) == "yes":
                    self.drop.remove(ks[0]); self.bind.append(ks[0]); self.kasumi_used = True
                    self.v_temp += 10000
                    self.L("  かすみ:ドロップからバインドしてヴァンガード+10000")
                    defense = self.vg_power()
                    if atk.power < defense:
                        return 0
        items = []
        opts = []
        for i, c in enumerate(self.hand):
            if self.is_g3(c):
                continue
            if c.kind == "sentinel":
                free = self.arcana[self.arc(c)] >= 2
                opts.append(dict(id=f"S{i}", label=cname(c), no=c.d["no"], sub="守護者:完全ガード" + ("(コストなし)" if free else "(手札1枚捨て)")))
                continue
            if c.kind == "trigger" and c.trig == "over":
                items.append((f"H{i}", c, 50000, 0)); opts.append(dict(id=f"H{i}", label=cname(c), no=c.d["no"], sub="シールド50000")); continue
            base, boost = self.guard_value(c, atk)
            if base + boost <= 0:
                continue
            can_boost = boost and (len(self.soul) >= 1 or self.energy >= 2)
            sh = base + (boost if can_boost else 0)
            items.append((f"H{i}", c, sh, 1 if can_boost else 0))
            opts.append(dict(id=f"H{i}", label=cname(c), no=c.d["no"], sub=f"シールド{sh}" + ("(SB1かEB2)" if can_boost else "")))
        for k in ("FL", "FR"):
            u = self.circles[k]
            if u and (u.card.grade == 2 or ("velvet" in u.card.tags or "kyoryoku" in u.card.tags) and "komyu" in u.card.tags and self.works(u.card) and self.arcana[self.arc(u.card)] >= 1) \
                    and not u.locked and not u.doomed and not getattr(self, "no_intercept", False) and not getattr(atk, "sweep", False):
                items.append((f"I{k}", k, 5000, 0)); opts.append(dict(id=f"I{k}", label=f"インターセプト:{cname(u.card)}", no=u.card.d["no"], sub="シールド5000"))
        for k in ("BL", "BR", "BC"):
            if getattr(self, "no_intercept", False):
                break
            u = self.circles[k]
            if u and (("kyoryoku" in u.card.tags or "velvet" in u.card.tags and "komyu" in u.card.tags) and self.works(u.card) and self.arcana[self.arc(u.card)] >= 1 and not u.doomed
                      or self.bc_intercept_ok(k, u)):
                items.append((f"I{k}", k, 5000, 0)); opts.append(dict(id=f"I{k}", label=f"インターセプト(後列):{cname(u.card)}", no=u.card.d["no"], sub="シールド5000"))
        need = atk.power - defense + 1
        info = dict(atk=atk.power, crit=atk.crit, drives=atk.drives, vg=defense, need=need, attacker=("V" if atk.is_v else "R"),
                    attacker_no=(attacker.vg.d["no"] if atk.is_v and hasattr(attacker, "vg") else (atk.attacker.card.d["no"] if atk.attacker else "")))
        if not opts:
            return 0
        self.hint(lambda: self.guard_hint(atk, attacker, defense))
        pick = self.ask("guard", f"ガード:相手のアタック {atk.power}(☆{atk.crit}" + (f"・ドライブ{atk.drives}" if atk.drives else "") + f")/ 自分 {defense}",
                        opts, multi=True, desc=f"止めるにはシールド{need}以上(5000単位なら{-(-need // 5000) * 5000})。何も選ばなければノーガード。"
                        + (("このアタックは前列すべてとバトル:アタックされている前列のリアガードはインターセプトできず、パワー" + str(atk.power) + "以下の前列のリアガード("
                            + "・".join(f"{cname(self.circles[k].card)} {self.unit_power(self.circles[k], self.opp, k)}" for k in ("FL", "FR") if self.circles[k]) + ")は退却します(ガードはヴァンガードにだけ)。")
                           if getattr(atk, "sweep", False) and any(self.circles[k] for k in ("FL", "FR")) else ""), extra=info)
        pick = list(pick)
        sent = [p for p in pick if p.startswith("S")]
        if sent:
            i = int(sent[0][1:])
            s = self.hand[i]
            a = self.arc(s)
            free = self.arcana[a] >= 2
            self.gzone = [dict(no=s.d["no"], n=self.short(s), s="完全ガード")]
            self.L("  守護者:", self.short(s), "(コストなし)" if free else "(手札1枚捨て)")
            self.hand.remove(s); self.drop.append(s)
            self.place_marker(a)
            if not free and self.hand:
                opts2 = [self.opt_card(c, j) for j, c in enumerate(self.hand)]
                j = int(self.ask("sent_cost", "守護者のコスト:手札から捨てるカードを選んでください", opts2))
                c = self.hand.pop(j); self.drop.append(c)
            return "perfect"
        chosen = [it for it in items if it[0] in pick]
        total = 0
        hand_cards = [it[1] for it in chosen if it[0].startswith("H")]
        for it in chosen:
            if it[0].startswith("I"):
                cc = self.circles[it[1]].card
                self.gzone.append(dict(no=cc.d["no"], n=self.short(cc), s=it[2], ic=True))
            else:
                self.gzone.append(dict(no=it[1].d["no"], n=self.short(it[1]), s=it[2]))
        self.L("  ガード内訳:", ", ".join((f"インターセプト{it[1]}" if it[0].startswith("I") else f"{self.short(it[1])}({it[2]})") for it in chosen) or "ノーガード")
        for it in chosen:
            total += it[2]
            if it[0].startswith("I"):
                self.drop.append(self.circles[it[1]].card); self.circles[it[1]] = None
        for c in hand_cards:
            it = next(x for x in chosen if x[1] is c)
            self.hand.remove(c)
            (self.soul if c.d["no"] in ("036", "045", "055") else self.drop).append(c)
            if it[3]:
                if not self.pay_sb(1):
                    self.pay_eb(2)
            if c.kind == "trigger" and c.d["extra"]["guard"][2] == "ec1":
                self.ec(1)
        return total


class AutoHuman(HumanPlayer):
    """先読みの試行用:人の側を、答えが尽きた所からふだんのAIで動かす(進行中のバトルなどは簡単な既定の答えで進める)"""
    def ask(self, kind, title, options, multi=False, desc="", minn=None, maxn=None, extra=None):
        if multi:
            n = minn or 0
            return [o["id"] for o in options[:n]]
        ids = [o["id"] for o in options]
        if kind == "main" and "end" in ids:
            return "end"
        return ids[0]


for _n, _f in list(HumanPlayer.__dict__.items()):
    if callable(_f) and hasattr(PersonaPlayer, _n) and _n not in ("__init__", "L", "ask"):
        setattr(AutoHuman, _n, getattr(PersonaPlayer, _n))


class AIPlayer(PersonaPlayer):
    """相手AI。strong=True なら、重要なガードの判断で先読みする(候補ごとに試合の続きを何十回も試す)"""
    ROLLOUTS = 16
    ROUNDS = 3
    MIN_STAKE = 4

    def __init__(self, *a, strong=False, cache=None, ctx=None, script=None, **k):
        super().__init__(*a, **k)
        self.strong = strong == "search" or strong is True
        self.level = strong if isinstance(strong, str) else ("search" if strong else "normal")
        self.cache = cache if cache is not None else []     # これまでのガードの判断 [{qi, c, s}]
        self.ctx = ctx                                       # 先読みのために試合をやり直す材料(cfg, answers)
        self.script = script                                 # 試行中:決まっている判断(None なら普通の対戦)
        self.gi = 0
        self.r_seed = 0

    def L(self, *a):
        if self.log is not None:
            self.log.append(f"[相手 T{self.turn}] " + " ".join(str(x) for x in a))

    def choose_guard(self, atk, items, need):
        k = self.gi; self.gi += 1
        if self.script is not None:                          # 試行中
            if k < len(self.script):
                ch = self.script[k]
                if k == len(self.script) - 1:
                    self.determinize()
                return ch[0], list(ch[1])
            return super().choose_guard(atk, items, need)
        if self.level == "learn":                            # 自己学習したモデルで選ぶ(すぐに決まり、毎回同じ答えなので記録しない)
            return self.learned_guard(atk, items, need)
        if k < len(self.cache):                              # もう決めてある判断
            e = self.cache[k]
            if e.get("m"):
                self.L(e["m"])
            return e["c"], list(e["s"])
        d = super().choose_guard(atk, items, need)
        pick = (d[0], sorted(d[1] or []))
        self.last_msg = ""
        if self.strong and self.damage + atk.crit >= self.MIN_STAKE:
            pick = self.search(atk, items, need, pick)
        self.cache.append(dict(qi=self.opp.qi, c=pick[0], s=list(pick[1]), m=self.last_msg))
        return pick[0], list(pick[1])

    def learned_guard(self, atk, items, need):
        import learn_ai
        if self.damage + atk.crit < 2:
            return super().choose_guard(atk, items, need)
        opts, default = learn_ai.guard_options(self, atk, items, need)
        if len(opts) <= 1:
            return default[0], list(default[1])
        sf = learn_ai.state_features(self, atk)
        pr = guard_model()([sf + learn_ai.option_features(self, atk, items, o) for o in opts])
        pick = opts[max(range(len(opts)), key=lambda i: pr[i])]
        return pick[0], list(pick[1])

    def search(self, atk, items, need, default):
        need0 = atk.power - self.vg_power() + 1
        opts = [("take", [])]
        for ex in ((0, 10000, 20000) if atk.drives else (0,)):
            cost, gs = self.knap(items, need0 + ex)
            if cost < 10 ** 9:
                opts.append(("guard", sorted(gs)))
        if self.sentinel_cost() < 10 ** 9:
            opts.append(("sentinel", []))
        opts.append(default)
        uniq = []
        for o in opts:
            if o not in uniq:
                uniq.append(o)
        if len(uniq) <= 1:
            return default
        cfg, answers = self.ctx
        prev = [(e["c"], e["s"]) for e in self.cache]
        alive = list(range(len(uniq))); wins = [0] * len(uniq); tries = [0] * len(uniq)
        for rnd in range(self.ROUNDS):
            seeds = [(len(prev) * 100 + rnd) * 1000 + r for r in range(self.ROLLOUTS)]
            for i in alive:
                for sd in seeds:
                    wins[i] += rollout(cfg, answers, prev + [uniq[i]], sd); tries[i] += 1
            if len(alive) <= 1:
                break
            alive.sort(key=lambda i: -wins[i] / tries[i])
            alive = alive[:max(1, (len(alive) + 1) // 2)]
        best = max(alive, key=lambda i: wins[i] / tries[i])
        lab = lambda o: {"take": "ノーガード", "sentinel": "守護者"}.get(o[0], f"ガード{len(o[1])}枚")
        self.last_msg = "  (先読み) " + " / ".join(f"{lab(o)} {wins[i]}/{tries[i]}" for i, o in enumerate(uniq)) + " → " + lab(uniq[best])
        self.L(self.last_msg)
        return uniq[best]

    def determinize(self):
        """見えない情報をシャッフル:自分と相手の山札の順番、相手(人)の手札"""
        rng = random.Random(self.r_seed)
        o = self.opp
        rng.shuffle(self.deck)
        pool = o.hand + o.deck
        rng.shuffle(pool)
        n = len(o.hand)
        o.hand, o.deck = pool[:n], pool[n:]
        self.rng.seed(self.r_seed + 1)


_GUARD_MODEL = None


def guard_model():
    global _GUARD_MODEL
    if _GUARD_MODEL is None:
        import os, learn_model
        _GUARD_MODEL = learn_model.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "learn_guard_model.json"))
    return _GUARD_MODEL


def rollout(cfg, answers, script, seed):
    """先読みの試行:人の答えとAIの判断(script)の通りに最初からやり直し、その先はAI同士で最後まで。AIが勝てば1"""
    rng = random.Random(cfg["seed"])
    me = HumanPlayer(cfg["me"], FINAL[cfg["me"]], rng, None, list(answers))
    me.autopilot_ok = True
    ai = AIPlayer(cfg["opp"], FINAL[cfg["opp"]], rng, None, script=script)
    ai.r_seed = seed * 7919 + 13
    me.opp, ai.opp = ai, me
    players = [me, ai] if cfg["first"] else [ai, me]
    try:
        me.mulligan()
        for t in range(40):
            res = players[t % 2].take_turn(players[(t + 1) % 2], t % 2 == 0, None)
            if res is not None:
                return int(res is ai)
        return int(ai.damage < me.damage)
    except Exception as e:
        ROLLOUT_ERRORS.append(repr(e))
        return 0


ROLLOUT_ERRORS = []


# ================================================================ 盤面の書き出し
def vbon(p):
    out = []
    if not getattr(p, "in_turn", False):
        # 相手のターン中:ダメージチェックのトリガー・かすみなどで上がった分(表示用。パワーにはすでに入っている)
        if 0 < p.v_temp < 10 ** 7:
            out.append(["このターン", p.v_temp, True])
        return out
    if p.persona_this_turn:
        out.append(["ペルソナライド", 10000])
    fl = sum(VG_GAUGES[x]["flat"] for x in p.vgauge)
    if fl:
        out.append(["ペルソナゲージ", fl])
    if p.p3_power:
        out.append(["G3の起動", p.p3_power])
    if p.p4_drive:
        out.append(["G3の起動", 5000])
    if getattr(p, "v_disp_tb", 0):
        out.append(["トリガー", p.v_disp_tb])
    return out


def snap(p, hide_hand=False):
    circ = {}
    opp = getattr(p, "opp", None)
    for k, u in p.circles.items():
        if u:
            pw = p.unit_power(u, opp, k) if opp is not None else u.card.power
            bon = []
            if getattr(p, "in_turn", False):
                if k in ("FL", "FR") and p.persona_this_turn:
                    bon.append(["ペルソナライド", 10000])
                if k == "BC" and p.persona_this_turn and p.act1_turn == p.turn and p.v_grade >= 3:
                    bon.append(["中央後列", 10000])
                if u.disp_tb:
                    bon.append(["トリガー", u.disp_tb])
            pw += sum(b[1] for b in bon)
            circ[k] = {"n": short_name(u.card), "g": u.gauge or "", "p": min(pw, 10 ** 9), "bon": bon, "gr": u.card.grade,
                       "no": u.card.d["no"], "r": u.rest, "lk": bool(u.locked), "dm": bool(u.doomed)}
    d = {"name": p.key, "vg": short_name(p.vg) if p.v_grade >= 3 else f"G{p.v_grade}主人公",
         "vgr": p.v_grade, "vp": min(p.vg_power() + sum(b[1] for b in vbon(p) if len(b) < 3), 10 ** 9), "vbon": [b[:2] for b in vbon(p)], "c": circ,
         "vno": p.vg.d["no"], "vr": p.v_rest, "vg_g": list(p.vgauge),
         "dmg": [[short_name(c), f, c.d["no"]] for c, f in p.dmg_zone], "soul": len(p.soul), "en": p.energy,
         "deck": len(p.deck), "drop": len(p.drop), "dropno": [c.d["no"] for c in p.drop], "soulno": [c.d["no"] for c in p.soul], "arc": dict(p.arcana),
         "gz": list(getattr(p, "gzone", [])), "hn": len(p.hand), "turn": p.turn}
    if not hide_hand:
        d["deckno"] = sorted(c.d["no"] for c in p.deck)          # 順番がわからないように番号順
        d["hno"] = [c.d["no"] for c in p.hand]
        d["hand"] = [short_name(c) for c in p.hand]
        d["htype"] = [c.trig if c.kind == "trigger" else ("sentinel" if c.kind == "sentinel" else f"g{c.grade}") for c in p.hand]
    return d


# ================================================================ 進行
def run(cfg, answers, cache=None):
    """cfg: {me, opp, seed, first, ai("normal"/"strong")} answers: [人の選択...] cache: 相手AIのガードの判断(先読みの結果)"""
    rng = random.Random(cfg["seed"])
    log = []
    cache = [e for e in (cache or []) if e["qi"] <= len(answers)]      # 1手戻した時:それより後の判断は捨てる
    me = HumanPlayer(cfg["me"], FINAL[cfg["me"]], rng, log, list(answers))
    ai = AIPlayer(cfg["opp"], FINAL[cfg["opp"]], rng, log, strong={"strong": "search", "search": "search", "learn": "learn"}.get(cfg.get("ai"), False), cache=cache, ctx=(cfg, list(answers)))
    me.opp, ai.opp = ai, me
    players = [me, ai] if cfg["first"] else [ai, me]
    state = dict(winner=None, turn_no=0)
    try:
        me.mulligan()
        for t in range(40):
            state["turn_no"] = t
            atk = players[t % 2]
            dfn = players[(t + 1) % 2]
            res = atk.take_turn(dfn, t % 2 == 0, None)
            if res is not None:
                raise GameOver("me" if res is me else "ai")
        raise GameOver("me" if me.damage < ai.damage else "ai")
    except NeedInput as e:
        return dict(status="ask", q=e.q, me=snap(me), ai=snap(ai, hide_hand=True), log=log, n=len(answers),
                    active="me" if getattr(me, "in_turn", False) else "ai", cache=ai.cache)
    except GameOver as e:
        why = ""
        loser = ai if e.winner == "me" else me
        if getattr(loser, "deckout", False):
            why = "山札切れ"
        elif loser.damage >= 6:
            why = "ダメージ6点"
        return dict(status="over", winner=e.winner, why=why, me=snap(me), ai=snap(ai), log=log, n=len(answers), cache=ai.cache)


def step(cfg_json, answers_json, cache_json="[]"):
    return json.dumps(run(json.loads(cfg_json), json.loads(answers_json), json.loads(cache_json)), ensure_ascii=False)
