"""Instagram comment-automation presets ("templates" in the ManyChat sense) -
the merchant layer on top of app/flows/executor.py + app/instagram/. A
merchant picks a preset, fills its `fields` (never the reply copy itself -
see app/automations/router.py's docstring for why), optionally scopes it to
specific posts, and the router materializes it as an ordinary Flow row
(channel="instagram_comment", template_key=<this key>). Copy, don't
reference: editing a preset here never touches an already-installed
merchant's Flow row (ManyChat's own template design, see the MVP plan).

Every preset's `fields` always includes "link" because
app/flows/schema.py's InstagramCommentResponse.link is a required field on
every instagram_comment response shape - even "giveaway_keyword", where the
link is closer to "terms/channel" than "buy here".

Some presets (e.g. "giveaway_keyword", "story_reply_link", "keyword_to_dm")
have an extra "keyword" field - the deliberate exception to "keywords are
fixed by the preset, only fields are merchant-editable" (see MVP plan
section A), since these triggers are inherently merchant-chosen, not
something a generic preset can guess. "keyword_to_dm" goes one step
further with a "message" field: a free-text private_reply the merchant
writes themselves, still routed through the same {link} substitution as
every other preset (app/instagram/service.py) - the one deliberate
exception to "reply copy always comes from the preset" (see
app/automations/router.py's docstring for the reasoning behind that
default).
"""

from typing import NotRequired, TypedDict


class TemplateField(TypedDict):
    key: str
    required: bool
    label: dict[str, str]
    # "text" (default, single-line) | "textarea" (multi-line, for
    # merchant-authored free text like keyword_to_dm's "message" field).
    type: NotRequired[str]


class Template(TypedDict):
    name: dict[str, str]
    description: dict[str, str]
    # 'instagram_comment' (comment-to-DM) | 'instagram_story_reply'
    # (story-reply-to-DM). Installed verbatim onto the resulting Flow row
    # (app/automations/router.py) - a preset always belongs to exactly
    # one channel, never both.
    channel: str
    keywords: list[str]
    private_reply: dict[str, str]
    # None for channels with no public-reply concept (story replies are
    # always a private DM back - there's no "under the comment" surface).
    public_reply: dict[str, str] | None
    fields: list[TemplateField]
    verticals: list[str] | None  # None = every vertical


_LINK_FIELD: TemplateField = {
    "key": "link",
    "required": True,
    "label": {"uz": "Havola (Telegram bot / katalog)", "ru": "Ссылка (Telegram-бот / каталог)"},
}

TEMPLATES: dict[str, Template] = {
    "keyword_to_dm": {
        "name": {"uz": "Kalit so'z yozganga xabar yuborish", "ru": "Сообщение написавшим ключевое слово"},
        "description": {
            "uz": (
                "O'zingiz tanlagan kalit so'z bilan izoh qoldirganlarga o'zingiz yozgan "
                "xabar DM orqali yuboriladi."
            ),
            "ru": "Тем, кто оставит комментарий с вашим ключевым словом, в личку придёт сообщение, которое вы сами напишете.",
        },
        "channel": "instagram_comment",
        "keywords": [],  # merchant-supplied via the "keyword" field
        # Unused - overridden per-install from the "message" field
        # (app/automations/router.py's _build_response_config). Kept as a
        # dict[str, str] only to satisfy the Template shape.
        "private_reply": {"uz": "", "ru": ""},
        "public_reply": {"uz": "DM'ga yubordik! \U0001f4e9", "ru": "Отправили в DM! \U0001f4e9"},
        "fields": [
            {"key": "keyword", "required": True, "label": {"uz": "Kalit so'z", "ru": "Ключевое слово"}},
            {
                "key": "message",
                "required": True,
                "type": "textarea",
                "label": {"uz": "Xabar matni", "ru": "Текст сообщения"},
            },
            _LINK_FIELD,
        ],
        "verticals": None,
    },
    "giveaway_keyword": {
        "name": {"uz": "Konkurs kalit so'zi", "ru": "Ключевое слово конкурса"},
        "description": {
            "uz": (
                "O'zingiz tanlagan kalit so'z bilan izoh qoldirganlarga ishtirok "
                "tasdiqlovchi DM yuboriladi."
            ),
            "ru": "Тем, кто оставит комментарий с вашим ключевым словом, придёт DM с подтверждением участия.",
        },
        "channel": "instagram_comment",
        "keywords": [],  # merchant-supplied via the "keyword" field, not fixed here
        "private_reply": {
            "uz": "Tabriklaymiz, ishtirokingiz qabul qilindi! \U0001f389 Batafsil: {link}",
            "ru": "Поздравляем, ваше участие принято! \U0001f389 Подробнее: {link}",
        },
        "public_reply": {"uz": "Ishtirokingiz qabul qilindi! \U0001f389", "ru": "Участие принято! \U0001f389"},
        "fields": [
            {
                "key": "keyword",
                "required": True,
                "label": {"uz": "Kalit so'z", "ru": "Ключевое слово"},
            },
            {
                "key": "link",
                "required": True,
                "label": {"uz": "Havola (shartlar / kanal)", "ru": "Ссылка (условия / канал)"},
            },
        ],
        "verticals": None,
    },
    "course_enroll": {
        "name": {"uz": "Kursga yozilish", "ru": "Запись на курс"},
        "description": {
            "uz": "Mijoz “yozilish” deb yozsa, unga kursga yozilish havolasi DM orqali yuboriladi.",
            "ru": "Когда клиент пишет «запись» в комментарии, ему в личку придёт ссылка для записи на курс.",
        },
        "channel": "instagram_comment",
        "keywords": ["yozilish", "yozilaman", "запись", "записаться"],
        "private_reply": {
            "uz": "Assalomu alaykum! Kursga yozilish: {link} \U0001f60a",
            "ru": "Здравствуйте! Запись на курс: {link} \U0001f60a",
        },
        "public_reply": {"uz": "DM'ga yubordik! \U0001f4e9", "ru": "Отправили в DM! \U0001f4e9"},
        "fields": [
            {
                "key": "link",
                "required": True,
                "label": {"uz": "Havola (ro'yxatdan o'tish)", "ru": "Ссылка (регистрация)"},
            }
        ],
        "verticals": ["course"],
    },
    "story_reply_link": {
        "name": {"uz": "Hikoya javobiga DM", "ru": "DM за ответ в Stories"},
        "description": {
            "uz": (
                "Mijoz hikoyangizga o'zingiz tanlagan kalit so'z bilan javob yozsa, "
                "unga havola DM orqali yuboriladi."
            ),
            "ru": "Когда клиент отвечает на вашу Историю вашим ключевым словом, ему в личку придёт ссылка.",
        },
        "channel": "instagram_story_reply",
        "keywords": [],  # merchant-supplied via the "keyword" field - a story reply has no universal fixed phrase
        "private_reply": {
            "uz": "Salom! Mana havola: {link} \U0001f60a",
            "ru": "Привет! Вот ссылка: {link} \U0001f60a",
        },
        # Stories have no public-reply surface - the reply is always a
        # private DM back to whoever replied.
        "public_reply": None,
        "fields": [
            {"key": "keyword", "required": True, "label": {"uz": "Kalit so'z", "ru": "Ключевое слово"}},
            _LINK_FIELD,
        ],
        "verticals": None,
    },
}


def templates_for_vertical(vertical: str | None) -> dict[str, Template]:
    return {
        key: template
        for key, template in TEMPLATES.items()
        if template["verticals"] is None or vertical in template["verticals"]
    }
