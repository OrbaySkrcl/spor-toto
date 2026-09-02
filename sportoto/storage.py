"""SQLite deposu: maçlar, oranlar, model çıktıları ve manuel düzeltmeler."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id     TEXT PRIMARY KEY,
    league       TEXT NOT NULL,
    season       TEXT,
    date         TEXT NOT NULL,          -- ISO 8601, YYYY-MM-DD
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    fthg         INTEGER,                -- maç sonu ev golü
    ftag         INTEGER,
    ftr          TEXT,                   -- 'H' | 'D' | 'A'
    hthg         INTEGER,                -- ilk yarı
    htag         INTEGER,
    hs           INTEGER, "as"  INTEGER, -- şut
    hst          INTEGER, ast   INTEGER, -- isabetli şut
    hc           INTEGER, ac    INTEGER, -- korner
    hf           INTEGER, af    INTEGER, -- faul
    hy           INTEGER, ay    INTEGER, -- sarı
    hr           INTEGER, ar    INTEGER, -- kırmızı
    odds_h       REAL, odds_d  REAL, odds_a  REAL,   -- açılış / ortalama
    codds_h      REAL, codds_d REAL, codds_a REAL,   -- kapanış
    source       TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_date   ON matches(date);
CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league, date);
CREATE INDEX IF NOT EXISTS idx_matches_home   ON matches(home);
CREATE INDEX IF NOT EXISTS idx_matches_away   ON matches(away);

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

-- Sakatlık/ceza gibi ücretsiz kaynaktan gelmeyen bilgiler için manuel düzeltme.
-- delta: takımın gol beklentisine uygulanacak logaritmik düzeltme
--        (-0.20 ≈ %18 gol üretimi kaybı).
CREATE TABLE IF NOT EXISTS adjustments (
    team       TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    attack     REAL DEFAULT 0.0,
    defense    REAL DEFAULT 0.0,
    note       TEXT,
    PRIMARY KEY (team, valid_from)
);

-- Haftalık tahminlerin otomatik gönderileceği Telegram sohbetleri.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id    INTEGER PRIMARY KEY,
    added_at   TEXT,
    last_sent  TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    match_key   TEXT NOT NULL,           -- 'league|date|home|away'
    created_at  TEXT NOT NULL,
    p_home      REAL, p_draw REAL, p_away REAL,
    model       TEXT,
    PRIMARY KEY (match_key, created_at, model)
);
"""

#: matches tablosundaki yazılabilir sütunlar (match_id hariç).
MATCH_COLUMNS = [
    "league", "season", "date", "home", "away",
    "fthg", "ftag", "ftr", "hthg", "htag",
    "hs", "as", "hst", "ast", "hc", "ac", "hf", "af", "hy", "ay", "hr", "ar",
    "odds_h", "odds_d", "odds_a", "codds_h", "codds_d", "codds_a",
    "source", "updated_at",
]


def make_match_id(league: str, date: str, home: str, away: str) -> str:
    """Kaynaktan bağımsız, çakışmayan ve tekrarlanabilir maç kimliği."""
    raw = f"{league}|{date}|{home}|{away}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Database:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- yazma ------------------------------------------------------------
    def upsert_matches(self, rows: Iterable[dict]) -> int:
        """Maçları ekler/günceller. Aynı maç tekrar gelirse üzerine yazar.

        `COALESCE(excluded.x, matches.x)` sayesinde eksik sütunlu bir kaynak
        (ör. oransız GitHub aynası) mevcut zengin veriyi silmez.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = []
        for row in rows:
            record = {c: row.get(c) for c in MATCH_COLUMNS}
            record["updated_at"] = now
            if not (record["league"] and record["date"] and record["home"] and record["away"]):
                continue
            record["match_id"] = make_match_id(
                record["league"], record["date"], record["home"], record["away"]
            )
            payload.append(record)
        if not payload:
            return 0

        cols = ["match_id"] + MATCH_COLUMNS
        quoted = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(f":{c}" for c in cols)
        updates = ", ".join(
            f'"{c}" = COALESCE(excluded."{c}", matches."{c}")'
            for c in MATCH_COLUMNS
            if c != "updated_at"
        )
        sql = (
            f"INSERT INTO matches ({quoted}) VALUES ({placeholders}) "
            f'ON CONFLICT(match_id) DO UPDATE SET {updates}, "updated_at" = excluded."updated_at"'
        )
        with self.connect() as conn:
            conn.executemany(sql, payload)
        return len(payload)

    def upsert_fixtures(self, rows: Iterable[dict]) -> int:
        """Yaklaşan maçları yazar; **açılış oranını korur**.

        Oran hareketi (açılıştan kapanışa kayma) bilgi taşır: para hangi yöne
        akıyor. Geçmiş veride kaynak hem açılış hem kapanış oranını verir; ama
        yaklaşan maçlar için yalnızca "şu anki" oran vardır. Bu yüzden kendi
        anlık görüntülerimizi tutarız:

          * `odds_*`  — ilk gördüğümüz oran (açılış). Bir daha yazılmaz.
          * `codds_*` — en son gördüğümüz oran (kapanışa en yakın). Her
            güncellemede tazelenir.

        Bu, geçmiş verideki açılış/kapanış düzeniyle birebir aynı anlama gelir,
        dolayısıyla eğitimde öğrenilen hareket etkisi üretimde de geçerlidir.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = []
        for row in rows:
            if not (row.get("league") and row.get("date") and row.get("home") and row.get("away")):
                continue
            payload.append({
                "match_id": make_match_id(row["league"], row["date"], row["home"], row["away"]),
                "league": row["league"], "date": row["date"],
                "home": row["home"], "away": row["away"],
                "odds_h": row.get("odds_h"), "odds_d": row.get("odds_d"),
                "odds_a": row.get("odds_a"),
                "source": row.get("source"), "updated_at": now,
            })
        if not payload:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO matches
                    (match_id, league, date, home, away,
                     odds_h, odds_d, odds_a, codds_h, codds_d, codds_a, source, updated_at)
                VALUES
                    (:match_id, :league, :date, :home, :away,
                     :odds_h, :odds_d, :odds_a, :odds_h, :odds_d, :odds_a,
                     :source, :updated_at)
                ON CONFLICT(match_id) DO UPDATE SET
                    codds_h = COALESCE(excluded.codds_h, matches.codds_h),
                    codds_d = COALESCE(excluded.codds_d, matches.codds_d),
                    codds_a = COALESCE(excluded.codds_a, matches.codds_a),
                    odds_h  = COALESCE(matches.odds_h, excluded.odds_h),
                    odds_d  = COALESCE(matches.odds_d, excluded.odds_d),
                    odds_a  = COALESCE(matches.odds_a, excluded.odds_a),
                    updated_at = excluded.updated_at
                """,
                payload,
            )
        return len(payload)

    def set_meta(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def add_adjustment(
        self,
        team: str,
        valid_from: str,
        attack: float = 0.0,
        defense: float = 0.0,
        valid_to: str | None = None,
        note: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO adjustments(team, valid_from, valid_to, attack, defense, note) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(team, valid_from) DO UPDATE SET "
                "valid_to=excluded.valid_to, attack=excluded.attack, "
                "defense=excluded.defense, note=excluded.note",
                (team, valid_from, valid_to, attack, defense, note),
            )

    def active_adjustments(self, on_date: str) -> dict[str, tuple[float, float]]:
        """Verilen tarihte geçerli manuel düzeltmeler: takım -> (atak, defans)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT team, attack, defense FROM adjustments "
                "WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)",
                (on_date, on_date),
            ).fetchall()
        return {r["team"]: (r["attack"] or 0.0, r["defense"] or 0.0) for r in rows}

    # -- okuma ------------------------------------------------------------
    def load_matches(
        self,
        leagues: list[str] | None = None,
        before: str | None = None,
        since: str | None = None,
        played_only: bool = True,
    ):
        """Maçları pandas DataFrame olarak döner (tarihe göre sıralı)."""
        import pandas as pd

        clauses, params = [], []
        if leagues:
            clauses.append(f"league IN ({','.join('?' * len(leagues))})")
            params.extend(leagues)
        if before:
            clauses.append("date < ?")
            params.append(before)
        if since:
            clauses.append("date >= ?")
            params.append(since)
        if played_only:
            clauses.append("ftr IS NOT NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM matches {where} ORDER BY date ASC, match_id ASC"

        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).reset_index(drop=True)
        return df

    def load_fixtures(self, days: int = 8, leagues: list[str] | None = None):
        """Önümüzdeki `days` gün içindeki **oynanmamış** maçları döner.

        Fikstürler `ingest` sırasında sonuçsuz maç olarak yazılır; oynandıktan
        sonra aynı satır sonuçla güncellenir. Bu yüzden "oynanmamış" = `ftr`
        boş demektir.
        """
        import pandas as pd
        from datetime import date, timedelta

        today = date.today()
        clauses = ["ftr IS NULL", "date >= ?", "date <= ?"]
        params: list = [today.isoformat(), (today + timedelta(days=days)).isoformat()]
        if leagues:
            clauses.append(f"league IN ({','.join('?' * len(leagues))})")
            params.extend(leagues)
        sql = (
            f"SELECT * FROM matches WHERE {' AND '.join(clauses)} "
            "ORDER BY date ASC, league ASC, home ASC"
        )
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).reset_index(drop=True)
        return df

    def upcoming_index(self, days: int = 30) -> dict:
        """(ev, deplasman) -> yaklaşan maç kaydı.

        Kullanıcı kupon listesini elle yapıştırdığında oran gelmez; oysa aynı
        maçın güncel oranları `ingest` sırasında zaten indirilmiştir. Bu dizin
        sayesinde yapıştırılan maç, saklanan fikstürle eşleştirilip oranları
        (ve gerçek tarihi) kullanılabilir. Piyasa modelin en güçlü tek sinyali
        olduğu için bu, tahmin kalitesinde en büyük tek kazanç.
        """
        fixtures = self.load_fixtures(days=days)
        index = {}
        for _, row in fixtures.iterrows():
            index[(row["home"], row["away"])] = {
                "match_id": row["match_id"],
                "league": row["league"],
                "date": row["date"],
                "odds_h": row.get("odds_h"),
                "odds_d": row.get("odds_d"),
                "odds_a": row.get("odds_a"),
            }
        return index

    def coverage(self, configured: list[str] | None = None) -> dict:
        """Hangi liglerde veri var, hangilerinde yok.

        Kullanıcının "bu maç neden zayıf tahmin edildi" sorusunu tahmine
        bırakmamak için: yapılandırılmış ama verisi gelmemiş ligler açıkça
        listelenir.
        """
        stats = self.stats()
        present = {row["league"]: row["n"] for row in stats["per_league"]}
        configured = [c.upper() for c in (configured or [])]
        return {
            "present": present,
            "empty": [c for c in configured if not present.get(c)],
            "missing_from_source": json.loads(self.get_meta("missing_leagues", "[]")),
        }

    # -- tahmin geçmişi ---------------------------------------------------
    def save_predictions(self, rows: Iterable[dict], model: str = "blend") -> int:
        """Tahminleri kaydeder ki sonradan gerçek sonuçlarla karşılaştırılabilsin.

        Yalnızca veritabanındaki bir fikstüre bağlanabilen (yani `match_id`
        taşıyan) tahminler saklanır; aksi hâlde sonuç geldiğinde eşleştirme
        yapılamaz ve kayıt işe yaramaz.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = [
            {
                "match_key": r["match_id"],
                "created_at": now,
                "p_home": float(r["p_home"]),
                "p_draw": float(r["p_draw"]),
                "p_away": float(r["p_away"]),
                "model": model,
            }
            for r in rows
            if r.get("match_id")
        ]
        if not payload:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO predictions(match_key, created_at, p_home, p_draw, p_away, model) "
                "VALUES(:match_key, :created_at, :p_home, :p_draw, :p_away, :model) "
                "ON CONFLICT(match_key, created_at, model) DO NOTHING",
                payload,
            )
        return len(payload)

    def prediction_history(self, limit: int = 500):
        """Kaydedilmiş tahminleri gerçekleşen sonuçlarla birleştirir.

        Her maç için en son yapılan tahmin kullanılır.
        """
        import pandas as pd

        sql = """
            SELECT p.match_key, p.created_at, p.p_home, p.p_draw, p.p_away,
                   m.league, m.date, m.home, m.away, m.ftr
            FROM predictions p
            JOIN matches m ON m.match_id = p.match_key
            WHERE m.ftr IS NOT NULL
              AND p.created_at = (
                    SELECT MAX(p2.created_at) FROM predictions p2
                    WHERE p2.match_key = p.match_key AND p2.model = p.model
              )
            ORDER BY m.date DESC
            LIMIT ?
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=[int(limit)])
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).reset_index(drop=True)
        return df

    def pending_prediction_count(self) -> int:
        """Kaydedilmiş ama henüz oynanmamış maç sayısı."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT p.match_key) n FROM predictions p "
                "JOIN matches m ON m.match_id = p.match_key WHERE m.ftr IS NULL"
            ).fetchone()
        return row["n"] or 0

    # -- Telegram aboneleri ----------------------------------------------
    def add_subscriber(self, chat_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO subscribers(chat_id, added_at) VALUES(?,?) "
                "ON CONFLICT(chat_id) DO NOTHING",
                (int(chat_id), now),
            )

    def remove_subscriber(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (int(chat_id),))

    def subscribers(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        return [r["chat_id"] for r in rows]

    def is_subscriber(self, chat_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscribers WHERE chat_id = ?", (int(chat_id),)
            ).fetchone()
        return row is not None

    def mark_sent(self, chat_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                "UPDATE subscribers SET last_sent = ? WHERE chat_id = ?", (now, int(chat_id))
            )

    def known_teams(self, leagues: list[str] | None = None) -> list[str]:
        clauses, params = [], []
        if leagues:
            clauses.append(f"league IN ({','.join('?' * len(leagues))})")
            params.extend(leagues)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT home AS team FROM matches {where} "
            f"UNION SELECT away AS team FROM matches {where}"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, params + params).fetchall()
        return sorted({r["team"] for r in rows if r["team"]})

    def team_leagues(self) -> dict[str, str]:
        """Her takımın en son oynadığı ligi döner."""
        sql = """
            SELECT team, league FROM (
                SELECT home AS team, league, date FROM matches
                UNION ALL
                SELECT away AS team, league, date FROM matches
            ) ORDER BY date ASC
        """
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return {r["team"]: r["league"] for r in rows}

    def stats(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) n, MIN(date) d0, MAX(date) d1, "
                "COUNT(DISTINCT league) nl, "
                "SUM(CASE WHEN odds_h IS NOT NULL THEN 1 ELSE 0 END) with_odds "
                "FROM matches WHERE ftr IS NOT NULL"
            ).fetchone()
            per_league = conn.execute(
                "SELECT league, COUNT(*) n, MAX(date) last FROM matches "
                "WHERE ftr IS NOT NULL GROUP BY league ORDER BY n DESC"
            ).fetchall()
            upcoming = conn.execute(
                "SELECT COUNT(*) n FROM matches WHERE ftr IS NULL AND date >= date('now')"
            ).fetchone()
        return {
            "matches": row["n"] or 0,
            "first_date": row["d0"],
            "last_date": row["d1"],
            "leagues": row["nl"] or 0,
            "with_odds": row["with_odds"] or 0,
            "per_league": [dict(r) for r in per_league],
            "upcoming": upcoming["n"] or 0,
        }
