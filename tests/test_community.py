import copy
import tempfile
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from community import Komariki
from community_store import CommunityStore
from api import TelegramError
from test_bot import FakeAPI, CHAT, ADMIN, USER, BOT, msg

OWNER = 777
OTHER = 30


class LiveFake(FakeAPI):
    def __init__(self):
        super().__init__()
        self.restrictions = {}
        self.next_message = 1000
        self.fail_ban = False
        self.fail_lock = False

    async def call(self, method, **params):
        if method == "sendMessage":
            self.calls.append((method, params))
            self.next_message += 1
            return {"message_id": self.next_message}
        if method == "getChatAdministrators":
            self.calls.append((method, params))
            return [
                {
                    "status": "administrator",
                    "user": {"id": ADMIN, "first_name": "Actual admin"},
                }
            ]
        if method == "getChatMember":
            result = await super().call(method, **params)
            if params["user_id"] in self.restrictions:
                result.update(self.restrictions[params["user_id"]])
            return result
        if method == "restrictChatMember":
            self.calls.append((method, params))
            if self.fail_lock:
                raise TelegramError({"error_code": 400, "description": "rights"})
            self.roles[params["user_id"]] = "restricted"
            self.restrictions[params["user_id"]] = {
                **params["permissions"],
                "until_date": params.get("until_date", 0),
                "is_member": True,
            }
            return True
        if method == "banChatMember":
            self.calls.append((method, params))
            if self.fail_ban:
                raise TelegramError({"error_code": 400, "description": "rights"})
            self.roles[params["user_id"]] = "kicked"
            self.restrictions.pop(params["user_id"], None)
            return True
        return await super().call(method, **params)


class CommunityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = LiveFake()
        self.db = CommunityStore(":memory:")
        self.db.execute("INSERT INTO allowed VALUES(?)", (CHAT,))
        self.app = Komariki(self.api, self.db, {OWNER})
        self.app.me = {"id": BOT, "username": "BaoAdminBot"}
        for uid in (ADMIN, USER, OTHER):
            self.db.touch(CHAT, {"id": uid, "first_name": f"Real {uid}"})

    def tearDown(self):
        self.db.db.close()

    def gate(self):
        return self.db.one("SELECT * FROM gates WHERE chat=? AND user=?", (CHAT, USER))

    async def join(self):
        await self.app.gate_join(CHAT, {"id": USER, "first_name": "New user"})

    async def test_owner_only_groups(self):
        with self.assertRaises(ValueError):
            await self.app.groups(ADMIN)
        await self.app.groups(OWNER)

    async def test_old_callback_not_accessible_to_chat_admin(self):
        q = {
            "id": "q",
            "from": {"id": ADMIN},
            "message": {"chat": {"id": ADMIN, "type": "private"}},
            "data": f"toggle:{CHAT}:antilink",
        }
        await self.app.handle({"callback_query": q})
        self.assertFalse(self.db.settings(CHAT)["antilink"])

    async def test_owner_edit_without_telegram_admin_status(self):
        await self.app.edit_input(
            OWNER,
            msg(private=True, uid=OWNER, reply=False),
            CHAT,
            "text",
            "rules",
            "New rules",
        )
        self.assertEqual(self.db.template(CHAT, "rules"), "New rules")

    async def test_nonowner_direct_text_rejected(self):
        self.app.selected[ADMIN] = CHAT
        await self.app.handle(
            {"message": msg("/text rules changed", private=True, reply=False)}
        )
        self.assertNotEqual(self.db.template(CHAT, "rules"), "changed")

    async def test_unknown_chat_immediately_left(self):
        m = msg("hello")
        m["chat"]["id"] = CHAT - 1
        await self.app.handle({"message": m})
        self.assertEqual(self.api.actions("leaveChat")[0]["chat_id"], CHAT - 1)
        self.assertIsNone(self.db.person(CHAT - 1, ADMIN))

    async def test_gate_locks_permanently(self):
        await self.join()
        p = self.api.actions("restrictChatMember")[0]
        self.assertEqual(p["until_date"], 0)
        self.assertFalse(p["permissions"]["can_send_messages"])
        self.assertEqual(self.gate()["state"], "pending")
        self.assertGreater(self.gate()["prompt"], 0)

    async def test_duplicate_join_does_not_reset_deadline(self):
        await self.join()
        first = dict(self.gate())
        await self.join()
        self.assertEqual(self.gate()["deadline"], first["deadline"])
        self.assertEqual(len(self.api.actions("restrictChatMember")), 1)

    async def test_forged_verification_token_denied(self):
        await self.join()
        with self.assertRaises(ValueError):
            await self.app.confirm(OTHER, self.gate()["token"])
        self.assertEqual(self.gate()["state"], "pending")

    async def test_plain_start_unlocks_and_welcomes(self):
        await self.join()
        await self.app.message(msg("/start", uid=USER, private=True, reply=False))
        self.assertEqual(self.gate()["state"], "approved")
        self.assertTrue(
            self.api.actions("restrictChatMember")[-1]["permissions"][
                "can_send_messages"
            ]
        )
        self.assertFalse(
            self.api.actions("restrictChatMember")[-1]["permissions"]["can_send_photos"]
        )
        welcome = [
            p
            for p in self.api.actions("sendMessage")
            if p["chat_id"] == CHAT and "Добро пожаловать" in p["text"]
        ]
        self.assertEqual(len(welcome), 1)
        self.assertEqual(
            [len(r) for r in welcome[0]["reply_markup"]["inline_keyboard"]], [1, 2]
        )

    async def test_confirmation_idempotent(self):
        await self.join()
        token = self.gate()["token"]
        await self.app.confirm(USER, token)
        count = len(self.api.actions("restrictChatMember"))
        await self.app.confirm(USER, token)
        self.assertEqual(len(self.api.actions("restrictChatMember")), count)

    async def test_expired_gate_banned(self):
        await self.join()
        self.db.execute("UPDATE gates SET deadline=?", (int(time.time()) - 1,))
        await self.app.tick()
        self.assertEqual(self.gate()["state"], "expired")
        self.assertEqual(len(self.api.actions("banChatMember")), 1)

    async def test_late_confirmation_denied(self):
        await self.join()
        self.db.execute("UPDATE gates SET deadline=?", (int(time.time()) - 1,))
        with self.assertRaises(ValueError):
            await self.app.confirm(USER, self.gate()["token"])
        self.assertEqual(self.gate()["state"], "pending")

    async def test_promoted_admin_not_banned(self):
        await self.join()
        self.api.roles[USER] = "administrator"
        self.db.execute("UPDATE gates SET deadline=1")
        await self.app.tick()
        self.assertFalse(self.api.actions("banChatMember"))
        self.assertEqual(self.gate()["state"], "approved")

    async def test_failed_ban_retries(self):
        await self.join()
        self.db.execute("UPDATE gates SET deadline=1")
        self.api.fail_ban = True
        await self.app.tick()
        self.assertEqual(self.gate()["state"], "banning")
        self.api.fail_ban = False
        await self.app.tick()
        self.assertEqual(self.gate()["state"], "expired")

    async def test_failed_lock_retries(self):
        self.api.fail_lock = True
        with self.assertRaises(TelegramError):
            await self.join()
        self.assertEqual(self.gate()["state"], "locking")
        self.api.fail_lock = False
        await self.app.tick()
        self.assertEqual(self.gate()["state"], "pending")

    async def test_manual_hold_not_lifted(self):
        await self.join()
        self.db.execute("UPDATE gates SET manual_hold=1")
        before = len(self.api.actions("restrictChatMember"))
        await self.app.confirm(USER)
        self.assertEqual(len(self.api.actions("restrictChatMember")), before)

    async def test_original_restriction_restored(self):
        self.api.roles[USER] = "restricted"
        self.api.restrictions[USER] = {
            "can_send_messages": True,
            "can_send_photos": False,
            "until_date": int(time.time()) + 3600,
            "is_member": True,
        }
        await self.join()
        self.assertFalse(self.api.restrictions[USER]["can_send_messages"])
        await self.app.confirm(USER)
        self.assertTrue(self.api.restrictions[USER]["can_send_messages"])
        self.assertFalse(self.api.restrictions[USER]["can_send_photos"])
        self.assertGreater(self.api.restrictions[USER]["until_date"], int(time.time()))

    async def test_gate_state_survives_restart(self):
        await self.join()
        token = self.gate()["token"]
        new = Komariki(self.api, self.db, {OWNER})
        new.me = self.app.me
        await new.confirm(USER, token)
        self.assertEqual(self.gate()["state"], "approved")

    async def test_cannot_unmute_pending(self):
        await self.join()
        with self.assertRaises(ValueError):
            await self.app.moderate(msg("/unmute"), "unmute", "")

    async def test_allow_owner_only(self):
        await self.app.handle(
            {"message": msg(f"/allow {CHAT-1}", uid=ADMIN, private=True, reply=False)}
        )
        self.assertFalse(self.db.allowed(CHAT - 1))
        await self.app.message(
            msg(f"/allow {CHAT-1}", uid=OWNER, private=True, reply=False)
        )
        self.assertTrue(self.db.allowed(CHAT - 1))

    async def test_button_configuration(self):
        await self.app.edit_input(
            OWNER, msg(private=True, uid=OWNER), CHAT, "buttons", "", "verify 123"
        )
        self.assertEqual(
            self.app.button(CHAT, "verify", url="https://t.me/x")[
                "icon_custom_emoji_id"
            ],
            "123",
        )
        with self.assertRaises(ValueError):
            await self.app.edit_input(
                OWNER,
                msg(private=True, uid=OWNER),
                CHAT,
                "buttons",
                "",
                "url javascript:alert(1)",
            )

    async def test_activity_dedup_edits_and_bot_exclusion(self):
        m = msg("hello", uid=USER, reply=False)
        await self.app.message(m)
        await self.app.message(m)
        await self.app.message(m, edited=True)
        m2 = copy.deepcopy(m)
        m2["message_id"] = 101
        m2["from"]["is_bot"] = True
        await self.app.message(m2)
        self.assertEqual(self.db.person(CHAT, USER)["posts"], 1)

    async def test_nick_only_changes_self(self):
        await self.app.entertainment(msg(uid=ADMIN), "ник", '"Мой Бао"')
        self.assertEqual(self.db.person(CHAT, ADMIN)["nick"], "Мой Бао")
        self.assertEqual(self.db.person(CHAT, USER)["nick"], "")

    async def test_leaderboard_links_do_not_mention(self):
        self.db.count(CHAT, 1, {"id": USER, "first_name": "Name"})
        await self.app.entertainment(msg(uid=USER), "топ", "")
        text = self.api.actions("sendMessage")[-1]["text"]
        self.assertIn("?start=p_", text)
        self.assertNotIn("tg://user", text)
        self.assertNotIn("@", text)

    async def test_admin_table_uses_real_name(self):
        self.db.execute('UPDATE people SET nick="FAKE NICK" WHERE user=?', (ADMIN,))
        await self.app.administration(CHAT, CHAT)
        self.assertNotIn("FAKE NICK", self.api.actions("sendMessage")[-1]["text"])

    def test_daily_only_once(self):
        self.assertEqual(self.db.daily(CHAT, USER), 100)
        with self.assertRaises(ValueError):
            self.db.daily(CHAT, USER)
        self.assertEqual(self.db.person(CHAT, USER)["coins"], 100)

    def test_feed_cost_and_cooldown(self):
        with self.assertRaises(ValueError):
            self.db.feed(CHAT, USER)
        self.db.daily(CHAT, USER)
        self.db.feed(CHAT, USER)
        self.assertEqual(self.db.person(CHAT, USER)["coins"], 85)
        self.assertEqual(self.db.person(CHAT, USER)["xp"], 12)
        with self.assertRaises(ValueError):
            self.db.feed(CHAT, USER)

    def test_gift_no_negative_balance(self):
        with self.assertRaises(ValueError):
            self.db.gift(CHAT, USER, OTHER, "кольцо", 100)
        self.db.daily(CHAT, USER)
        self.db.gift(CHAT, USER, OTHER, "роза", 30)
        self.assertEqual(self.db.person(CHAT, USER)["coins"], 70)

    async def invitation(self, kind):
        await self.app.entertainment(msg(uid=ADMIN), kind, "")
        return self.db.one("SELECT * FROM challenges ORDER BY id DESC LIMIT 1")

    async def click(self, row, uid, choice="yes", chat=CHAT):
        await self.app.play_callback(
            {
                "id": "q",
                "from": {"id": uid},
                "data": f'play:{row["id"]}:{choice}',
                "message": {
                    "message_id": row["message"],
                    "chat": {"id": chat, "type": "supergroup"},
                },
            }
        )

    async def test_duel_only_recipient_and_once(self):
        row = await self.invitation("дуэль")
        await self.click(row, OTHER)
        self.assertEqual(self.db.person(CHAT, ADMIN)["coins"], 0)
        await self.click(row, USER)
        total = (
            self.db.person(CHAT, ADMIN)["coins"] + self.db.person(CHAT, USER)["coins"]
        )
        self.assertEqual(total, 20)
        await self.click(row, USER)
        self.assertEqual(
            self.db.person(CHAT, ADMIN)["coins"] + self.db.person(CHAT, USER)["coins"],
            20,
        )

    async def test_crosschat_duel_rejected(self):
        row = await self.invitation("дуэль")
        await self.click(row, USER, chat=CHAT - 1)
        self.assertEqual(self.db.person(CHAT, USER)["coins"], 0)

    async def test_marriage_needs_consent_and_is_reciprocal(self):
        row = await self.invitation("брак")
        await self.click(row, OTHER)
        self.assertIsNone(self.db.one("SELECT * FROM couples"))
        await self.click(row, USER)
        self.assertEqual(
            self.db.one("SELECT partner FROM couples WHERE user=?", (ADMIN,))[
                "partner"
            ],
            USER,
        )
        self.assertEqual(
            self.db.one("SELECT partner FROM couples WHERE user=?", (USER,))["partner"],
            ADMIN,
        )

    async def test_expired_invitation_rejected(self):
        row = await self.invitation("брак")
        self.db.execute("UPDATE challenges SET expires=1")
        await self.click(row, USER)
        self.assertIsNone(self.db.one("SELECT * FROM couples"))

    async def test_marriage_does_not_grant_admin(self):
        row = await self.invitation("брак")
        await self.click(row, USER)
        self.assertNotIn(USER, self.app.owners)

    async def test_started_user_without_gate_not_mutated(self):
        await self.app.message(msg("/start", uid=OTHER, private=True, reply=False))
        self.assertFalse(self.api.actions("restrictChatMember"))
        self.assertFalse(self.api.actions("banChatMember"))

    async def test_gate_persists_in_sqlite_file(self):
        await self.join()
        with tempfile.TemporaryDirectory() as folder:
            import sqlite3

            path = str(Path(folder) / "state.sqlite3")
            disk = sqlite3.connect(path)
            self.db.db.backup(disk)
            disk.close()
            reopened = CommunityStore(path)
            app = Komariki(self.api, reopened, {OWNER})
            app.me = self.app.me
            await app.confirm(USER, self.gate()["token"])
            self.assertEqual(
                reopened.one("SELECT state FROM gates")["state"], "approved"
            )
            reopened.db.close()

    async def test_gift_improves_both_spouses(self):
        row = await self.invitation("брак")
        await self.click(row, USER)
        self.db.daily(CHAT, ADMIN)
        self.db.gift(CHAT, ADMIN, USER, "роза", 30)
        self.assertEqual(
            [r["love"] for r in self.db.db.execute("SELECT love FROM couples")], [1, 1]
        )

    async def test_divorce_only_initiator(self):
        row = await self.invitation("брак")
        await self.click(row, USER)
        self.db.execute("DELETE FROM cooldowns")
        await self.app.entertainment(msg(uid=ADMIN), "развод", "")
        row = self.db.one("SELECT * FROM challenges ORDER BY id DESC LIMIT 1")
        await self.click(row, USER)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM couples")["n"], 2)
        await self.click(row, ADMIN)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM couples")["n"], 0)

    async def test_service_join_from_bot_sender_still_verified(self):
        m = msg("", uid=BOT, reply=False)
        m["from"]["is_bot"] = True
        m["new_chat_members"] = [{"id": USER, "first_name": "New"}]
        await self.app.handle({"message": m})
        self.assertEqual(self.gate()["state"], "pending")

    async def test_manual_external_mute_event_keeps_hold(self):
        await self.join()
        event = {
            "chat": {"id": CHAT, "type": "supergroup"},
            "from": {"id": ADMIN},
            "old_chat_member": {
                "status": "restricted",
                "is_member": True,
                "user": {"id": USER},
            },
            "new_chat_member": {
                "status": "restricted",
                "is_member": True,
                "until_date": int(time.time()) + 500,
                "user": {"id": USER},
            },
        }
        await self.app.handle({"chat_member": event})
        self.assertEqual(self.gate()["manual_hold"], 1)


if __name__ == "__main__":
    unittest.main()
