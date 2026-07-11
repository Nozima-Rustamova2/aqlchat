"""Reference utterances for the intent classifier (app/intent/classifier.py).

Deliberately phrased WITHOUT the keywords already used in
examples/flows.example.yaml (e.g. no "salom"/"rahmat"/"operator") - the
whole value of a semantic layer on top of the keyword-based flow executor
(app/flows/executor.py) is catching paraphrases the keyword list misses.
Reusing the same keywords here would just re-detect what substring
matching already catches, adding embedding-inference cost for nothing.

One Uzbek Latin / Uzbek Cyrillic / Russian anchor per intent, following the
same pattern used for the BGE-M3 cross-lingual check and the FAQ threshold
calibration (see aqlchat-phase1-plan memory).

"product_inquiry" has no merchant-configurable reply - see
app/intent/router.py, which resolves it against the products table
instead of a canned flow reply.
"""

ANCHORS: dict[str, list[str]] = {
    "greeting": [
        "Xayrli kun sizga",
        "Хайрли кун сизга",
        "Добрый день",
    ],
    "thanks": [
        "Judayam minnatdorman sizga",
        "Жудаям миннатдорман сизга",
        "Я вам очень благодарен",
    ],
    "human_handoff": [
        "Menejeringiz bilan bog'lansam bo'ladimi",
        "Менежерингиз билан боғлансам бўладими",
        "Могу я связаться с вашим менеджером",
        # Added after calibration testing found the single Russian anchor
        # above too narrow ("contact your manager" phrasing) to catch
        # "switch/connect me to a person" phrasing - a held-out case
        # ("Переключите меня на живого человека") scored only 0.555
        # against it and misclassified as greeting. Two anchors per
        # language/intent going forward, not just one, for this reason.
        "Соедините меня с живым сотрудником",
    ],
    "complaint": [
        "Mahsulot singan holda keldi, juda xafa bo'ldim",
        "Маҳсулот синган ҳолда келди, жуда хафа бўлдим",
        "Товар пришел сломанным, я очень расстроен",
        # Added after calibration testing found the anchors above (all
        # "broken product") too narrow to cover a "wrong order" complaint
        # ("Buyurtmam noto'g'ri kelib qoldi, chalkashib ketdi" scored only
        # 0.629, below threshold). Same lesson as the human_handoff fix
        # above: a single complaint sub-theme doesn't generalize to
        # others, needs its own anchor.
        "Menga noto'g'ri buyurtma yuborilgan",
    ],
    "product_inquiry": [
        "Bu buyum haqida batafsil aytib berolasizmi",
        "Бу буюм ҳақида батафсил айтиб бероласизми",
        "Расскажите подробнее об этом товаре",
    ],
}
