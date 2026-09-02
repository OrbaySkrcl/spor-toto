"""Telegram botu — uzun yoklama (long polling) ile, ek kütüphane gerektirmez.

Neden `python-telegram-bot` yerine düz HTTP:
  * Bağımlılık yüzeyi küçülür (yalnızca `requests`), Railway'de imaj küçük ve
    kurulum hızlı olur — ücretsiz kotada bu önemli.
  * Uzun yoklama webhook gerektirmez; alan adı, TLS sertifikası veya açık port
    olmadan çalışır. Railway'de "worker" olarak çalıştırmak yeterlidir.

Komutlar:
    /start /yardim   — kullanım
    /durum           — veri ve model durumu
    /butce 2000      — kupon bütçesini ayarla (TL)
    /kolon 576       — bütçe yerine doğrudan kolon sınırı
    /tahmin A - B    — tek maç olasılığı
    /kupon           — ardından 15 satırlık listeyi gönderin
    /egri            — bütçe/şans eğrisi
    /tablo           — kolon ve bedel tabloları
    /guncelle        — veriyi tazele ve yeniden eğit
"""

from __future__ import annotations

import html
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import requests

from ..config import Settings
from ..coupon.optimizer import budget_frontier, optimize_coupon
from ..report import (
    format_coupon,
    format_frontier,
    format_predictions,
    format_stats,
    format_tables,
)
from ..storage import Database
from ..teams import parse_coupon

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3800  # Telegram sınırı 4096; <pre> etiketlerine pay bırakıldı

WELCOME = """<b>Spor Toto Tahmin Botu</b>

15 maçlık kuponunuz için 1/0/2 olasılıkları üretir ve bütçenize göre
<b>P(15/15)'i maksimize eden</b> sistem dağılımını hesaplar.

<b>Kullanım</b>
1️⃣ Bütçenizi ayarlayın:  <code>/butce 2000</code>
2️⃣ 15 maçı alt alta gönderin:
<code>1. Galatasaray - Fenerbahçe
2. Beşiktaş - Trabzonspor
...</code>

<b>Komutlar</b>
/durum — veri ve model durumu
/tahmin Takım A - Takım B — tek maç
/kolon 576 — kolon sınırı (bütçe yerine)
/egri — bütçe / kazanma şansı eğrisi
/eksik Takım -0.25 sebep — sakatlık/ceza düzeltmesi
/tablo — kolon adedi ve kupon bedeli tabloları
/guncelle — veriyi tazele, modeli yeniden eğit
/yardim — bu mesaj

⚠️ Bu bir olasılık aracıdır, kazanç garantisi değildir. Spor Toto'da 15/15
tutturmak optimize edilmiş kuponlarda bile düşük olasılıklı bir olaydır;
/egri komutu size gerçek rakamları gösterir."""


@dataclass
class ChatState:
    """Sohbet başına kupon ayarları."""

    budget: float | None = None
    columns: int | None = None
    last_predictions: list = field(default_factory=list)


class SporTotoBot:
    def __init__(self, settings: Settings, token: str):
        self.settings = settings
        self.token = token
        self.session = requests.Session()
        self.states: dict[int, ChatState] = {}
        self.predictor = None
        self._lock = threading.Lock()
        self._offset = 0

    # -- Telegram API -----------------------------------------------------
    def _call(self, method: str, **params):
        try:
            resp = self.session.post(
                API.format(token=self.token, method=method), json=params, timeout=70
            )
            data = resp.json()
        except Exception as exc:
            log.warning("Telegram %s başarısız: %s", method, exc)
            return None
        if not data.get("ok"):
            log.warning("Telegram %s hatası: %s", method, data.get("description"))
            return None
        return data.get("result")

    def send(self, chat_id: int, text: str, monospace: bool = False) -> None:
        """Uzun metinleri Telegram sınırına göre parçalayarak gönderir."""
        for chunk in _split(text, MAX_MESSAGE):
            body = f"<pre>{html.escape(chunk)}</pre>" if monospace else chunk
            self._call("sendMessage", chat_id=chat_id, text=body,
                       parse_mode="HTML", disable_web_page_preview=True)

    # -- model ------------------------------------------------------------
    def ensure_model(self, chat_id: int | None = None):
        """Tahminciyi yükler (ilk çağrıda eğitir). Eş zamanlı çağrılara karşı kilitli."""
        with self._lock:
            if self.predictor is not None:
                return self.predictor
            if chat_id:
                self.send(chat_id, "⏳ Model yükleniyor, bu ilk seferde biraz sürebilir…")

            # Boş kurulumda (ör. Railway'de ilk açılış) veriyi kendiliğinden indir.
            if not Database(self.settings.db_path).stats()["matches"]:
                from ..ingest import ingest

                log.info("Veritabanı boş; ilk veri indirmesi başlıyor")
                if chat_id:
                    self.send(chat_id, "⏳ İlk kurulum: veri indiriliyor…")
                ingest(self.settings)

            from ..predictor import Predictor

            predictor = Predictor(self.settings)
            reused = predictor.load_blend()
            predictor.train(calibrate=not reused, progress=False)
            if not reused:
                predictor.save_blend()
            self.predictor = predictor
            log.info("Model hazır: %s", predictor.blend.describe())
            return predictor

    def retrain(self, chat_id: int) -> None:
        with self._lock:
            self.predictor = None
        self.send(chat_id, "⏳ Model yeniden eğitiliyor…")
        from ..predictor import Predictor

        predictor = Predictor(self.settings)
        report = predictor.train(calibrate=True, progress=False)
        predictor.save_blend()
        with self._lock:
            self.predictor = predictor
        self.send(
            chat_id,
            f"✅ Eğitim tamam.\n{report['matches']} maç "
            f"({report['first_date']} → {report['last_date']})\n"
            f"<code>{html.escape(predictor.blend.describe())}</code>",
        )

    # -- komutlar ---------------------------------------------------------
    def handle(self, message: dict) -> None:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return
        state = self.states.setdefault(chat_id, ChatState())

        try:
            self._dispatch(chat_id, state, text)
        except Exception as exc:
            log.exception("Mesaj işlenemedi")
            self.send(chat_id, f"⚠️ Hata: {html.escape(str(exc))}")

    def _dispatch(self, chat_id: int, state: ChatState, text: str) -> None:
        lowered = text.lower()
        command = lowered.split()[0].split("@")[0] if lowered.startswith("/") else ""
        argument = text[len(command):].strip() if command else text

        if command in {"/start", "/yardim", "/help"}:
            self.send(chat_id, WELCOME)
        elif command == "/durum":
            self._cmd_status(chat_id)
        elif command == "/butce":
            self._cmd_budget(chat_id, state, argument)
        elif command == "/kolon":
            self._cmd_columns(chat_id, state, argument)
        elif command == "/tablo":
            self.send(chat_id, format_tables(self.settings.coupon.column_price), monospace=True)
        elif command == "/guncelle":
            threading.Thread(target=self._cmd_update, args=(chat_id,), daemon=True).start()
        elif command == "/egit":
            threading.Thread(target=self.retrain, args=(chat_id,), daemon=True).start()
        elif command == "/tahmin":
            self._cmd_predict(chat_id, argument)
        elif command == "/egri":
            self._cmd_frontier(chat_id, state)
        elif command == "/eksik":
            self._cmd_adjust(chat_id, argument)
        elif command == "/kupon":
            self._cmd_coupon(chat_id, state, argument)
        else:
            # Komutsuz mesaj: birden çok maç satırı varsa kupon sayılır.
            fixtures = parse_coupon(text)
            if len(fixtures) >= 3:
                self._cmd_coupon(chat_id, state, text)
            elif len(fixtures) == 1:
                self._cmd_predict(chat_id, text)
            else:
                self.send(chat_id, "Anlayamadım. /yardim yazarak kullanımı görebilirsiniz.")

    def _cmd_status(self, chat_id: int) -> None:
        db = Database(self.settings.db_path)
        stats = db.stats()
        if not stats["matches"]:
            self.send(chat_id, "Veritabanı boş. /guncelle ile veriyi indirin.")
            return
        text = format_stats(stats)
        last = db.get_meta("last_ingest")
        if last:
            text += f"\n\nSon güncelleme: {last}"
        self.send(chat_id, text, monospace=True)
        predictor = self.predictor
        if predictor is not None:
            self.send(chat_id, f"<b>Model:</b> <code>{html.escape(predictor.blend.describe())}</code>")

    def _cmd_budget(self, chat_id: int, state: ChatState, argument: str) -> None:
        if not argument:
            current = state.budget or self.settings.coupon.default_budget
            self.send(chat_id, f"Güncel bütçe: <b>{current:,.0f} TL</b>".replace(",", "."))
            return
        try:
            budget = float(argument.replace(".", "").replace(",", ".").replace("tl", "").strip())
        except ValueError:
            self.send(chat_id, "Bütçeyi sayı olarak yazın: <code>/butce 2000</code>")
            return
        state.budget = budget
        state.columns = None
        price = self.settings.coupon.column_price
        self.send(
            chat_id,
            f"✅ Bütçe: <b>{budget:,.0f} TL</b> → en fazla <b>{int(budget // price):,}</b> kolon "
            f"(kolon {price:,.2f} TL)".replace(",", "."),
        )

    def _cmd_columns(self, chat_id: int, state: ChatState, argument: str) -> None:
        try:
            state.columns = max(1, int(argument.strip()))
            state.budget = None
        except ValueError:
            self.send(chat_id, "Kolon sınırını sayı olarak yazın: <code>/kolon 576</code>")
            return
        self.send(chat_id, f"✅ Kolon sınırı: <b>{state.columns:,}</b>".replace(",", "."))

    def _cmd_update(self, chat_id: int) -> None:
        from ..ingest import ingest

        self.send(chat_id, "⏳ Veri güncelleniyor…")
        result = ingest(self.settings)
        self.send(chat_id, f"✅ {html.escape(result.summary())}")
        self.retrain(chat_id)

    def _cmd_predict(self, chat_id: int, argument: str) -> None:
        fixtures = parse_coupon(argument)
        if not fixtures:
            self.send(chat_id, "Maçı şöyle yazın: <code>/tahmin Galatasaray - Fenerbahçe</code>")
            return
        predictor = self.ensure_model(chat_id)
        predictions = predictor.predict([{"home": h, "away": a} for h, a in fixtures])
        self.send(chat_id, format_predictions(predictions, show_components=True), monospace=True)

    def _cmd_coupon(self, chat_id: int, state: ChatState, argument: str) -> None:
        fixtures = parse_coupon(argument)
        if len(fixtures) < 2:
            self.send(
                chat_id,
                "15 maçı alt alta gönderin:\n<code>1. Galatasaray - Fenerbahçe\n"
                "2. Beşiktaş - Trabzonspor\n…</code>",
            )
            return

        predictor = self.ensure_model(chat_id)
        predictions = predictor.predict([{"home": h, "away": a} for h, a in fixtures])
        state.last_predictions = predictions
        self.send(chat_id, format_predictions(predictions), monospace=True)

        price = self.settings.coupon.column_price
        budget = state.budget if state.columns is None else None
        if budget is None and state.columns is None:
            budget = self.settings.coupon.default_budget
        plan = optimize_coupon(
            predictions, max_columns=state.columns, budget=budget, column_price=price
        )
        self.send(chat_id, format_coupon(plan), monospace=True)
        if len(fixtures) != self.settings.coupon.n_matches:
            self.send(
                chat_id,
                f"ℹ️ {len(fixtures)} maç okundu; Spor Toto kuponu "
                f"{self.settings.coupon.n_matches} maçtır.",
            )

    def _cmd_adjust(self, chat_id: int, argument: str) -> None:
        """`/eksik Galatasaray -0.25 golcü sakat` — elle takım gücü düzeltmesi.

        Sakatlık/ceza verisi ücretsiz kaynakta olmadığı için bilgiyi kullanıcıdan
        alırız. Düzeltme logaritmik ölçektedir: -0.25 ≈ %22 daha az gol beklentisi.
        """
        from datetime import date, timedelta

        tokens = argument.split()
        if len(tokens) < 2:
            self.send(
                chat_id,
                "Kullanım: <code>/eksik Galatasaray -0.25 golcü sakat</code>\n"
                "Sayı logaritmik düzeltmedir: -0.25 ≈ %22 daha az gol beklentisi. "
                "14 gün geçerli olur.\n<code>/eksik liste</code> ile mevcutları görün.",
            )
            return
        if tokens[0].lower() in {"liste", "list"}:
            self._send_adjustment_list(chat_id)
            return

        # Sayı, takım adı ile not arasında yer alır: "Ad Soyad -0.25 açıklama"
        delta_index = next(
            (i for i, t in enumerate(tokens) if _is_number(t)), None
        )
        if delta_index in (None, 0):
            self.send(chat_id, "Düzeltme miktarını bulamadım. Örnek: <code>/eksik Galatasaray -0.25</code>")
            return
        team_name = " ".join(tokens[:delta_index])
        delta = float(tokens[delta_index].replace(",", "."))
        note = " ".join(tokens[delta_index + 1 :]) or None

        db = Database(self.settings.db_path)
        from ..teams import TeamResolver

        match = TeamResolver(db.known_teams()).resolve(team_name)
        if not match.confident:
            alts = ", ".join(t for t, _ in match.alternatives[:3])
            self.send(
                chat_id,
                f"Takımı bulamadım: <b>{html.escape(team_name)}</b>\n"
                f"En yakın: {html.escape(str(match.team))} (benzerlik {match.score:.2f})"
                + (f"\nAlternatifler: {html.escape(alts)}" if alts else ""),
            )
            return

        start = date.today()
        db.add_adjustment(
            match.team, start.isoformat(), attack=delta,
            valid_to=(start + timedelta(days=14)).isoformat(), note=note,
        )
        # Düzeltmeler tahmin anında okunur; modeli yeniden eğitmeye gerek yok.
        self.send(
            chat_id,
            f"✅ <b>{html.escape(match.team)}</b> atak düzeltmesi <b>{delta:+.2f}</b> "
            f"(14 gün)" + (f" — {html.escape(note)}" if note else ""),
        )

    def _send_adjustment_list(self, chat_id: int) -> None:
        from datetime import date

        active = Database(self.settings.db_path).active_adjustments(date.today().isoformat())
        if not active:
            self.send(chat_id, "Geçerli düzeltme yok.")
            return
        lines = [f"{team:<26} atak {atk:+.2f}  defans {dfn:+.2f}"
                 for team, (atk, dfn) in sorted(active.items())]
        self.send(chat_id, "Geçerli düzeltmeler:\n" + "\n".join(lines), monospace=True)

    def _cmd_frontier(self, chat_id: int, state: ChatState) -> None:
        if not state.last_predictions:
            self.send(chat_id, "Önce bir kupon gönderin, sonra /egri yazın.")
            return
        price = self.settings.coupon.column_price
        plans = budget_frontier(state.last_predictions, price, max_columns=100_000)
        self.send(chat_id, format_frontier(plans, price), monospace=True)

    # -- otomatik tazeleme ------------------------------------------------
    def _auto_refresh_loop(self, interval_hours: float) -> None:
        """Veriyi periyodik olarak tazeler ve modeli yeniden kalibre eder.

        Railway'de botu kurup unutabilmek için gerekli: yeni hafta sonuçları
        girdikçe model kendiliğinden güncel kalır. Hata hâlinde döngü ölmez,
        bir sonraki turda yeniden dener.
        """
        from ..ingest import ingest
        from ..predictor import Predictor

        while True:
            time.sleep(interval_hours * 3600.0)
            try:
                result = ingest(self.settings)
                log.info("Otomatik güncelleme: %s", result.summary())
                predictor = Predictor(self.settings)
                predictor.train(calibrate=True, progress=False)
                predictor.save_blend()
                with self._lock:
                    self.predictor = predictor
                log.info("Otomatik yeniden eğitim tamam: %s", predictor.blend.describe())
            except Exception:
                log.exception("Otomatik güncelleme başarısız; bir sonraki turda denenecek")

    # -- ana döngü --------------------------------------------------------
    def run(self) -> int:
        me = self._call("getMe")
        if not me:
            log.error("Telegram jetonu geçersiz veya API'ye ulaşılamıyor.")
            return 1
        log.info("Bot çalışıyor: @%s", me.get("username"))

        # Modeli arka planda önden yükle ki ilk komut beklemesin.
        threading.Thread(target=self.ensure_model, daemon=True).start()

        interval = _auto_refresh_hours()
        if interval > 0:
            log.info("Otomatik veri tazeleme açık: her %.0f saatte bir", interval)
            threading.Thread(
                target=self._auto_refresh_loop, args=(interval,), daemon=True
            ).start()

        backoff = 1.0
        while True:
            updates = self._call("getUpdates", offset=self._offset, timeout=50)
            if updates is None:
                time.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
                continue
            backoff = 1.0
            for update in updates:
                self._offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if message:
                    self.handle(message)


def _is_number(token: str) -> bool:
    try:
        float(token.replace(",", "."))
    except ValueError:
        return False
    return True


def _auto_refresh_hours() -> float:
    """`SPORTOTO_AUTO_UPDATE_HOURS` (0 = kapalı, varsayılan 24)."""
    raw = os.environ.get("SPORTOTO_AUTO_UPDATE_HOURS", "24").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 24.0


def _split(text: str, limit: int) -> list[str]:
    """Metni satır sınırlarını koruyarak parçalara böler."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    size = 0
    for line in text.splitlines(keepends=True):
        # Tek bir satır bile sınırı aşıyorsa sert böl.
        while len(line) > limit:
            if current:
                chunks.append("".join(current))
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if size + len(line) > limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def run_bot(settings: Settings, token: str | None = None) -> int:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.error(
            "Bot jetonu yok. TELEGRAM_BOT_TOKEN ortam değişkenini ayarlayın "
            "veya --token ile verin."
        )
        return 1
    settings.ensure_dirs()
    return SporTotoBot(settings, token).run()
