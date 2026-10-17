import time
from datetime import datetime, timezone, timedelta
from storage import Store


def day(now=None):
    return datetime.fromtimestamp(
        now or time.time(), timezone(timedelta(hours=3))
    ).strftime("%Y-%m-%d")


class CommunityStore(Store):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS allowed(chat INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS community_config(chat INTEGER,key TEXT,value TEXT,PRIMARY KEY(chat,key));
        CREATE TABLE IF NOT EXISTS people(chat INTEGER,user INTEGER,name TEXT,username TEXT,nick TEXT DEFAULT '',posts INTEGER DEFAULT 0,coins INTEGER DEFAULT 0,xp INTEGER DEFAULT 0,feeds INTEGER DEFAULT 0,last_feed INTEGER DEFAULT 0,last_daily TEXT DEFAULT '',PRIMARY KEY(chat,user));
        CREATE TABLE IF NOT EXISTS activity(chat INTEGER,user INTEGER,day TEXT,posts INTEGER DEFAULT 0,PRIMARY KEY(chat,user,day));
        CREATE TABLE IF NOT EXISTS counted(chat INTEGER,message INTEGER,PRIMARY KEY(chat,message));
        CREATE TABLE IF NOT EXISTS gates(chat INTEGER,user INTEGER,token TEXT UNIQUE,deadline INTEGER,state TEXT,manual_hold INTEGER DEFAULT 0,prompt INTEGER DEFAULT 0,PRIMARY KEY(chat,user));
        CREATE TABLE IF NOT EXISTS gate_original(chat INTEGER,user INTEGER,permissions TEXT,until INTEGER,PRIMARY KEY(chat,user));
        CREATE TABLE IF NOT EXISTS ranks(chat INTEGER,user INTEGER,rank INTEGER,PRIMARY KEY(chat,user));
        CREATE TABLE IF NOT EXISTS challenges(id INTEGER PRIMARY KEY AUTOINCREMENT,chat INTEGER,a INTEGER,b INTEGER,kind TEXT,state TEXT DEFAULT 'pending',expires INTEGER,message INTEGER DEFAULT 0,created INTEGER);
        CREATE TABLE IF NOT EXISTS couples(chat INTEGER,user INTEGER,partner INTEGER,since INTEGER,love INTEGER DEFAULT 0,PRIMARY KEY(chat,user));
        CREATE TABLE IF NOT EXISTS gifts(id INTEGER PRIMARY KEY AUTOINCREMENT,chat INTEGER,sender INTEGER,receiver INTEGER,item TEXT,created INTEGER);
        CREATE TABLE IF NOT EXISTS cooldowns(chat INTEGER,user INTEGER,kind TEXT,until INTEGER,PRIMARY KEY(chat,user,kind));
        """
        )
        self.db.commit()

    def one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def config(self, chat, key, default=""):
        row = self.one(
            "SELECT value FROM community_config WHERE chat=? AND key=?", (chat, key)
        )
        return row["value"] if row else default

    def configure(self, chat, key, value):
        self.execute(
            "INSERT OR REPLACE INTO community_config VALUES(?,?,?)",
            (chat, key, str(value)),
        )

    def allowed(self, chat):
        return bool(self.one("SELECT 1 FROM allowed WHERE chat=?", (chat,)))

    def touch(self, chat, user):
        name = " ".join(
            x for x in (user.get("first_name", ""), user.get("last_name", "")) if x
        )[:128] or str(user["id"])
        self.execute(
            "INSERT INTO people(chat,user,name,username) VALUES(?,?,?,?) ON CONFLICT(chat,user) DO UPDATE SET name=excluded.name,username=excluded.username",
            (chat, user["id"], name, user.get("username", "")),
        )
        return self.person(chat, user["id"])

    def person(self, chat, uid):
        return self.one("SELECT * FROM people WHERE chat=? AND user=?", (chat, uid))

    def count(self, chat, message, user):
        self.touch(chat, user)
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO counted VALUES(?,?)", (chat, message)
            )
            if not cur.rowcount:
                return False
            self.db.execute(
                "UPDATE people SET posts=posts+1 WHERE chat=? AND user=?",
                (chat, user["id"]),
            )
            self.db.execute(
                "INSERT INTO activity VALUES(?,?,?,1) ON CONFLICT(chat,user,day) DO UPDATE SET posts=posts+1",
                (chat, user["id"], day()),
            )
        return True

    def cooldown(self, chat, uid, kind, seconds):
        now = int(time.time())
        row = self.one(
            "SELECT until FROM cooldowns WHERE chat=? AND user=? AND kind=?",
            (chat, uid, kind),
        )
        if row and row["until"] > now:
            raise ValueError(f'Подожди ещё {max(1,(row["until"]-now+59)//60)} мин')
        self.execute(
            "INSERT OR REPLACE INTO cooldowns VALUES(?,?,?,?)",
            (chat, uid, kind, now + seconds),
        )

    def daily(self, chat, uid):
        today = day()
        with self.db:
            cur = self.db.execute(
                "UPDATE people SET coins=coins+100,last_daily=? WHERE chat=? AND user=? AND last_daily<>?",
                (today, chat, uid, today),
            )
            if not cur.rowcount:
                raise ValueError(
                    "Сегодня бонус уже получен. Новый день начинается в 00:00 по Москве"
                )
        return 100

    def feed(self, chat, uid):
        now = int(time.time())
        p = self.person(chat, uid)
        if p["last_feed"] + 14400 > now:
            raise ValueError(
                f'Бао сыт. Следующее кормление через {(p["last_feed"]+14400-now+59)//60} мин'
            )
        with self.db:
            cur = self.db.execute(
                "UPDATE people SET coins=coins-15,xp=xp+12,feeds=feeds+1,last_feed=? WHERE chat=? AND user=? AND coins>=15 AND last_feed<=?",
                (now, chat, uid, now - 14400),
            )
            if not cur.rowcount:
                raise ValueError("Нужно 15 монет. Получи !бао бонус")
        return self.person(chat, uid)

    def gift(self, chat, sender, receiver, item, cost):
        with self.db:
            cur = self.db.execute(
                "UPDATE people SET coins=coins-? WHERE chat=? AND user=? AND coins>=?",
                (cost, chat, sender, cost),
            )
            if not cur.rowcount:
                raise ValueError("Не хватает монет. Получи !бао бонус")
            self.db.execute(
                "INSERT INTO gifts(chat,sender,receiver,item,created) VALUES(?,?,?,?,?)",
                (chat, sender, receiver, item, int(time.time())),
            )
            self.db.execute(
                "UPDATE couples SET love=love+1 WHERE chat=? AND ((user=? AND partner=?) OR (user=? AND partner=?))",
                (chat, sender, receiver, receiver, sender),
            )

    def challenge(self, chat, a, b, kind):
        now = int(time.time())
        self.cooldown(chat, a, "invite", 120)
        if self.one(
            'SELECT 1 FROM challenges WHERE chat=? AND kind=? AND state="pending" AND expires>? AND (a IN (?,?) OR b IN (?,?))',
            (chat, kind, now, a, b, a, b),
        ):
            raise ValueError(
                "У одного из вас уже есть активное приглашение. Оно действует 10 минут"
            )
        return self.execute(
            "INSERT INTO challenges(chat,a,b,kind,expires,created) VALUES(?,?,?,?,?,?)",
            (chat, a, b, kind, now + 600, now),
        ).lastrowid

    def marry(self, row):
        with self.db:
            if self.one(
                "SELECT 1 FROM couples WHERE chat=? AND user IN (?,?)",
                (row["chat"], row["a"], row["b"]),
            ):
                raise ValueError("Один из участников уже состоит в браке")
            now = int(time.time())
            for a, b in ((row["a"], row["b"]), (row["b"], row["a"])):
                self.db.execute(
                    "INSERT INTO couples VALUES(?,?,?,?,0)", (row["chat"], a, b, now)
                )
            self.db.execute(
                'UPDATE challenges SET state="accepted" WHERE id=? AND state="pending"',
                (row["id"],),
            )

    def duel(self, row, winner):
        now = int(time.time())
        with self.db:
            for uid in (row["a"], row["b"]):
                cd = self.one(
                    'SELECT until FROM cooldowns WHERE chat=? AND user=? AND kind="duel"',
                    (row["chat"], uid),
                )
                if cd and cd["until"] > now:
                    raise ValueError("После дуэли каждый участник отдыхает 1 час")
            for uid in (row["a"], row["b"]):
                self.db.execute(
                    'INSERT OR REPLACE INTO cooldowns VALUES(?,?,"duel",?)',
                    (row["chat"], uid, now + 3600),
                )
                self.db.execute(
                    "UPDATE people SET coins=coins+? WHERE chat=? AND user=?",
                    (15 if uid == winner else 5, row["chat"], uid),
                )
            self.db.execute(
                'UPDATE challenges SET state="accepted" WHERE id=? AND state="pending"',
                (row["id"],),
            )
