"""SQLite is the only durable source for reports, update status and notes."""
import json
import sqlite3
from contextlib import contextmanager

from backend.settings import DB_PATH


def encode(value):
    # Analysis objects may contain numpy scalars; normalize before persistence/API use.
    def default(v):
        if hasattr(v, "item"):
            return v.item()
        raise TypeError(type(v).__name__)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=default)


class Store:
    def __init__(self, path=DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                    payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, trade_date)
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                    report_date TEXT NOT NULL, data_date TEXT NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                    summary TEXT NOT NULL, importance TEXT NOT NULL DEFAULT 'normal',
                    engine TEXT NOT NULL, created_at TEXT NOT NULL,
                    read_at TEXT, metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_reports_symbol_date ON reports(symbol, report_date);
                CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date);
                CREATE TABLE IF NOT EXISTS profiles (
                    symbol TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fetch_status (
                    symbol TEXT PRIMARY KEY, state TEXT NOT NULL, message TEXT NOT NULL,
                    attempted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put_snapshot(self, symbol, payload, now):
        with self.connect() as db:
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?) ON CONFLICT(symbol,trade_date) "
                       "DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                       (symbol, payload["data_date"], encode(payload), now))

    def snapshot(self, symbol, date=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM snapshots WHERE symbol=? AND trade_date<=? "
                             "ORDER BY trade_date DESC LIMIT 1", (symbol, date or "9999")).fetchone()
        return json.loads(row[0]) if row else None

    def snapshots(self, symbols, date=None):
        return {symbol: self.snapshot(symbol, date) for symbol in symbols}

    def report(self, report_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        return dict(row) if row else None

    def put_report(self, r):
        with self.connect() as db:
            old = db.execute("SELECT body,engine FROM reports WHERE id=?", (r["id"],)).fetchone()
            # Never downgrade an existing AI report to a fallback during a retry.
            if old and old["engine"] == "ai" and r["engine"] != "ai":
                return
            db.execute("""INSERT INTO reports
                (id,symbol,report_date,data_date,kind,title,body,summary,importance,engine,created_at,metadata)
                VALUES(:id,:symbol,:report_date,:data_date,:kind,:title,:body,:summary,:importance,:engine,:created_at,:metadata)
                ON CONFLICT(id) DO UPDATE SET body=excluded.body, summary=excluded.summary,
                    engine=excluded.engine, metadata=excluded.metadata""", {**r, "metadata": encode(r.get("metadata", {}))})

    def reports(self, symbol=None, date=None, symbols=None):
        clauses, args = [], []
        if symbol:
            clauses.append("symbol=?")
            args.append(symbol)
        if date:
            clauses.append("report_date=?")
            args.append(date)
        if symbols is not None:
            if not symbols:
                return []
            clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
            args.extend(symbols)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM reports" + where + " ORDER BY report_date DESC, created_at DESC LIMIT 400", args).fetchall()
        return [{**dict(r), "metadata": json.loads(r["metadata"])} for r in rows]

    def dates(self, symbols=None):
        if symbols is not None and not symbols:
            return []
        where = " WHERE symbol IN (" + ",".join("?" for _ in symbols) + ")" if symbols is not None else ""
        args = list(symbols) * 2 if symbols is not None else []
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT report_date FROM reports" + where + " UNION SELECT trade_date FROM snapshots" + where + " ORDER BY 1 DESC", args)]

    def seed_watchlist(self, items):
        with self.connect() as db:
            first = db.execute("INSERT OR IGNORE INTO app_meta VALUES('watchlist_seeded','1')").rowcount
            if first:
                db.executemany("INSERT INTO watchlist VALUES(?,?,1,?)", [(item["symbol"], encode(item), i) for i, item in enumerate(items)])

    def watchlist(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM watchlist WHERE active=1 ORDER BY position,symbol")]

    def add_stock(self, item):
        with self.connect() as db:
            old = db.execute("SELECT payload FROM watchlist WHERE symbol=?", (item["symbol"],)).fetchone()
            item = {**(json.loads(old[0]) if old else {}), **item}
            db.execute("INSERT INTO watchlist VALUES(?,?,1,(SELECT COALESCE(MAX(position),-1)+1 FROM watchlist)) "
                       "ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,active=1", (item["symbol"], encode(item)))
        return item

    def remove_stock(self, symbol):
        with self.connect() as db:
            db.execute("UPDATE watchlist SET active=0 WHERE symbol=?", (symbol,))

    def set_status(self, symbol, state, message, now):
        with self.connect() as db:
            db.execute("INSERT INTO fetch_status VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                       "state=excluded.state,message=excluded.message,attempted_at=excluded.attempted_at",
                       (symbol, state, message, now))

    def statuses(self):
        with self.connect() as db:
            return {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM fetch_status")}

    def save_note(self, symbol, note, now):
        with self.connect() as db:
            db.execute("INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                       "note=excluded.note,updated_at=excluded.updated_at", (symbol, note, now))

    def profile(self, symbol):
        with self.connect() as db:
            row = db.execute("SELECT * FROM profiles WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else {"symbol": symbol, "note": "", "updated_at": None}

    def mark_read(self, symbol, now, through, report_ids):
        if not report_ids:
            return
        placeholders = ','.join('?' for _ in report_ids)
        with self.connect() as db:
            db.execute(f"UPDATE reports SET read_at=? WHERE symbol=? AND created_at<=? AND id IN ({placeholders}) AND read_at IS NULL", (now, symbol, through, *report_ids))
