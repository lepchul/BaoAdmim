import copy
import json
import re
import secrets
import time
from urllib.parse import urlparse

from api import TelegramError
from formatting import esc, clean, render
from moderation import BaoAdmin, ADMIN, MOD, ALIASES, HELP, keyboard
from community_store import day

COMMANDS = """🐾 Команды Комариков

!бао ник "ник" - задать ник
!бао ник - свой ник; ответом - чужой
!бао профиль - карточка участника
!бао топ - актив за всё время
!бао топ день - актив сегодня
!бао топ неделя - актив за 7 дней
!бао правила / !бао админы
!бао дуэль - вызвать ответом на сообщение
!бао растить / !бао питомец - твой Бао
!бао кормить - кормление раз в 4 часа
!бао бонус - 100 монет каждый день
!бао магазин - подарки и цены
!бао подарок роза - подарить ответом
!бао брак - предложение ответом
!бао семья - брак и свадебный статус
!бао развод - с подтверждением
!бао монетка / !бао шар вопрос
!бао совместимость - ответом на человека
!бао команды

Модерация для админов: /warn, /mute 30m, /ban, /unmute, /unban, /unwarn, /del, /pin, /unpin
/rules, /report, /emojiid, /id
"""
RANKS = {
    5: "Дяди Бао",
    4: "Старшие админы",
    3: "Младшие админы",
    2: "Старшие модераторы",
    1: "Младшие модераторы",
}
GIFTS = {
    "рыбка": ("🐟", 15),
    "роза": ("🌹", 30),
    "торт": ("🎂", 60),
    "кольцо": ("💍", 100),
    "корона": ("👑", 250),
}
STAGES = [
    (800, "Легендарный прабао"),
    (420, "Старый сын дяди Бао"),
    (180, "Взрослый дядя Бао"),
    (60, "Повзрослевший сын дяди Бао"),
    (0, "Сын дяди Бао"),
]
BUTTONS = {
    "verify": "Подтвердить вход",
    "rules": "Правила",
    "admins": "Администрация",
    "commands": "Список команд бота",
}


class Komariki(BaoAdmin):
    def owner(self, uid):
        if uid not in self.owners:
            raise ValueError("Овнер-меню доступно только владельцам из OWNER_IDS")

    def allowed(self, chat):
        if not self.db.allowed(chat):
            raise ValueError(
                "Этот бот работает только в Комарики чатс и выбранных владельцем группах"
            )

    async def require(self, chat, uid, right=None):
        self.allowed(chat)
        if uid in self.owners and right is None:
            return {"status": "administrator"}
        return await super().require(chat, uid, right)

    async def say(self, chat, text, **kwargs):
        kwargs.setdefault("disable_notification", True)
        try:
            return await super().say(chat, text, **kwargs)
        except TelegramError as e:
            markup = kwargs.get("reply_markup", {})
            if e.code != 400 or not any(
                "icon_custom_emoji_id" in b
                for row in markup.get("inline_keyboard", [])
                for b in row
            ):
                raise
            kwargs = copy.deepcopy(kwargs)
            for row in kwargs["reply_markup"]["inline_keyboard"]:
                for b in row:
                    b.pop("icon_custom_emoji_id", None)
            return await super().say(chat, text, **kwargs)

    async def reject_chat(self, chat):
        try:
            await self.say(
                chat,
                "🐾 Bao Admin работает только в Комарики чатс: @komariki_chatss\nЭтот чат не разрешён владельцем. Я выхожу.",
            )
        except TelegramError:
            pass
        await self.api.call("leaveChat", chat_id=chat)

    def button(self, chat, key, **kwargs):
        b = {"text": BUTTONS[key], **kwargs}
        icon = self.db.config(chat, "icon_" + key)
        if icon:
            b["icon_custom_emoji_id"] = icon
        return b

    def public_buttons(self, chat):
        root = f'https://t.me/{self.me["username"]}?start='
        rules = self.db.config(chat, "rules_url") or root + f"r_{chat}"
        return {
            "inline_keyboard": [
                [self.button(chat, "rules", url=rules)],
                [
                    self.button(chat, "admins", url=root + f"a_{chat}"),
                    self.button(chat, "commands", url=root + f"c_{chat}"),
                ],
            ]
        }

    def label(self, chat, uid, real=False):
        p = self.db.person(chat, uid)
        return (p["name"] if real else p["nick"] or p["name"]) if p else str(uid)

    def link(self, chat, uid, real=False):
        # Profile deep links do not create Telegram mention entities.
        return f'<a href="https://t.me/{self.me["username"]}?start=p_{chat}_{uid}">{esc(self.label(chat,uid,real))}</a>'

    async def start(self, uid):
        rows = [
            [
                {
                    "text": "Добавить в свой чат",
                    "url": f'https://t.me/{self.me["username"]}?startgroup=komariki',
                }
            ],
            [
                {"text": "Инструкция по установке", "callback_data": "public:install"},
                {"text": "Команды", "callback_data": "public:commands"},
            ],
        ]
        if uid in self.owners:
            rows.append([{"text": "Овнер-меню", "callback_data": "groups:0"}])
        await self.say(
            uid,
            "🐾 <b>Установка</b>\nДобавь бота админом и выдай права удаления, блокировки и закрепления.\n\nBao Admin создан для @komariki_chatss. В других группах он работает только с разрешения владельца.",
            reply_markup={"inline_keyboard": rows},
        )

    async def groups(self, uid, page=0):
        self.owner(uid)
        rows = []
        for row in self.db.db.execute(
            "SELECT a.chat,c.title FROM allowed a LEFT JOIN chats c ON c.id=a.chat ORDER BY a.chat"
        ).fetchall():
            rows.append(
                [(str(row["title"] or row["chat"])[:40], f'panel:{row["chat"]}')]
            )
        await self.say(
            uid,
            "👑 <b>Овнер-меню</b>\nНастройки доступны только OWNER_IDS.\nРазрешить чат: /allow -100...\nУбрать чат: /disallow -100...\nСписок: /allowed\nРежим техработ: /maintenance on или off",
            reply_markup=keyboard(rows),
        )

    async def panel(self, uid, chat):
        self.owner(uid)
        self.allowed(chat)
        await super().panel(uid, chat)
        await self.say(
            uid,
            "Комарики | дополнительные настройки",
            reply_markup=keyboard(
                [
                    [
                        ("Кнопки и ссылки", f"community:{chat}:buttons"),
                        ("Таблица админов", f"community:{chat}:ranks"),
                    ],
                    [
                        ("Ожидают входа", f"community:{chat}:gates"),
                        ("Тексты приветствия", f"texts:{chat}"),
                    ],
                ]
            ),
        )

    async def texts(self, uid, chat):
        self.owner(uid)
        return await super().texts(uid, chat)

    async def edit_input(self, uid, message, chat, kind, key, payload=None):
        self.owner(uid)
        self.allowed(chat)
        body = payload if payload is not None else message.get("text", "").strip()
        if kind == "buttons":
            parts = body.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError(
                    "Напиши: verify ID, rules ID, admins ID, commands ID или url https://..."
                )
            key, value = parts
            if key == "url":
                u = urlparse(value)
                if value != "off" and (
                    u.scheme != "https" or not u.hostname or u.username or u.password
                ):
                    raise ValueError("Нужна HTTPS-ссылка или off")
                if len(value) > 500:
                    raise ValueError("Ссылка слишком длинная")
                self.db.configure(chat, "rules_url", "" if value == "off" else value)
            elif key in BUTTONS:
                if value != "off":
                    if not re.fullmatch(r"\d{1,24}", value):
                        raise ValueError("Нужен ID премиум-эмодзи или off")
                    if not await self.api.call(
                        "getCustomEmojiStickers", custom_emoji_ids=[value]
                    ):
                        raise ValueError("Эмодзи не найден")
                self.db.configure(chat, "icon_" + key, "" if value == "off" else value)
            else:
                raise ValueError("Ключи: verify, rules, admins, commands, url")
            self.db.log(chat, uid, 0, "button", key)
            self.pending.pop(uid, None)
            await self.say(
                uid,
                "Сохранено. Предпросмотр кнопок:",
                reply_markup=self.public_buttons(chat),
            )
            if key == "verify":
                await self.say(
                    uid,
                    "Кнопка проверки:",
                    reply_markup={
                        "inline_keyboard": [
                            [
                                self.button(
                                    chat,
                                    "verify",
                                    url=f'https://t.me/{self.me["username"]}?start=verify',
                                )
                            ]
                        ]
                    },
                )
            return
        if kind == "ranks":
            parts = body.split()
            if (
                len(parts) != 2
                or not parts[0].isdigit()
                or parts[1] not in ("0", "1", "2", "3", "4", "5")
            ):
                raise ValueError(
                    "Формат: ID уровень. Например 123456789 5. Уровень 0 скрывает из таблицы"
                )
            target = int(parts[0])
            member = await self.member(chat, target)
            if member["status"] in ("left", "kicked") or member["user"].get("is_bot"):
                raise ValueError("Выбери участника этого чата")
            self.db.touch(chat, member["user"])
            self.db.execute(
                "INSERT OR REPLACE INTO ranks VALUES(?,?,?)",
                (chat, target, int(parts[1])),
            )
            self.db.log(chat, uid, target, "rank", parts[1])
            self.pending.pop(uid, None)
            await self.administration(uid, chat)
            return
        return await super().edit_input(uid, message, chat, kind, key, payload)

    async def callback(self, q):
        data = q.get("data", "")
        uid = q["from"]["id"]
        msg = q.get("message", {})
        c = msg.get("chat", {})
        if data.startswith("play:"):
            return await self.play_callback(q)
        if data.startswith("public:") and c.get("type") == "private" and c["id"] == uid:
            await self.api.call("answerCallbackQuery", callback_query_id=q["id"])
            if data == "public:commands":
                return await self.say(uid, COMMANDS)
            return await self.say(
                uid,
                "Установка: добавь бота администратором. Нужны права удаления, блокировки и закрепления.\nНовые чаты разрешает только владелец через /allow ID. По умолчанию бот предназначен для @komariki_chatss.",
            )
        self.owner(uid)
        if c.get("type") != "private" or c.get("id") != uid:
            raise ValueError("Открой овнер-меню в личке")
        if data.startswith("community:"):
            await self.api.call("answerCallbackQuery", callback_query_id=q["id"])
            _, raw, kind = data.split(":")
            chat = int(raw)
            self.allowed(chat)
            self.selected[uid] = chat
            if kind in ("buttons", "ranks"):
                self.pending[uid] = (chat, kind, "", time.monotonic() + 900)
                text = (
                    "Кнопки: verify, rules, admins, commands.\nПришли ключ и ID премиум-эмодзи, например:\nverify 5368324170671202286\n\nСсылка правил: url https://...\nУдалить: verify off или url off\nID можно получить через /emojiid.\n/cancel для отмены"
                    if kind == "buttons"
                    else "Таблица по звёздам, только реальные имена Telegram.\nПришли ID и уровень 1..5. Уровень 0 скрывает участника.\nПример: 123456789 5\nЭто оформление списка, не выдача прав Telegram.\n/cancel для отмены"
                )
                return await self.say(uid, text)
            if kind == "gates":
                rows = self.db.db.execute(
                    'SELECT user,deadline,state FROM gates WHERE chat=? AND state IN ("pending","locking","releasing","banning") LIMIT 30',
                    (chat,),
                ).fetchall()
                return await self.say(
                    uid,
                    "Ожидают входа:\n"
                    + (
                        "\n".join(
                            f'<code>{r["user"]}</code> | {r["state"]} | осталось {max(0,(r["deadline"]-int(time.time()))//60)} мин'
                            for r in rows
                        )
                        or "Никого"
                    ),
                )
        # The legacy callback path is now owner-only, including stale buttons.
        return await super().callback(q)

    async def administration(self, dest, chat):
        self.allowed(chat)
        actual = await self.api.call("getChatAdministrators", chat_id=chat)
        roster = {}
        for m in actual:
            u = m["user"]
            if u.get("is_bot"):
                continue
            self.db.touch(chat, u)
            roster[u["id"]] = 5 if m["status"] == "creator" else 3
        for r in self.db.db.execute(
            "SELECT user,rank FROM ranks WHERE chat=?", (chat,)
        ).fetchall():
            try:
                m = await self.member(chat, r["user"])
                if m["status"] in ("left", "kicked"):
                    roster.pop(r["user"], None)
                    continue
                self.db.touch(chat, m["user"])
                roster[r["user"]] = r["rank"]
            except TelegramError:
                roster.pop(r["user"], None)
        lines = ["🐾 <b>Администрация Комариков</b>"]
        for rank, title in RANKS.items():
            people = [uid for uid, r in roster.items() if r == rank]
            if people:
                lines.append("\n" + "⭐" * rank + " <b>" + title + "</b>")
                lines.extend("▫️ " + self.link(chat, uid, real=True) for uid in people)
        # Send bounded chunks for unusually large administrator tables.
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > 3500:
                await self.say(dest, chunk)
                chunk = ""
            chunk += line + "\n"
        await self.say(dest, chunk)

    async def profile(self, dest, chat, uid):
        self.allowed(chat)
        p = self.db.person(chat, uid)
        if not p:
            raise ValueError("Участник ещё не писал после запуска бота")
        couple = self.db.one(
            "SELECT partner FROM couples WHERE chat=? AND user=?", (chat, uid)
        )
        text = (
            f'🐾 <b>{esc(p["nick"] or p["name"])}</b>\nИмя Telegram: {esc(p["name"])}\nID: <code>{uid}</code>\nСообщений: {p["posts"]}\nМонет: {p["coins"]}\nСемья: '
            + (esc(self.label(chat, couple["partner"])) if couple else "не в браке")
        )
        buttons = []
        if p["username"] and re.fullmatch(r"[A-Za-z0-9_]+", p["username"]):
            buttons = [
                [{"text": "Профиль Telegram", "url": "https://t.me/" + p["username"]}]
            ]
        await self.say(dest, text, reply_markup={"inline_keyboard": buttons})

    async def welcome(self, chat, user):
        info = await self.api.call("getChat", chat_id=chat)
        fields = {
            "user": self.link(chat, user["id"]),
            "chat": esc(info.get("title", "Комарики чатс")),
            "admin": "Bao Admin",
            "reason": "",
            "duration": "",
            "count": "",
            "limit": "",
        }
        body = self.db.template(chat, "welcome")
        emojis = self.db.emojis(chat)
        try:
            await self.say(
                chat,
                render(body, emojis, fields),
                reply_markup=self.public_buttons(chat),
            )
        except TelegramError as e:
            if e.code != 400:
                raise
            await self.say(
                chat,
                render(body, emojis, fields, custom=False),
                reply_markup=self.public_buttons(chat),
            )

    async def gate_join(self, chat, user, joined=None):
        self.allowed(chat)
        if user.get("is_bot"):
            return
        self.db.touch(chat, user)
        m = await self.member(chat, user["id"])
        if m["status"] in ADMIN or m["status"] in ("left", "kicked"):
            return
        old = self.db.one(
            "SELECT * FROM gates WHERE chat=? AND user=?", (chat, user["id"])
        )
        now = int(time.time())
        joined = joined or now
        if old and (
            old["state"] in ("locking", "pending", "releasing", "banning")
            or (
                old["state"] == "approved" and abs(old["deadline"] - 86400 - joined) < 5
            )
        ):
            return
        token = secrets.token_urlsafe(18)
        self.db.execute(
            "DELETE FROM gate_original WHERE chat=? AND user=?", (chat, user["id"])
        )
        if m["status"] == "restricted":
            perms = {
                k: v
                for k, v in m.items()
                if k.startswith("can_") and isinstance(v, bool)
            }
            self.db.execute(
                "INSERT OR REPLACE INTO gate_original VALUES(?,?,?,?)",
                (chat, user["id"], json.dumps(perms), m.get("until_date", 0)),
            )
        self.db.execute(
            "INSERT OR REPLACE INTO gates VALUES(?,?,?,?,?,?,0)",
            (chat, user["id"], token, joined + 86400, "locking", 0),
        )
        await self.lock_gate(chat, user["id"])

    async def lock_gate(self, chat, uid):
        row = self.db.one("SELECT * FROM gates WHERE chat=? AND user=?", (chat, uid))
        await self.bot_right(chat, "can_restrict_members")
        info = await self.api.call("getChat", chat_id=chat)
        if info["type"] != "supergroup":
            raise ValueError("Проверка входа требует супергруппу")
        if not row["manual_hold"]:
            await self.api.call(
                "restrictChatMember",
                chat_id=chat,
                user_id=uid,
                permissions={"can_send_messages": False},
                use_independent_chat_permissions=True,
                until_date=0,
            )
        self.db.execute(
            'UPDATE gates SET state="pending" WHERE chat=? AND user=?', (chat, uid)
        )
        await self.gate_prompt(chat, uid)

    async def gate_prompt(self, chat, uid):
        row = self.db.one("SELECT * FROM gates WHERE chat=? AND user=?", (chat, uid))
        if row["prompt"]:
            return
        result = await self.say(
            chat,
            f"{self.link(chat,uid)}\nЧтобы зайти в чат, надо написать /start в ЛС боту.\nНажми кнопку и запусти бота. На подтверждение есть 24 часа, затем бан.",
            reply_markup={
                "inline_keyboard": [
                    [
                        self.button(
                            chat,
                            "verify",
                            url=f'https://t.me/{self.me["username"]}?start=v_{row["token"]}',
                        )
                    ]
                ]
            },
        )
        self.db.execute(
            "UPDATE gates SET prompt=? WHERE chat=? AND user=?",
            (result["message_id"], chat, uid),
        )

    async def confirm(self, uid, token=None):
        if token:
            row = self.db.one("SELECT * FROM gates WHERE token=?", (token,))
            if not row or row["user"] != uid:
                raise ValueError(
                    "Эта кнопка предназначена другому участнику или устарела"
                )
            rows = [row]
        else:
            rows = self.db.db.execute(
                'SELECT * FROM gates WHERE user=? AND state IN ("pending","locking","releasing")',
                (uid,),
            ).fetchall()
        count = 0
        for row in rows:
            if not self.db.allowed(row["chat"]):
                continue
            if row["state"] == "approved":
                await self.say(uid, "Вход уже подтверждён")
                continue
            if row["state"] == "locking":
                await self.lock_gate(row["chat"], uid)
                row = self.db.one(
                    "SELECT * FROM gates WHERE chat=? AND user=?", (row["chat"], uid)
                )
            if row["state"] not in ("pending", "releasing") or (
                row["deadline"] <= int(time.time()) and row["state"] != "releasing"
            ):
                raise ValueError(
                    "Срок подтверждения истёк. Обратись к администрации чата"
                )
            self.db.execute(
                'UPDATE gates SET state="releasing" WHERE chat=? AND user=?',
                (row["chat"], uid),
            )
            await self.release_gate(row["chat"], uid)
            count += 1
        if count:
            await self.say(
                uid,
                "✅ Вход подтверждён. Возвращайся в чат. Отдельные наказания модераторов при этом не снимаются",
            )
        return count

    async def release_gate(self, chat, uid):
        row = self.db.one("SELECT * FROM gates WHERE chat=? AND user=?", (chat, uid))
        m = await self.member(chat, uid)
        if m["status"] in ("left", "kicked"):
            self.db.execute(
                'UPDATE gates SET state="left" WHERE chat=? AND user=?', (chat, uid)
            )
            return
        if not row["manual_hold"] and m["status"] == "restricted":
            # A timed restriction after joining belongs to a moderator, not the gate.
            if not m.get("until_date", 0):
                info = await self.api.call("getChat", chat_id=chat)
                perms = info.get("permissions")
                if not perms:
                    raise ValueError("Не удалось получить права группы")
                original = self.db.one(
                    "SELECT * FROM gate_original WHERE chat=? AND user=?", (chat, uid)
                )
                until = 0
                if original and (
                    original["until"] == 0 or original["until"] > int(time.time())
                ):
                    perms = json.loads(original["permissions"])
                    until = original["until"]
                    if until:
                        until = max(until, int(time.time()) + 60)
                await self.api.call(
                    "restrictChatMember",
                    chat_id=chat,
                    user_id=uid,
                    permissions=perms,
                    use_independent_chat_permissions=True,
                    until_date=until,
                )
        self.db.execute(
            'UPDATE gates SET state="approved" WHERE chat=? AND user=?', (chat, uid)
        )
        self.db.log(chat, uid, uid, "verified")
        if row["prompt"]:
            try:
                await self.api.call(
                    "deleteMessage", chat_id=chat, message_id=row["prompt"]
                )
            except TelegramError:
                pass
        await self.welcome(chat, m["user"])

    async def tick(self):
        now = int(time.time())
        self.db.execute(
            'UPDATE challenges SET state="expired" WHERE state="pending" AND expires<=?',
            (now,),
        )
        rows = self.db.db.execute(
            'SELECT * FROM gates WHERE state IN ("locking","releasing","banning") OR (state="pending" AND (deadline<=? OR prompt=0)) ORDER BY deadline LIMIT 100',
            (now,),
        ).fetchall()
        for row in rows:
            chat, uid = row["chat"], row["user"]
            if not self.db.allowed(chat):
                continue
            try:
                if row["state"] == "releasing":
                    await self.release_gate(chat, uid)
                elif row["deadline"] <= now or row["state"] == "banning":
                    m = await self.member(chat, uid)
                    if m["status"] in ADMIN or m["status"] in ("left", "kicked"):
                        self.db.execute(
                            "UPDATE gates SET state=? WHERE chat=? AND user=?",
                            ("approved" if m["status"] in ADMIN else "left", chat, uid),
                        )
                        continue
                    self.db.execute(
                        'UPDATE gates SET state="banning" WHERE chat=? AND user=?',
                        (chat, uid),
                    )
                    await self.api.call("banChatMember", chat_id=chat, user_id=uid)
                    self.db.execute(
                        'UPDATE gates SET state="expired" WHERE chat=? AND user=?',
                        (chat, uid),
                    )
                    self.db.log(chat, self.me["id"], uid, "verification_timeout")
                elif row["state"] == "locking":
                    await self.lock_gate(chat, uid)
                elif not row["prompt"]:
                    await self.gate_prompt(chat, uid)
            except (TelegramError, ValueError):
                # Keep the durable state for a later attempt when permissions return.
                import logging

                logging.getLogger("bao_admin").warning(
                    "Verification retry pending chat=%s user=%s", chat, uid
                )

    async def moderate(self, message, command, args):
        chat = message["chat"]["id"]
        actor = message["from"]["id"]
        if command in ("mute", "ban", "unmute", "warn"):
            await self.require(chat, actor, "can_restrict_members")
            target, _ = await self.target(message, args)
            gate = self.db.one(
                "SELECT * FROM gates WHERE chat=? AND user=?", (chat, target["id"])
            )
            if gate and gate["state"] in ("locking", "pending", "releasing"):
                if command in ("mute", "warn", "unmute"):
                    raise ValueError(
                        "Участник ещё не подтвердил вход через /start в личке. До подтверждения можно только забанить"
                    )
                # Conservative hold: even if the API times out, verification cannot lift a manual penalty.
                self.db.execute(
                    "UPDATE gates SET manual_hold=1 WHERE chat=? AND user=?",
                    (chat, target["id"]),
                )
        return await super().moderate(message, command, args)

    async def game_target(self, m):
        reply = m.get("reply_to_message", {})
        user = reply.get("from")
        if not user or reply.get("sender_chat") or user.get("is_bot"):
            raise ValueError("Используй команду ответом на сообщение человека")
        if user["id"] == m["from"]["id"]:
            raise ValueError("Выбери другого участника")
        member = await self.member(m["chat"]["id"], user["id"])
        if member["status"] in ("left", "kicked"):
            raise ValueError("Этот участник уже вышел из чата")
        gate = self.db.one(
            "SELECT state FROM gates WHERE chat=? AND user=?",
            (m["chat"]["id"], user["id"]),
        )
        if gate and gate["state"] in ("locking", "pending", "releasing", "banning"):
            raise ValueError("Участник ещё не подтвердил вход")
        self.db.touch(m["chat"]["id"], member["user"])
        return member["user"]

    async def entertainment(self, m, command, args):
        chat = m["chat"]["id"]
        uid = m["from"]["id"]
        self.db.touch(chat, m["from"])
        self.db.cooldown(chat, uid, "fun", 3)
        if command in ("команды", "помощь", ""):
            return await self.say(chat, COMMANDS)
        if command == "правила":
            return await self.emit(chat, "rules", m["from"])
        if command in ("админы", "администрация"):
            return await self.administration(chat, chat)
        if command == "ник":
            if args:
                nick = args.strip().strip('"«»').strip()
                if nick == "сброс":
                    nick = ""
                elif not 1 <= len(nick) <= 32 or any(
                    ord(ch) < 32
                    or ch
                    in "{}<>@\u200b\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                    for ch in nick
                ):
                    raise ValueError(
                        "Ник: 1..32 символа, без ссылок, @ и управляющих символов"
                    )
                if re.search(r"(?i)(https?://|www\.|t\.me/)", nick):
                    raise ValueError("В нике нельзя использовать ссылки")
                self.db.execute(
                    "UPDATE people SET nick=? WHERE chat=? AND user=?",
                    (clean(nick), chat, uid),
                )
            target = uid
            if not args and m.get("reply_to_message", {}).get("from"):
                u = m["reply_to_message"]["from"]
                self.db.touch(chat, u)
                target = u["id"]
            return await self.say(
                chat, f"🐾 Ник: {self.link(chat,target)}\nID: <code>{target}</code>"
            )
        if command == "профиль":
            target = m.get("reply_to_message", {}).get("from", m["from"])
            self.db.touch(chat, target)
            return await self.profile(chat, chat, target["id"])
        if command == "топ":
            period = args.strip().lower()
            if period not in ("", "день", "неделя"):
                raise ValueError("!бао топ, !бао топ день или !бао топ неделя")
            if period:
                since = day(time.time() - 6 * 86400) if period == "неделя" else day()
                rows = self.db.db.execute(
                    "SELECT user,SUM(posts) AS score FROM activity WHERE chat=? AND day>=? GROUP BY user ORDER BY score DESC,user LIMIT 15",
                    (chat, since),
                ).fetchall()
                total = self.db.one(
                    "SELECT COALESCE(SUM(posts),0) AS n FROM activity WHERE chat=? AND day>=?",
                    (chat, since),
                )["n"]
            else:
                rows = self.db.db.execute(
                    "SELECT user,posts AS score FROM people WHERE chat=? AND posts>0 ORDER BY posts DESC,user LIMIT 15",
                    (chat,),
                ).fetchall()
                total = self.db.one(
                    "SELECT COALESCE(SUM(posts),0) AS n FROM people WHERE chat=?",
                    (chat,),
                )["n"]
            text = (
                "🏆 <b>Актив: "
                + (period or "всё время")
                + "</b>\n"
                + "\n".join(
                    f'{i}. {self.link(chat,r["user"])} | {r["score"]}'
                    for i, r in enumerate(rows, 1)
                )
            )
            return await self.say(
                chat,
                text
                + f"\n\nВсего сообщений: {total}\nСчёт идёт с подключения этой версии бота",
            )
        if command == "бонус":
            n = self.db.daily(chat, uid)
            return await self.say(
                chat, f'🪙 +{n} монет. Баланс: {self.db.person(chat,uid)["coins"]}'
            )
        if command in ("растить", "питомец", "бао", "кормить"):
            if command == "кормить":
                self.db.feed(chat, uid)
            p = self.db.person(chat, uid)
            stage = next(name for minimum, name in STAGES if p["xp"] >= minimum)
            next_level = next(
                (v for v in sorted(x[0] for x in STAGES) if v > p["xp"]), None
            )
            progress = (
                f'{p["xp"]}/{next_level}'
                if next_level
                else f'{p["xp"]}, максимальная стадия'
            )
            return await self.say(
                chat,
                f'🐈 <b>Жиробасинка Бао</b>\nХозяин: {self.link(chat,uid)}\nСтадия: {stage}\nРост: {progress}\nУсловный вес: {2+p["feeds"]*.2:.1f} кг\nКормлений: {p["feeds"]}\nМонет: {p["coins"]}\n\n!бао кормить: 15 монет, +12 роста, раз в 4 часа\n!бао бонус: монеты на корм и подарки',
            )
        if command == "магазин":
            return await self.say(
                chat,
                "🎁 <b>Подарки</b>\n"
                + "\n".join(
                    f"{icon} {name}: {price} монет"
                    for name, (icon, price) in GIFTS.items()
                )
                + "\n\nОтветь на сообщение: !бао подарок роза\nМонеты игровые, без покупки за деньги",
            )
        if command == "подарок":
            item = args.strip().lower()
            if item not in GIFTS:
                raise ValueError("Подарки: " + ", ".join(GIFTS))
            target = await self.game_target(m)
            icon, cost = GIFTS[item]
            self.db.gift(chat, uid, target["id"], item, cost)
            return await self.say(
                chat,
                f'{icon} {self.link(chat,uid)} дарит {esc(item)} участнику {self.link(chat,target["id"])}\nЕсли вы в браке, отношения стали теплее на 1',
            )
        if command in ("дуэль", "брак"):
            target = await self.game_target(m)
            kind = "duel" if command == "дуэль" else "marry"
            if kind == "marry" and self.db.one(
                "SELECT 1 FROM couples WHERE chat=? AND user IN (?,?)",
                (chat, uid, target["id"]),
            ):
                raise ValueError("Один из вас уже состоит в браке")
            id = self.db.challenge(chat, uid, target["id"], kind)
            text = (
                f'⚔️ {self.link(chat,uid)} вызывает {self.link(chat,target["id"])} на дуэль Бао!\nПобедителя выберет случай. Призы: 15 и 5 монет.'
                if kind == "duel"
                else f'💍 {self.link(chat,uid)} предлагает брак участнику {self.link(chat,target["id"])}'
            )
            sent = await self.say(
                chat,
                text + "\nОтветить может только приглашённый. Срок: 10 минут.",
                reply_markup=keyboard(
                    [
                        [
                            ("Принять", f"play:{id}:yes"),
                            ("Отказаться", f"play:{id}:no"),
                        ],
                        [("Отменить приглашение", f"play:{id}:cancel")],
                    ]
                ),
            )
            self.db.execute(
                "UPDATE challenges SET message=? WHERE id=?", (sent["message_id"], id)
            )
            return
        if command == "семья":
            row = self.db.one(
                "SELECT * FROM couples WHERE chat=? AND user=?", (chat, uid)
            )
            if not row:
                return await self.say(
                    chat, "Пока не в браке. Ответь человеку: !бао брак"
                )
            days = (int(time.time()) - row["since"]) // 86400
            wedding = (
                "Золотая свадьба"
                if days >= 100
                else (
                    "Серебряная свадьба"
                    if days >= 30
                    else "Бумажная свадьба" if days >= 7 else "Зелёная свадьба"
                )
            )
            status = (
                "Неразлучные Бао"
                if row["love"] >= 20
                else "Тёплые отношения" if row["love"] >= 5 else "Молодожёны"
            )
            return await self.say(
                chat,
                f'💚 <b>{wedding}</b>\n{self.link(chat,uid)} + {self.link(chat,row["partner"])}\nВместе: {days} дн\nСтатус: {status}\nПодарков друг другу: {row["love"]}\nИгровые годовщины: 7, 30 и 100 дней',
            )
        if command == "развод":
            row = self.db.one(
                "SELECT * FROM couples WHERE chat=? AND user=?", (chat, uid)
            )
            if not row:
                raise ValueError("Ты не состоишь в браке")
            id = self.db.challenge(chat, uid, row["partner"], "divorce")
            sent = await self.say(
                chat,
                "Расторгнуть игровой брак? Подтвердить может только инициатор.",
                reply_markup=keyboard(
                    [
                        [
                            ("Подтвердить развод", f"play:{id}:yes"),
                            ("Отмена", f"play:{id}:cancel"),
                        ]
                    ]
                ),
            )
            self.db.execute(
                "UPDATE challenges SET message=? WHERE id=?", (sent["message_id"], id)
            )
            return
        if command == "монетка":
            return await self.say(chat, "🪙 " + secrets.choice(["Орёл", "Решка"]))
        if command == "шар":
            if not args:
                raise ValueError("!бао шар вопрос")
            return await self.say(
                chat,
                "🔮 "
                + secrets.choice(
                    [
                        "Бао одобряет",
                        "Лучше после обеда",
                        "Шансы есть",
                        "Сначала покорми кота",
                        "Сегодня точно нет",
                        "Мурка говорит да",
                    ]
                ),
            )
        if command == "совместимость":
            target = await self.game_target(m)
            import hashlib

            seed = f'{chat}:{min(uid,target["id"])}:{max(uid,target["id"])}:{day()}'
            score = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % 101
            return await self.say(
                chat,
                f'🐾 {self.link(chat,uid)} + {self.link(chat,target["id"])}\nБао-совместимость сегодня: {score}%\nШуточный прогноз, каждый день новый',
            )
        raise ValueError("Неизвестная команда. Напиши !бао команды")

    async def play_callback(self, q):
        uid = q["from"]["id"]
        msg = q.get("message", {})
        chat = msg.get("chat", {}).get("id")
        try:
            self.allowed(chat)
            _, raw, choice = q["data"].split(":")
            row = self.db.one("SELECT * FROM challenges WHERE id=?", (int(raw),))
            if (
                not row
                or row["chat"] != chat
                or row["message"] != msg.get("message_id")
            ):
                raise ValueError("Неверное приглашение")
            if row["state"] != "pending" or row["expires"] <= int(time.time()):
                raise ValueError("Приглашение закрыто или истекло")
            required = (
                row["a"] if choice == "cancel" or row["kind"] == "divorce" else row["b"]
            )
            if uid != required:
                raise ValueError("Эта кнопка не для тебя")
            if choice not in ("yes", "no", "cancel"):
                raise ValueError("Неизвестное действие")
            if choice == "yes":
                for person in (row["a"], row["b"]):
                    member = await self.member(chat, person)
                    if member["status"] in ("left", "kicked"):
                        raise ValueError("Один из участников уже вышел из чата")
                    gate = self.db.one(
                        "SELECT state FROM gates WHERE chat=? AND user=?",
                        (chat, person),
                    )
                    if gate and gate["state"] in (
                        "locking",
                        "pending",
                        "releasing",
                        "banning",
                    ):
                        raise ValueError("Сначала подтвердите вход")
                if row["kind"] == "duel":
                    winner = secrets.choice([row["a"], row["b"]])
                    self.db.duel(row, winner)
                    text = (
                        secrets.choice(
                            [
                                "Бао совершил прыжок животиком!",
                                "Рыбный вихрь решил исход боя!",
                                "Мощный мурчательный удар!",
                            ]
                        )
                        + "\nПобедитель: "
                        + self.link(chat, winner)
                        + "\nПобедителю 15 монет, второму участнику 5. Реванш через час"
                    )
                elif row["kind"] == "marry":
                    self.db.marry(row)
                    text = f'💚 Зелёная свадьба!\n{self.link(chat,row["a"])} и {self.link(chat,row["b"])} теперь молодожёны\n!бао семья покажет отношения'
                else:
                    with self.db.db:
                        current = self.db.one(
                            "SELECT partner,since FROM couples WHERE chat=? AND user=?",
                            (chat, row["a"]),
                        )
                        if (
                            not current
                            or current["partner"] != row["b"]
                            or current["since"] > row["created"]
                        ):
                            raise ValueError("Этот брак уже изменился")
                        self.db.db.execute(
                            "DELETE FROM couples WHERE chat=? AND user IN (?,?)",
                            (chat, row["a"], row["b"]),
                        )
                        self.db.db.execute(
                            'UPDATE challenges SET state="accepted" WHERE id=?',
                            (row["id"],),
                        )
                    text = "Игровой брак расторгнут"
            else:
                self.db.execute(
                    "UPDATE challenges SET state=? WHERE id=?", (choice, row["id"])
                )
                text = (
                    "Приглашение отменено"
                    if choice == "cancel"
                    else "Приглашение отклонено"
                )
            await self.api.call(
                "answerCallbackQuery", callback_query_id=q["id"], text="Готово"
            )
            try:
                await self.api.call(
                    "editMessageReplyMarkup",
                    chat_id=chat,
                    message_id=row["message"],
                    reply_markup={"inline_keyboard": []},
                )
            except TelegramError:
                pass
            await self.say(chat, text)
        except ValueError as e:
            await self.api.call(
                "answerCallbackQuery",
                callback_query_id=q["id"],
                text=str(e)[:180],
                show_alert=True,
            )

    async def message(self, m, edited=False):
        c = m["chat"]
        chat = c["id"]
        group = c["type"] in ("group", "supergroup")
        u = m.get("from")
        if group:
            self.allowed(chat)
        if not u or m.get("sender_chat") or u.get("is_bot"):
            return
        uid = u["id"]
        text = m.get("text", "")
        parts = text.split(maxsplit=1)
        first = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        addressed = first.split("@")
        if (
            first.startswith("/")
            and len(addressed) > 1
            and addressed[1] != self.me["username"].lower()
        ):
            return await super().message(m, edited)
        cmd = addressed[0]
        if not group:
            if edited:
                return
            if cmd == "/start":
                if arg.startswith("v_"):
                    await self.confirm(uid, arg[2:])
                else:
                    await self.confirm(uid)
                if re.fullmatch(r"p_-?\d+_\d+", arg):
                    _, raw, target = arg.split("_")
                    return await self.profile(uid, int(raw), int(target))
                if re.fullmatch(r"[arc]_-?\d+", arg):
                    kind, raw = arg.split("_")
                    target = int(raw)
                    self.allowed(target)
                    if kind == "a":
                        return await self.administration(uid, target)
                    if kind == "r":
                        return await self.emit_private_rules(uid, target, u)
                    return await self.say(uid, COMMANDS)
                if arg.startswith("admin_"):
                    self.owner(uid)
                    return await self.panel(uid, int(arg[6:]))
                return await self.start(uid)
            if cmd in ("/owner", "/admin"):
                return await self.groups(uid)
            if cmd in ("/help", "/commands"):
                return await self.say(uid, COMMANDS)
            if cmd in ("/allow", "/disallow", "/allowed"):
                self.owner(uid)
                if cmd == "/allowed":
                    ids = [
                        str(r["chat"])
                        for r in self.db.db.execute("SELECT chat FROM allowed")
                    ]
                    return await self.say(
                        uid, "Разрешённые чаты:\n<pre>" + esc("\n".join(ids)) + "</pre>"
                    )
                if not re.fullmatch(r"-100\d{5,}", arg.strip()):
                    raise ValueError("Укажи числовой ID супергруппы: -100...")
                target = int(arg)
                if cmd == "/allow":
                    if self.db.one("SELECT COUNT(*) AS n FROM allowed")[
                        "n"
                    ] >= 20 and not self.db.allowed(target):
                        raise ValueError("Максимум 20 выбранных групп")
                    self.db.execute(
                        "INSERT OR IGNORE INTO allowed VALUES(?)", (target,)
                    )
                    return await self.say(
                        uid, "Чат разрешён. Теперь можно добавить бота"
                    )
                if self.db.one(
                    'SELECT 1 FROM gates WHERE chat=? AND state IN ("pending","locking","releasing","banning")',
                    (target,),
                ):
                    raise ValueError(
                        "Сначала заверши проверки новичков. Нельзя оставить их с мутом после выхода бота"
                    )
                self.db.execute("DELETE FROM allowed WHERE chat=?", (target,))
                try:
                    await self.reject_chat(target)
                except TelegramError:
                    pass
                return await self.say(uid, "Чат убран из разрешённых")
            if (
                cmd in ("/text", "/emoji", "/limits", "/maintenance")
                or uid in self.pending
            ):
                self.owner(uid)
            return await super().message(m, edited)
        self.db.chat(c)
        if edited:
            return await super().message(m, True)
        if m.get("new_chat_members") or m.get("left_chat_member"):
            return
        if any(
            k in m
            for k in (
                "text",
                "caption",
                "photo",
                "video",
                "animation",
                "sticker",
                "voice",
                "audio",
                "document",
                "video_note",
                "poll",
                "dice",
                "contact",
                "location",
            )
        ):
            self.db.count(chat, m["message_id"], u)
        if cmd in ("/owner", "/admin") or first == "админ":
            self.owner(uid)
            return await self.say(
                chat,
                "👑 Овнер-меню в личке",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {
                                "text": "Открыть",
                                "url": f'https://t.me/{self.me["username"]}?start=admin_{chat}',
                            }
                        ]
                    ]
                },
            )
        if first == "!бао":
            if not self.db.settings(chat)["enabled"]:
                return
            if self.db.meta("maintenance") == "on" and uid not in self.owners:
                return
            if await self.automated(m):
                return
            gate = self.db.one(
                "SELECT state FROM gates WHERE chat=? AND user=?", (chat, uid)
            )
            if gate and gate["state"] in ("locking", "pending", "releasing", "banning"):
                return
            p = arg.split(maxsplit=1)
            return await self.entertainment(
                m, p[0].lower() if p else "", p[1] if len(p) > 1 else ""
            )
        if cmd in ("/help", "/commands"):
            if await self.automated(m):
                return
            return await self.say(chat, COMMANDS)
        return await super().message(m, False)

    async def emit_private_rules(self, uid, chat, user):
        body = self.db.template(chat, "rules")
        emojis = self.db.emojis(chat)
        info = await self.api.call("getChat", chat_id=chat)
        fields = {
            "user": esc(user.get("first_name", "Участник")),
            "chat": esc(info.get("title", "Комарики")),
            "admin": "Bao Admin",
            "reason": "",
            "duration": "",
            "count": "",
            "limit": "",
        }
        try:
            await self.say(uid, render(body, emojis, fields))
        except TelegramError as e:
            if e.code != 400:
                raise
            await self.say(uid, render(body, emojis, fields, custom=False))

    async def handle(self, update):
        m = update.get("message") or update.get("edited_message")
        event = update.get("chat_member") or update.get("my_chat_member")
        c = m.get("chat") if m else event.get("chat") if event else None
        if c and c["type"] in ("group", "supergroup"):
            if not self.db.allowed(c["id"]):
                if event and event.get("new_chat_member", {}).get("status") in (
                    "left",
                    "kicked",
                ):
                    return
                return await self.reject_chat(c["id"])
            self.db.chat(c)
            if event:
                new = event["new_chat_member"]
                old = event.get("old_chat_member", {})
                if "my_chat_member" in update:
                    return
                present = lambda member: member.get("status") in (
                    "member",
                    "administrator",
                    "creator",
                ) or (
                    member.get("status") == "restricted"
                    and member.get("is_member", False)
                )
                if present(new) and not present(old):
                    await self.gate_join(c["id"], new["user"], event.get("date"))
                elif (
                    present(new)
                    and present(old)
                    and event.get("from", {}).get("id") != self.me["id"]
                    and new != old
                ):
                    self.db.execute(
                        'UPDATE gates SET manual_hold=1 WHERE chat=? AND user=? AND state IN ("pending","locking","releasing")',
                        (c["id"], new["user"]["id"]),
                    )
                elif not present(new):
                    self.db.execute(
                        'UPDATE gates SET state="left" WHERE chat=? AND user=? AND state IN ("pending","locking","releasing","banning","approved")',
                        (c["id"], new["user"]["id"]),
                    )
                return
            if m and "edited_message" not in update:
                if m.get("new_chat_members"):
                    for u in m["new_chat_members"]:
                        await self.gate_join(c["id"], u, m.get("date"))
                    return
                if m.get("left_chat_member"):
                    self.db.execute(
                        'UPDATE gates SET state="left" WHERE chat=? AND user=?',
                        (c["id"], m["left_chat_member"]["id"]),
                    )
                    return
        return await super().handle(update)
