"""
ペルソナ30th(DZ-TBP02)のカードデータ(カード単位エンジン用)

公式カードリストの効果テキストを、エンジンが扱える項目に書き換えたもの。
- series: そのカードが効果を発揮するヴァンガード('P3'はP3とP3Pの両方、'P3only'/'P3Ponly'は片方のみ)
- arcana: アルカナ。P3only/P3Ponly付きは {'P3':..,'P3P':..} で指定
- tags: 種族(team=S.E.E.S./自称特別捜査隊/心の怪盗団、kyoryoku=協力者、velvet=ベルベットルームの住人、komyu=コミュ/コープ)
- persona: ペルソナゲージのトークン(下の GAUGES のキー)
- on_call: (R)に登場した時の効果のキー(エンジンの effect_on_call で処理)
"""

# ---- ペルソナゲージ
# passive_g3: 相手のヴァンガードがG3以上ならアタック中+5000(自分のターン)
# skills: [(コスト種類, コスト量, パワー, その他)]  コスト種類: 'EB','SB','free_retire'(アサルトダイブ)
# evolved: 覚醒/転生/進化後 (パワー+10000、スキル)
# ヴァンガードのペルソナゲージ(G1主人公のライド時に置かれ、ライドしても引き継ぐ。進化/転生しても元のゲージは残る)
VG_GAUGES = {
    "オルフェウス": dict(flat=0, skills=[("EB", 1, 5000, ""), ("EB", 2, 0, "tarunda")]),
    "タナトス": dict(flat=5000, skills=[("EB", 2, 0, "retire")]),
    "メサイア": dict(flat=5000, skills=[("EB", 2, 0, "crit")]),
    "イザナギ": dict(flat=0, skills=[("SB", 1, 5000, ""), ("EB", 1, 5000, "")], evolve="伊邪那岐大神"),
    "伊邪那岐大神": dict(flat=10000, skills=[("EB", 2, 10000, "lock")]),
    "アルセーヌ": dict(flat=0, skills=[("EB", 1, 5000, ""), ("SB", 1, 0, "recycle")], evolve="ラウール"),
    "ラウール": dict(flat=10000, skills=[("EB", 2, 15000, "")]),
}
VG_BASE = {"P3": "オルフェウス", "P4": "イザナギ", "P5": "アルセーヌ"}

GAUGES = {
    # P3
    "イオ": dict(g3=5000, skills=[("EB", 1, 5000, "g3bonus"), ("EB", 2, 0, "recarm")], evolve="イシス"),
    "イシス": dict(flat=10000, skills=[("EB", 3, 0, "CC1")]),
    "ヘルメス": dict(g3=5000, skills=[("EB", 2, 10000, ""), ("free_retire", 0, 10000, "")], evolve="トリスメギストス"),
    "トリスメギストス": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "パラディオン": dict(g3=5000, skills=[("SB", 1, 10000, ""), ("SB", 1, 5000, "sweep_g3")], evolve="アテナ"),
    "アテナ": dict(flat=10000, skills=[("SB", 2, 15000, "sweep")]),
    "ポリデュークス": dict(g3=5000, skills=[("EB", 2, 5000, "lock"), ("EB", 2, 0, "tarunda")], evolve="カエサル"),
    "カエサル": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "ペンテシレア": dict(g3=5000, skills=[("EB", 2, 5000, "lock"), ("EC", 0, 0, "ec_grade")], evolve="アルテミシア"),
    "アルテミシア": dict(flat=10000, skills=[("EB", 2, 10000, "lock")]),
    "ケルベロス": dict(g3=5000, skills=[("EB", 3, 15000, "g3only"), ("EB", 1, 5000, "")]),
    "ネメシス": dict(g3=5000, skills=[("EB", 2, 10000, ""), ("EB", 2, 5000, "lock")], evolve="カーラ・ネミ"),
    "カーラ・ネミ": dict(flat=10000, skills=[("EB", 3, 20000, "")]),
    "カストール": dict(g3=5000, skills=[("SB", 1, 10000, ""), ("SB", 1, 5000, "sweep_g3")]),
    # P4
    "ジライヤ": dict(g3=5000, skills=[("SB", 1, 5000, "persona_bonus"), ("EB", 1, 5000, "")], evolve="タケハヤスサノオ"),
    "タケハヤスサノオ": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "トモエ": dict(g3=5000, skills=[("SB", 1, 5000, ""), ("SB", 1, 5000, "sweep_g3")], evolve="ハラエドノオオカミ"),
    "ハラエドノオオカミ": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "コノハナサクヤ": dict(g3=5000, skills=[("EB", 2, 10000, ""), ("EB", 2, 0, "recarm")], evolve="スメオオミカミ"),
    "スメオオミカミ": dict(flat=10000, skills=[("EB", 3, 10000, "sweep")]),
    "タケミカヅチ": dict(g3=5000, skills=[("SB", 1, 10000, ""), ("EB", 2, 5000, "lock")], evolve="タケジザイテン"),
    "タケジザイテン": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "キントキドウジ": dict(g3=5000, skills=[("EB", 2, 5000, "lock")], evolve="カムイモシリ"),
    "カムイモシリ": dict(flat=10000, skills=[("EB", 2, 8000, "")]),
    "スクナヒコナ": dict(g3=5000, skills=[("SB", 1, 0, "guard2"), ("EB", 1, 5000, "g3only")], evolve="ヤマトスメラミコト"),
    "ヤマトスメラミコト": dict(flat=10000, skills=[("EB", 3, 10000, "crit")]),
    # P5
    "キャプテン・キッド": dict(g3=5000, skills=[("free_retire", 0, 10000, ""), ("EB", 2, 5000, "lock")], evolve="ウィリアム"),
    "ウィリアム": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "カルメン": dict(g3=5000, skills=[("EB", 2, 10000, ""), ("EB", 2, 0, "tarunda")], evolve="セレスティーヌ"),
    "セレスティーヌ": dict(flat=10000, skills=[("EB", 3, 10000, "sweep")]),
    "ロビンフッド": dict(g3=5000, skills=[("EB", 1, 5000, ""), ("EB", 3, 5000, "hamaon")], evolve="ヘリワード"),
    "ヘリワード": dict(flat=10000, skills=[("EB", 3, 10000, "crit")]),
    "ゾロ": dict(g3=5000, skills=[("EB", 1, 5000, "g3bonus"), ("EB", 2, 0, "recarm")], evolve="ディエゴ"),
    "ディエゴ": dict(flat=10000, skills=[("SB", 1, 5000, "sweep")]),
    "ゴエモン": dict(g3=5000, skills=[("EB", 2, 5000, "lock"), ("SB", 1, 0, "guard2")], evolve="ゴロキチ"),
    "ゴロキチ": dict(flat=10000, skills=[("SB", 1, 15000, "")]),
    "ヨハンナ": dict(g3=5000, skills=[("EB", 2, 10000, "")], evolve="アグネス"),
    "アグネス": dict(flat=10000, skills=[("EB", 3, 0, "debuff")]),
    "ミラディ": dict(g3=5000, skills=[("SB", 2, 10000, "retire"), ("EB", 1, 5000, "")], evolve="ルーシー"),
    "ルーシー": dict(flat=10000, skills=[]),
    "サンドリヨン": dict(g3=5000, skills=[("SB", 1, 10000, "g3only"), ("EB", 2, 10000, "g3only")], evolve="エラ"),
    "エラ": dict(flat=10000, skills=[("SB", 1, 10000, "")]),
}

# ---- カード
# kind: unit / trigger / sentinel
C = {}


def card(no, name, grade, kind="unit", power=None, shield=5000, tags=(), arcana=None, persona=None,
         on_call=None, series=None, trig=None, guard=None, extra=None):
    if power is None:
        power = {0: 6000, 1: 8000, 2: 10000, 3: 13000}[grade]
    C[no] = dict(no=no, name=name, grade=grade, kind=kind, power=power, shield=shield, tags=set(tags),
                 arcana=arcana, persona=persona, on_call=on_call, series=series, trig=trig,
                 guard=guard, extra=extra or {})


# エース(どのデッキでも同じ効果)
for no, nm in [("001", "もう一人の自分 主人公(P1)"), ("002", "手放せないもの 主人公(P2罪)"),
               ("003", "思い出してはならないもの 主人公(P2罰)"), ("023", "想像を超える者 主人公(P5X)/WONDER")]:
    card(no, nm, 2, shield=0, tags=("ace",), on_call="ace_search", series="any")

# ===== P3
card("004", "“死”に立ち向かう者 主人公(P3)", 3, shield=0, tags=("team", "g3", "P3only"), arcana="審判", series="P3")
card("005", "“死”に抗う者 主人公(P3P)", 3, shield=0, tags=("team", "g3", "P3Ponly"), arcana="審判", series="P3P")
card("006", "父の死の真相を求めて 岳羽 ゆかり", 2, tags=("team", "komyu"), arcana="恋愛", persona="イオ", on_call="cb_draw", series="P3")
card("007", "お調子者の爆炎スラッガー 伊織 順平", 2, tags=("team", "komyu"), arcana={"P3P": "魔術師"}, persona="ヘルメス", on_call="cb_draw", series="P3")
card("008", "機械の乙女 アイギス", 2, tags=("team", "komyu"), arcana="永劫", persona="パラディオン", series="P3", extra={"drop_call": ("EB", 2)})
card("029", "無敗のボクサー 真田 明彦", 2, tags=("team", "komyu"), arcana={"P3P": "星"}, persona="ポリデュークス", series="P3", extra={"end_soul_draw": True})
card("030", "高嶺の生徒会長 桐条 美鶴", 2, tags=("team", "komyu"), arcana="女帝", persona="ペンテシレア", series="P3", extra={"end_soul_draw": True})
card("031", "義理堅き忠犬 コロマル", 2, tags=("team", "komyu"), arcana={"P3P": "剛毅"}, persona="ケルベロス", on_call="cb_retire", series="P3")
card("032", "心に秘めた想い 天田 乾", 2, tags=("team", "komyu"), arcana={"P3P": "正義"}, persona="ネメシス", on_call="cb_recall_amada", series="P3")
card("033", "贖罪の在処 荒垣 真次郎", 2, tags=("team", "komyu"), arcana={"P3P": "月"}, persona="カストール", series="P3", guard="aragaki")
card("009", "余命いくばくもない青年 神木 秋成", 1, tags=("kyoryoku", "komyu"), arcana="太陽", on_call="cb_discard_opp", series="P3")
card("010", "謎の少年(ファルロス)", 1, tags=("kyoryoku", "komyu"), arcana="死神", series="P3", extra={"boost_draw": True})
card("024", "探求する者 エリザベス", 1, tags=("velvet",), series="P3", extra={"discard_search": "persona_ability", "elizabeth": True})
card("058", "ベルベットルームの主 イゴール", 1, tags=("velvet",), series="any", extra={"discard_search": "velvet"})
card("082", "ベルベットルームのベルボーイ テオドア", 1, tags=("velvet",), series="any", guard="theodore", extra={"drop_follow": True})
card("034", "テレビ通販社長 たなか社長", 1, tags=("kyoryoku", "komyu"), arcana="悪魔", on_call="eb3_retire", series="P3")
card("060", "自分にしかできないこと 山岸 風花", 1, kind="sentinel", power=6000, shield=0, tags=("team", "komyu"), arcana="女教皇", series="P3")
card("061", "クラスメイト 友近 健二", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "魔術師"}, series="P3only")
card("062", "文化部 平賀 慶介", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "運命"}, on_call="sc1", series="P3only")
card("063", "生徒会会計 伏見 千尋", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "正義"}, on_call="fushimi", series="P3only")
card("064", "謎の転入生 望月 綾時", 1, tags=("kyoryoku", "komyu"), arcana={"P3P": "運命"}, on_call="ayato", series="P3")
card("091", "運動部の同級生 岩崎 理緒", 1, tags=("kyoryoku", "komyu"), arcana={"P3P": "戦車"}, on_call="cb_self_5000", series="P3Ponly")
card("092", "神社の女の子 大橋 舞子", 1, tags=("kyoryoku", "komyu"), arcana="刑死者", on_call="maiko", series="P3")
card("093", "生徒会 小田桐 秀利", 1, tags=("kyoryoku", "komyu"), arcana="皇帝", on_call="eb1_lock", series="P3")
card("094", "古本屋の老夫婦 北村 文吉＆光子", 1, tags=("kyoryoku", "komyu"), arcana="法王", on_call="ec1", series="P3")
card("095", "グルメキング 末光 望美", 1, tags=("kyoryoku", "komyu"), arcana="月", on_call="eb2_cc1", series="P3")
card("096", "女子マネージャー 西脇 結子", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "剛毅"}, series="P3only")
card("097", "大人びた女子生徒 長谷川 沙織", 1, tags=("kyoryoku", "komyu"), arcana={"P3P": "隠者"}, on_call="eb1_zero_retire", series="P3Ponly")
card("098", "他校のエース 早瀬 護", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "星"}, on_call="hayase", series="P3only")
card("099", "同好会の留学生 ベベ", 1, tags=("kyoryoku", "komyu"), arcana="節制", on_call="eb1_lock", series="P3")
card("100", "運動部 宮本 一志", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "戦車"}, on_call="cb_self_5000", series="P3only")
card("101", "型破りな僧侶 無達", 1, tags=("kyoryoku", "komyu"), arcana="塔", on_call="sc1", series="P3")
card("102", "インターネット Y子", 1, tags=("kyoryoku", "komyu"), arcana={"P3": "隠者"}, on_call="eb1_zero_retire", series="P3only")

# ===== P4
card("011", "真実を追う者 主人公(P4)", 3, shield=0, tags=("team", "g3"), arcana="愚者", series="P4")
card("012", "無二の相棒 花村 陽介", 2, tags=("team", "komyu"), arcana="魔術師", persona="ジライヤ", on_call="cb_draw", series="P4")
card("013", "カンフーマニア 里中 千枝", 2, tags=("team", "komyu"), arcana="戦車", persona="トモエ", on_call="chie_search", series="P4")
card("014", "天城屋旅館の次期女将 天城 雪子", 2, tags=("team", "komyu"), arcana="女教皇", persona="コノハナサクヤ", on_call="yukiko", series="P4")
card("039", "漢の花道 巽 完二", 2, tags=("team", "komyu"), arcana="皇帝", persona="タケミカヅチ", on_call="cb_retire", series="P4")
card("040", "クマはクマクマ！", 2, tags=("team", "komyu"), arcana="星", persona="キントキドウジ", on_call="kuma", series="P4")
card("041", "探偵王子 白鐘 直斗", 2, tags=("team", "komyu"), arcana="運命", persona="スクナヒコナ", series="P4", extra={"drop_call": ("CB", 1)})
card("015", "ほんとの妹 堂島 菜々子", 1, tags=("kyoryoku", "komyu"), arcana="正義", on_call="nanako", series="P4")
card("016", "ベルベットルームの住人見習い マリー", 1, tags=("velvet", "komyu"), arcana="永劫", on_call="marie", series="P4")
card("042", "運動部のマネージャー 海老原 あい", 1, tags=("kyoryoku", "komyu"), arcana="月", on_call="search_g3_soul", series="P4")
card("043", "神社復興を目指して キツネ", 1, tags=("kyoryoku", "komyu"), arcana="隠者", on_call="cb1_ec3", series="P4")
card("083", "ベルベットルームの秘書 マーガレット", 1, tags=("velvet", "komyu"), arcana="女帝", on_call="draw_soul", series="P4")
card("069", "稲羽市立病院のナース 上原 小夜子", 1, tags=("kyoryoku", "komyu"), arcana="悪魔", on_call="drop_to_soul", series="P4")
card("070", "喪服の老婦人 黒田 ひさ乃", 1, tags=("kyoryoku", "komyu"), arcana="死神", on_call="loot", series="P4")
card("071", "稲羽警察署の刑事 堂島 遼太郎", 1, tags=("kyoryoku", "komyu"), arcana="法王", on_call="ryotaro", series="P4")
card("111", "バスケ部の同級生 一条 康", 1, tags=("kyoryoku", "komyu"), arcana="剛毅", on_call="sc1", series="P4")
card("112", "演劇部の同級生 小沢 結実", 1, tags=("kyoryoku", "komyu"), arcana="太陽", on_call="ec1", series="P4")
card("113", "酒屋の息子 小西 尚紀", 1, tags=("kyoryoku", "komyu"), arcana="刑死者", on_call="ec1", series="P4")
card("117", "学童保育の若い母親 南 絵里", 1, tags=("kyoryoku", "komyu"), arcana="節制", on_call="sc1", series="P4")
card("115", "サッカー部の同級生 長瀬 大輔", 1, tags=("kyoryoku", "komyu"), arcana="剛毅", on_call="sc1", series="P4")
card("116", "吹奏楽部の下級生 松永 綾音", 1, tags=("kyoryoku", "komyu"), arcana="太陽", on_call="ec1", series="P4")
card("067", "女子高生アイドル 久慈川 りせ", 1, kind="sentinel", power=6000, shield=0, tags=("team", "komyu"), arcana="恋愛", series="P4")

# ===== P5
card("017", "TAKE YouR hEaRT 主人公(P5)/JOKER", 3, shield=0, tags=("team", "g3"), arcana="愚者", series="P5")
card("018", "怪盗団の切り込み隊長 坂本 竜司/SKULL", 2, tags=("team", "komyu"), arcana="戦車", persona="キャプテン・キッド", series="P5", extra={"ryuji": True})
card("019", "怪盗団の演技派(?)女優 高巻 杏/PANTHER", 2, tags=("team", "komyu"), arcana="恋愛", persona="カルメン", on_call="cb_draw", series="P5")
card("020", "探偵王子の再来 明智 吾郎/CROW", 2, tags=("team", "komyu"), arcana="正義", persona="ロビンフッド", series="P5", extra={"akechi": True})
card("048", "怪盗団の先導役 モルガナ/MONA", 2, tags=("team", "komyu"), arcana="魔術師", persona="ゾロ", on_call="morgana", series="P5")
card("049", "怪盗団の芸術家 喜多川 祐介/FOX", 2, tags=("team", "komyu"), arcana="皇帝", persona="ゴエモン", on_call="drop_to_soul", series="P5")
card("050", "怪盗団の作戦参謀 新島 真/QUEEN", 2, tags=("team", "komyu"), arcana="女教皇", persona="ヨハンナ", on_call="cb_retire", series="P5")
card("051", "怪盗団の御令嬢 奥村 春/NOIR", 2, tags=("team", "komyu"), arcana="女帝", persona="ミラディ", on_call="ec2", series="P5")
card("052", "仮初の栄華 芳澤 かすみ/VIOLET", 2, tags=("team", "komyu"), arcana="信念", persona="サンドリヨン", series="P5", extra={"kasumi": True})
card("021", "怪盗専属の闇医者 武見 妙", 1, tags=("kyoryoku", "komyu"), arcana="死神", on_call="takemi", series="P5")
card("022", "神算鬼謀の女棋士 東郷 一二三", 1, tags=("kyoryoku", "komyu"), arcana="星", on_call="togo", series="P5")
card("053", "二つの顔を持つ女教師 川上 貞代", 1, tags=("kyoryoku", "komyu"), arcana="節制", on_call="kawakami", series="P5")
card("059", "更生を見守る双子の看守 ジュスティーヌ & カロリーヌ", 1, tags=("velvet", "komyu"), arcana="剛毅", on_call="jc_buff", series="P5")
card("075", "居候先の監視者 佐倉 惣治郎", 1, tags=("kyoryoku", "komyu"), arcana="法王", on_call="ec1", series="P5")
card("076", "尋問室の女検事 新島 冴", 1, tags=("kyoryoku", "komyu"), arcana="審判", on_call="sae", series="P5")
card("078", "自称･怪盗団の広報役 三島 由輝", 1, tags=("kyoryoku", "komyu"), arcana="月", on_call="search_g3_soul", series="P5")
card("079", "運命を見通す占い師 御船 千早", 1, tags=("kyoryoku", "komyu"), arcana="運命", on_call="chihaya", series="P5")
card("124", "訳アリの武器調達人 岩井 宗久", 1, tags=("kyoryoku", "komyu"), arcana="刑死者", on_call="iwai", series="P5")
card("127", "不撓不屈の政治家 吉田 寅之助", 1, tags=("kyoryoku", "komyu"), arcana="太陽", on_call="draw_soul", series="P5")
card("084", "ベルベットルームのコンシェルジュ メロペ", 1, tags=("velvet",), series="any")
card("068", "若い刑事 足立 透", 1, tags=("kyoryoku", "komyu"), arcana="道化師", series="P4")          # 遼太郎との組み合わせ効果は未実装
card("077", "非常勤カウンセラー 丸喜 拓人", 1, tags=("kyoryoku", "komyu"), arcana="顧問官", series="P5")  # ヴァンガードを選ばせない効果は未実装
card("074", "怪盗団の天才ハッカー 佐倉 双葉/NAVI", 1, kind="sentinel", power=6000, shield=0, tags=("team", "komyu"), arcana="隠者", series="P5")

# ===== トリガーオーダー (guard: (シールド, コストで追加, 条件))
def trig(no, name, t, guard):
    card(no, name, 0, kind="trigger", power=0, shield=guard[0], trig=t, extra={"guard": guard})


for no, nm, t, g in [
    ("025", "オルフェウス！", "over", (50000, 0, "")), ("027", "イザナギ！", "over", (50000, 0, "")), ("028", "アルセーヌ！", "over", (50000, 0, "")),
    ("035", "お願い、イオ！", "draw", (5000, 15000, "")), ("036", "力を貸せ、ネメシス！", "front", (15000, 0, "")),
    ("037", "ルキア", "heal", (10000, 15000, "crit2")), ("038", "ユノ", "heal", (10000, 15000, "second")),
    ("065", "パラディオン、起動！", "crit", (15000, 0, "ec1")), ("066", "ワオォーン！！", "front", (20000, 0, "")),
    ("103", "やっちまえ！ヘルメス！", "crit", (15000, 0, "")), ("104", "来い！ポリデュークス！", "crit", (15000, 0, "")),
    ("105", "カストール！", "crit", (15000, 0, "")), ("106", "行くぞ、ペンテシレア！", "draw", (5000, 0, "")),
    ("107", "お願い、ルキア…！", "heal", (15000, 0, "")),
    ("044", "来い、ジライヤ！", "draw", (5000, 15000, "")), ("045", "来い、スクナヒコナ！", "front", (15000, 0, "")),
    ("046", "ヒミコ", "heal", (10000, 15000, "crit2")), ("047", "コウゼオン", "heal", (10000, 15000, "second")),
    ("072", "守って、トモエ！", "crit", (15000, 0, "ec1")), ("073", "おいで、コノハナサクヤ！", "front", (20000, 0, "")),
    ("118", "いくぜ、タケミカヅチ！", "crit", (15000, 0, "")), ("119", "GO！キントキドウジ！", "draw", (5000, 0, "")),
    ("120", "おいで、ヒミコ！", "heal", (15000, 0, "")),
    ("054", "威を示せ、ゾロ！", "draw", (5000, 15000, "")), ("055", "射殺せ、ロビンフッド！", "front", (15000, 0, "")),
    ("056", "ネクロノミコン", "heal", (10000, 15000, "crit2")), ("057", "アルアジフ", "heal", (10000, 15000, "second")),
    ("080", "駆けろ、ヨハンナ！", "crit", (15000, 0, "ec1")), ("081", "踊れ！カルメン！", "front", (20000, 0, "")),
    ("128", "奪え！キッド！", "crit", (15000, 0, "")), ("129", "惑わせ、ミラディ！", "crit", (15000, 0, "")),
    ("130", "魅せて！サンドリヨン！", "crit", (15000, 0, "")), ("131", "蹴散らせ、ゴエモン！", "draw", (5000, 0, "")),
    ("132", "楽勝だったンゴ～！", "heal", (15000, 0, "")),
]:
    trig(no, nm, t, g)

# ライドデッキ(主人公)
RIDE_LINES = {
    "P3": ["089", "087", "085", "004"],
    "P3P": ["090", "088", "086", "005"],
    "P4": ["110", "109", "108", "011"],
    "P5": ["123", "122", "121", "017"],
}
for no in ["089", "090", "110", "123"]:
    card(no, f"G0主人公{no}", 0, tags=("team",))
for no in ["087", "088", "109", "122"]:
    card(no, f"G1主人公{no}", 1, tags=("team",))
for no in ["085", "086", "108", "121"]:
    card(no, f"G2主人公{no}", 2, tags=("team",), arcana="愚者")

SERIES_OF_DECK = {"P3": "P3", "P3P": "P3", "P4": "P4", "P5": "P5"}


def arcana_of(cdef, deck_key):
    a = cdef["arcana"]
    if isinstance(a, dict):
        return a.get(deck_key)
    return a


def works_in(cdef, deck_key):
    """そのデッキのヴァンガードでカードの効果が働くか"""
    s = cdef["series"]
    if s in (None, "any"):
        return True
    if s == "P3only":
        return deck_key == "P3"
    if s == "P3Ponly":
        return deck_key == "P3P"
    if s == "P3":
        return deck_key in ("P3", "P3P")
    return s == deck_key

# 他の弾のカード(2026/11/08):レガリスピースのノーマルオーダー(DZ-SS10/019)。デッキに1枚、ファイト中1回
card("R19", "純黒の影は闇に蠢く", 3, kind="order", power=0, shield=0, series="any", extra={"regalis": True})
