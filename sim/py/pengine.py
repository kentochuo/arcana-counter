"""
カード単位エンジン(ペルソナ30th用)

ペルソナ側はカード1枚ずつの効果を再現する(pcards.py)。
相手の環境デッキは従来どおりプロファイル(engine.py の DeckProfile)で動かす。

再現している主なもの
- ライドデッキの主人公の効果(G1:1ドロー+チームトークン、G2:山札7枚から1枚回収)
- ペルソナライド、G3主人公の2つの【起】(CB1で2体コール/作品ごとの強化)
- ペルソナゲージ(登場時に獲得、+5000、アタック時スキル)、アルカナマーカー(最大3)、
  P3の覚醒(バインドで覚醒)、P4の転生・P5の進化(アルカナ3つで自動)
- キャラごとのコール時効果(ドロー、退却、サーチ、EC/CC、ソウル)とターン終了時の効果
- 中央後列のアタック(G3の効果)、エリザベス、竜司のスタンド、明智、舞子、伏見
- ガード:守護者(アルカナ3つでコスト無し)、トリガーオーダーの固定シールド、条件付きヒール、
  オーバー50000、インターセプト(前列G2/アルカナ1つ以上の協力者)
"""
from __future__ import annotations

import os
import random
from collections import Counter

from pcards import C, GAUGES, VG_GAUGES, VG_BASE, RIDE_LINES, arcana_of, works_in
from engine import DeckProfile, D_TRIG_SHIELD, TRIGGERS

TEAM_TOKEN = {"P3": "S.E.E.S.", "P3P": "S.E.E.S.", "P4": "自称特別捜査隊", "P5": "心の怪盗団"}
SENTINEL_NO = {"P3": "060", "P3P": "060", "P4": "067", "P5": "074"}


class Card:
    __slots__ = ("d", "uid")

    def __init__(self, no, uid):
        self.d = C[no]
        self.uid = uid

    def __getattr__(self, k):
        return self.d[k]

    def __repr__(self):
        return self.d["name"][:10]

    def __deepcopy__(self, memo):          # カードは変化しないので、盤面をコピーする時も同じものを使う
        return self


def short_name(c):
    n = c.d["name"]
    if c.d["kind"] == "trigger":
        return f"{n}[{c.d['trig']}]"
    return n.split(" ")[-1] if " " in n else n


class Unit:
    __slots__ = ("card", "gauge", "bonus", "attacks", "can_attack_back", "end_retire", "end_hand",
                 "end_draw_retire", "maiko_cb", "called_turn", "rest", "locked", "doomed", "lock_rest", "disp_tb")

    def __init__(self, card):
        self.card = card
        self.rest = False
        self.locked = False   # 次の相手ターン:スタンドできない・インターセプトできない
        self.lock_rest = False  # ロックされた時にレストしていた(=次のターンもレストのまま)
        self.disp_tb = 0        # 表示用:このターンにトリガーで上がったパワー
        self.doomed = False   # シールド0、持ち主のターン終了時に退却
        self.gauge = None
        self.bonus = 0
        self.attacks = 0
        self.can_attack_back = False
        self.end_retire = False
        self.end_hand = False
        self.end_draw_retire = False
        self.maiko_cb = False
        self.called_turn = -1


EX_JP = {"": "", "CC1": "カウンターチャージ1", "crit": "クリティカル+1", "debuff": "相手パワー低下", "ec_grade": "エネルギーチャージ",
         "g3bonus": "", "g3only": "", "guard2": "", "hamaon": "ハマオン(G1以下を退却)", "lock": "ロック(アタック不可)",
         "persona_bonus": "", "persona_only": "", "recarm": "リカーム(ドロップから再コール)", "recycle": "再利用",
         "retire": "リアガード退却", "sweep": "前列すべてとバトル", "sweep_g3": "前列すべてとバトル", "tarunda": "タルンダ(相手VGのパワー低下)"}


class Attack:
    __slots__ = ("power", "crit", "drives", "is_v", "restrict", "crit_ability", "second", "attacker", "sweep", "nosent", "guard", "dleft")

    def __init__(self, power, crit, drives, is_v, restrict=False, crit_ability=False, second=False, attacker=None):
        self.power, self.crit, self.drives, self.is_v = power, crit, drives, is_v
        self.restrict, self.crit_ability, self.second, self.attacker = restrict, crit_ability, second, attacker
        self.sweep = False
        self.nosent = False     # 守護者をコールできない(アルファルド“幻影”など)
        self.guard = 0          # このアタックに相手が出したガード値("perfect" なら完全ガード)。トリガーの振り分けに使う
        self.dleft = 0          # 残りのドライブチェックの回数


def trig_to_self(atk, opp, has_next, old_now):
    """トリガーのパワー/☆を、今アタックしているユニットに乗せるか(True)、次にアタックするユニットに回すか(False)。
    相手のガードを見てから決める(2026/11/10 総点検):
    - 完全ガードされた → 全部次へ / すでにヒットする → パワーは次へ、☆は自分 / あと1枚でヒット → 自分
    - あと2枚必要 → 相手4点以上でドライブが2回以上残っていれば賭ける、それ以外は次へ"""
    if not has_next:
        return True, True
    g = atk.guard
    if g == "perfect":
        return False, False
    gap = opp.vg_power() + g - atk.power
    if gap <= 0:
        return False, True
    n = -(-gap // 10000)
    if n == 1:
        return True, True
    if n == 2 and atk.dleft >= 2 and old_now:
        return True, True
    return False, False


# =============================================================== 相手(プロファイル型)
class ProfilePlayer:
    """engine.py のプレイヤーを、カード単位エンジンの相手として動くように移植したもの。"""

    def L(self, *a):
        if self.log is not None:
            self.log.append(f"[{self.prof.name} T{self.turn}] " + " ".join(str(x) for x in a))

    def __init__(self, prof: DeckProfile, rng, log=None):
        self.log = log
        from engine import make_card
        self.prof, self.rng = prof, rng
        self.deck = prof.build()
        rng.shuffle(self.deck)
        self.hand, self.damage, self.soul, self.energy, self.v_grade = [], 0, 0, 0, 0
        self.front, self.back = [None, None], [None, None, None]
        self.turn, self.persona_this_turn, self.v_temp = 0, False, 0
        self.rested = set()
        self.locked, self.doomed = set(), set()   # 前列の列番号
        self.deckout, self.gain_carry, self.heal_carry, self.defense_used, self.online_turns = False, 0.0, 0.0, False, 0
        self.draw(5)
        self.mulligan()

    # ---- 基本
    def draw(self, n=1):
        for _ in range(n):
            if not self.deck:
                self.deckout = True
                return
            self.hand.append(self.deck.pop())

    def take_top(self):
        if not self.deck:
            self.deckout = True
            return None
        return self.deck.pop()

    def vg_power(self):
        return {0: 6000, 1: 8000, 2: 10000, 3: 13000}[self.v_grade] + self.v_temp

    def mulligan(self):
        keep, back, pk, sk = [], [], 0, 0
        for c in self.hand:
            if c.kind in ("g1", "g2"):
                keep.append(c)
            elif c.persona and pk < 1:
                keep.append(c); pk += 1
            elif c.kind == "sentinel" and sk < 1:
                keep.append(c); sk += 1
            else:
                back.append(c)
        self.hand = keep
        self.deck += back
        self.rng.shuffle(self.deck)
        self.draw(len(back))

    def discard_cheapest(self):
        if not self.hand:
            return
        def value(c):
            if c.kind == "sentinel": return 100
            if c.persona and self.v_grade >= 2: return 90
            if c.is_trigger: return 50
            if c.kind == "g3": return 10
            if c.kind == "g2": return 30
            return 25
        self.hand.remove(min(self.hand, key=value))

    def retire_one(self):
        for i in range(2):
            if self.front[i]:
                self.front[i] = None
                return True
        for i in (0, 2, 1):
            if self.back[i]:
                self.back[i] = None
                return True
        return False

    def lock_one(self):
        for i in range(2):
            if self.front[i] and i not in self.locked:
                self.locked.add(i); return True
        return False

    def doom_one(self):
        for i in range(2):
            if self.front[i] and i not in self.doomed:
                self.doomed.add(i); return True
        return False

    def low_grade_rg(self):
        return any(self.back) or any(u and u[0].grade <= 1 for u in self.front)

    def retire_low_grade(self):
        for i in (1, 0, 2):
            if self.back[i]:
                self.back[i] = None; return True
        return self.retire_one()

    def end_cleanup(self):
        for i in self.doomed:
            self.front[i] = None
        self.locked, self.doomed = set(), set()

    def rg_targets(self):
        """竜司などが殴れるリアガード(前列)のパワー"""
        return [(i, u[0].power) for i, u in enumerate(self.front) if u]

    def retire_front(self, i):
        self.front[i] = None

    def rg_value(self, i):
        return 30 if self.front[i] else 0

    def defend_rg(self, i, atk, attacker):
        if not self.front[i] or atk.power < self.front[i][0].power:
            return True
        self.front[i] = None           # 簡易モデルはリアガードを守らない
        return False

    # ---- ターン
    def take_turn(self, o, first, game):
        p = self.prof
        self.turn += 1
        self.rested = set()
        self.persona_this_turn = False
        self.v_temp = 0
        o.v_temp = 0
        self.draw(1)
        if self.deckout:
            return o
        if self.turn == 1:
            if not first:
                self.energy = min(10, self.energy + 3)
        else:
            self.energy = min(10, self.energy + 3)
        if self.v_grade < 3:
            self.discard_cheapest()
            self.v_grade += 1
            self.soul += 1
            if self.v_grade == 1 and not first:
                self.draw(1)
        else:
            pc = next((c for c in self.hand if c.persona), None)
            if pc:
                self.hand.remove(pc); self.soul += 1; self.draw(1); self.persona_this_turn = True
        online = self.turn >= p.online_turn and self.v_grade >= 3
        if online:
            self.online_turns += 1
            self.gain_carry += p.card_gain
            while self.gain_carry >= 1:
                self.draw(1); self.gain_carry -= 1
            while self.gain_carry <= -1 and self.hand:
                self.discard_cheapest(); self.gain_carry += 1
            self.heal_carry += p.self_heal
            while self.heal_carry >= 1 and self.damage > 0:
                self.damage -= 1; self.heal_carry -= 1
            r = p.retire_per_turn
            while r > 0:
                if r >= 1 or self.rng.random() < r:
                    o.retire_one()
                r -= 1
        if self.deckout:
            return o
        self.call_units(o, first)
        self.L(f"=== ターン開始 G{self.v_grade}{' ペルソナライド' if self.persona_this_turn else ''} 手札{len(self.hand)} ダメージ{self.damage} 前列{[u[0].kind for u in self.front if u]} 後列{[u[0].kind for u in self.back if u]}")
        if first and self.turn == 1:
            self.end_cleanup()
            return None
        res = self.battle(o, online, game)
        self.end_cleanup()
        return res

    def call_units(self, o, first):
        g = self.v_grade
        want_front = 2 if g >= 2 else (1 if o.damage <= 1 and g >= 1 and not (first and self.turn == 1) else 0)
        want_back = 3 if g >= 3 else (1 if g >= 2 else 0)
        reserve = 2 if self.damage >= 4 else 1

        def callable_cards(mg):
            pin = sum(1 for k in self.hand if k.persona)
            return sorted([c for c in self.hand if c.kind in ("g1", "g2", "g3") and c.grade <= mg
                           and not (c.persona and g >= 2 and pin <= 1)], key=lambda c: -c.power)
        for i in range(2):
            if want_front <= sum(1 for u in self.front if u):
                break
            if self.front[i] is None:
                cands = [c for c in callable_cards(g) if c.kind in ("g2", "g3")] or callable_cards(g)
                if cands and len(self.hand) > reserve:
                    c = cands[0]; self.hand.remove(c); self.front[i] = [c, 0]
        for i in (1, 0, 2):
            if want_back <= sum(1 for u in self.back if u):
                break
            if self.back[i] is None:
                cands = [c for c in callable_cards(g) if c.kind == "g1"]
                if cands and len(self.hand) > reserve:
                    c = cands[0]; self.hand.remove(c); self.back[i] = [c, 0]

    def battle(self, o, online, game):
        p = self.prof
        pb = 10000 if self.persona_this_turn else 0
        o.defense_used = False
        growth = p.power_growth * max(0, self.online_turns - 1) if online else 0
        lines = []
        if self.front[0]: lines.append(("R", 0))
        lines.append(("V", 1))
        if self.front[1]: lines.append(("R", 2))
        n_extra = 0
        if online and p.extra_attacks > 0:
            n_extra = int(p.extra_attacks) + (1 if self.rng.random() < p.extra_attacks - int(p.extra_attacks) else 0)
        pend_pow, pend_crit = {0: 0, 1: 0, 2: 0}, {0: 0, 1: 0, 2: 0}

        def booster(col):
            b = self.back[col]
            return b[0].power if b else 0
        for k, (kind, col) in enumerate(lines):
            if kind == "V":
                power = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[self.v_grade] + booster(1) + pb + (p.v_power if online else 0) + self.v_temp + growth
                crit = 1
                if online and p.v_crit > 0:
                    crit += int(p.v_crit) + (1 if self.rng.random() < p.v_crit - int(p.v_crit) else 0)
                drives = 2 if self.v_grade >= 3 else 1
                if online and self.rng.random() < p.v_drive_bonus:
                    drives += 1
            else:
                u = self.front[0 if col == 0 else 1]
                if u is None or (0 if col == 0 else 1) in self.locked:
                    continue
                power = u[0].power + booster(col) + pb + (p.r_power if online else 0) + growth
                crit, drives = 1, 0
            power += pend_pow[col]; crit += pend_crit[col]
            pend_pow[col] = 0; pend_crit[col] = 0
            self.rested.add(("FL", "V", "FR")[col])
            if self.back[col]:
                self.rested.add(("BL", "BC", "BR")[col])
            res = self.resolve(o, Attack(power, crit, drives, kind == "V", restrict=(kind == "V" and online),
                                         crit_ability=(kind == "V" and crit >= 2)),
                               pend_pow, pend_crit, lines[k + 1:], game)
            o.gzone = []
            if res is not None:
                return res
            if self.deckout:
                return o
        for _ in range(n_extra):
            res = self.resolve(o, Attack(p.extra_power + pb + growth, p.extra_crit, 0, False, second=True),
                               pend_pow, pend_crit, [], game)
            o.gzone = []
            if res is not None:
                return res
        return None

    def resolve(self, o, atk, pend_pow, pend_crit, remaining, game):
        self.L(f"アタック {'V' if atk.is_v else 'R'} パワー{atk.power} ☆{atk.crit} ドライブ{atk.drives}")
        guarded = o.defend(atk, self)
        for _ in range(atk.drives):
            c = self.take_top()
            if c is None:
                return o
            self.hand.append(c)
            self.L("  ドライブチェック:", c.kind)
            if c.is_trigger:
                self.apply_trigger(o, c, atk, remaining, pend_pow, pend_crit)
        self.L(f"  → 最終パワー{atk.power if atk.power < 10**7 else '1億+'} ☆{atk.crit} 相手VG{o.vg_power() if o.vg_power() < 10**7 else '1億+'}+ガード{guarded}")
        if guarded == "perfect":
            return None
        if atk.power >= o.vg_power() + guarded:
            for _ in range(atk.crit):
                if o.damage_check(self):
                    return self
        return None

    def apply_trigger(self, o, c, atk, remaining, pend_pow, pend_crit):
        now = o.damage >= 4 or not remaining
        if c.kind == "front":
            atk.power += 10000
            for _, col in remaining:
                pend_pow[col] += 10000
        elif c.kind == "over":
            atk.power += 100_000_000
            self.draw(1)
        else:
            if now:
                atk.power += 10000
            else:
                pend_pow[remaining[0][1]] += 10000
            if c.kind == "crit":
                if now or o.damage >= 3:
                    atk.crit += 1
                else:
                    pend_crit[remaining[0][1]] += 1
            elif c.kind == "draw":
                self.draw(1)
            elif c.kind == "heal":
                if self.damage > 0 and self.damage >= o.damage:
                    self.damage -= 1

    def damage_check(self, attacker):
        c = self.take_top()
        if c is None:
            return True
        self.damage += 1
        self.L("  ダメージチェック:", c.kind, f"→ {self.damage}点")
        if c.is_trigger:
            if c.kind == "heal" and self.damage >= attacker.damage and self.damage < 6:
                self.damage -= 1
            elif c.kind == "draw":
                self.draw(1)
            elif c.kind == "over":
                self.draw(1); self.v_temp += 100_000_000
            if c.kind in ("crit", "front", "heal", "draw"):
                self.v_temp += 10000
        return self.damage >= 6

    def defend(self, atk, attacker):
        self.gzone = []
        p_att = getattr(attacker, "prof", None)
        defense = self.vg_power()
        if atk.power < defense:
            return 0
        hit = self.damage + atk.crit
        if hit >= 6:
            passes = atk.drives
        elif self.damage >= 4 and atk.drives:
            passes = 1
        elif self.damage >= 3 and atk.drives == 0:
            passes = 0
        else:
            if self.damage <= 2 or (self.damage == 3 and atk.drives == 0 and len(self.hand) < 5):
                return 0
            passes = 0
        need = atk.power - defense + 1 + passes * 10000
        free = 0
        if self.prof.defense_shield and self.v_grade >= 3 and not self.defense_used and need > 0 and passes > 0:
            free = self.prof.defense_shield; self.defense_used = True; need -= free
            if need <= 0:
                return free
        sents = [c for c in self.hand if c.kind == "sentinel"]
        if sents and (hit >= 6 or (self.damage >= 4 and atk.drives)) and len(self.hand) >= 2 and need > 10000:
            self.hand.remove(sents[0]); self.discard_cheapest()
            self.gzone = [dict(no=None, n="守護者", s="完全ガード")]
            return "perfect"
        pool = []
        for c in self.hand:
            if c.kind == "sentinel":
                continue
            if c.is_trigger:
                if c.kind == "over":
                    if self.prof.over_guard and hit >= 6:
                        pool.append((c, self.prof.over_guard, 3))
                    continue
                bonus = c.boost if (c.boost and (self.soul >= 1 or self.energy >= 2)) else 0
                pool.append((c, c.shield + bonus, 2 if bonus else 1))
            elif c.shield > 0:
                pool.append((c, c.shield, 0))
        pool.sort(key=lambda x: (-x[1], x[2]))
        chosen, total = [], 0
        for c, s, _ in pool:
            if total >= need:
                break
            chosen.append((c, s)); total += s
        inter = 0
        if total < need:
            for i in range(2):
                u = self.front[i]
                if u and u[0].kind == "g2":
                    inter = 5000; self.front[i] = None
                    self.gzone.append(dict(no=None, n="G2", s=5000, ic=True))
                    break
        if total + inter < need and hit < 6:
            return inter + free
        for c, s in chosen:
            self.gzone.append(dict(no=None, n=c.kind, s=s))
            self.hand.remove(c)
            if c.is_trigger and c.kind != "over" and s > c.shield:
                if self.energy >= 2: self.energy -= 2
                elif self.soul >= 1: self.soul -= 1
        return total + inter + free


# =============================================================== ペルソナ(カード単位)
# デッキごとのプレイ方針の調整値(tune.json があれば読み込む。tune_params.py で探す)
TUNE = {}
try:
    import json as _json, os as _os
    _tp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tune.json")
    if _os.path.exists(_tp):
        TUNE = _json.load(open(_tp, encoding="utf-8"))
except Exception:
    TUNE = {}


class PersonaPlayer:
    GUARD_TABLE = {1: 8, 2: 14, 3: 35, 4: 65, 5: 110}  # ダメージを受ける時の損(ガード方針:2026/10/23 A/Bで最良だった方針B)
    KEEP_G3 = 1                                        # マリガンで残すG3の枚数
    RESERVE_ADJ = 0                                    # 手札に残す枚数の調整(+で温存、-で展開)
    ESOUL_CC_FIRST = False                             # エリザベスのソウル起動で、裏のダメージがあればCC1を優先する
    USE_ESOUL = True                                   # エリザベスのソウル起動を使うか
    RES = (2, 3, 4, 5)                                 # 手札に残す枚数(ダメージ0〜2 / 3 / 4 / 5点)
    SENT_COST = 40                                     # 守護者を使う時の損
    SENT_SPARE = 8                                     # 守護者が2枚以上ある時の損の割引
    PASS5 = 1                                          # 5点目になる時にドライブ何枚分のトリガーを想定してガードするか
    EARLY = {1: (0, 0), 2: (2, 2)}                     # ヴァンガードがG1/G2の時に出すリアガードの上限(前列, 後列) 2026/10/28の比較で G1は出さない・G2は前2後2 が最良
    EARLY_G1_SECOND = (1, 0)                           # 後攻のG1のターンは前列に1体だけ出して殴る(先攻/後攻別の比較で最良)
    G1_STOP_DMG = 2                                    # G1の時、相手のダメージがこの点数以上なら前列に出さない
    G1_BACK_BC = False                                 # G1の時の後列はヴァンガードの後ろ(中央後列)に置く
    BACK_ORDER = ("BL", "BR", "BC")                    # G1/G2の時に後列を埋める順番
    TAKE_LAST = 1.0                                    # このターン最後のアタックを受ける時の、ダメージの損の倍率
    TAKE_ONE = 1.0                                     # 相手のアタックがあと1回残っている時の倍率
    TAKE_MORE = 1.0                                    # 相手のアタックがあと2回以上残っている時の倍率
    YUKIKO_SMART = False                               # 雪子の付与:相手を選び、ターン終了時の「退却して1枚引く」を必要な時だけ使う
    YUKIKO_HAND = 3                                    # (YUKIKO_SMART時)手札がこの枚数以下なら退却して引く
    BC_G2 = True                                       # G3の時、中央後列にチームのG2を置いてアタックさせる(False:G1を置いてヴァンガードをブースト)
    BC_G2_P3 = True                                   # P3/P3P:エリザベスが無い時、中央後列にS.E.E.S.のG2を置いてアタックさせる
    DECK_SAFE = 10                                     # 山札がこの枚数以下なら、G3の【起】1(山札から2体コール)や任意のドローを控える
    NAOTO_FOCUS = os.environ.get("NAOTO_FOCUS") == "1"  # 直斗を意識したプレイ(2026/11/08):運命を貯めて転生、メギドラオン優先
    GEN_DRAW_MIN = 7                                   # エネルギージェネレーター【起】(ターン1回 EB7で1枚引く)を使うエネルギー量(自分のターン終了前)
    # ---- 2026/11/10 総点検で追加したつまみ
    ORDER_EARLY = ""                                   # G2までのターンのアタック順(""ならATTACK_ORDERと同じ)
    ATTACK_ORDER = "RVR"                               # アタックの順番 RVR:リア→ヴァンガード→リア(従来) / VRR:ヴァンガードから / RRV:ヴァンガードを最後
    RG_ATK = 1                                         # リアガードへのアタック 0:しない 1:ヴァンガードに届かない時だけ 2:価値の高いリアガードも狙う
    RG_ATK_DMG = 2                                     # (RG_ATK=2)相手のダメージがこの点数以下の時に狙う
    RG_ATK_VAL = 45                                    # (RG_ATK=2)この価値以上のリアガードを狙う(手札1枚≒30)
    RG_GUARD_K = 0.75                                  # 自分のリアガードがアタックされた時、ガードの損がユニットの価値×この値より小さければ守る
    NO_EXACT_V = 1                                     # ドライブがこの回数以上のヴァンガードのアタックを、ちょうどの枚数ではガードしない(0で無効)
    ELIZ_RESERVE = 1                                   # 1: 相手4点以上でも、エリザベスのメギドラオン用にエネルギー2を残す(試験用)
    RECARM_ATTACK = 2                                  # リカームで出したユニットはそのターンにアタックする。2: 相手がG2までのうちだけエネルギーの予備を残さず使う(1: いつでも優先、0: 旧動作=アタックしない)
    RECARM_PLAN = 1                                    # 1: G2のターンにリカームを仕込む(G2をライドコストで捨て、前列を1つ空けて、ゆかりのリカームで出して殴る)
    TRIG_SMART = 1                                     # 1: 相手のガードを見てからトリガーの振り分けを決める(2026/11/10)
    MULL_SENT = 1                                      # マリガンで残す守護者の枚数
    MULL_UNITS = 9                                     # マリガンで残すG1/G2ユニットの上限
    MULL_HEAL = 0                                      # マリガンで残すヒールトリガーの枚数
    FRONT_SWAP = 6000                                  # 前列を上書きコールする時、今のユニットよりこの値以上強ければ入れ替える
    def __init__(self, deck_key, recipe, rng, log=None, attrs=None):
        """recipe: {カード番号: 枚数} 山札50枚(トリガー込み)"""
        self.key, self.rng, self.log = deck_key, rng, log
        for k, v in TUNE.get(deck_key, {}).items():
            if k == "GUARD_TABLE":
                v = {int(a): b for a, b in v.items()}
            elif k == "RES":
                v = tuple(v)
            elif k == "EARLY":
                v = {int(a): tuple(b) for a, b in v.items()}
            elif k == "EARLY_G1_SECOND" and v is not None:
                v = tuple(v)
            setattr(self, k, v)
        for k, v in (attrs or {}).items():       # 試験用:プレイ方針のつまみを上書き(ab_test.py)
            setattr(self, k, v)
        self.series = "P3" if deck_key in ("P3", "P3P") else deck_key
        self.deck = []
        uid = 0
        for no, n in recipe.items():
            for _ in range(n):
                self.deck.append(Card(no, uid)); uid += 1
        assert len(self.deck) == 50, f"{deck_key}: {len(self.deck)}枚"
        rng.shuffle(self.deck)
        self.ride_line = [Card(no, 1000 + i) for i, no in enumerate(RIDE_LINES[deck_key])]
        self.g3_no = RIDE_LINES[deck_key][3]
        self.vg = self.ride_line[0]
        self.v_grade = 0
        self.hand, self.drop, self.soul, self.bind = [], [], [], []
        self.dmg_zone = []      # [card, faceup]
        self.energy = 0
        self.circles = {k: None for k in ("FL", "FR", "BL", "BC", "BR")}
        self.arcana = Counter()
        self.team_token = False
        self.awakened = set()
        self.turn = 0
        self.persona_this_turn = False
        self.persona_ever = False
        self.unique_used = False
        self.v_temp = 0
        self.deckout = False
        self.once = set()          # このターンに解決した同名能力
        self.act1_turn = -99       # G3の【起】1を使ったターン(中央後列アタック許可)
        self.p4_drive = False
        self.p3_power = 0
        self.p5_bc_drive = False
        self.v_crit2 = False
        self.triple = False
        self.cb_discount = 0
        self.kasumi_used = False
        self.vgauge = []           # ヴァンガードのペルソナゲージ
        self.igor_heal_used = False
        self.marie_pr_used = False
        self.v_rest = False
        self.eliz_soul_turn = -1
        self.focus_th = deck_key in ("P3", "P3P") and recipe.get("010", 0) + recipe.get("064", 0) >= 4
        if self.focus_th:
            self.KEEP_G3 = max(self.KEEP_G3, 2)
        self.draw(5)
        self.mulligan()

    # ---------------------------------------------------------- 基本
    def short(self, c):
        return short_name(c)

    def L(self, *a):
        if self.log is not None:
            self.log.append(f"[{self.key} T{self.turn}] " + " ".join(str(x) for x in a))

    @property
    def damage(self):
        return len(self.dmg_zone)

    def cb_avail(self):
        return sum(1 for c, f in self.dmg_zone if f)

    def pay_cb(self, n):
        n = max(0, n)
        if self.cb_avail() < n:
            return False
        if n:
            self.L(f"  CB{n}(残り{self.cb_avail() - n})")
        for e in self.dmg_zone:
            if n == 0:
                break
            if e[1]:
                e[1] = False; n -= 1
        return True

    def cc(self, n):
        for e in self.dmg_zone:
            if n == 0:
                break
            if not e[1]:
                e[1] = True; n -= 1

    def pay_sb(self, n):
        if len(self.soul) < n:
            return False
        if n:
            self.L(f"  SB{n}(ソウル残り{len(self.soul) - n})")
        for _ in range(n):
            self.drop.append(self.soul.pop(0))
        return True

    def pay_eb(self, n):
        if self.energy < n:
            return False
        self.energy -= n
        if n:
            self.L(f"  EB{n}(エネルギー残り{self.energy})")
        return True

    def ec(self, n):
        self.energy = min(10, self.energy + n)

    def draw(self, n=1):
        for _ in range(n):
            if not self.deck:
                self.deckout = True
                return
            self.hand.append(self.deck.pop())
        self.deck_empty_check()

    def deck_empty_check(self, log=True):
        """総合ルール1.2.2.2:山札が0枚なら次のチェックタイミングで敗北"""
        if not self.deck:
            self.deckout = True
        if self.deckout and log and not getattr(self, "_dmsg", False):
            self._dmsg = True
            self.L("  山札が0枚になった(山札切れで敗北)")
        return self.deckout

    def take_top(self):
        if not self.deck:
            self.deckout = True
            return None
        c = self.deck.pop()
        self.deck_empty_check(log=False)
        return c

    def sc(self, n):
        for _ in range(n):
            c = self.take_top()
            if c:
                self.soul.append(c)

    def vg_power(self):
        return {0: 6000, 1: 8000, 2: 10000, 3: 13000}[self.v_grade] + self.v_temp

    def works(self, card):
        return works_in(card.d, self.key)

    def arc(self, card):
        return arcana_of(card.d, self.key)

    def units(self):
        return [u for u in self.circles.values() if u]

    def is_g3(self, c):
        return c.d["no"] == self.g3_no

    def mulligan(self):
        keep, back, g3k, sk, uk, hk = [], [], 0, 0, 0, 0
        for c in sorted(self.hand, key=lambda c: -self.unit_score(c) if c.kind == "unit" else 0):
            if c.kind == "unit" and c.grade in (1, 2) and not self.is_g3(c) and uk < self.MULL_UNITS:
                keep.append(c); uk += 1
            elif self.is_g3(c) and g3k < self.KEEP_G3:
                keep.append(c); g3k += 1
            elif c.kind == "sentinel" and sk < self.MULL_SENT:
                keep.append(c); sk += 1
            elif c.kind == "trigger" and c.trig == "heal" and hk < self.MULL_HEAL:
                keep.append(c); hk += 1
            else:
                back.append(c)
        self.hand = keep
        self.deck += back
        self.rng.shuffle(self.deck)
        self.draw(len(back))

    def card_value(self, c):
        """手札から捨てる/ソウルに置く時の価値(低いほど先に手放す)"""
        if c.kind == "sentinel": return 100
        if c.kind == "order": return 1 if getattr(self, "regalis_used", False) else 88
        if self.is_g3(c):
            if sum(1 for k in self.hand if self.is_g3(k)) <= 1: return 95
            return 90 if getattr(self, "focus_th", False) and "メサイア" not in self.vgauge else 20
        if c.kind == "trigger":
            if c.trig == "over": return 85
            return 45 + c.shield / 1000
        if c.d["no"] == "024" and self.key in ("P3", "P3P"): return 70
        if c.grade == 2: return 35
        return 30

    def _has_recarm(self, c):
        return bool(c.kind == "unit" and c.persona and any(sk[3] == "recarm" for sk in GAUGES[c.persona].get("skills", [])))

    def decide_skip_pref(self, pref):
        """ライドコストでエリザベス/イゴール(捨てるとサーチ)を捨てられるなら、そちらを優先する"""
        return bool(pref and any(pref(c) for c in self.hand))

    def discard_one(self, to="drop", prefer=None):
        if not self.hand:
            return None
        cands = self.hand
        if prefer:
            pc = [c for c in self.hand if prefer(c)]
            if pc:
                cands = pc
        c = min(cands, key=self.card_value)
        self.hand.remove(c)
        (self.soul if to == "soul" else self.drop).append(c)
        return c

    def look_top(self, n, pred, score, pick_kind=None):
        """山札の上n枚から条件に合う最良の1枚を取り出す(残りはシャッフル)"""
        top = self.deck[-n:]
        cands = [c for c in top if pred(c)]
        if not cands:
            self.rng.shuffle(self.deck)
            return None
        best = max(cands, key=score)
        if pick_kind and len({c.name for c in cands}) > 1:
            best = self.choose_card(pick_kind, cands, best)
        self.deck.remove(best)
        self.rng.shuffle(self.deck)
        return best

    def search_deck(self, pred, score):
        cands = [c for c in self.deck if pred(c)]
        if not cands:
            return None
        best = max(cands, key=score)
        self.deck.remove(best)
        self.rng.shuffle(self.deck)
        return best

    # ---------------------------------------------------------- アルカナ
    def place_marker(self, a):
        if not self.team_token or not a:
            return
        if a == "月" and getattr(self, "moon_lock", False):
            return
        if self.arcana[a] < 3:
            self.arcana[a] += 1
            self.L(f"  アルカナ「{a}」{self.arcana[a]}つ目")
            self.check_evolve()

    def check_evolve(self, opp=None):
        opp = opp or getattr(self, "opp", None)
        if self.series not in ("P4", "P5") or opp is None or opp.v_grade < 3:
            return
        for gname in list(self.vgauge):
            ev = VG_GAUGES[gname].get("evolve")
            if ev and ev not in self.vgauge and self.arcana["愚者"] >= 3:
                self.vgauge.append(ev)
                self.L("ヴァンガードのペルソナ 転生/進化", gname, "→", ev)
        for u in self.units():
            if u.gauge and "evolve" in GAUGES[u.gauge] and self.arcana[self.arc(u.card)] >= 3:
                self.L("転生/進化", u.card, u.gauge, "→", GAUGES[u.gauge]["evolve"])
                u.gauge = GAUGES[u.gauge]["evolve"]

    # ---------------------------------------------------------- 登場
    def call(self, card, circle, from_where="hand", opp=None, in_battle=False):
        old = self.circles[circle]
        if old:
            self.drop.append(old.card)
        u = Unit(card)
        u.called_turn = self.turn
        self.circles[circle] = u
        self.L("コール", card, "→", circle, f"({from_where})")
        self.on_appear(u, circle, opp, in_battle)
        return u

    def on_appear(self, u, circle, opp, in_battle=False):
        c = u.card
        if not self.works(c) and c.d["series"] not in ("any",):
            return
        # アルカナマーカー(コミュ/コープ)
        if "komyu" in c.tags and c.kind == "unit":
            self.place_marker(self.arc(c))
        # ファルロス:死神3つでタナトスをヴァンガードのゲージに
        if c.d["no"] == "010" and self.series == "P3" and self.vgauge and "タナトス" not in self.vgauge \
                and self.arcana["死神"] >= 3:
            self.vgauge.append("タナトス"); self.L("  ファルロス:ヴァンガードにタナトス")
        # ペルソナ
        if c.persona and self.v_grade >= 1:
            for k, o in self.circles.items():
                if o and o is not u and o.card.name == c.name:
                    self.drop.append(o.card); self.circles[k] = None
            g = c.persona
            if self.series == "P3" and c.name in self.awakened:
                g = GAUGES[g]["evolve"]
            u.gauge = g
            self.check_evolve(opp)
        # テオドア(ドロップ):別名のベルベットルームの住人が登場した時、CB1かEB3で(R)にコール
        if "velvet" in c.tags and c.no != "082":
            th = [x for x in self.drop if x.no == "082"]
            spot = self.free_back()
            if th and spot and not any(x and x.card.no == "082" for x in self.circles.values()):
                if (self.energy >= 6 and self.pay_eb(3)) or (self.cb_avail() >= 2 and self.pay_cb(1)):
                    self.drop.remove(th[0]); self.call(th[0], spot, "drop", opp, in_battle)
        # コール時効果
        eff = c.on_call
        if eff:
            self.effect(eff, u, circle, opp, in_battle)

    def effect(self, eff, u, circle, opp, in_battle):
        name = u.card.name
        c = u.card
        self.L(f"  登場時効果[{eff}] {self.short(u.card)}")
        if eff == "ace_search":
            if not self.unique_used and not in_battle:
                self.unique_used = True
                g = self.search_deck(lambda k: self.is_g3(k), lambda k: 0)
                if g:
                    self.hand.append(g); self.L("エースでG3をサーチ")
            return
        once_types = {"hayase", "cb_discard_opp", "sc1", "fushimi", "cb_self_5000", "maiko", "eb1_lock", "ec1", "eb2_cc1",
                      "eb1_zero_retire", "eb3_retire", "nanako", "marie", "search_g3_soul", "cb1_ec3", "draw_soul",
                      "drop_to_soul_g1", "loot", "takemi", "togo", "kawakami", "jc_buff", "sae", "chihaya", "iwai", "ayato"}
        if eff in once_types or (u.card.grade == 1 and eff in ("drop_to_soul",)):
            if name in self.once:
                return
            self.once.add(name)
        a = self.arcana[self.arc(u.card)] if self.arc(u.card) else 0
        if eff == "cb_draw":
            if self.cb_avail() >= 1 and self.decide("cb_draw", c, self.cb_avail() >= 2 or self.act1_turn == self.turn):
                self.pay_cb(1); self.draw(1)
        elif eff == "cb_retire":
            if opp and self.cb_avail() >= 1 and opp.rg_targets() and self.decide("cb_retire", c, True) and opp.retire_one():
                self.pay_cb(1)
        elif eff == "cb_recall_amada":
            if self.cb_avail() >= 1 and not in_battle:                 # 「あなたのメインフェイズに」登場した時だけ
                cands = [k for k in self.drop if k.name in ("無敗のボクサー 真田 明彦", "義理堅き忠犬 コロマル")
                         and all(not x or x.card.name != k.name for x in self.circles.values())]
                spot = self.free_front()
                if cands and spot and self.decide("cb_recall", c, True):
                    self.pay_cb(1); k = cands[0]; self.drop.remove(k); self.call(k, spot, "drop", opp)
        elif eff == "cb_discard_opp":
            if opp and opp.hand and (a >= 3 or (self.cb_avail() >= 1 and self.decide("cb_discard_opp", c, self.cb_avail() >= 2))):
                if a >= 3 or self.pay_cb(1):
                    opp.hand.remove(self.rng.choice(opp.hand)) if hasattr(opp, "prof") else opp.discard_one()
                    self.L("  神木:相手の手札を1枚捨てさせた" + ("(太陽3つ:コストなし)" if a >= 3 else ""))
        elif eff == "sc1":
            self.sc(2 if a >= 3 and u.card.no in ("111", "115", "117") else 1)
            if a >= 3 and u.card.no == "062":
                self.ec(2)
            if a >= 3 and u.card.no == "101":
                self.v_temp += 5000; self.L("  無達:塔3つ、ヴァンガード+5000")
        elif eff == "fushimi":
            if len(self.hand) >= 2:
                self.discard_one("soul")
                if a >= 3 and self.v_grade >= 3:
                    self.triple = True; self.L("伏見:トリプルドライブ")
                else:
                    self.draw(1)
        elif eff == "cb_self_5000":
            if a >= 3 or (self.cb_avail() >= 1 and self.decide("cb_self_5000", c, self.cb_avail() >= 2)):
                if a >= 3 or self.pay_cb(1):
                    u.bonus += 5000
        elif eff == "maiko":
            if a >= 3:
                if self.pay_cb(1):
                    u.end_hand = True; self.v_crit2 = True; self.L("舞子:ヴァンガードのクリティカル2")
            elif self.cb_avail() >= 1 and self.decide("maiko", c, self.cb_avail() >= 2):
                self.pay_cb(1); u.end_hand = True
        elif eff in ("eb1_lock",):
            if opp and self.energy >= 1 and opp.rg_targets() and self.decide("eb1_lock", c, self.energy >= 3) and self.pay_eb(1):
                opp.lock_one()
                if a >= 3 and u.card.no == "093":
                    self.ec(2)
                # ベベ(節制3つ・ターン1回):さらにEB1で、ドロップのクリティカルトリガーを1枚山札に戻してシャッフル(2026/11/08 追加)
                if a >= 3 and u.card.no == "099" and getattr(self, "bebe_turn", -1) != self.turn and self.energy >= 1:
                    cr = [x for x in self.drop if x.kind == "trigger" and x.trig == "crit"]
                    if cr and self.decide("bebe_crit", c, True) and self.pay_eb(1):
                        self.bebe_turn = self.turn
                        self.drop.remove(cr[0]); self.deck.append(cr[0]); self.rng.shuffle(self.deck)
                        self.L("  ベベ:節制3つ、クリティカルを山札に戻す")
        elif eff == "kuma":
            ks = [k for k in self.drop if k.d["no"] == "119"]
            if ks and self.energy >= 3 and self.pay_eb(1):
                self.drop.remove(ks[0]); self.hand.append(ks[0])
        elif eff == "ryotaro":
            # 足立(ドロップ):バトルフェイズ以外に遼太郎が登場した時、SB1で(R)にコール(同名1ターン1回)
            ad = [x for x in self.drop if x.d["no"] == "068"]
            spot = self.free_back() or self.free_front()
            if ad and not in_battle and "adachi" not in self.once and spot and len(self.soul) >= 2 and self.pay_sb(1):
                self.once.add("adachi")
                self.drop.remove(ad[0])
                au = self.call(ad[0], spot, "drop", opp)
                # 道化師3つ:同名の効果で登場したターン、相手の手札をランダムに1枚捨てる(ファイト中1回)
                if self.arcana["道化師"] >= 3 and not getattr(self, "adachi_used", False) and opp and opp.hand:
                    self.adachi_used = True
                    opp.hand.remove(self.rng.choice(opp.hand)) if hasattr(opp, "prof") else opp.discard_one()
                    self.L("  足立:相手の手札をランダムに1枚捨てる")
            k = self.look_top(5, lambda x: x.d["no"] == "015", lambda x: 0)
            if k:
                self.hand.append(k)
                if a < 3:
                    for kk, x in self.circles.items():
                        if x is u:
                            self.circles[kk] = None; self.deck.insert(0, u.card); break
        elif eff == "ec1":
            self.ec(2 if a >= 3 and u.card.no in ("094", "112", "116") else 1)
            if a >= 3 and u.card.no == "075" and self.drop:
                self.soul.append(self.drop.pop())
            if a >= 3 and u.card.no == "113":
                self.v_temp += 5000; self.L("  小西:刑死者3つ、ヴァンガード+5000")
        elif eff == "ec2":
            self.ec(2)
        elif eff == "eb2_cc1":
            if a >= 3 or (self.energy >= 2 and self.decide("eb2_cc1", c, True) and self.pay_eb(2)):
                self.cc(1)
        elif eff == "eb1_zero_retire":
            # 相手リアガードのシールドを0にし、相手のターン終了時に退却させる(相手の次のターンはまだアタックできる)
            if opp and self.energy >= 1 and opp.rg_targets() and self.decide("eb1_zero", c, self.energy >= 3) and self.pay_eb(1):
                n = 2 if a >= 3 else 1
                for _ in range(n):
                    opp.doom_one()
        elif eff == "eb3_retire":
            if opp and self.energy >= 3 and opp.rg_targets() and self.decide("eb3_retire", c, self.energy >= 5) and self.pay_eb(3):
                opp.retire_one()
                if a >= 3:
                    self.v_temp += 10000
        elif eff == "chie_search":
            # 〈自称特別捜査隊〉なら主人公G3・守護者(りせ)も選べる。手札にG3がなければG3、次に守護者を優先
            need_sent = not any(c.kind == "sentinel" for c in self.hand)
            need_g3 = not any(self.is_g3(c) for c in self.hand)
            def csc(x):
                if self.is_g3(x): return 2_000_000 if need_g3 else -2_000_000      # ユニットの評価(1万台)より必ず大きく
                if x.kind == "sentinel": return 1_000_000 if need_sent else -1_000_000
                return self.unit_score(x)
            k = self.look_top(7, lambda x: "team" in x.tags and x.kind in ("unit", "sentinel") and x.name != name, csc, pick_kind="chie")
            if k:
                self.hand.append(k)
                if not (self.cb_avail() >= 2 and self.pay_cb(1)):
                    self.discard_one("soul")
        elif eff == "yukiko":
            others = [x for x in self.units() if x is not u and "team" in x.card.tags and x.card.kind == "unit"]
            if others:
                if self.YUKIKO_SMART:
                    # ロック・シールド0で次のターン役に立たないユニット、なければ一番弱いユニットに与える
                    tgt = min(others, key=lambda x: (not (x.locked or x.doomed), self.unit_score(x.card)))
                else:
                    tgt = others[0]
                tgt.end_draw_retire = True
        elif eff == "nanako":
            k = self.look_top(7, lambda x: x.kind == "unit" and (self.is_g3(x) or x.no in ("012", "040")),
                              lambda x: 2 if self.is_g3(x) and not any(self.is_g3(h) for h in self.hand) else (1 if x.no == "012" else 0))
            if k:
                self.hand.append(k)
        elif eff == "marie":
            gs = [x for x in self.units() if x.gauge]
            if gs:
                best = max(gs, key=lambda x: self.arcana[self.arc(x.card)] if self.arcana[self.arc(x.card)] < 3 else -1)
                self.place_marker(self.arc(best.card))
        elif eff == "search_g3_soul":
            need = not any(self.is_g3(h) for h in self.hand)
            k = self.look_top(7, lambda x: self.is_g3(x), lambda x: 0) if need else None
            if k:
                self.hand.append(k)
                if u.card.no == "042":
                    if a >= 3 and self.drop:
                        self.soul.append(self.drop.pop())
                    else:
                        self.discard_one("soul")
                        if a <= 2:
                            self.moon_lock = True      # このファイト中、月のマーカーを置けない
                elif not (a >= 3 and self.pay_eb(3)):
                    self.discard_one("soul")
        elif eff == "ayato":
            src = [c for c in self.soul + self.drop if c.d["no"] == "010"]
            if src and self.team_token:
                c = src[0]; (self.soul if c in self.soul else self.drop).remove(c); self.bind.append(c)
                self.place_marker("死神")
                self.ec(2 if (self.key == "P3P" and self.arcana["運命"] >= 3) else 1)
                self.L("  綾時:ファルロスをバインドして死神+エネルギー")
        elif eff == "cb1_ec3":
            if a >= 3 or (self.cb_avail() >= 1 and self.decide("cb1_ec3", c, self.cb_avail() >= 2 and self.energy <= 6)):
                if a >= 3 or self.pay_cb(1):
                    self.ec(3)
        elif eff == "draw_soul":
            self.draw(1)
            if a >= 3 and u.card.no == "127" and self.cb_avail() >= 1 and self.pay_cb(1):
                pass                                   # 吉田(太陽3つ):CB1でソウルに置かない
            else:
                self.discard_one("soul")
                if a >= 3 and u.card.no == "083":
                    self.v_temp += 5000; self.L("  マーガレット:女帝3つ、ヴァンガード+5000")
        elif eff == "drop_to_soul":
            if self.drop:
                self.soul.append(self.drop.pop())
        elif eff == "loot":
            self.draw(1); self.discard_one("drop")
        elif eff == "morgana":
            # 〈心の怪盗団〉なら守護者(双葉)も選べる。手札に守護者がなければ守護者を優先
            # 主人公G3(ペルソナライド用)も〈心の怪盗団〉なので選べる。手札にG3がなければG3、次に守護者を優先
            need_sent = not any(c.kind == "sentinel" for c in self.hand)
            need_g3 = not any(self.is_g3(c) for c in self.hand)
            def msc(x):
                if self.is_g3(x): return 2_000_000 if need_g3 else -2_000_000      # ユニットの評価(1万台)より必ず大きく
                if x.kind == "sentinel": return 1_000_000 if need_sent else -1_000_000
                return self.unit_score(x)
            k = self.look_top(5, lambda x: "team" in x.tags and x.kind in ("unit", "sentinel") and x.name != name, msc, pick_kind="morgana")
            if k:
                self.hand.append(k); self.discard_one("soul")
        elif eff == "takemi":
            if a >= 3 or len(self.soul) >= 2:
                cands = [k for k in self.drop if "team" in k.tags and k.kind == "unit" and k.grade <= self.v_grade]
                spot = self.free_front() or (self.free_back() if cands and cands[0].grade == 1 else None)
                if cands and spot and (a >= 3 or self.pay_sb(1)):
                    k = max(cands, key=self.unit_score); self.drop.remove(k); self.call(k, spot, "drop", opp)
        elif eff == "togo":
            g2 = [(k, x) for k, x in self.circles.items() if x and x.card.grade == 2 and "team" in x.card.tags and x.card.kind == "unit"]
            if g2 and self.decide("togo", c, True):
                k, x = min(g2, key=lambda kx: self.unit_score(kx[1].card))
                tgt = self.search_deck(lambda c: c.grade == 2 and "team" in c.tags and c.kind == "unit" and c.name != x.card.name and not c.d["extra"].get("x"),
                                       lambda c: self.unit_score(c))
                if tgt:
                    self.soul.append(x.card); self.circles[k] = None
                    nu = self.call(tgt, k, "deck", opp)
                    if a >= 3:
                        nu.bonus += 5000
        elif eff == "kawakami":
            if a >= 3 or self.pay_sb(1):
                self.cb_discount = 1
        elif eff == "jc_buff":
            gs = [x for x in self.units() if x.gauge and x.card.kind == "unit"]
            if gs and self.pay_eb(1):
                n = 2 if a >= 3 else 1
                for x in sorted(gs, key=lambda x: -x.card.power)[:n]:
                    x.bonus += 5000
        elif eff == "sae":
            if self.cb_avail() >= 1 and self.decide("sae", c, self.cb_avail() >= 2):
                want_g3 = a >= 3 and not any(self.is_g3(h) for h in self.hand)
                k = self.search_deck(lambda x: (self.is_g3(x) if want_g3 else x.no == "020"), lambda x: 0)
                if k:
                    self.pay_cb(1); self.hand.append(k)
        elif eff == "hayase":
            if a >= 3:
                self.hayase_turn = self.turn
        elif eff == "chihaya":
            n = 2 if a >= 3 else 1
            top = self.deck[-n:]
            keep = [c for c in top if c.kind == "trigger"]
            for c in top:
                if c.kind != "trigger":
                    self.deck.remove(c); self.soul.append(c)
        elif eff == "iwai":
            if a >= 3 or self.pay_sb(1):
                tgt = [x for k, x in self.circles.items() if x and k in ("FL", "FR") and "team" in x.card.tags]
                if tgt:
                    tgt[0].bonus += 10000

    # ---------------------------------------------------------- 盤面の判断
    def unit_score(self, c):
        """前列アタッカーとしての評価"""
        s = c.power
        if self.NAOTO_FOCUS and c.no == "041" and self.key == "P4" and self.arcana["運命"] < 3:
            s += 8000                           # 直斗を意識:運命を3つ貯めて転生させる
        if c.persona:
            s += 6000
            if self.series == "P3" and c.name in self.awakened:
                s += 8000
            a = self.arc(c)
            if a and self.arcana[a] in (1, 2):
                s += 1500 * self.arcana[a]      # 同じキャラを出し直して3つに近づける
        if c.on_call in ("cb_draw",) and self.cb_avail() >= 1:
            s += 3000
        if c.on_call == "cb_retire" and self.cb_avail() >= 1:
            s += 2000
        if c.d["extra"].get("akechi"):
            s += 2500
        if c.d["extra"].get("ryuji"):
            s += 2500
        return s

    def back_score(self, c, circle):
        if c.d["extra"].get("elizabeth") and circle == "BC" and self.key in ("P3", "P3P") and self.v_grade >= 3:
            return 40000
        if getattr(self, "focus_th", False) and "タナトス" not in self.vgauge:
            if c.no == "010":
                return 30000 if self.arcana["死神"] >= 3 else 16000
            if c.no == "064" and any(k.no == "010" for k in self.soul + self.drop):
                return 15000
        s = c.power if c.grade <= 1 else 0
        if c.on_call and c.on_call not in ("ace_search",):
            s += 2000
        if c.kind == "sentinel":
            s = -1
        return s

    def free_front(self):
        for k in ("FL", "FR"):
            if self.circles[k] is None:
                return k
        return None

    def free_back(self):
        for k in ("BC", "BL", "BR"):
            if self.circles[k] is None:
                return k
        return None

    def hand_reserve(self, opp):
        r = self.RES[0] + self.RESERVE_ADJ
        if self.damage >= 3: r = self.RES[1]
        if self.damage >= 4: r = self.RES[2]
        if self.damage >= 5: r = self.RES[3]
        return r

    # ---------------------------------------------------------- ターン
    def take_turn(self, o, first, game):
        self.opp = o
        self.first = first
        self.turn += 1
        self.persona_this_turn = False
        self.v_temp = 0
        o.v_temp = 0
        self.once = set()
        self.p4_drive = False
        self.p3_power = 0
        self.p5_bc_drive = False
        self.v_crit2 = False
        self.triple = False
        self.cb_discount = 0
        for u in self.units():
            u.bonus, u.attacks, u.end_retire, u.end_hand, u.end_draw_retire, u.maiko_cb = 0, 0, False, False, False, False
            u.rest = bool(u.locked and u.lock_rest)       # ロックされていたらスタンドできない
            u.disp_tb = 0
        self.v_disp_tb = 0
        self.in_turn, o.in_turn = True, False
        self.v_rest = False
        self.draw(1)
        if self.deckout:
            return o
        if self.turn == 1:
            if not first:
                self.ec(3)
        else:
            self.ec(3)
        self.L(f"=== ターン開始 {'先攻' if first else '後攻'} ダメージ{self.damage}(表{self.cb_avail()}) ソウル{len(self.soul)} エネルギー{self.energy} 山札{len(self.deck)}")
        self.L("  手札:", ", ".join(self.short(c) for c in self.hand))
        self.ride_phase(first, o)
        if self.deck_empty_check():
            return o
        self.check_evolve(o)
        self.main_phase(o)
        if self.deck_empty_check():
            return o
        if first and self.turn == 1:
            self.end_phase(o)
            return None
        res = self.battle(o, game)
        if res is not None:
            return res
        self.end_phase(o)
        return None

    def ride_phase(self, first, o):
        if self.v_grade < 3:
            # ライドコスト:エリザベス/イゴールを優先して捨てる
            def pref(c):
                ex = c.d["extra"]
                if ex.get("discard_search") == "persona_ability":
                    return True
                if ex.get("discard_search") == "velvet":
                    return True
                return False
            if self.RECARM_PLAN and self.v_grade == 1 and any(self._has_recarm(c) for c in self.hand) and not self.decide_skip_pref(pref):
                # リカームの仕込み:G2にライドするターン、ゆかりが手札にあれば、別名のチームのG2をコストで捨てる(ゆかりのリカームで前列に戻す)
                yn = next(c.name for c in self.hand if self._has_recarm(c))
                if not any("team" in k.tags and k.kind == "unit" and k.grade == 2 and k.name != yn for k in self.drop):
                    _g2 = [c for c in self.hand if c.kind == "unit" and c.grade == 2 and "team" in c.tags and not self.is_g3(c) and c.name != yn and not self._has_recarm(c)]
                    if len(_g2) >= 1 and len([c for c in self.hand if c.kind == "unit" and c.grade == 2 and not self.is_g3(c)]) >= 2:
                        _d = max(_g2, key=self.unit_score)
                        pref = lambda c, _d=_d: c is _d
            disc = self.discard_one("drop", prefer=pref)
            if disc is not None and disc.d["extra"].get("discard_search"):
                kind = disc.d["extra"]["discard_search"]
                if kind == "persona_ability":
                    k = self.look_top(7, lambda x: bool(x.persona), self.unit_score)
                else:
                    k = self.look_top(7, lambda x: "velvet" in x.tags, lambda x: 1 if x.no == "024" else 0)
                if k:
                    self.hand.append(k); self.L("ライドコストで", disc, "→", k)
            self.soul.append(self.vg)
            self.v_grade += 1
            self.vg = self.ride_line[self.v_grade]
            self.rd_ride_turn = self.turn       # ライドデッキからライドしたターン(純黒の影は闇に蠢くのコスト判定)
            self.L(f"ライド G{self.v_grade}(コスト:{self.short(disc) if disc else 'なし'})")
            if self.v_grade == 1:
                if not first:
                    self.draw(1)
                self.draw(1); self.team_token = True
                self.vgauge = [VG_BASE[self.series]]
                self.L("  G1主人公:1ドロー、チームトークン設置" + ("(後攻G0の1ドローも)" if not first else ""))
            elif self.v_grade == 2:
                self.place_marker(self.arc(self.vg))   # G2主人公はアルカナ:愚者
                need_g3 = not any(self.is_g3(h) for h in self.hand)
                k = self.look_top(7, lambda x: x.kind == "unit" and (need_g3 or not self.is_g3(x)),
                                  lambda x: 99999 if (need_g3 and self.is_g3(x)) else self.unit_score(x) + (5000 if x.grade == 2 else 0))
                if k:
                    self.hand.append(k)
                self.L("  G2主人公:山札7枚から", self.short(k) if k else "なし")
            elif self.v_grade == 3:
                self.place_marker(self.arc(self.vg))
                if self.series == "P3":
                    self.place_marker("愚者")
        else:
            g3 = next((c for c in self.hand if self.is_g3(c)), None)
            if g3:
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

    def main_calls(self, o):
        """メインフェイズのうち、G3の【起】1と手札からのコール(人が操作する時はここを差し替える)"""
        g = self.v_grade
        # 川上は先に出してCBを軽くする
        if g >= 3 and self.cb_avail() == 0:
            kw = [c for c in self.hand if c.on_call == "kawakami"]
            spot = self.free_back()
            if kw and spot and spot != "BC" and len(self.soul) >= 1:
                self.hand.remove(kw[0]); self.call(kw[0], spot, "hand", o)
        # G3の【起】1:CB1で2体コール
        low_deck = len(self.deck) <= self.DECK_SAFE and o.damage < 4      # 山札が少ない時は山札を減らす起動を控える(山札切れ対策)
        if g >= 3 and self.cb_avail() >= max(0, 1 - self.cb_discount) and self.decide("act1", self.vg, not low_deck) and self.pay_cb(max(0, 1 - self.cb_discount)):
            self.cb_discount = 0
            self.act1_turn = self.turn
            self.act1(o)
        self.call_from_hand(o)
        if getattr(self, "focus_th", False) and g >= 1 and "タナトス" not in self.vgauge and self.arcana["死神"] >= 3:
            ph = [c for c in self.hand if c.no == "010"]
            spot = self.free_back() or next((k for k in ("BL", "BR") if self.circles[k] and self.circles[k].card.no != "010"), None)
            if ph and spot:
                self.hand.remove(ph[0]); self.call(ph[0], spot, "hand", o)

    def main_phase(self, o):
        g = self.v_grade
        self.marie_persona()
        self.regalis(o)
        self.main_calls(o)
        # P3:覚醒
        if self.series == "P3" and g >= 3 and o.v_grade >= 3 and getattr(self, "awaken_turn", -1) != self.turn:     # S.E.E.S.の【起】【ターン1回】
            cands = [(k, u) for k, u in self.circles.items() if u and u.gauge and "evolve" in GAUGES[u.gauge]]
            for k, u in sorted(cands, key=lambda kv: kv[0] not in ("FL", "FR")):
                src = [c for c in self.drop + self.soul if c.name == u.card.name]     # コストはドロップ優先(ソウルはスキル用に残す)
                if src:
                    c = src[0]
                    (self.soul if c in self.soul else self.drop).remove(c)
                    self.bind.append(c)
                    self.awakened.add(u.card.name)
                    self.awaken_turn = self.turn
                    u.gauge = GAUGES[u.gauge]["evolve"]
                    self.L("覚醒", u.card, "→", u.gauge, "(" + ("ドロップ" if c not in self.soul else "ソウル") + "の同名カードをバインド)")
                    break
        # G3の【起】2(ペルソナライドしたターン)
        if g >= 3 and self.persona_this_turn:
            if self.series == "P3":
                self.p3_power = 1000 * sum(self.arcana.values())
                if len(self.vgauge) == 2 and self.arcana["審判"] >= 3:
                    self.vgauge.append("メサイア"); self.L("ペルソナ:メサイア")
            elif self.series == "P4":
                self.p4_drive = True
            elif self.series == "P5":
                self.p5_bc_drive = True
        # エリザベス【起】(手札/R):ソウルに置いてCC1かEC3(どのデッキでも使える)
        if g >= 2 and self.eliz_soul_turn != self.turn and self.USE_ESOUL:
            el = [c for c in self.hand if c.d["extra"].get("elizabeth")]
            keep_for_bc = self.key in ("P3", "P3P") and (self.circles["BC"] is None or not self.circles["BC"].card.d["extra"].get("elizabeth"))
            facedown = len(self.dmg_zone) - self.cb_avail()
            if el and self.decide("eliz_soul", el[0], not (keep_for_bc and len(el) == 1) and len(self.hand) >= 4 and (self.energy <= 7 or facedown >= 1)):
                if True:
                    self.hand.remove(el[0]); self.soul.append(el[0]); self.eliz_soul_turn = self.turn
                    if self.energy <= 7 and not (self.ESOUL_CC_FIRST and facedown >= 1):
                        self.ec(3); self.L("エリザベスをソウルへ:エネルギーチャージ3")
                    else:
                        self.cc(1); self.L("エリザベスをソウルへ:カウンターチャージ1")
        # 菜々子(正義3つ):ドロップから、クマがいてリアガードがすべて別名ならSB1でコール
        if g >= 3 and self.key == "P4" and self.arcana["正義"] >= 3 and len(self.soul) >= 2:
            nn = [x for x in self.drop if x.d["no"] == "015"]
            kuma = any(u and u.card.d["no"] == "040" for u in self.circles.values())
            same = any(u and u.card.d["no"] == "015" for u in self.circles.values())
            spot = self.free_back()
            if nn and kuma and not same and spot and self.pay_sb(1):
                self.drop.remove(nn[0]); self.call(nn[0], spot, "drop", o); self.L("  菜々子:ドロップからコール")
        # ドロップから出られるユニット
        for c in list(self.drop):
            ex = c.d["extra"].get("drop_call")
            if not ex or not self.works(c) or g < 2:
                continue
            if any(u and u.card.name == c.name for u in self.circles.values()):
                continue
            spot = self.free_front()
            if not spot:
                continue
            kind, n = ex
            if kind == "EB" and self.energy >= n and self.decide("drop_call_eb", c, self.energy >= n + 2) and self.pay_eb(n):
                self.drop.remove(c); self.call(c, spot, "drop", o)
            elif kind == "CB" and self.cb_avail() >= 1 and self.decide("drop_call_cb", c, True) and self.pay_cb(1):
                self.drop.remove(c); self.call(c, spot, "drop", o)
                if self.deck:
                    self.soul.append(self.deck.pop())

    # ---- 自己学習の差し込み口(2026/11/08):払うかどうか選べるコストや、何を選ぶか。環境変数 LEARN_MAIN でモデルを使う
    def decide(self, kind, card=None, default=True, extra=None):
        if LEARN_MAIN is None:
            return default
        import learn_main
        sf = learn_main.state_features(self)
        X = [sf + learn_main.option_features(self, kind, card, yes, extra) for yes in (False, True)]
        pr = LEARN_MAIN(X)
        return pr[1] > pr[0]

    def choose_card(self, kind, cands, default):
        if LEARN_MAIN is None:
            return default
        import learn_main
        sf = learn_main.state_features(self)
        uniq = list({c.name: c for c in cands}.values())
        pr = LEARN_MAIN([sf + learn_main.option_features(self, kind, c, True) for c in uniq])
        return uniq[max(range(len(uniq)), key=lambda i: pr[i])]

    def regalis_other(self, o):
        """国家表記のないレガリスピースのうち、メインフェイズに使うもの(2026/11/11)。使ったら True"""
        def use(c, where=None):
            (where or self.hand).remove(c); self.regalis_used = True
        # 煌結晶:手札かドロップから除外(プレイではないのでグレードの制限なし)
        cr = next((c for c in self.hand + self.drop if c.d["extra"].get("regalis") == "crystal"), None)
        if cr and self.v_grade >= 2 and len(self.deck) > 8:
            spot = self.free_front() or self.free_back()
            if spot:
                use(cr, self.hand if cr in self.hand else self.drop); self.bind.append(cr)
                top = [self.deck.pop() for _ in range(min(5, len(self.deck)))]
                ok = [c for c in top if c.kind == "unit" and 1 <= c.grade <= self.v_grade and not self.is_g3(c)
                      and (c.grade == 2 or spot not in ("FL", "FR"))]
                pick = max(ok, key=self.unit_score) if ok else None
                rest = [c for c in top if c is not pick]
                self.deck.extend(rest); self.rng.shuffle(self.deck)
                if pick:
                    self.L("煌結晶:山札5枚から", self.short(pick), "をコール"); self.call(pick, spot, "deck", o)
                else:
                    self.draw(1); self.L("煌結晶:1枚引く")
                return True
        rp = [c for c in self.hand if c.kind == "order" and c.d["extra"].get("regalis") in ("grail", "sword", "ladder")]
        if not rp or self.v_grade < 3:
            return False
        c = rp[0]; k = c.d["extra"]["regalis"]
        if k == "grail":
            # 恩寵湛えし聖なる杯:このターンにライドしていなければ、ペルソナライドを発動(1枚引く、前列+10000、ペルソナライドしたことになる)
            if self.persona_this_turn or getattr(self, "rd_ride_turn", -1) == self.turn:
                return False
            use(c); self.drop.append(c)
            self.draw(1); self.persona_this_turn = True; self.persona_ever = True
            self.grail_turn = self.turn
            self.L("恩寵湛えし聖なる杯:ペルソナライドを発動(1枚引く)")
            return True
        if k == "sword":
            use(c); self.drop.append(c); self.draw(2); self.L("燦爛たる光明の宝剣:2枚引く")
            return True
        if k == "ladder":
            use(c); self.soul.append(c); self.draw(1); self.v_temp += 5000
            self.ladder_until = self.turn + 1          # 次の相手のターン終了時まで、リアガードは相手の効果で選べない
            self.L("その輝きは遠く空の彼方より:1枚引く、ヴァンガード+5000")
            return True
        return False

    def regalis(self, o):
        """純黒の影は闇に蠢く(レガリスピース・ファイト中1回):このターンにライドデッキからライドしていなければコストなし(それ以外はCB1)。
        ドロップから、グレード合計がヴァンガード以下になるように守護者以外のノーマルユニットを2枚までコール。1枚以下ならソウルへ"""
        if getattr(self, "regalis_used", False):
            return
        if self.regalis_other(o):
            return
        rp = [c for c in self.hand if c.kind == "order" and c.d["extra"].get("regalis") is True]
        if not rp or self.v_grade < 3:
            return
        free = getattr(self, "rd_ride_turn", -1) != self.turn
        if not free and self.cb_avail() < 1:
            return
        spots = [k for k in ("FL", "FR") if self.circles[k] is None] + [k for k in ("BC", "BL", "BR") if self.circles[k] is None]
        pool = [c for c in self.drop if c.kind == "unit" and not self.is_g3(c) and c.grade >= 1]
        best, bs = None, -1
        for i, a in enumerate(pool):
            for b in [None] + pool[i + 1:]:
                pick = [a] + ([b] if b else [])
                if sum(x.grade for x in pick) > self.v_grade or len({x.name for x in pick}) < len(pick):
                    continue
                sc = sum(self.unit_score(x) for x in pick) + (100000 if len(pick) == 2 else 0)
                if sc > bs:
                    best, bs = pick, sc
        if not best or not self.decide("regalis", rp[0], len(best) == 2 and (free or o.damage >= 4), extra=(len(best), sum(self.unit_score(x) for x in best) / 10000, 1.0 if free else 0.0)):
            return
        if len(spots) < len(best):      # 空きが足りなければ、弱いリアガードの上からコール(エリザベスのBCは残す)
            occ = [(k, u) for k, u in self.circles.items() if u and k not in spots
                   and not (k == "BC" and u.card.d["extra"].get("elizabeth"))]
            occ.sort(key=lambda kv: self.unit_score(kv[1].card) + (8000 if kv[1].gauge else 0))
            low = min(self.unit_score(x) for x in best)
            for k, u in occ:
                if len(spots) >= len(best):
                    break
                if self.unit_score(u.card) + (8000 if u.gauge else 0) < low:
                    spots.append(k)
            if len(spots) < len(best):
                return
        if not free:
            self.pay_cb(1)
        c0 = rp[0]
        self.hand.remove(c0); self.regalis_used = True
        self.drop.append(c0)
        self.L("純黒の影は闇に蠢く:" + ("コストなし" if free else "CB1"))
        if len(best) <= 1:
            self.drop.remove(c0); self.soul.append(c0)      # 1枚以下ならソウルへ
        best.sort(key=self.unit_score, reverse=True)
        fronts = [k for k in spots if k in ("FL", "FR")]
        rest = [k for k in spots if k not in fronts]
        for x in best:
            if x not in self.drop:          # 1枚目の登場時の効果などでドロップから動いていたら飛ばす
                continue
            k = fronts.pop(0) if fronts else rest.pop(0)
            self.drop.remove(x)
            self.call(x, k, "drop", o)

    def act1(self, o):
        top = self.deck[-7:]
        team = [c for c in top if "team" in c.tags and c.kind == "unit" and not self.is_g3(c)]
        sec_tag = lambda c: c.kind == "unit" and ("velvet" in c.tags or ("kyoryoku" in c.tags and self.works(c)))
        sec = [c for c in top if sec_tag(c)]
        picks = []
        on_board = {u.card.name for u in self.units()}
        team = [c for c in team if c.name not in on_board] or team
        if team:
            t = max(team, key=self.unit_score)
            picks.append(t)
        if self.circles["BC"] and self.circles["BC"].card.d["extra"].get("elizabeth"):
            sec = [c for c in sec if not c.d["extra"].get("elizabeth")] or sec
        if sec:
            def s2(c):
                if c.d["extra"].get("elizabeth") and self.key in ("P3", "P3P"):
                    return 50 if (self.circles["BC"] is None or not self.circles["BC"].card.d["extra"].get("elizabeth")) else 5
                if getattr(self, "focus_th", False) and "タナトス" not in self.vgauge and c.no in ("010", "064"):
                    return 60 if (c.no == "010" and self.arcana["死神"] >= 3) else 30
                return 10 + (5 if c.on_call else 0)
            picks.append(max(sec, key=s2))
        for c in picks:
            self.deck.remove(c)
        self.rng.shuffle(self.deck)
        for c in picks:
            if c.grade >= 2:
                bc = self.circles["BC"]
                if self.series in ("P4", "P5") and (bc is None or "team" not in bc.card.tags or bc.card.grade < 2) and self.free_front() is None:
                    spot = "BC"
                else:
                    spot = self.free_front() or min(("FL", "FR"), key=lambda k: self.unit_score(self.circles[k].card))
            else:
                bce = self.circles["BC"] and self.circles["BC"].card.d["extra"].get("elizabeth")
                if c.d["extra"].get("elizabeth") and self.key in ("P3", "P3P") and not bce:
                    spot = "BC"
                else:
                    spot = self.free_back() or "BL"
            self.L("G3の起動でコール", c, "→", spot)
            self.call(c, spot, "deck", o)

    def call_from_hand(self, o):
        g = self.v_grade
        reserve = self.hand_reserve(o)
        if g == 0 or (self.first and self.turn == 1):
            return
        def units():
            on_board = {u.card.name for u in self.units()}
            return [c for c in self.hand if c.kind == "unit" and 1 <= c.grade <= g and not self.is_g3(c)
                    and c.name not in on_board]
        fmax, bmax = self.EARLY.get(g, (2, 3))
        if g == 1 and not self.first and self.EARLY_G1_SECOND is not None:
            fmax, bmax = self.EARLY_G1_SECOND
        # リカームの仕込み(RECARM_PLAN):G2のターン、ゆかりを前列に置いて反対側の前列を空け、アタック時のリカーム(EB2)でドロップのG2を出して殴る
        plan_rc = False
        if g == 2 and self.RECARM_PLAN and self.energy >= 2:
            tg = lambda who: any("team" in k.tags and k.kind == "unit" and k.grade <= 2 and not self.is_g3(k) and k.name != who for k in self.drop)
            emp = [k for k in ("FL", "FR") if self.circles[k] is None]
            fy = next((self.circles[k] for k in ("FL", "FR") if self.circles[k] and self._has_recarm(self.circles[k].card) and self.circles[k].gauge), None)
            hy = next((c for c in self.hand if self._has_recarm(c)), None)
            if fy and emp and tg(fy.card.name):
                plan_rc = True
            elif hy and len(emp) == 2 and tg(hy.name):
                self.hand.remove(hy); self.call(hy, "FL", "hand", o); plan_rc = True
            if plan_rc:
                self.L("  リカームの仕込み:前列を1つ空けておく")
        # 前列
        for spot in ("FL", "FR"):
            if len(self.hand) <= reserve:
                break
            if plan_rc and self.circles[spot] is None:
                continue
            if g < 3 and self.circles[spot] is None and sum(1 for k in ("FL", "FR") if self.circles[k]) >= fmax:
                continue
            cands = units()
            if g >= 2:
                cands = [c for c in cands if c.grade == 2]
            if not cands:
                continue
            best = max(cands, key=self.unit_score)
            cur = self.circles[spot]
            if cur is None or (cur.rest and cur.locked) or (g >= 3 and self.unit_score(best) > self.unit_score(cur.card) + self.FRONT_SWAP):
                if g <= 1 and o.damage >= self.G1_STOP_DMG:
                    continue
                if cur is not None and cur.rest and cur.locked:
                    self.L("  ロックされてスタンドできない", self.short(cur.card), "の上にコールして入れ替え")
                self.hand.remove(best)
                self.call(best, spot, "hand", o)
        # P3:エリザベスは中央後列へ(いるG1を入れ替える)
        if g >= 3 and self.key in ("P3", "P3P"):
            bc = self.circles["BC"]
            el = [c for c in self.hand if c.d["extra"].get("elizabeth")]
            if el and (bc is None or not bc.card.d["extra"].get("elizabeth")) and len(self.hand) > reserve - 1:
                self.hand.remove(el[0]); self.call(el[0], "BC", "hand", o)
        # 後列(P4/P5はG3以降、中央後列にチームのG2を置いてアタックさせる)
        if g >= 3 or bmax > 0:
            for spot in (("BC", "BL", "BR") if g >= 3 else (("BC",) + tuple(x for x in self.BACK_ORDER if x != "BC") if (g == 1 and self.G1_BACK_BC) else self.BACK_ORDER)):
                if len(self.hand) <= reserve:
                    break
                if g < 3 and self.circles[spot] is None and sum(1 for k in ("BL", "BC", "BR") if self.circles[k]) >= bmax:
                    continue
                cur = self.circles[spot]
                p3_bc = (self.series == "P3" and self.BC_G2_P3 and (cur is None or not cur.card.d["extra"].get("elizabeth"))
                         and not any(c.d["extra"].get("elizabeth") for c in self.hand))
                if spot == "BC" and g >= 3 and self.BC_G2 and (self.series in ("P4", "P5") or p3_bc) and (cur is None or cur.card.grade < 2):
                    on_board = {u.card.name for u in self.units()}
                    g2 = [c for c in self.hand if c.kind == "unit" and c.grade == 2 and "team" in c.tags
                          and not self.is_g3(c) and c.name not in on_board]
                    if g2:
                        best = max(g2, key=self.unit_score)
                        self.hand.remove(best)
                        self.call(best, spot, "hand", o)
                        continue
                if cur is not None:
                    continue
                on_board = {u.card.name for u in self.units()}
                cands = [c for c in self.hand if c.kind == "unit" and c.grade == 1 and (c.name not in on_board or not c.persona)]
                if not cands:
                    continue
                best = max(cands, key=lambda c: self.back_score(c, spot))
                self.hand.remove(best)
                self.call(best, spot, "hand", o)

    # ---------------------------------------------------------- バトル
    def unit_power(self, u, opp, circle):
        c = u.card
        if not getattr(self, "in_turn", True):
            # 相手のターン中(アタックされる側):ペルソナゲージの【永】(あなたのターン中)・アタックした時の効果・
            # そのターン中の効果はどれも切れているので、元々のパワーだけ(中央後列のエリザベスの18001は常に)
            if c.d["extra"].get("elizabeth") and circle == "BC" and self.key in ("P3", "P3P") and self.v_grade >= 3:
                return 18001
            return c.power
        p = c.power + u.bonus
        if c.d["extra"].get("elizabeth") and circle == "BC" and self.key in ("P3", "P3P") and self.v_grade >= 3:
            p = 18001 + u.bonus
        if u.gauge:
            gd = GAUGES[u.gauge]
            if "flat" in gd:
                p += gd["flat"]
                # 覚醒/転生/進化のトークンは元のトークンに追加で置かれる(元のトークンの「相手G3なら+5000」も残る)
                if opp.v_grade >= 3 and any(bd.get("evolve") == u.gauge and bd.get("g3") for bd in GAUGES.values()):
                    p += 5000
            elif gd.get("g3") and opp.v_grade >= 3:
                p += gd["g3"]
        if "ace" in c.tags and opp.v_grade >= 3:
            p += 5000 + (5000 if self.persona_ever else 0)
        return p

    def use_skill(self, u, opp, atk):
        """アタック時にゲージのスキルを1つ使う(払えるもののうち、一番パワーが上がるもの。同じなら余っている資源を使う)"""
        if not u.gauge:
            return
        gd = GAUGES[u.gauge]
        pushing = opp.damage >= 4
        best = None
        skills = list(gd["skills"])
        for bname, bd in GAUGES.items():          # 覚醒/転生/進化しても元のゲージのスキルは残る
            if bd.get("evolve") == u.gauge:
                skills += bd["skills"]
        for kind, n, pw, ex in skills:
            if ex == "persona_only" and not self.persona_this_turn:
                continue
            if ex == "g3only" and opp.v_grade < 3:
                continue
            if ex == "g3bonus" and opp.v_grade >= 3:
                pw += 5000
            if ex == "persona_bonus" and self.persona_this_turn:
                pw += 5000
            if kind == "EB":
                ok = self.energy >= n + (0 if ((pushing and not self.eliz_reserve()) or (self.NAOTO_FOCUS and ex == "crit")) else 2)
                spare = self.energy - n
            elif kind == "SB":
                ok = len(self.soul) >= n + (0 if pushing else 1)
                spare = len(self.soul) * 2 - n
            elif kind == "free_retire":
                ok = pushing or u.card.d["extra"].get("akechi")
                spare = -5
            elif kind == "EC":
                ok = self.energy <= 10 - self.v_grade and not pushing
                spare = 10
            else:
                continue
            if ex == "sweep_g3" and opp.v_grade < 3:
                continue
            if ex == "recarm" and not self._recarm_target():
                continue
            if ex == "recarm" and self.energy >= n and not self.eliz_reserve() and (
                    self.RECARM_ATTACK == 1 or (self.RECARM_ATTACK == 2 and opp.v_grade <= 2)):
                ok = True                       # 2: 相手がG2までのうち(出したユニットのアタックが届く)だけ、エネルギーの予備を残さず使う
            if ex == "hamaon" and (self.v_grade < 3 or not opp.low_grade_rg()):
                continue
            if not ok:
                continue
            val = pw + (3000 if ex in ("crit", "retire") else 0) + (2000 if ex == "CC1" else 0)
            if self.NAOTO_FOCUS and ex == "crit":
                val += 20000                    # 直斗を意識:転生したらクリティカル+1のスキルを最優先
            if ex == "ec_grade":
                val = 1500 * self.v_grade if self.energy <= 4 else 2000
            elif ex == "recarm":
                val = 16000 if (self.RECARM_ATTACK == 1 or (self.RECARM_ATTACK == 2 and opp.v_grade <= 2)) else RECARM_VAL
            elif ex == "tarunda":
                base = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[opp.v_grade]
                val = max(0, base - 8000) * 2
            elif ex in ("sweep", "sweep_g3"):
                val += 3000 * len(opp.rg_targets())
            elif ex == "lock":
                val += 2000 if opp.rg_targets() else 0
            elif ex == "hamaon":
                val += 3500
            key = (val, spare)
            if best is None or key > best[0]:
                best = (key, kind, n, pw, ex)
        if best is None:
            return
        _, kind, n, pw, ex = best
        # 自己学習の差し込み口:このアタックでスキルを使うか(資源を温存するか)
        if kind in ("EB", "SB") and not self.decide("skill_use", u.card, True,
                extra=(pw / 10000, (atk.power - opp.vg_power()) / 10000, n if kind == "EB" else -n)):
            return
        if kind == "EB":
            self.pay_eb(n)
        elif kind == "SB":
            self.pay_sb(n)
        elif kind == "EC":
            self.ec(self.v_grade)
        else:
            u.end_retire = True
        atk.power += pw
        self.L(f"  スキル({u.gauge}) {kind}{n}" + (f" +{pw}" if pw else "") + (f" {EX_JP.get(ex, ex)}" if EX_JP.get(ex, ex) else ""))
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
            k, spot = self._recarm_target()
            self.drop.remove(k); self.call(k, spot, "drop", opp, in_battle=True)
            self.recarm_spot = spot              # battle() が、このユニットのアタックを順番の最後に足す(RECARM_ATTACK)
        if ex == "crit":
            atk.crit += 1; atk.crit_ability = True
        elif ex == "CC1":
            self.cc(1)
        elif ex == "retire":
            opp.retire_one()

    def marie_persona(self):
        """マリー(永劫3つ):VGのペルソナゲージ2枚以上なら自身をバインドしてペルソナライドを発動"""
        if self.persona_this_turn or self.marie_pr_used or self.v_grade < 3 or len(self.vgauge) < 2:
            return
        if self.arcana.get("永劫", 0) < 3:
            return
        for k, u in self.circles.items():
            if u and u.card.d["no"] == "016":
                self.circles[k] = None; self.bind.append(u.card)
                self.marie_pr_used = True
                self.persona_this_turn = True
                self.draw(1)
                self.L("  マリー:自身をバインドしてペルソナライド発動")
                return

    def _recarm_target(self):
        """リカーム:ドロップから別名のチームのユニット(守護者以外)を、空いている(R)にコール"""
        spot = self.free_front()
        if not spot and self.RECARM_ATTACK and self.circles["BC"] is None and self.v_grade >= 3 and self.act1_turn == self.turn:
            spot = "BC"
        spot = spot or self.free_back()
        if not spot:
            return None
        names = {u.card.name for u in self.units()}
        ks = [k for k in self.drop if "team" in k.tags and k.kind == "unit" and k.name not in names and not self.is_g3(k)
              and k.grade <= self.v_grade]
        if not ks:
            return None
        return max(ks, key=self.unit_score), spot

    def eliz_reserve(self):
        """ペルソナライドしたターンで、中央後列のエリザベスがまだアタックしていないなら、メギドラオン用のエネルギー2を残す(ELIZ_RESERVE=1の時)"""
        if not self.ELIZ_RESERVE or not self.persona_this_turn:
            return False
        u = self.circles.get("BC")
        return bool(u and not u.rest and u.card.d["extra"].get("elizabeth"))

    def use_vg_skill(self, opp, atk, remaining):
        """ヴァンガードのアタック時、ペルソナゲージの〔スキル〕を1つ使う"""
        if not self.vgauge:
            return
        pushing = opp.damage >= 4
        n_rest = sum(1 for sp, _ in remaining if sp != "V")
        best = None
        for gname in self.vgauge:
            for kind, n, pw, ex in VG_GAUGES[gname]["skills"]:
                if kind == "EB":
                    ok = self.energy >= n + (0 if (pushing and not self.eliz_reserve()) else 2); spare = self.energy - n
                else:
                    ok = len(self.soul) >= n + (0 if pushing else 1); spare = len(self.soul) * 2 - n
                if not ok:
                    continue
                val = pw
                if ex == "tarunda":
                    base = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[opp.v_grade]
                    if base <= 8000:
                        continue
                    val = (base - 8000) * (1 + n_rest)
                elif ex == "retire":
                    if not opp.rg_targets():
                        continue
                    val = 4000
                elif ex == "crit":
                    val = 12000 if opp.damage >= 3 else 4000
                elif ex == "lock":
                    val += 1000
                elif ex == "recycle":
                    if not any(c.kind == "trigger" and c.trig == "crit" for c in self.drop):
                        continue
                    val = 600
                key = (val, spare)
                if best is None or key > best[0]:
                    best = (key, gname, kind, n, pw, ex)
        if best is None:
            return
        _, gname, kind, n, pw, ex = best
        (self.pay_eb if kind == "EB" else self.pay_sb)(n)
        atk.power += pw
        if ex == "tarunda":
            base = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[opp.v_grade]
            opp.v_temp -= base - 8000
        elif ex == "retire":
            opp.retire_one()
        elif ex == "crit":
            atk.crit += 1; atk.crit_ability = True
        elif ex == "recycle":
            c = next(c for c in self.drop if c.kind == "trigger" and c.trig == "crit")
            self.drop.remove(c); self.deck.append(c); self.rng.shuffle(self.deck)
        self.L(f"  ヴァンガードのスキル({gname}) {kind}{n}" + (f" +{pw}" if pw else "") + (f" {EX_JP.get(ex, ex)}" if EX_JP.get(ex, ex) else ""))

    def battle(self, opp, game):
        self.restand = set()
        pb = 10000 if self.persona_this_turn else 0
        self.kasumi_used = False
        circ = self.circles
        g = self.v_grade
        # 中央後列がアタックするか
        bc = circ["BC"]
        bc_attack = False
        if bc and g >= 3 and self.act1_turn == self.turn:
            if self.series == "P3":
                ok = bc.card.d["extra"].get("elizabeth") or "team" in bc.card.tags
            else:
                ok = "team" in bc.card.tags and bc.card.kind == "unit"
            bc_attack = bool(ok) and bc.card.kind == "unit"
        plan = []
        if circ["FL"]: plan.append(("FL", "BL"))
        plan.append(("V", None if bc_attack else "BC"))
        if circ["FR"]: plan.append(("FR", "BR"))
        if bc_attack: plan.append(("BC", None))
        _ord = self.ATTACK_ORDER
        if g < 3 and self.ORDER_EARLY:          # G2までのターンだけ別の順番(試験用)
            _ord = self.ORDER_EARLY
        if _ord == "VRR":
            plan.sort(key=lambda x: x[0] != "V")
        elif _ord == "RRV":
            plan.sort(key=lambda x: x[0] == "V")
        elif _ord == "RVL":                     # 右→ヴァンガード→左(試験用)
            pr = {"FR": 0, "V": 1, "FL": 2, "BC": 3}
            plan.sort(key=lambda x: pr[x[0]])
        # 竜司:相手リアガードを殴ってスタンド
        extra_attacks = []
        pend = {}

        def boost(b):
            if not b:
                return 0
            bu = circ[b]
            if bu and bu.card.grade <= 1 and bu.card.kind in ("unit",) and not bu.rest:
                p = self.unit_power(bu, opp, b) if b != "BC" else bu.card.power + bu.bonus
                if bu.card.no == "010" and self.series == "P3" and self.arcana["死神"] >= 3:
                    p += 5000                               # ファルロス(死神3つ):ブーストした時+5000
                return p
            return 0
        i = 0
        while i < len(plan):
            spot, b = plan[i]
            remaining = plan[i + 1:]
            if spot == "V":
                power = self.vg_power() + boost(b) + pb + self.p3_power + (5000 if self.p4_drive else 0)
                crit = 2 if self.v_crit2 else 1
                drives = 1 if g < 3 else (3 if (self.p4_drive or self.triple) else 2)
                power += sum(VG_GAUGES[x]["flat"] for x in self.vgauge)
                atk = Attack(power, crit, drives, True, crit_ability=self.v_crit2)
                self.use_vg_skill(opp, atk, remaining)
            else:
                u = circ[spot]
                if u is not None and u.rest and spot in getattr(self, "restand", set()):
                    self.restand.discard(spot); u.rest = False        # オーバートリガー:アタックしたバトル終了時にスタンド
                if u is None or u.rest:
                    i += 1
                    continue
                power = self.unit_power(u, opp, spot) + boost(b)
                if spot in ("FL", "FR"):
                    power += pb
                if spot == "BC" and pb:
                    power += 10000
                crit, drives = 1, 0
                if spot == "BC" and self.p5_bc_drive and u.gauge:
                    drives = 1
                atk = Attack(power, crit, drives, False, attacker=u)
                # 竜司:前列に相手リアガードがいれば先にそちらを殴る
                if u.card.d["extra"].get("ryuji") and self.works(u.card) and u.attacks == 0 and g >= 3 \
                        and opp.rg_targets() and len(self.soul) >= 1:
                    self.use_skill(u, opp, atk)
                    self.recarm_spot = None
                    idx, rp = max(opp.rg_targets(), key=lambda t: t[1])
                    u.attacks += 1
                    if atk.power >= rp:
                        opp.retire_front(idx)
                        self.L("竜司がリアガードを退却")
                    u.rest = True
                    if self.pay_sb(1):
                        u.rest = False
                        self.L("竜司スタンド")
                        plan.append((spot, None))
                    i += 1
                    continue
                tgt = self.rg_attack_target(opp, atk.power + pend.get(spot, (0, 0))[0])
                if tgt is not None:
                    # リアガードへのアタック(スキルは使わない)
                    u.attacks += 1; u.rest = True
                    if b and circ[b] and boost(b):
                        circ[b].rest = True
                    atk.power += pend.get(spot, (0, 0))[0]; pend.pop(spot, None)
                    nm = opp.rg_name(tgt) if hasattr(opp, "rg_name") else "リアガード"
                    alive = opp.defend_rg(tgt, atk, self)
                    self.L("アタック", u.card, atk.power, "→ 相手のリアガード", nm, "ガードされた" if alive else "退却させた")
                    self.rg_atk_n = getattr(self, "rg_atk_n", 0) + 1
                    i += 1
                    continue
                if spot == "BC" and u.card.d["extra"].get("elizabeth") and self.persona_this_turn and self.energy >= 2:
                    self.pay_eb(2); atk.power += 9999; atk.crit += 1; atk.crit_ability = True
                else:
                    self.use_skill(u, opp, atk)
                    _rs = getattr(self, "recarm_spot", None)
                    if _rs and self.RECARM_ATTACK:
                        self.recarm_spot = None
                        _ru = circ.get(_rs)
                        if _ru and not _ru.rest and all(p0[0] != _rs for p0 in plan[i + 1:]):
                            if _rs in ("FL", "FR"):
                                plan.append((_rs, "BL" if _rs == "FL" else "BR"))
                            elif _rs == "BC" and g >= 3 and self.act1_turn == self.turn and "team" in _ru.card.tags:
                                plan.append(("BC", None))        # G3の【起】を使ったターン:中央後列のチームのユニットは後列からアタックできる
                        remaining = plan[i + 1:]
                u.attacks += 1
                atk.second = u.attacks >= 2
            atk.power += pend.get(spot, (0, 0))[0]
            atk.crit += pend.get(spot, (0, 0))[1]
            pend.pop(spot, None)
            if spot == "V":
                self.v_rest = True
            elif circ[spot]:
                circ[spot].rest = True
            boosted = bool(b and circ[b] and boost(b))
            if boosted:
                circ[b].rest = True
            self.cur_spot = spot
            res = self.resolve(opp, atk, remaining, pend, plan, i)
            opp.gzone = []
            if res is not None:
                return res
            # ファルロス:P3をブーストしたバトル終了時、ソウルに置いて1枚引く
            if boosted and circ[b] and circ[b].card.no == "010" and self.series == "P3" and len(self.deck) > self.DECK_SAFE \
                    and self.decide("boost_soul", circ[b].card, len(self.hand) <= 6):
                self.soul.append(circ[b].card); circ[b] = None; self.draw(1)
                self.L("  ファルロス:ソウルに置いて1枚引く")
            # 早瀬(星3つ):ヴァンガードのアタック終了時 SC1/EC1
            if spot == "V" and getattr(self, "hayase_turn", -1) == self.turn:
                self.sc(1); self.ec(1)
            if self.deckout or not self.deck:
                self.deck_empty_check()
                return opp
            i += 1
        return None

    def resolve(self, opp, atk, remaining, pend, plan, idx):
        guarded = opp.defend(atk, self)
        atk.guard = guarded
        self.L("アタック", "V" if atk.is_v else atk.attacker.card, atk.power, f"☆{atk.crit}", f"ドライブ{atk.drives}", "相手ガード", guarded, f"相手{opp.damage}点")
        for _dj in range(atk.drives):
            atk.dleft = atk.drives - _dj - 1
            c = self.take_top()
            if c is None:
                return opp
            self.hand.append(c)
            self.L("  ドライブチェック:", self.short(c))
            if c.kind == "trigger":
                self.apply_trigger(opp, c, atk, remaining, pend, plan, idx)
            if self.deckout:
                self.deck_empty_check()
                return opp
        hit = guarded != "perfect" and atk.power >= opp.vg_power() + guarded
        self.L(f"  → 最終パワー{atk.power if atk.power < 10**7 else '1億+'} ☆{atk.crit} 相手VG{opp.vg_power() if opp.vg_power() < 10**7 else '1億+'}+ガード{guarded} {'ヒット' if hit else 'ガード成功'}")
        if atk.sweep:
            # 相手の前列すべてとバトル:前列のリアガードは(ガードされない前提で)パワーが届けば退却
            for i, pw in sorted(opp.rg_targets(), reverse=True):
                if atk.power >= pw:
                    opp.retire_front(i); self.L("  前列すべてとバトル:リアガード退却")
        if guarded == "perfect":
            return None
        if atk.power >= opp.vg_power() + guarded:
            for _ in range(atk.crit):
                if opp.damage_check(self):
                    return self
                if getattr(opp, "deckout", False) or (hasattr(opp, "deck") and not opp.deck):
                    if hasattr(opp, "deck_empty_check"):
                        opp.deck_empty_check()
                    return self
        return None

    def apply_trigger(self, opp, c, atk, remaining, pend, plan, idx):
        now = opp.damage >= 4 or not remaining
        nxt = remaining[0][0] if remaining else None
        t = c.trig
        if self.TRIG_SMART:
            ps, cs = trig_to_self(atk, opp, nxt is not None, opp.damage >= 4)
        else:
            ps, cs = (now or nxt is None), (now or opp.damage >= 3 or nxt is None)
        # 表示用:トリガーで上がったパワーをユニットに記録(そのターン中)
        def _disp(sp, v=10000):
            if sp == "V":
                self.v_disp_tb = getattr(self, "v_disp_tb", 0) + v
            elif sp and self.circles.get(sp):
                self.circles[sp].disp_tb += v
        cur = getattr(self, "cur_spot", "V")
        if t == "front":
            for sp in ("V", "FL", "FR"):
                _disp(sp)
        elif t in ("crit", "draw", "heal"):
            _disp(cur if ps else nxt)
        elif t == "over":
            _disp(cur, 100_000_000)
        if t == "front":
            atk.power += 10000
            for s, _ in remaining:
                if s in ("FL", "FR"):
                    p, k = pend.get(s, (0, 0)); pend[s] = (p + 10000, k)
        elif t == "over":
            self.hand.remove(c)
            self.draw(1)
            atk.power += 100_000_000
            # 追加効果:まだ殴っていないリアガードにスタンド付与(→もう一度アタック)
            if remaining:
                plan.append(remaining[0])
                self.restand = getattr(self, "restand", set()) | {remaining[0][0]}
            else:
                # 選ばなかったら、1枚引き、手札からユニットカードを1枚まで(R)にコール
                self.draw(1)
                self.over_call(opp)
        else:
            if ps:
                atk.power += 10000
            else:
                p, k = pend.get(nxt, (0, 0)); pend[nxt] = (p + 10000, k)
            if t == "crit":
                if cs:
                    atk.crit += 1
                else:
                    p, k = pend.get(nxt, (0, 0)); pend[nxt] = (p, k + 1)
            elif t == "draw":
                self.draw(1)
            elif t == "heal":
                if self.damage > 0 and self.damage >= opp.damage:
                    self.drop.append(self.dmg_zone.pop()[0])

    def over_call(self, opp):
        """オーバートリガーの追加効果(スタンドしているリアガードを選ばなかった時):手札からユニットを1枚(R)にコール"""
        cands = [c for c in self.hand if c.kind == "unit" and 1 <= c.grade <= self.v_grade and not self.is_g3(c)]
        spot = self.free_front()
        if spot:
            g2 = [c for c in cands if c.grade == 2]
            if g2:
                best = max(g2, key=self.unit_score); self.hand.remove(best)
                self.L("  オーバートリガー:手札から", self.short(best), "をコール"); self.call(best, spot, "hand", opp, in_battle=True)
                return
        spot = self.free_back()
        g1 = [c for c in cands if c.grade == 1]
        if spot and g1:
            best = max(g1, key=lambda c: self.back_score(c, spot)); self.hand.remove(best)
            self.L("  オーバートリガー:手札から", self.short(best), "をコール"); self.call(best, spot, "hand", opp, in_battle=True)

    def end_phase(self, o):
        # エネルギージェネレーター:【起】【ターン1回】EB7で1枚引く(バトル後、余ったエネルギーで使う)
        if self.energy >= max(7, self.GEN_DRAW_MIN) and len(self.deck) > self.DECK_SAFE and self.pay_eb(7):
            self.draw(1); self.L("  エネルギージェネレーター:EB7で1枚引く")
        for k, u in list(self.circles.items()):
            if u and u.doomed:
                self.drop.append(u.card); self.circles[k] = None
                self.L("  ", self.short(u.card), "退却(シールド0の効果)")
            elif u:
                u.locked = False
        self.L("  盤面:", " ".join(f"{k}={self.short(u.card)}{'[' + u.gauge + ']' if u.gauge else ''}" for k, u in self.circles.items() if u),
               f"/ アルカナ {dict(self.arcana)}")
        for k, u in list(self.circles.items()):
            if not u:
                continue
            ex = u.card.d["extra"]
            if ex.get("akechi") and u.attacks and self.works(u.card):
                self.drop.append(u.card); self.circles[k] = None; self.draw(1); self.ec(1)
                self.L("  明智:退却→1ドロー、EC1")
                continue
            if ex.get("end_soul_draw") and u.attacks and self.works(u.card) and self.decide("end_soul", u.card, len(self.hand) <= 6):
                self.soul.append(u.card); self.circles[k] = None
                ec2 = self.decide("end_soul_ec", u.card, self.energy <= 4)
                self.L(f"  {self.short(u.card)}:ソウルへ→{'EC2' if ec2 else '1ドロー'}")
                if ec2:
                    self.ec(2)
                else:
                    self.draw(1)
                continue
            if u.end_retire:
                self.drop.append(u.card); self.circles[k] = None
                continue
            if u.end_draw_retire and (not self.YUKIKO_SMART or u.locked or u.doomed or len(self.hand) <= self.YUKIKO_HAND):
                self.drop.append(u.card); self.circles[k] = None; self.draw(1)
                self.L(f"  {self.short(u.card)}:退却して1枚引く(雪子)")
                continue
            if u.end_hand:
                self.hand.append(u.card); self.circles[k] = None
                self.L(f"  {self.short(u.card)}:手札に戻る")

    # ---------------------------------------------------------- 守り
    def retire_one(self):
        for k in ("FL", "FR", "BL", "BR", "BC"):
            u = self.circles[k]
            if u:
                # テオドア:エリザベスの身代わり
                if u.card.d["extra"].get("elizabeth"):
                    th = [kk for kk, x in self.circles.items() if x and x.card.no == "082"]
                    if th:
                        self.drop.append(self.circles[th[0]].card); self.circles[th[0]] = None
                        return True
                self.drop.append(u.card); self.circles[k] = None
                return True
        return False

    def rg_targets(self):
        return [(i, self.unit_power(self.circles[k], self.opp, k)) for i, k in enumerate(("FL", "FR")) if self.circles[k]]

    def retire_front(self, i):
        k = ("FL", "FR")[i]
        if self.circles[k]:
            self.drop.append(self.circles[k].card); self.circles[k] = None

    def rg_attack_target(self, opp, power):
        """リアガードにアタックするなら、その相手前列の番号(しないなら None)"""
        mode = self.RG_ATK
        if not mode or not hasattr(opp, "defend_rg"):
            return None
        cands = [(i, pw) for i, pw in opp.rg_targets() if power >= pw]
        if not cands:
            return None
        best = max(cands, key=lambda t: opp.rg_value(t[0]))
        if power < opp.vg_power():
            return best[0]
        if mode >= 2 and opp.damage <= self.RG_ATK_DMG and opp.rg_value(best[0]) >= self.RG_ATK_VAL:
            return best[0]
        return None

    def rg_name(self, i):
        u = self.circles[("FL", "FR")[i]]
        return self.short(u.card) if u else "-"

    def rg_value(self, i):
        """前列のリアガードの価値(手札1枚≒30)。ペルソナゲージ付きは高い"""
        u = self.circles[("FL", "FR")[i]]
        if not u:
            return 0
        v = 30
        if u.gauge:
            v += 20
            if u.card.name in self.awakened or "flat" in GAUGES.get(u.gauge, {}):
                v += 15
        return v

    def defend_rg(self, i, atk, attacker):
        """自分の前列のリアガードがアタックされた。守れたら True、退却したら False"""
        k = ("FL", "FR")[i]
        u = self.circles[k]
        if not u:
            return True
        need = atk.power - self.unit_power(u, attacker, k) + 1
        if need <= 0:
            return True
        self._nosent = False
        items = [it for it in self.guard_items(atk) if not (it[0] == "I" and it[1] == k)]
        cost, gs = self.knap(items, need)
        if gs is not None and cost < self.rg_value(i) * self.RG_GUARD_K and len(self.hand) - sum(1 for j in gs if items[j][0] == "H") >= 2:
            self.gzone = []
            self.apply_guard("guard", items, gs)
            return True
        self.L("  リアガード", self.short(u.card), "がアタックされて退却")
        self.drop.append(u.card); self.circles[k] = None
        return False

    def lock_one(self):
        """相手のリアガード1体:次の相手ターン終了時までインターセプトできず、スタンドフェイズにスタンドできない
        (レストしている前列を優先して選ぶ。スタンドしているユニットはインターセプトだけ封じられる)"""
        cands = [self.circles[k] for k in ("FL", "FR") if self.circles[k] and not self.circles[k].locked]
        if not cands:
            return False
        u = max(cands, key=lambda x: (x.rest, x.card.power))
        u.locked = True; u.lock_rest = u.rest
        self.L("  ロックされた:", self.short(u.card), "(レスト中→次のターンもスタンドできない)" if u.rest else "(スタンド中→インターセプトだけできない)")
        return True

    def doom_one(self):
        for k in ("FL", "FR", "BL", "BR"):
            u = self.circles[k]
            if u and not u.doomed:
                u.doomed = True; return True
        return False

    def low_grade_rg(self):
        return any(u and u.card.grade <= 1 for u in self.circles.values())

    def retire_low_grade(self):
        for k in ("BL", "BR", "BC", "FL", "FR"):
            u = self.circles[k]
            if u and u.card.grade <= 1:
                self.drop.append(u.card); self.circles[k] = None; return True
        return False

    def bc_intercept_ok(self, k, u):
        """G3の【起】1を使った次の相手ターン終了時まで、中央後列のチーム(P3はS.E.E.S.)のG2はインターセプトできる"""
        return (k == "BC" and u is not None and self.v_grade >= 3 and self.act1_turn == self.turn and u.card.grade == 2
                and "team" in u.card.tags and u.card.kind == "unit" and not u.locked and not u.doomed)

    def guard_value(self, c, atk):
        """手札のカードをガードに出した時のシールド(コスト込みの判断は呼び出し側)"""
        if c.kind == "trigger":
            base, boost, cond = c.d["extra"]["guard"]
            if c.trig == "over":
                return base, 0
            if cond == "crit2" and atk.crit >= 2 and atk.crit_ability:
                return base + boost, 0
            if cond == "second" and atk.second:
                return base + boost, 0
            if base == 20000 and self.opp.v_grade < 3:
                return 15000, 0
            if boost and cond == "":
                ov = self.opp.v_grade if getattr(self, "opp", None) else 3
                return base, (boost if (self.v_grade < ov or ov >= 3) else 10000)
            return base, 0
        if c.kind == "unit":
            s = c.shield
            if c.grade == 3:
                return 0, 0
            if "kyoryoku" in c.tags and self.works(c) and self.arcana[self.arc(c)] >= 2:
                s += 5000
            if "velvet" in c.tags and "komyu" in c.tags and self.works(c) and self.arcana[self.arc(c)] >= 2:   # マリー等:2つでシールド+5000
                s += 5000
            if c.d.get("guard") == "theodore" and self.key == "P3P":
                s += 5000
            if c.d.get("guard") == "aragaki" and self.works(c):
                s += 5000 + (5000 if any(u and u.card.no == "032" for u in self.circles.values()) else 0)
            return s, 0
        return 0, 0

    def defend(self, atk, attacker):
        r = self._defend(atk, attacker)
        self.L("被アタック", atk.power, f"☆{atk.crit}", "ガード", r, f"手札{len(self.hand)}", f"{self.damage}点")
        return r

    def guard_cost(self, c):
        """ガードに使った時に失う価値(手札1枚≒30)"""
        if c.kind == "order":      # レガリスピース:使う前は手放さない、使えなくなったら最初に捨てる
            return 2 if getattr(self, "regalis_used", False) else 90
        if c.kind == "trigger":
            return 60 if c.trig == "over" else 28
        if self.is_g3(c):
            return 200
        if c.d["extra"].get("elizabeth") and self.key in ("P3", "P3P"):
            return 55
        return 30 if c.grade == 2 else 26

    def take_cost(self, n):
        """ダメージをn点受ける時の損(6点目は負け)"""
        table = self.GUARD_TABLE
        cost = 0
        for p in range(self.damage + 1, self.damage + n + 1):
            if p >= 6:
                return 10 ** 6
            cost += table[p]
        return cost

    def _defend(self, atk, attacker):
        self.gzone = []          # 表示用:このバトルでガーディアンサークルに置いたカード
        self._nosent = getattr(atk, "nosent", False)
        defense = self.vg_power()
        if atk.power < defense:
            return 0
        hit = self.damage + atk.crit
        lethal = hit >= 6
        passes = atk.drives if lethal else (min(self.PASS5, atk.drives) if self.damage + atk.crit >= 5 and atk.drives else 0)
        need = atk.power - defense + 1 + passes * 10000
        # かすみ(ドロップからバインドして+10000)
        if self.key == "P5" and not self.kasumi_used and self.v_grade >= 3 and atk.is_v:
            ks = [c for c in self.drop if c.d["extra"].get("kasumi")]
            if ks and need > 0 and (self.damage >= 3 or lethal):
                self.drop.remove(ks[0]); self.bind.append(ks[0]); self.kasumi_used = True
                self.v_temp += 10000; need -= 10000
                self.L("  かすみ:ドロップからバインドしてヴァンガード+10000")
                if need <= 0:
                    return 0
        items = self.guard_items(atk)
        self.cur_rem = self.remaining_attacks(attacker, atk)
        choice, guard_set = self.choose_guard(atk, items, need)
        return self.apply_guard(choice, items, guard_set)

    def guard_items(self, atk):
        """ガードに使えるもの: (種類, 対象, シールド, 失う価値, 追加コストの有無)"""
        items = []
        if not getattr(self, "regalis_used", False):
            for c in self.hand:
                k = c.d["extra"].get("regalis") if c.kind == "order" and c.d["extra"].get("blitz") else None
                if not k or not atk.is_v and k == "wing":
                    continue
                og = getattr(self, "opp", None).v_grade if getattr(self, "opp", None) else 3
                if k == "twin" and self.v_grade >= 2:
                    items.append(("H", c, 10000, 14, 0))
                elif k == "wing" and self.v_grade >= 2 and (self.cb_avail() >= 1 or self.energy >= 3):
                    same = any(x.name == self.vg.name for x in self.soul + self.drop)
                    items.append(("H", c, 25000 if same else 15000, 10, 0))      # 1枚引くので失う価値は小さい
                elif k == "barrier" and self.v_grade >= 3 and len(self.soul) >= 1:
                    items.append(("H", c, 25000, 30, 0))
                elif k == "tide" and (self.v_grade >= 2 or self.v_grade < og):
                    items.append(("H", c, 15000, 26, 0))
        tw = next((c for c in self.drop if c.d["extra"].get("regalis") == "twin"), None)
        if tw:      # 双つに連なる守護の法陣:ドロップから除外して、アタックされているユニットのグレード×5000(レガリスピースを使った後でも可)
            items.append(("H", tw, 5000 * max(1, self.v_grade), 2, 0))
        for c in self.hand:
            if c.kind == "sentinel" or self.is_g3(c) or c.kind == "order":
                continue
            if c.kind == "trigger" and c.trig == "over":
                items.append(("H", c, 50000, self.guard_cost(c), 0))
                continue
            base, boost = self.guard_value(c, atk)
            if base + boost <= 0:
                continue
            can_boost = boost and (len(self.soul) >= 1 or self.energy >= 2)
            items.append(("H", c, base + (boost if can_boost else 0), self.guard_cost(c) + (4 if can_boost else 0), 1 if can_boost else 0))
        for k in ("FL", "FR"):
            u = self.circles[k]
            if u and (u.card.grade == 2 or ("velvet" in u.card.tags or "kyoryoku" in u.card.tags) and "komyu" in u.card.tags and self.works(u.card) and self.arcana[self.arc(u.card)] >= 1) and not u.locked and not u.doomed and not getattr(self, "no_intercept", False) \
                    and not getattr(atk, "sweep", False):   # 前列すべてとバトル:アタックされている前列のリアガードはインターセプトできない
                items.append(("I", k, 5000, IC_GAUGE if u.gauge else IC_PLAIN, 0))
        for k in ("BL", "BR", "BC"):
            if getattr(self, "no_intercept", False):
                break
            u = self.circles[k]
            if u and ("kyoryoku" in u.card.tags or "velvet" in u.card.tags and "komyu" in u.card.tags) and self.works(u.card) and self.arcana[self.arc(u.card)] >= 1 and not u.doomed:
                items.append(("I", k, 5000, IC_BACK, 0))
            elif self.bc_intercept_ok(k, u):
                items.append(("I", k, 5000, IC_GAUGE if u.gauge else IC_PLAIN, 0))
        return items

    @staticmethod
    def knap(items, need):
        """最小コストで need 以上のシールドを集める(シールドを5000単位にしたナップサック)。(コスト, 使うitemの番号)"""
        unit = 5000
        target = -(-need // unit)
        INF = 10 ** 9
        best = [(0, [])] + [(INF, None)] * target
        for idx, it in enumerate(items):
            sh = it[2] // unit
            for t in range(target, -1, -1):
                if best[t][0] == INF:
                    continue
                nt = min(target, t + sh)
                cst = best[t][0] + it[3]
                if cst < best[nt][0]:
                    best[nt] = (cst, best[t][1] + [idx])
        return best[target]

    def sentinel_cost(self):
        INF = 10 ** 9
        if getattr(self, "_nosent", False):
            return INF
        sents = [c for c in self.hand if c.kind == "sentinel"]
        sent_cost = INF
        if sents and self.v_grade >= 1:
            s0 = sents[0]
            free = self.arcana[self.arc(s0)] >= 2
            others = [c for c in self.hand if c is not s0]
            if free or others:
                sent_cost = self.SENT_COST + (0 if free else min(self.guard_cost(c) for c in others) if others else 0)
                sent_cost -= self.SENT_SPARE * (len(sents) - 1)     # 守護者が余っているなら使いやすい
        return sent_cost

    def remaining_attacks(self, attacker, atk):
        """このアタックの後に、相手がこのターンにまだアタックできるユニットの数(スタンドしている前列・中央後列、まだのヴァンガード)"""
        try:
            n = 0
            bc_ok = getattr(attacker, "act1_turn", -1) == getattr(attacker, "turn", -2) and getattr(attacker, "v_grade", 0) >= 3
            for k in ("FL", "FR") + (("BC",) if bc_ok else ()):
                u = attacker.circles.get(k)
                if u and not u.rest and u is not atk.attacker:
                    n += 1
            if not atk.is_v and not getattr(attacker, "v_rest", True):
                n += 1
            return n
        except Exception:
            return 1

    def choose_guard(self, atk, items, need):
        """ガードの判断。環境変数 LEARN_GUARD に学習モデル(JSON)を指定すると、自己学習したモデルで選ぶ(2026/11/03)"""
        if LEARN_PRED is not None and self.damage + atk.crit >= 2:
            import learn_ai
            opts, default = learn_ai.guard_options(self, atk, items, need)
            if len(opts) <= 1:
                return default[0], list(default[1])
            if self.NO_EXACT_V and atk.is_v and atk.drives >= self.NO_EXACT_V and len(opts) > 2:
                # ヴァンガードのアタックを「ちょうど止まる枚数」でガードしない(トリガー1枚で貫通されるため。2026/11/10 採用)
                _c, _gs = self.knap(items, atk.power - self.vg_power() + 1)
                _ex = ("guard", tuple(sorted(_gs or [])))
                _o2 = [o for o in opts if o != _ex]
                if len(_o2) >= 2 and len(_o2) < len(opts):
                    opts = _o2
            sf = learn_ai.state_features(self, atk)
            pr = LEARN_PRED([sf + learn_ai.option_features(self, atk, items, o) for o in opts])
            pick = opts[max(range(len(opts)), key=lambda i: pr[i])]
            return pick[0], list(pick[1])
        return self.base_choose_guard(atk, items, need)

    def base_choose_guard(self, atk, items, need):
        """いつもの判断:ダメージの損・ガードの損・守護者の損を比べて一番小さいもの
        (このターン最後のアタックならダメージの損を重く、まだアタックが残っていれば軽く見る:先読みAIの判断の集計から)"""
        guard_cost, guard_set = self.knap(items, need)
        sent_cost = self.sentinel_cost()
        rem = getattr(self, "cur_rem", 1)
        take = self.take_cost(atk.crit)
        if take < 10 ** 6:
            take = take * (self.TAKE_LAST if rem == 0 else self.TAKE_MORE if rem >= 2 else self.TAKE_ONE)
        options = [("take", take), ("guard", guard_cost), ("sentinel", sent_cost)]
        choice = min(options, key=lambda x: x[1])[0]
        return choice, guard_set

    def apply_guard(self, choice, items, guard_set):
        if choice == "take":
            return 0
        if choice == "sentinel":
            sents = [c for c in self.hand if c.kind == "sentinel"]
            s = sents[0]
            a = self.arc(s)
            free = self.arcana[a] >= 2
            self.gzone = [dict(no=s.d["no"], n=self.short(s), s="完全ガード")]
            self.L("  守護者:", self.short(s), "(コストなし)" if free else "(手札1枚捨て)")
            self.hand.remove(s); self.drop.append(s)
            self.place_marker(a)
            if not free:
                c = min([c for c in self.hand], key=self.guard_cost) if self.hand else None
                if c:
                    self.hand.remove(c); self.drop.append(c)
            return "perfect"
        chosen = [items[i] for i in guard_set]
        for it in chosen:
            cc = self.circles[it[1]].card if it[0] == "I" else it[1]
            self.gzone.append(dict(no=cc.d["no"], n=self.short(cc), s=it[2], ic=it[0] == "I"))
        self.L("  ガード内訳:", ", ".join((f"インターセプト{it[1]}" if it[0] == "I" else f"{self.short(it[1])}({it[2]})") for it in chosen))
        total = 0
        for it in chosen:
            total += it[2]
            if it[0] == "I":
                self.drop.append(self.circles[it[1]].card); self.circles[it[1]] = None
            elif it[1].kind == "order":
                c = it[1]; k = c.d["extra"].get("regalis")
                if c in self.drop:                    # 双つに連なる守護の法陣(ドロップから除外)
                    self.drop.remove(c); self.bind.append(c)
                    continue
                self.hand.remove(c); self.regalis_used = True
                if k == "wing":
                    if self.energy >= 3: self.pay_eb(3)
                    else: self.pay_cb(1)
                    if it[2] >= 25000:
                        x = next(x for x in self.soul + self.drop if x.name == self.vg.name)
                        (self.soul if x in self.soul else self.drop).remove(x); self.bind.append(x)
                    self.bind.append(c); self.draw(1)
                else:
                    if k == "barrier":
                        self.pay_sb(1)
                    self.drop.append(c)
            else:
                c = it[1]
                self.hand.remove(c)
                (self.soul if c.d["no"] in ("036", "045", "055") else self.drop).append(c)
                if it[4]:
                    if not self.pay_sb(1):
                        self.pay_eb(2)
                if c.kind == "trigger" and c.d["extra"]["guard"][2] == "ec1":
                    self.ec(1)
        return total

    def damage_check(self, attacker):
        c = self.take_top()
        if c is None:
            return True
        self.dmg_zone.append([c, True])
        self.L("  ダメージチェック:", self.short(c), f"→ {self.damage}点")
        if c.d["no"] == "058" and len(self.dmg_zone) == 5 and not self.igor_heal_used and self.deck:
            top = self.deck[-1]
            if top.name == self.vg.name:
                self.igor_heal_used = True
                self.drop.append(self.dmg_zone.pop(0)[0])
                self.L("  イゴール:山札の上が主人公と同名→1点回復")
        if c.kind == "trigger":
            t = c.trig
            if t == "heal" and self.damage >= attacker.damage and self.damage < 6:
                self.drop.append(self.dmg_zone.pop(0)[0])
            elif t == "draw":
                self.draw(1)
            elif t == "over":
                self.dmg_zone.pop(); self.draw(1); self.v_temp += 100_000_000
            if t in ("crit", "front", "heal", "draw"):
                self.v_temp += 10000
        return self.damage >= 6


# =============================================================== 対戦
# 試験用のつまみ(2026/11/09):インターセプトの「失う価値」(手札1枚≒30)と、リカームの評価
IC_PLAIN = int(os.environ.get("IC_PLAIN", 12)); IC_GAUGE = int(os.environ.get("IC_GAUGE", 20)); IC_BACK = int(os.environ.get("IC_BACK", 10))   # 11/09:積極的にインターセプトする方が勝率が上がったので下げた(元は22・34・18)
RECARM_VAL = int(os.environ.get("RECARM_VAL", 7000))
LEARN_MAIN = None
if os.environ.get("LEARN_MAIN"):
    import learn_model as _lm2
    LEARN_MAIN = _lm2.load_fast(os.environ["LEARN_MAIN"])
LEARN_PRED = None
if os.environ.get("LEARN_GUARD"):
    import learn_model as _lm
    LEARN_PRED = _lm.load_fast(os.environ["LEARN_GUARD"])


class PGame:
    def __init__(self, a, b, seed, a_first, log=None):
        """a, b: ('persona', deck_key, recipe) か DeckProfile"""
        self.rng = random.Random(seed)
        pa = self.make(a, log)
        pb = self.make(b, log)
        pa.opp, pb.opp = pb, pa
        self.pa = pa
        self.players = [pa, pb] if a_first else [pb, pa]

    def make(self, spec, log):
        if isinstance(spec, DeckProfile):
            return ProfilePlayer(spec, self.rng, log)
        if spec[0] == "meta":
            import meta_engine
            return meta_engine.make(spec[1], self.rng, log, spec[2] if len(spec) > 2 else None)
        key, recipe = spec[1], spec[2]
        return PersonaPlayer(key, recipe, self.rng, log, spec[3] if len(spec) > 3 else None)

    def play(self, max_turns=30):
        for t in range(max_turns):
            atk = self.players[t % 2]
            dfn = self.players[(t + 1) % 2]
            res = atk.take_turn(dfn, t % 2 == 0, self)
            if res is not None:
                return res is self.pa
        p0, p1 = self.players
        winner = p0 if p0.damage <= p1.damage else p1
        return winner is self.pa


def psimulate(a, b, n=1000, seed=0):
    half = n // 2
    wf = ws = 0
    for i in range(half):
        if PGame(a, b, seed * 1_000_003 + i, True).play():
            wf += 1
        if PGame(a, b, seed * 1_000_003 + half + i, False).play():
            ws += 1
    return {"win_rate": (wf + ws) / (2 * half), "win_rate_first": wf / half, "win_rate_second": ws / half, "games": 2 * half}
