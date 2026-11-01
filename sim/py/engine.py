"""
ヴァンガード(D/DZスタンダード)簡易対戦シミュレーター — エンジン本体

再現しているもの
- 50枚の山札(トリガー16枚+完全ガード+G1/G2/G3)と別枠のライドデッキ(G0〜G3)
- ライドデッキからのライド(手札1枚コスト)、後攻のG0ドロー、エネルギージェネレーター(毎ターン+3、上限10、後攻は初回+3)
- ペルソナライド(手札に同名G3があれば、1ドロー+前列パワー+10000)
- G3のツインドライブ、ドライブ/ダメージトリガー(クリティカル/ドロー/前列/ヒール/オーバー)
- リアガードのコールと縦列ブースト、インターセプト
- ガード:G1/G2はシールド5000、トリガーは5000+(SB1かEB2で+15000)、完全ガード(手札1枚コスト)
- 6ダメージで敗北、山札切れで敗北

カードの個別効果は再現せず、デッキの「プロファイル」(パワー加算、追加攻撃、手札増加、
ガード制限など)としてまとめて与える。プロファイルの数値はカードの効果テキストから見積もる。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
import math

# ---------------------------------------------------------------- カード

TRIGGERS = ("crit", "draw", "front", "heal", "over")

# Dスタンダードのトリガーユニットの標準シールド(クリティカル/フロント/ヒール15000、ドロー5000)
D_TRIG_SHIELD = {"crit": (15000, 0), "front": (15000, 0), "heal": (15000, 0), "draw": (5000, 0)}


@dataclass(frozen=True)
class Card:
    kind: str          # crit/draw/front/heal/over/sentinel/g1/g2/g3
    grade: int
    power: int
    shield: int        # 基本シールド
    persona: bool = False   # ペルソナライド対象(ヴァンガードと同名のG3)
    boost: int = 0          # ガード時にコスト(SB1かEB2)で増えるシールド

    @property
    def is_trigger(self) -> bool:
        return self.kind in TRIGGERS


def make_card(kind: str, persona: bool = False) -> Card:
    if kind in TRIGGERS:
        return Card(kind, 0, 5000, 5000)
    if kind == "sentinel":
        return Card(kind, 1, 8000, 0)
    if kind == "g1":
        return Card(kind, 1, 8000, 5000)
    if kind == "g2":
        return Card(kind, 2, 10000, 5000)
    if kind == "g3":
        return Card(kind, 3, 13000, 0, persona=persona)
    raise ValueError(kind)


# ---------------------------------------------------------------- デッキ定義

@dataclass
class DeckProfile:
    """デッキの個性。数値は『G3ライド後の1ターンあたり』の平均的な効果量。"""
    name: str
    # 山札構成(合計50)
    crit: int = 8
    draw: int = 3
    front: int = 0
    heal: int = 4
    over: int = 1
    sentinel: int = 4
    g1: int = 10
    g2: int = 10
    g3: int = 10           # うちペルソナライド対象の枚数は persona_copies
    persona_copies: int = 4
    # 効果
    v_power: int = 0           # ヴァンガードのアタック時パワー加算
    r_power: int = 0           # リアガードのアタック時パワー加算
    v_crit: int = 0            # ヴァンガードのクリティカル加算
    extra_attacks: int = 0     # スタンド等による追加アタック回数
    extra_power: int = 23000   # 追加アタックのパワー
    card_gain: float = 0.0     # 効果による1ターンあたりの手札増加(期待値)
    guard_min_cards: int = 0   # 相手はヴァンガードへのアタックに手札からN枚以上でしかガードできない
    guard_tax: int = 0         # 相手のガードに必要なシールド増加(擬似的なガード制限)
    retire_per_turn: float = 0.0  # 相手リアガードを退却させる期待枚数
    self_heal: float = 0.0     # 効果による1ターンあたりの回復期待値
    v_drive_bonus: float = 0.0 # ヴァンガードのドライブ+1が付く確率(例:条件付きトリプルドライブ)
    defense_shield: int = 0    # 相手ターンに1回使える追加シールド(手札を使わない守り。例:ソウルからのガーディアンコール)
    power_growth: int = 0      # 効果が働き始めてから1ターンごとに増える全アタックのパワー(例:成長型ギミック)
    extra_crit: int = 1        # 追加アタックのクリティカル
    # トリガーのガード値 {種類: (コストなしのシールド, コスト(SB1かEB2)を払った時の追加)}
    # 空なら D_TRIG_SHIELD(Dスタンダード標準)を使う。
    trig_shield: dict = field(default_factory=dict)
    over_guard: int = 0        # オーバートリガーを手札からガードに使えるときのシールド(ペルソナは50000)
    online_turn: int = 3       # 効果が働き始める自分のターン数(既定:G3ライドのターン)
    first_vg_name_lock: bool = False  # 予備
    notes: str = ""

    def deck_size(self) -> int:
        return (self.crit + self.draw + self.front + self.heal + self.over
                + self.sentinel + self.g1 + self.g2 + self.g3)

    def build(self) -> list[Card]:
        assert self.deck_size() == 50, f"{self.name}: 山札が{self.deck_size()}枚です(50枚必要)"
        assert self.heal <= 4 and self.over <= 1 and self.sentinel <= 4
        assert self.crit + self.draw + self.front + self.heal + self.over == 16, f"{self.name}: トリガーは16枚"
        cards: list[Card] = []
        table = self.trig_shield or D_TRIG_SHIELD
        for k in TRIGGERS:
            spec = table.get(k)
            for i in range(getattr(self, k)):
                c = make_card(k)
                if spec is not None and k != "over":
                    # 1種類なら全部同じ、リストなら1枚ずつ(足りない分は最後の値)
                    base, boost = spec[min(i, len(spec) - 1)] if isinstance(spec, list) else spec
                    c = Card(k, 0, 5000, base, boost=boost)
                cards.append(c)
        cards += [make_card("sentinel") for _ in range(self.sentinel)]
        cards += [make_card("g1") for _ in range(self.g1)]
        cards += [make_card("g2") for _ in range(self.g2)]
        cards += [make_card("g3", persona=i < self.persona_copies) for i in range(self.g3)]
        return cards


# ---------------------------------------------------------------- プレイヤー状態

@dataclass
class Unit:
    card: Card
    power_bonus: int = 0   # このターンだけの加算


@dataclass
class Player:
    prof: DeckProfile
    rng: random.Random
    deck: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    damage: list[bool] = field(default_factory=list)   # True=表
    soul: int = 0
    energy: int = 0
    v_grade: int = 0
    front: list[Unit | None] = field(default_factory=lambda: [None, None])   # 左右のリアガード
    back: list[Unit | None] = field(default_factory=lambda: [None, None, None])  # 左・中央・右
    turn_count: int = 0
    persona_this_turn: bool = False
    v_temp_power: int = 0
    lost_by_deckout: bool = False
    gain_carry: float = 0.0
    defense_used: bool = False
    online_turns: int = 0
    heal_carry: float = 0.0

    # ---- 基本操作
    def draw(self, n: int = 1) -> None:
        for _ in range(n):
            if not self.deck:
                self.lost_by_deckout = True
                return
            self.hand.append(self.deck.pop())

    def take_top(self) -> Card | None:
        if not self.deck:
            self.lost_by_deckout = True
            return None
        return self.deck.pop()

    @property
    def dmg(self) -> int:
        return len(self.damage)

    def v_power(self) -> int:
        return {0: 6000, 1: 8000, 2: 10000, 3: 13000}[self.v_grade]

    def discard_cheapest(self) -> None:
        """ライドコストなどで手札を1枚捨てる。価値の低いものから。"""
        if not self.hand:
            return
        def value(c: Card) -> float:
            if c.kind == "sentinel":
                return 100
            if c.persona and self.v_grade >= 2:
                return 90
            if c.is_trigger:
                return 50
            if c.kind == "g3":
                return 10
            if c.kind == "g2":
                return 30
            return 25
        c = min(self.hand, key=value)
        self.hand.remove(c)


# ---------------------------------------------------------------- 対戦

class Game:
    def __init__(self, a: DeckProfile, b: DeckProfile, seed: int | None = None,
                 a_first: bool = True):
        self.rng = random.Random(seed)
        pa = Player(a, self.rng)
        pb = Player(b, self.rng)
        self.players = [pa, pb] if a_first else [pb, pa]
        self.a_is_first = a_first
        for p in self.players:
            p.deck = p.prof.build()
            self.rng.shuffle(p.deck)
            p.draw(5)
            self.mulligan(p)

    # 手札交換:G1・G2が無ければ、トリガーと余分なG3を戻す(簡易)
    def mulligan(self, p: Player) -> None:
        keep, back = [], []
        persona_kept = 0
        for c in p.hand:
            if c.kind in ("g1", "g2"):
                keep.append(c)
            elif c.persona and persona_kept < 1:
                keep.append(c); persona_kept += 1
            elif c.kind == "sentinel" and sum(k.kind == "sentinel" for k in keep) < 1:
                keep.append(c)
            else:
                back.append(c)
        p.hand = keep
        p.deck += back
        self.rng.shuffle(p.deck)
        p.draw(len(back))

    # ------------------------------------------------------------ 1ゲーム
    def play(self, max_turns: int = 30) -> int:
        """勝者のインデックス(0=プロファイルa, 1=プロファイルb)を返す。"""
        for t in range(max_turns):
            atk = self.players[t % 2]
            dfn = self.players[(t + 1) % 2]
            first_player = (t % 2 == 0)
            result = self.take_turn(atk, dfn, first_player, t)
            if result is not None:
                return self._winner_index(result)
        # 打ち切り:ダメージが少ない方の勝ち
        p0, p1 = self.players
        loser = p0 if p0.dmg > p1.dmg else p1
        return self._winner_index(p1 if loser is p0 else p0)

    def _winner_index(self, winner: Player) -> int:
        first_is_a = self.a_is_first
        idx_in_players = self.players.index(winner)
        is_a = (idx_in_players == 0) == first_is_a
        return 0 if is_a else 1

    # ------------------------------------------------------------ ターン
    def take_turn(self, p: Player, o: Player, first_player: bool, t: int) -> Player | None:
        p.turn_count += 1
        p.persona_this_turn = False
        p.v_temp_power = 0
        o.v_temp_power = 0
        for u in p.front + p.back:
            if u:
                u.power_bonus = 0

        # ドロー(DZスタンダードは先攻1ターン目もドローする)
        p.draw(1)
        if p.lost_by_deckout:
            return o

        # ライドフェイズ:エネルギーチャージ
        if p.turn_count >= 2 or not first_player:
            pass
        if p.turn_count == 1:
            # ジェネレーターはライド時にクレストへ。後攻ならチャージ3
            if not first_player:
                p.energy = min(10, p.energy + 3)
        else:
            p.energy = min(10, p.energy + 3)

        if p.v_grade < 3:
            # ライドデッキから次のグレードへ(手札1枚コスト)
            p.discard_cheapest()
            p.v_grade += 1
            p.soul += 1
            if p.v_grade == 1 and not first_player:
                p.draw(1)   # G0「後攻ならライドされた時1枚引く」
        else:
            persona = next((c for c in p.hand if c.persona), None)
            if persona:
                p.hand.remove(persona)
                p.soul += 1
                p.draw(1)
                p.persona_this_turn = True

        prof = p.prof
        online = p.turn_count >= prof.online_turn and p.v_grade >= 3

        # 効果による手札増加・回復(期待値を端数繰り越しで整数化)
        if online:
            p.online_turns += 1
            p.gain_carry += prof.card_gain
            while p.gain_carry >= 1:
                p.draw(1); p.gain_carry -= 1
            while p.gain_carry <= -1 and p.hand:
                p.discard_cheapest(); p.gain_carry += 1
            p.heal_carry += prof.self_heal
            while p.heal_carry >= 1 and p.dmg > 0:
                p.damage.pop(); p.heal_carry -= 1
            # 相手リアガードの退却
            r = prof.retire_per_turn
            while r > 0:
                if r >= 1 or self.rng.random() < r:
                    self._retire_one(o)
                r -= 1
        if p.lost_by_deckout:
            return o

        # メインフェイズ:コール
        self.call_units(p, o, first_player)

        # バトルフェイズ(先攻1ターン目はアタックできない)
        if first_player and p.turn_count == 1:
            return None
        return self.battle(p, o, online)

    def _retire_one(self, o: Player) -> None:
        for i in range(2):
            if o.front[i]:
                o.front[i] = None
                return
        for i in (0, 2, 1):
            if o.back[i]:
                o.back[i] = None
                return

    # ------------------------------------------------------------ コール
    def call_units(self, p: Player, o: Player, first_player: bool) -> None:
        g = p.v_grade
        # どれだけ盤面に出すか:自分のG2以降は前列を埋め、G3以降は後列も埋める
        want_front = 2 if g >= 2 else (1 if o.dmg <= 1 and g >= 1 and not (first_player and p.turn_count == 1) else 0)
        want_back = 3 if g >= 3 else (1 if g >= 2 else 0)
        # 守りのために手札は最低限残す(完全ガードと、相手の脅威に応じた枚数)
        reserve = 2 if p.dmg >= 4 else 1

        def callable_cards(max_grade: int) -> list[Card]:
            return sorted([c for c in p.hand if c.kind in ("g1", "g2", "g3") and c.grade <= max_grade
                           and not (c.persona and g >= 2 and sum(k.persona for k in p.hand) <= 1)],
                          key=lambda c: -c.power)

        for i in range(2):
            if want_front <= sum(1 for u in p.front if u):
                break
            if p.front[i] is None:
                cands = [c for c in callable_cards(g) if c.kind in ("g2", "g3")] or callable_cards(g)
                if cands and len(p.hand) > reserve:
                    c = cands[0]; p.hand.remove(c); p.front[i] = Unit(c)
        for i in (1, 0, 2):
            if want_back <= sum(1 for u in p.back if u):
                break
            if p.back[i] is None:
                cands = [c for c in callable_cards(g) if c.kind == "g1"] or []
                if cands and len(p.hand) > reserve:
                    c = cands[0]; p.hand.remove(c); p.back[i] = Unit(c)

    # ------------------------------------------------------------ バトル
    def battle(self, p: Player, o: Player, online: bool) -> Player | None:
        prof = p.prof
        persona_bonus = 10000 if p.persona_this_turn else 0
        o.defense_used = False
        growth = prof.power_growth * max(0, p.online_turns - 1) if online else 0

        # アタックする列を組み立てる [(種類, 列番号)]。リアガード→ヴァンガード→リアガードの順
        lines = []
        if p.front[0]: lines.append(("R", 0))
        lines.append(("V", 1))
        if p.front[1]: lines.append(("R", 2))
        # 追加アタック
        n_extra = 0
        if online and prof.extra_attacks > 0:
            n_extra = int(prof.extra_attacks) + (1 if self.rng.random() < prof.extra_attacks - int(prof.extra_attacks) else 0)

        pending_trigger_power = {0: 0, 1: 0, 2: 0}
        pending_crit = {0: 0, 1: 0, 2: 0}

        def booster(col: int) -> int:
            b = p.back[col]
            return b.card.power if b else 0

        attacks_done = 0
        for kind, col in lines:
            if kind == "V":
                power = p.v_power() + booster(1) + persona_bonus + (prof.v_power if online else 0) + p.v_temp_power + growth
                crit = 1
                if online and prof.v_crit > 0:
                    # 小数は確率として扱う(例:0.5 → 半分のターンで+1)
                    crit += int(prof.v_crit) + (1 if self.rng.random() < prof.v_crit - int(prof.v_crit) else 0)
                drives = 2 if p.v_grade >= 3 else 1
                if online and self.rng.random() < prof.v_drive_bonus:
                    drives += 1
            else:
                u = p.front[0 if col == 0 else 1]
                power = u.card.power + booster(col) + persona_bonus + (prof.r_power if online else 0) + u.power_bonus + growth
                crit = 1
                drives = 0
            power += pending_trigger_power[col]; crit += pending_crit[col]
            pending_trigger_power[col] = 0; pending_crit[col] = 0
            res = self.resolve_attack(p, o, kind, power, crit, drives, col, pending_trigger_power, pending_crit,
                                      lines[attacks_done + 1:], online)
            attacks_done += 1
            if res is not None:
                return res
            if p.lost_by_deckout:
                return o
        for _ in range(n_extra):
            power = prof.extra_power + persona_bonus + growth
            res = self.resolve_attack(p, o, "R", power, prof.extra_crit, 0, 0, pending_trigger_power, pending_crit, [], online)
            if res is not None:
                return res
        return None

    def resolve_attack(self, p: Player, o: Player, kind: str, power: int, crit: int, drives: int,
                       col: int, pend_pow: dict, pend_crit: dict, remaining: list, online: bool) -> Player | None:
        # 攻撃対象は常にヴァンガード(簡易)
        defense = o.v_power() + o.v_temp_power
        guarded = self.defend(o, p, power, crit, drives, defense, kind == "V" and online)

        # ドライブチェック
        for _ in range(drives):
            c = p.take_top()
            if c is None:
                return o
            p.hand.append(c)
            if c.is_trigger:
                power, crit = self._apply_trigger(p, o, c, power, crit, remaining, pend_pow, pend_crit, drive=True)
        if guarded == "perfect":
            return None
        total_def = defense + (guarded if isinstance(guarded, int) else 0)
        if power >= total_def:
            for _ in range(crit):
                lost = self.deal_damage(o, p)
                if lost:
                    return p
        return None

    def _apply_trigger(self, p: Player, o: Player, c: Card, power: int, crit: int, remaining: list,
                       pend_pow: dict, pend_crit: dict, drive: bool):
        """ドライブチェックのトリガー。パワーは、相手が5点ならアタック中のユニットへ、
        そうでなければまだアタックしていない列へ振る(簡易な振り分け)。"""
        target_now = o.dmg >= 4 or not remaining
        if c.kind == "front":
            power += 10000
            for k, ccol in remaining:
                pend_pow[ccol] += 10000
        elif c.kind == "over":
            power += 100_000_000
            p.draw(1)
        else:
            if target_now:
                power += 10000
            else:
                pend_pow[remaining[0][1]] += 10000
            if c.kind == "crit":
                if target_now or o.dmg >= 3:
                    crit += 1
                else:
                    pend_crit[remaining[0][1]] += 1
            elif c.kind == "draw":
                p.draw(1)
            elif c.kind == "heal":
                if p.dmg > 0 and p.dmg >= o.dmg:
                    p.damage.pop()
        return power, crit

    def deal_damage(self, o: Player, p: Player) -> bool:
        c = o.take_top()
        if c is None:
            return True
        o.damage.append(True)
        if c.is_trigger:
            if c.kind == "heal" and o.dmg >= p.dmg and o.dmg < 6:
                o.damage.pop()
            elif c.kind == "draw":
                o.draw(1)
            elif c.kind == "over":
                o.draw(1)
                o.v_temp_power += 100_000_000
            # クリティカル/前列/ヒールのパワーは自分のヴァンガードへ(このターンの防御に効く)
            if c.kind in ("crit", "front", "heal", "draw"):
                o.v_temp_power += 10000
        return o.dmg >= 6

    # ------------------------------------------------------------ ガード判断
    def defend(self, o: Player, p: Player, power: int, crit: int, drives: int, defense: int,
               restrict: bool):
        """ガードを決めて手札から出す。戻り値は追加シールド値か 'perfect'。"""
        prof = p.prof
        if power < defense:
            return 0
        hit_dmg = o.dmg + crit
        # 何回分のトリガーに耐えるか(パス数)
        if hit_dmg >= 6:
            passes = drives            # 致命傷:ドライブ全部に耐える
        elif o.dmg >= 4 and drives:
            passes = 1
        elif o.dmg >= 3 and drives == 0:
            passes = 0
        else:
            # 序盤はノーガード
            if o.dmg <= 2 or (o.dmg == 3 and drives == 0 and len(o.hand) < 5):
                return 0
            passes = 0
        need = power - defense + 1 + passes * 10000 + (prof.guard_tax if restrict else 0)
        min_cards = prof.guard_min_cards if restrict else 0
        # 手札を使わない守り(1ターン1回)
        free = 0
        if o.prof.defense_shield and o.v_grade >= 3 and not o.defense_used and need > 0 and passes > 0:
            free = o.prof.defense_shield
            o.defense_used = True
            need -= free
            if need <= 0:
                return free

        # 完全ガードを優先する場面:致命傷 or 4点以上でのヴァンガードアタック
        sentinels = [c for c in o.hand if c.kind == "sentinel"]
        if sentinels and (hit_dmg >= 6 or (o.dmg >= 4 and drives)) and len(o.hand) >= 2 and need > 10000:
            if min_cards <= 1:
                o.hand.remove(sentinels[0])
                o.discard_cheapest()
                return "perfect"

        # 通常ガード:価値の低いカードから必要量を満たす
        pool = []
        for c in o.hand:
            if c.kind == "sentinel":
                continue
            if c.is_trigger:
                if c.kind == "over":
                    if o.prof.over_guard and hit_dmg >= 6:
                        pool.append((c, o.prof.over_guard, 3))
                    continue
                base, boost = c.shield, c.boost
                bonus = boost if (boost and (o.soul >= 1 or o.energy >= 2)) else 0
                pool.append((c, base + bonus, 2 if bonus else 1))
            elif c.shield > 0:
                pool.append((c, c.shield, 0))
        # 優先度:シールド効率の良いものから
        pool.sort(key=lambda x: (-x[1], x[2]))
        chosen, total = [], 0
        for c, s, _ in pool:
            if total >= need and len(chosen) >= min_cards:
                break
            chosen.append((c, s)); total += s
        # インターセプト(前列のG2)
        intercept = 0
        if total < need:
            for i in range(2):
                u = o.front[i]
                if u and u.card.kind == "g2":
                    intercept = 5000
                    o.front[i] = None
                    break
        if total + intercept < need or len(chosen) < min_cards:
            # 守り切れないなら、致命傷でない限り出さない
            if hit_dmg < 6:
                if intercept:
                    return intercept + free  # インターセプトは使ったまま
                return free
        for c, s in chosen:
            o.hand.remove(c)
            if c.is_trigger and c.kind != "over" and s > c.shield:
                if o.energy >= 2:
                    o.energy -= 2
                elif o.soul >= 1:
                    o.soul -= 1
        return total + intercept + free


# ---------------------------------------------------------------- 集計

def simulate(a: DeckProfile, b: DeckProfile, n: int = 2000, seed: int = 0) -> dict:
    """先攻・後攻を半々にしてn戦。aの勝率などを返す。"""
    wins_first = wins_second = 0
    half = n // 2
    for i in range(half):
        if Game(a, b, seed=seed * 1_000_003 + i, a_first=True).play() == 0:
            wins_first += 1
        if Game(a, b, seed=seed * 1_000_003 + half + i, a_first=False).play() == 0:
            wins_second += 1
    return {
        "win_rate": (wins_first + wins_second) / (2 * half),
        "win_rate_first": wins_first / half,
        "win_rate_second": wins_second / half,
        "games": 2 * half,
    }
