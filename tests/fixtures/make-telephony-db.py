#!/usr/bin/env python3
"""Build fixture telephony databases mirroring the real on-device schema.

The rows are chosen to break naive parsing: bodies containing the ", " sequence
that makes `content query` output ambiguous, embedded quotes, newlines, emoji,
and calls of every CallLog type so the incoming-only filter can be proven.
"""
import pathlib
import sqlite3
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".tree-msg")
tel = root / "data/data/com.android.providers.telephony/databases"
con = root / "data/data/com.android.providers.contacts/databases"
tel.mkdir(parents=True, exist_ok=True)
con.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(tel / "mmssms.db")
db.execute("DROP TABLE IF EXISTS sms")
db.execute("""CREATE TABLE sms (
    _id INTEGER PRIMARY KEY, thread_id INTEGER, address TEXT, body TEXT,
    date INTEGER, date_sent INTEGER, read INTEGER, type INTEGER, sub_id INTEGER)""")
db.executemany(
    "INSERT INTO sms (_id,thread_id,address,body,date,date_sent,read,type,sub_id) VALUES (?,?,?,?,?,?,?,?,?)",
    [
        # type 1 = inbox, 2 = sent. Only the inbox may ever be relayed.
        (1, 10, "+15550001", "plain message",                       1756200000000, 1756199999000, 0, 1, 1),
        (2, 10, "+15550001", "hello, world, with commas",           1756200001000, 1756200000000, 0, 1, 1),
        (3, 11, "+15550002", 'quotes "inside" and \\ backslash',    1756200002000, 1756200001000, 0, 1, 1),
        (4, 11, "+15550002", "line one\nline two",                  1756200003000, 1756200002000, 0, 1, 1),
        (5, 12, "+15550003", "emoji \U0001f50b and unicode я", 1756200004000, 1756200003000, 0, 1, 1),
        (6, 12, "+15550003", "THIS IS A SENT MESSAGE",              1756200005000, 1756200004000, 1, 2, 1),
    ],
)
db.commit()
db.close()

db = sqlite3.connect(con / "calllog.db")
db.execute("DROP TABLE IF EXISTS calls")
db.execute("""CREATE TABLE calls (
    _id INTEGER PRIMARY KEY, number TEXT, date INTEGER, duration INTEGER,
    type INTEGER, subscription_id TEXT)""")
db.executemany(
    "INSERT INTO calls (_id,number,date,duration,type,subscription_id) VALUES (?,?,?,?,?,?)",
    [
        (1, "+15550001", 1756200000000, 42, 1, "1"),   # incoming
        (2, "+15550002", 1756200001000, 17, 2, "1"),   # outgoing - must never appear
        (3, "+15550003", 1756200002000, 0,  3, "1"),   # missed
        (4, "+15550004", 1756200003000, 0,  5, "1"),   # rejected
        (5, "+15550005", 1756200004000, 0,  6, "1"),   # blocked
    ],
)
db.commit()
db.close()

print(root)
