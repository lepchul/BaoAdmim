"""Entry point for the Komariki edition of Bao Admin."""

import asyncio
import logging
import os
import httpx
from dotenv import load_dotenv
from api import API, TelegramError
from community import Komariki
from community_store import CommunityStore

log = logging.getLogger("bao_admin")


async def main():
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    owners = {
        int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()
    }
    if not token or not owners:
        raise SystemExit("Заполни BOT_TOKEN и OWNER_IDS. Без владельца запуск запрещён")
    db = CommunityStore(os.getenv("DATABASE_PATH", "data/bao_admin.sqlite3"))
    api = API(token)
    app = Komariki(api, db, owners)
    try:
        app.me = await api.call("getMe")
        if (await api.call("getWebhookInfo")).get("url"):
            raise SystemExit("Отключи старый webhook перед запуском polling")
        if db.meta("komariki_allowlist_initialized") != "yes":
            ids = [
                int(x.strip())
                for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",")
                if x.strip()
            ]
            if not ids:
                info = await api.call("getChat", chat_id="@komariki_chatss")
                if info["type"] != "supergroup":
                    raise SystemExit("Нужна супергруппа. Укажи ALLOWED_CHAT_IDS")
                ids = [info["id"]]
                db.chat(info)
            if any(x >= 0 for x in ids):
                raise SystemExit("ALLOWED_CHAT_IDS должны быть отрицательными ID групп")
            for id in ids:
                db.execute("INSERT OR IGNORE INTO allowed VALUES(?)", (id,))
            db.meta("komariki_allowlist_initialized", "yes")
        for row in db.chats():
            if not db.allowed(row["id"]):
                try:
                    await app.reject_chat(row["id"])
                except TelegramError:
                    pass
        await api.call(
            "setMyCommands",
            commands=[
                {"command": k, "description": v}
                for k, v in (
                    ("start", "Старт и подтверждение входа"),
                    ("help", "Список команд"),
                    ("rules", "Правила"),
                    ("emojiid", "ID премиум-эмодзи"),
                    ("id", "ID"),
                    ("warn", "Предупреждение"),
                    ("mute", "Мут"),
                    ("unmute", "Размут"),
                    ("ban", "Бан"),
                    ("unban", "Разбан"),
                    ("report", "Жалоба"),
                    ("cancel", "Отмена ввода"),
                )
            ],
        )
        offset = int(db.meta("offset") or 0)
        log.info("Bao Admin Komariki started, bot id=%s", app.me["id"])
        while True:
            try:
                await app.tick()
                updates = await api.call(
                    "getUpdates",
                    offset=offset,
                    timeout=20,
                    allowed_updates=[
                        "message",
                        "edited_message",
                        "callback_query",
                        "my_chat_member",
                        "chat_member",
                    ],
                )
                for update in updates:
                    offset = update["update_id"] + 1
                    db.meta("offset", offset)
                    try:
                        await app.handle(update)
                    except (TelegramError, httpx.HTTPError, ValueError) as e:
                        log.warning(
                            "Update %s incomplete: %s",
                            update["update_id"],
                            type(e).__name__,
                        )
                    except Exception:
                        log.exception("Update handler failed")
            except TelegramError as e:
                if e.code in (401, 409):
                    raise SystemExit("Проверь токен. Запускай только один экземпляр")
                log.warning("Telegram unavailable, code=%s", e.code)
                await asyncio.sleep(min(e.retry_after, 30))
            except (httpx.HTTPError, ValueError):
                log.warning("Connection unavailable, reconnecting")
                await asyncio.sleep(3)
    finally:
        await api.close()
        db.db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
