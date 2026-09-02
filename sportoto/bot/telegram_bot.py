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
from ..coupon.optimizer import budget_frontier, compare_budgets, optimize_coupon
from ..report import (
    format_comparison_mobile,
    format_coupon_mobile,
    format_coverage,
    format_frontier_mobile,
    format_predictions_mobile,
    format_quality,
    format_stats,
    format_track_record,
    format_tables_mobile,
    format_weekly_mobile,
)
from ..storage import Database
from ..teams import parse_coupon

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

#: Telegram'ın "/" menüsünde görünecek komutlar ve açıklamaları.
BOT_COMMANDS = [
    ("hafta", "Bu haftanın maçları ve tahminleri"),
    ("ayarlar", "Bütçe ve hedefi tek dokunuşla ayarla"),
    ("otomatik", "En tahmin edilebilir 15 maçtan kupon"),
    ("karsilastir", "Farklı bütçeleri yan yana gör"),
    ("butce", "Kupon bütçenizi ayarlayın (TL)"),
    ("hedef", "Kaç doğru hedefleyelim (12-15)"),
    ("egri", "Bütçe / kazanma şansı eğrisi"),
    ("basari", "Modelin ölçülmüş isabet oranı"),
    ("gecmis", "Gerçek karne: tahminler vs sonuçlar"),
    ("kapsam", "Hangi ligler kapsanıyor"),
    ("durum", "Veri ve model durumu"),
    ("eksik", "Sakatlık/ceza bildir"),
    ("tablo", "Kolon adedi ve kupon bedeli"),
    ("abone", "Haftalık tahminleri otomatik al"),
    ("guncelle", "Veriyi tazele, modeli yenile"),
    ("yardim", "Kullanım rehberi"),
]

#: Sohbette sürekli görünen kısayol tuşları. Butona basınca metni gönderirler,
#: bu yüzden komut ayrıştırıcısı bu etiketleri de tanır.
KEYBOARD_LABELS = {
    "📅 Bu Hafta": "/hafta",
    "🎫 Otomatik Kupon": "/otomatik",
    "⚖️ Bütçe Karşılaştır": "/karsilastir",
    "📈 Karne": "/gecmis",
    "⚙️ Ayarlar": "/ayarlar",
    "❓ Yardım": "/yardim",
}

MAIN_KEYBOARD = {
    "keyboard": [
        ["📅 Bu Hafta", "🎫 Otomatik Kupon"],
        ["⚖️ Bütçe Karşılaştır", "📈 Karne"],
        ["⚙️ Ayarlar", "❓ Yardım"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "15 maçı alt alta yapıştırın…",
}
MAX_MESSAGE = 3800  # Telegram sınırı 4096; <pre> etiketlerine pay bırakıldı

WELCOME = """<b>⚽ Spor Toto Tahmin Botu</b>

Her hafta oynanacak maçlar için 1/0/2 olasılıkları üretir ve bütçenize göre
<b>15/15 şansını maksimize eden</b> sistem kuponunu hesaplar.

<b>Aşağıdaki tuşlarla başlayın</b> 👇
📅 Bu Hafta · 🎫 Otomatik Kupon · ⚖️ Bütçe Karşılaştır
📈 Karne · ⚙️ Ayarlar

Tüm komutlar için mesaj kutusundaki <b>menü butonuna</b> basın.
/gecmis — gerçek karne (tahminleriniz vs sonuçlar)

<b>Kendi kuponunuz için</b>
1️⃣ <code>/butce 2000</code> ile bütçenizi ayarlayın
2️⃣ 15 maçı alt alta gönderin:
<code>1. Galatasaray - Fenerbahçe
2. Beşiktaş - Trabzonspor
...</code>

<b>Diğer komutlar</b>
/abone — haftalık tahminleri otomatik gönder
/kapsam — hangi ligler kapsanıyor
/karsilastir — bütçeleri yan yana gör
/tahmin Takım A - Takım B — tek maç
/kolon 576 — bütçe yerine kolon sınırı
/hedef 13 — kaç doğruyu hedefleyelim (Spor Toto 12'den öder)
/egri — bütçe / kazanma şansı eğrisi
/eksik Takım -0.25 sebep — sakatlık düzeltmesi
/tablo — kolon adedi ve bedel tabloları
/durum — veri ve model durumu
/guncelle — veriyi tazele, modeli yeniden eğit

⚠️ Bu bir olasılık aracıdır, kazanç garantisi değildir. 15/15 tutturmak
optimize edilmiş kuponlarda bile düşük olasılıklı bir olaydır; /egri
komutu size gerçek rakamları gösterir."""



@dataclass
class ChatState:
    """Sohbet başına kupon ayarları."""

    budget: float | None = None
    columns: int | None = None
    #: Optimizasyonun maksimize edeceği eşik. None = hepsi doğru (15/15).
    target: int | None = None
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

    def send(self, chat_id: int, text: str, monospace: bool = False,
             markup: dict | None = None) -> None:
        """Uzun metinleri Telegram sınırına göre parçalayarak gönderir.

        `markup` verilirse yalnızca son parçaya eklenir; aksi hâlde tuş takımı
        her parçada tekrar çizilir.
        """
        chunks = _split(text, MAX_MESSAGE)
        for index, chunk in enumerate(chunks):
            body = f"<pre>{html.escape(chunk)}</pre>" if monospace else chunk
            params = {
                "chat_id": chat_id, "text": body,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            }
            if markup is not None and index == len(chunks) - 1:
                params["reply_markup"] = markup
            self._call("sendMessage", **params)

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

    # -- menüler ----------------------------------------------------------
    def _register_commands(self) -> None:
        """Telegram'ın "/" menüsünü doldurur ve menü butonunu açar.

        Kullanıcının komutları ezberlemesi gerekmesin: mesaj kutusundaki menü
        butonuna basınca açıklamalı liste gelir.
        """
        ok = self._call(
            "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in BOT_COMMANDS],
        )
        self._call("setChatMenuButton", menu_button={"type": "commands"})
        log.info("Komut menüsü %s", "kuruldu" if ok else "kurulamadı")

    # -- menüler ----------------------------------------------------------
    def _settings_markup(self, state: "ChatState") -> dict:
        """Bütçe ve hedef için tek dokunuşluk seçenekler."""
        budget = state.budget or self.settings.coupon.default_budget
        target = state.target or self.settings.coupon.n_matches

        def label(text, selected):
            return f"● {text}" if selected else text

        return {
            "inline_keyboard": [
                [{"text": "— Bütçe —", "callback_data": "noop"}],
                [
                    {"text": label(f"{amount} TL", abs(budget - amount) < 1),
                     "callback_data": f"butce:{amount}"}
                    for amount in (250, 500, 1000)
                ],
                [
                    {"text": label(f"{amount} TL", abs(budget - amount) < 1),
                     "callback_data": f"butce:{amount}"}
                    for amount in (2500, 5000, 10000)
                ],
                [{"text": "— Hedef (kaç doğru) —", "callback_data": "noop"}],
                [
                    {"text": label(f"{k}+", target == k), "callback_data": f"hedef:{k}"}
                    for k in (12, 13, 14, 15)
                ],
                [
                    {"text": "📅 Bu hafta", "callback_data": "cmd:hafta"},
                    {"text": "🎫 Kupon", "callback_data": "cmd:otomatik"},
                ],
            ]
        }

    def _cmd_settings(self, chat_id: int, state: "ChatState") -> None:
        price = self.settings.coupon.column_price
        budget = state.budget or self.settings.coupon.default_budget
        target = state.target or self.settings.coupon.n_matches
        self._call(
            "sendMessage",
            chat_id=chat_id,
            parse_mode="HTML",
            text=(
                "⚙️ <b>AYARLAR</b>\n\n"
                f"💰 Bütçe: <b>{budget:,.0f} TL</b> "
                f"(≈ {int(budget // price):,} kolon)\n"
                f"🎯 Hedef: <b>en az {target} doğru</b>\n"
                f"🧾 Kolon fiyatı: {price:,.2f} TL\n\n"
                "<i>Aşağıdan seçin ya da <code>/butce 3500</code> gibi "
                "kendi değerinizi yazın.</i>"
            ).replace(",", "."),
            reply_markup=self._settings_markup(state),
        )

    def handle_callback(self, query: dict) -> None:
        """Inline butonlara basıldığında çalışır."""
        data = query.get("data") or ""
        message = query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        self._call("answerCallbackQuery", callback_query_id=query.get("id"))
        if not chat_id or data == "noop":
            return
        state = self.states.setdefault(chat_id, ChatState())
        try:
            action, _, value = data.partition(":")
            if action == "butce":
                self._cmd_budget(chat_id, state, value)
                self._refresh_settings(query, state)
            elif action == "hedef":
                self._cmd_target(chat_id, state, value)
                self._refresh_settings(query, state)
            elif action == "cmd":
                self._dispatch(chat_id, state, f"/{value}")
        except Exception as exc:
            log.exception("Buton işlenemedi")
            self.send(chat_id, f"⚠️ Hata: {html.escape(str(exc))}")

    def _refresh_settings(self, query: dict, state: "ChatState") -> None:
        """Seçim yapıldıktan sonra menüdeki işaretleri günceller."""
        message = query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        if chat_id and message_id:
            self._call(
                "editMessageReplyMarkup",
                chat_id=chat_id, message_id=message_id,
                reply_markup=self._settings_markup(state),
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
        # Kısayol tuşları metin gönderir; onları komuta çevir.
        text = KEYBOARD_LABELS.get(text.strip(), text)
        lowered = text.lower()
        command = lowered.split()[0].split("@")[0] if lowered.startswith("/") else ""
        argument = text[len(command):].strip() if command else text

        if command in {"/start", "/yardim", "/help", "/menu"}:
            self.send(chat_id, WELCOME, markup=MAIN_KEYBOARD)
        elif command in {"/ayarlar", "/ayar"}:
            self._cmd_settings(chat_id, state)
        elif command in {"/karsilastir", "/karsilastır"}:
            self._cmd_compare(chat_id, state)
        elif command == "/kapsam":
            self._cmd_coverage(chat_id)
        elif command == "/durum":
            self._cmd_status(chat_id)
        elif command == "/butce":
            self._cmd_budget(chat_id, state, argument)
        elif command == "/kolon":
            self._cmd_columns(chat_id, state, argument)
        elif command == "/tablo":
            self.send(chat_id, format_tables_mobile(self.settings.coupon.column_price))
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
        elif command in {"/hafta", "/fikstur", "/fikstür"}:
            self._cmd_week(chat_id, argument)
        elif command in {"/otomatik", "/oto"}:
            self._cmd_auto_coupon(chat_id, state)
        elif command == "/basari":
            self._cmd_quality(chat_id)
        elif command in {"/gecmis", "/karne"}:
            self._cmd_history(chat_id)
        elif command == "/hedef":
            self._cmd_target(chat_id, state, argument)
        elif command == "/abone":
            self._cmd_subscribe(chat_id, True)
        elif command in {"/abonelikiptal", "/abonelik_iptal", "/durdur"}:
            self._cmd_subscribe(chat_id, False)
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
        self.send(chat_id, format_predictions_mobile(predictions, title="TAHMİN"))

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
        self.send(chat_id, format_predictions_mobile(predictions))

        price = self.settings.coupon.column_price
        budget = state.budget if state.columns is None else None
        if budget is None and state.columns is None:
            budget = self.settings.coupon.default_budget
        plan = optimize_coupon(
            predictions, max_columns=state.columns, budget=budget,
            column_price=price, target=state.target,
        )
        predictor.record(predictions)
        self.send(chat_id, format_coupon_mobile(plan))
        if len(fixtures) != self.settings.coupon.n_matches:
            self.send(
                chat_id,
                f"ℹ️ {len(fixtures)} maç okundu; Spor Toto kuponu "
                f"{self.settings.coupon.n_matches} maçtır.",
            )

    def _cmd_week(self, chat_id: int, argument: str = "") -> None:
        """Bu haftanın fikstürü ve tahminleri."""
        days = 8
        tokens = argument.split()
        if tokens and tokens[0].isdigit():
            days = max(1, min(int(tokens[0]), 30))

        predictor = self.ensure_model(chat_id)
        predictions = predictor.upcoming(days=days)
        if not predictions:
            self.send(
                chat_id,
                "Yaklaşan maç bulunamadı.\n\n"
                "Olası nedenler:\n"
                "• Veri kaynağı fikstürleri henüz yayınlamamış (genelde maçtan "
                "birkaç gün önce açıklanır)\n"
                "• Veri güncel değil → <code>/guncelle</code> deneyin\n\n"
                "Bu arada kendi listenizi 15 satır hâlinde gönderebilirsiniz.",
            )
            return
        predictor.record(predictions)
        self.send(chat_id, format_weekly_mobile(predictions))
        self.send(
            chat_id,
            f"📋 <b>{len(predictions)} maç</b> tahmin edildi. "
            "Sistem kuponu için <code>/otomatik</code>, "
            "kendi listeniz için 15 maçı alt alta gönderin.",
        )

    def _cmd_auto_coupon(self, chat_id: int, state: ChatState) -> None:
        """En tahmin edilebilir 15 yaklaşan maçtan sistem kuponu kurar."""
        predictor = self.ensure_model(chat_id)
        predictions = predictor.upcoming(days=8)
        need = self.settings.coupon.n_matches
        if len(predictions) < need:
            self.send(
                chat_id,
                f"Otomatik kupon için en az {need} yaklaşan maç gerekiyor; "
                f"şu an {len(predictions)} maç var. <code>/guncelle</code> deneyin "
                "veya kendi listenizi gönderin.",
            )
            return

        # En yüksek güvenli maçlar seçilir: 15/15 şansını en çok bunlar artırır.
        chosen = sorted(predictions, key=lambda p: -p.confidence)[:need]
        chosen.sort(key=lambda p: (p.date or "", p.home))
        state.last_predictions = chosen

        self.send(
            chat_id,
            "🤖 <b>Otomatik kupon</b>\n"
            "Bu, resmî Spor Toto listesi <b>değildir</b> — yaklaşan maçlar "
            f"arasından en tahmin edilebilir {need} tanesi seçildi. Resmî liste "
            "için maçları alt alta gönderin.",
        )
        self.send(chat_id, format_predictions_mobile(chosen))

        price = self.settings.coupon.column_price
        budget = state.budget if state.columns is None else None
        if budget is None and state.columns is None:
            budget = self.settings.coupon.default_budget
        plan = optimize_coupon(
            chosen, max_columns=state.columns, budget=budget,
            column_price=price, target=state.target,
        )
        predictor.record(chosen)
        self.send(chat_id, format_coupon_mobile(plan))

    def _cmd_quality(self, chat_id: int) -> None:
        predictor = self.ensure_model(chat_id)
        self.send(chat_id, format_quality(predictor.quality), monospace=True)

    def _cmd_compare(self, chat_id: int, state: ChatState) -> None:
        """Aynı maçlar için farklı bütçeleri yan yana gösterir."""
        predictions = state.last_predictions
        if not predictions:
            predictor = self.ensure_model(chat_id)
            upcoming = predictor.upcoming(days=8)
            need = self.settings.coupon.n_matches
            if len(upcoming) < need:
                self.send(
                    chat_id,
                    "Önce bir kupon gönderin ya da /hafta ile maçları getirin; "
                    "sonra /karsilastir yazın.",
                )
                return
            predictions = sorted(upcoming, key=lambda p: -p.confidence)[:need]
            state.last_predictions = predictions

        price = self.settings.coupon.column_price
        plans = compare_budgets(
            predictions, [250, 500, 1000, 2500, 5000, 10000], price, target=state.target
        )
        self.send(chat_id, format_comparison_mobile(plans, price),
                  markup=self._settings_markup(state))

    def _cmd_coverage(self, chat_id: int) -> None:
        db = Database(self.settings.db_path)
        self.send(chat_id, format_coverage(db.coverage(self.settings.leagues)))

    def _cmd_history(self, chat_id: int) -> None:
        predictor = self.ensure_model(chat_id)
        self.send(chat_id, format_track_record(predictor.track_record()))

    def _cmd_target(self, chat_id: int, state: ChatState, argument: str) -> None:
        """Optimizasyonun hangi eşiği maksimize edeceğini ayarlar."""
        need = self.settings.coupon.n_matches
        if not argument.strip():
            current = state.target or need
            self.send(
                chat_id,
                f"Güncel hedef: <b>en az {current} doğru</b>.\n\n"
                f"Spor Toto 12'den itibaren ödeme yapar; makul bütçelerde "
                f"<code>/hedef 13</code> daha anlamlıdır.\n"
                f"Hepsini tutturmaya oynamak için <code>/hedef {need}</code>.",
            )
            return
        try:
            target = int(argument.split()[0])
        except ValueError:
            self.send(chat_id, f"Sayı yazın: <code>/hedef 13</code> (1-{need})")
            return
        if not 1 <= target <= need:
            self.send(chat_id, f"Hedef 1 ile {need} arasında olmalı.")
            return
        state.target = target
        note = (
            " Kupon artık hepsini tutturmaya değil, bu eşiği geçmeye göre "
            "dağıtılacak." if target < need else ""
        )
        self.send(chat_id, f"✅ Hedef: <b>en az {target} doğru</b>.{note}")

    def _cmd_subscribe(self, chat_id: int, enable: bool) -> None:
        db = Database(self.settings.db_path)
        if enable:
            db.add_subscriber(chat_id)
            day = _TR_WEEKDAYS[_weekly_day()]
            self.send(
                chat_id,
                f"✅ Abone oldunuz. Haftalık tahminler her <b>{day}</b> "
                "otomatik gönderilecek.\nİptal için /abonelikiptal",
            )
        else:
            db.remove_subscriber(chat_id)
            self.send(chat_id, "Abonelik iptal edildi. Tekrar açmak için /abone")

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
        self.send(chat_id, format_frontier_mobile(plans, price))

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

    # -- haftalık otomatik gönderim ---------------------------------------
    def _weekly_push_loop(self) -> None:
        """Abonelere haftada bir fikstür tahminlerini gönderir.

        Yarım saatte bir uyanır, gün/saat penceresine bakar ve aboneye son
        gönderimden 3 günden fazla geçtiyse yollar. Bu, yeniden başlatmalardan
        sonra çift gönderimi de engeller (son gönderim tarihi diskte tutulur).
        """
        from datetime import datetime, timedelta, timezone

        while True:
            time.sleep(1800)
            try:
                now = datetime.now(timezone.utc)
                if now.weekday() != _weekly_day() or now.hour != _weekly_hour():
                    continue
                db = Database(self.settings.db_path)
                targets = db.subscribers()
                if not targets:
                    continue

                predictor = self.ensure_model()
                predictions = predictor.upcoming(days=8)
                if not predictions:
                    log.info("Haftalık gönderim atlandı: yaklaşan maç yok")
                    continue
                body = format_weekly_mobile(predictions)

                for chat_id in targets:
                    with db.connect() as conn:
                        row = conn.execute(
                            "SELECT last_sent FROM subscribers WHERE chat_id = ?", (chat_id,)
                        ).fetchone()
                    last = row["last_sent"] if row else None
                    if last:
                        try:
                            if datetime.fromisoformat(last) > now - timedelta(days=3):
                                continue
                        except ValueError:
                            pass
                    self.send(chat_id, "📅 <b>Haftalık tahminler</b>")
                    self.send(chat_id, body)
                    db.mark_sent(chat_id)
                log.info("Haftalık gönderim tamamlandı (%d abone)", len(targets))
            except Exception:
                log.exception("Haftalık gönderim başarısız; sonraki turda denenecek")

    # -- ana döngü --------------------------------------------------------
    def run(self) -> int:
        me = self._call("getMe")
        if not me:
            log.error("Telegram jetonu geçersiz veya API'ye ulaşılamıyor.")
            return 1
        log.info("Bot çalışıyor: @%s", me.get("username"))
        self._register_commands()

        # Modeli arka planda önden yükle ki ilk komut beklemesin.
        threading.Thread(target=self.ensure_model, daemon=True).start()

        threading.Thread(target=self._weekly_push_loop, daemon=True).start()

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
                if "callback_query" in update:
                    self.handle_callback(update["callback_query"])
                    continue
                message = update.get("message") or update.get("edited_message")
                if message:
                    self.handle(message)


_TR_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def _weekly_day() -> int:
    """Haftalık gönderim günü (0 = Pazartesi). Varsayılan: Perşembe."""
    try:
        return max(0, min(int(os.environ.get("SPORTOTO_WEEKLY_DAY", "3")), 6))
    except ValueError:
        return 3


def _weekly_hour() -> int:
    """Haftalık gönderim saati (UTC). Varsayılan 09:00 UTC = 12:00 TR."""
    try:
        return max(0, min(int(os.environ.get("SPORTOTO_WEEKLY_HOUR", "9")), 23))
    except ValueError:
        return 9


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


MISSING_TOKEN_BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║  KURULUM TAMAMLANMADI — TELEGRAM_BOT_TOKEN eksik                     ║
╚══════════════════════════════════════════════════════════════════════╝

Bot çalışabilmek için bir Telegram jetonuna ihtiyaç duyar.

  1. Telegram'da @BotFather ile konuşun, /newbot yazın.
  2. Size verdiği uzun jetonu kopyalayın
     (şuna benzer: 1234567890:AAF...).
  3. Railway'de bu servise gidin → Variables sekmesi →
     "New Variable" → isim: TELEGRAM_BOT_TOKEN, değer: jetonunuz → Add.
  4. Railway servisi kendiliğinden yeniden başlatır.

Başka hiçbir değişken zorunlu değildir; kalıcı disk (/data) bağlıysa
kendiliğinden bulunur.

Süreç burada bekliyor — jetonu ekleyip kaydedince yeniden başlayacaktır.
"""


def run_bot(settings: Settings, token: str | None = None) -> int:
    token = (token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        # Çıkmak yerine beklemek bilinçli bir tercih: konteyner yöneticileri
        # (Railway dahil) çöken süreci saniyede bir yeniden başlatır ve gerçek
        # mesaj yüzlerce satırın altında kaybolur. Burada tek bir okunabilir
        # yönerge basılır ve süreç sessizce bekler.
        log.error(MISSING_TOKEN_BANNER)
        while True:
            time.sleep(3600)
            if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
                log.info("Jeton bulundu, bot başlatılıyor.")
                break
        token = os.environ["TELEGRAM_BOT_TOKEN"].strip()

    settings.ensure_dirs()
    log.info("Veri klasörü: %s", settings.data_dir)
    return SporTotoBot(settings, token).run()
