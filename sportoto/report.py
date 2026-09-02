"""Metin çıktıları — hem CLI hem Telegram botu aynı biçimlendiriciyi kullanır."""

from __future__ import annotations

from .coupon.optimizer import CouponPlan
from .coupon.pricing import columns_for

_BAR = "█"


def _tr_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " TL"


def _tr_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def probability_bar(p: float, width: int = 10) -> str:
    filled = int(round(p * width))
    return _BAR * filled + "·" * (width - filled)


def format_predictions(predictions, show_components: bool = False) -> str:
    """Maç bazında 1/0/2 olasılık tablosu."""
    lines = [f"{'#':>2} {'Maç':<34}{'1':>7}{'0':>7}{'2':>7}  {'Fav':>3} {'Belirsizlik':>11}"]
    lines.append("─" * 76)
    for i, p in enumerate(predictions, 1):
        name = f"{p.home} - {p.away}"
        if len(name) > 33:
            name = name[:32] + "…"
        lines.append(
            f"{i:>2} {name:<34}{p.p_home:>7.1%}{p.p_draw:>7.1%}{p.p_away:>7.1%}"
            f"  {p.favourite:>3} {probability_bar(p.entropy):>11}"
        )
        if show_components and p.components:
            parts = " ".join(
                f"{k}:{v[0]:.0%}/{v[1]:.0%}/{v[2]:.0%}" for k, v in p.components.items()
            )
            lines.append(f"   └─ {parts}")
    warnings = [w for p in predictions for w in p.warnings]
    if warnings:
        lines.append("")
        lines.append("⚠ Uyarılar:")
        lines.extend(f"   • {w}" for w in dict.fromkeys(warnings))
    return "\n".join(lines)


def format_coupon(plan: CouponPlan, title: str = "OPTİMİZE SİSTEM KUPONU") -> str:
    """Optimize edilmiş kuponun okunabilir dökümü."""
    n = len(plan.selections)
    lines = [title, "─" * 76]
    lines.append(f"{'#':>2} {'Maç':<34}{'İşaret':>8}{'Kapsama':>10}{'Risk':>9}")
    lines.append("─" * 76)
    for s in plan.selections:
        name = f"{s.home} - {s.away}" if s.away else s.home
        if len(name) > 33:
            name = name[:32] + "…"
        marker = {1: " ", 2: "▪", 3: "▪▪"}[s.size]
        lines.append(
            f"{s.index + 1:>2} {name:<34}{s.picks_text:>8}{s.cover:>10.1%}{s.risk:>8.1%} {marker}"
        )
    lines.append("─" * 76)
    lines.append(
        f"Dağılım : {plan.singles} tekli · {plan.doubles} ikili · {plan.triples} üçlü"
    )
    lines.append(
        f"Kolon   : {_tr_int(plan.columns)}"
        + (f"   Bedel: {_tr_money(plan.cost)}" if plan.column_price else "")
    )
    lines.append(f"P({n}/{n}) : {plan.p_all_correct:.4%}")
    if plan.distribution:
        lines.append("")
        lines.append("Beklenen isabet dağılımı:")
        for k in range(n, max(n - 5, -1), -1):
            p = plan.distribution.get(k, 0.0)
            lines.append(f"   {k:>2}/{n}: {p:>7.3%}   (en az {k}: {plan.probability_at_least(k):.3%})")
    return "\n".join(lines)


def format_frontier(plans: list[CouponPlan], column_price: float, top: int = 14) -> str:
    """Bütçe–kazanma şansı eğrisi: her ek liranın ne getirdiğini gösterir."""
    if not plans:
        return "BÜTÇE / ŞANS EĞRİSİ\n(hesaplanacak kupon yok)"
    # Başlıklar maç sayısına uyarlanır: 15 maçlık kuponda P(15/15)/P(≥14)/P(≥13),
    # daha kısa listelerde karşılık gelen eşikler.
    n = len(plans[0].selections)
    second, third = max(n - 1, 1), max(n - 2, 1)
    lines = ["BÜTÇE / ŞANS EĞRİSİ", "─" * 76]
    lines.append(
        f"{'Kolon':>8}{'Bedel':>16}{f'P({n}/{n})':>12}"
        f"{f'P(≥{second})':>11}{f'P(≥{third})':>11}{'TL/kat':>12}"
    )
    lines.append("─" * 76)
    base = plans[0].p_all_correct
    shown = plans[:: max(1, len(plans) // top)] if len(plans) > top else plans
    for plan in shown:
        gain = plan.p_all_correct / base if base > 0 else float("nan")
        cost = plan.columns * column_price
        per_gain = cost / gain if gain > 0 else float("nan")
        lines.append(
            f"{_tr_int(plan.columns):>8}{_tr_money(cost):>16}{plan.p_all_correct:>12.4%}"
            f"{plan.probability_at_least(second):>11.3%}"
            f"{plan.probability_at_least(third):>11.3%}"
            f"{per_gain:>11,.0f}₺".replace(",", ".")
        )
    lines.append("─" * 76)
    lines.append(
        "TL/kat: tek kolona göre kaç kat şans aldığınızın lira maliyeti. "
        "Küçük olan daha verimli."
    )
    lines.append(
        f"Not: yalnızca P({n}/{n}) bütçeyle birlikte monoton artar — optimizasyon "
        f"onu maksimize eder. Alt eşikler (P(≥{second}), P(≥{third})) yer yer "
        "geri gidebilir; bu beklenen bir davranıştır."
    )
    return "\n".join(lines)


def format_tables(column_price: float, max_doubles: int = 8, max_triples: int = 5) -> str:
    """Rehberdeki kolon adedi ve kupon bedeli tabloları."""
    corner = "2li/3lü"
    lines = ["ÜRETİLEN KOLON ADEDİ", "─" * 76]
    header = "   ".join(f"{t:>8}" for t in range(max_triples + 1))
    lines.append(f"{corner:>8}   {header}")
    for d in range(max_doubles + 1):
        row = "   ".join(f"{_tr_int(columns_for(d, t)):>8}" for t in range(max_triples + 1))
        lines.append(f"{d:>8}   {row}")
    lines.append("")
    lines.append(f"TOPLAM KUPON BEDELİ (kolon = {_tr_money(column_price)})")
    lines.append("─" * 76)
    lines.append(f"{corner:>8}   {header}")
    for d in range(max_doubles + 1):
        row = "   ".join(
            f"{columns_for(d, t) * column_price:>8,.0f}".replace(",", ".")
            for t in range(max_triples + 1)
        )
        lines.append(f"{d:>8}   {row}")
    return "\n".join(lines)


#: Güven seviyesi etiketleri — kullanıcı yüzdeyi yorumlayabilsin diye.
_CONFIDENCE_BANDS = (
    (0.75, "çok güçlü"),
    (0.60, "güçlü"),
    (0.48, "orta"),
    (0.40, "zayıf"),
    (0.00, "belirsiz"),
)

_TR_DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
_TR_MONTHS = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def confidence_label(p: float) -> str:
    for threshold, label in _CONFIDENCE_BANDS:
        if p >= threshold:
            return label
    return "belirsiz"


def _tr_date(iso: str | None) -> str:
    if not iso:
        return "Tarih yok"
    try:
        from datetime import date

        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d.day} {_TR_MONTHS[d.month - 1]} {_TR_DAYS[d.weekday()]}"


def format_weekly(predictions, league_names: dict | None = None, show_odds: bool = False) -> str:
    """Haftanın fikstürü: tarihe göre gruplanmış tahmin listesi.

    Her maç için en olası sonuç ve o sonucun olasılığı ("güven") gösterilir;
    listenin sonunda hepsini favoriye oynarsanız kaç maçı beklediğiniz yazar.
    """
    if not predictions:
        return (
            "Yaklaşan maç bulunamadı.\n"
            "Veri kaynağı fikstürleri henüz yayınlamamış olabilir; "
            "/guncelle ile tazeleyip tekrar deneyin."
        )

    league_names = league_names or {}
    lines = [f"HAFTANIN TAHMİNLERİ ({len(predictions)} maç)", "═" * 76]

    current_date = object()
    for p in predictions:
        if p.date != current_date:
            current_date = p.date
            lines.append("")
            lines.append(f"▸ {_tr_date(p.date)}")
            lines.append(f"  {'Maç':<36}{'1':>6}{'0':>6}{'2':>6}   {'Tahmin':>16}")
            lines.append("  " + "─" * 72)
        name = f"{p.home} - {p.away}"
        if len(name) > 35:
            name = name[:34] + "…"
        verdict = f"{p.favourite} · %{p.confidence * 100:.0f} {confidence_label(p.confidence)}"
        lines.append(
            f"  {name:<36}{p.p_home:>6.0%}{p.p_draw:>6.0%}{p.p_away:>6.0%}   {verdict:>16}"
        )

    expected = sum(p.confidence for p in predictions)
    lines.append("")
    lines.append("═" * 76)
    lines.append(
        f"Hepsini favoriye oynarsanız beklenen doğru sayısı: "
        f"{expected:.1f} / {len(predictions)}  (%{expected / len(predictions) * 100:.0f})"
    )
    warnings = [w for p in predictions for w in p.warnings]
    if warnings:
        lines.append("")
        lines.append("⚠ " + "; ".join(dict.fromkeys(warnings))[:400])
    return "\n".join(lines)


def format_quality(quality: dict) -> str:
    """Modelin ölçülmüş örnek dışı başarısı — "ne kadar güvenebilirim" cevabı."""
    if not quality:
        return (
            "Model başarısı henüz ölçülmedi. /egit komutuyla eğitim yapıldığında "
            "örnek dışı başarı oranı hesaplanır."
        )
    lines = ["MODEL BAŞARISI (örnek dışı ölçüm)", "─" * 50]
    lines.append(f"Ölçüm dönemi   : {quality.get('first_date')} → {quality.get('last_date')}")
    lines.append(f"Ölçülen maç    : {_tr_int(int(quality.get('n', 0)))}")
    hit = quality.get("favourite_hit_rate")
    if hit is not None:
        lines.append(f"Favori tutturma: %{hit * 100:.1f}")
    base = quality.get("baseline_hit_rate")
    if base is not None:
        lines.append(f"Referans (hep aynı sonucu oynamak): %{base * 100:.1f}")
    if quality.get("rps") is not None:
        lines.append(f"RPS            : {quality['rps']:.4f}  (düşük = iyi)")
    if quality.get("log_loss") is not None:
        lines.append(f"Log-loss       : {quality['log_loss']:.4f}  (rastgele = 1,0986)")
    lines.append("")
    lines.append(
        "Not: 'favori tutturma' 15 maçın 15'ini bilme oranı değildir; tek maç\n"
        "bazında en olası sonucun kaç kez tuttuğudur. 15/15 için /egri'ye bakın."
    )
    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    lines = ["VERİ DURUMU", "─" * 50]
    if not stats["matches"]:
        lines.append("Veritabanı boş. `sportoto ingest` ile veriyi indirin.")
        return "\n".join(lines)
    lines.append(f"Maç sayısı      : {_tr_int(stats['matches'])}")
    lines.append(f"Tarih aralığı   : {stats['first_date']} → {stats['last_date']}")
    lines.append(f"Lig sayısı      : {stats['leagues']}")
    coverage = stats["with_odds"] / stats["matches"] if stats["matches"] else 0.0
    lines.append(f"Oranlı maç      : {_tr_int(stats['with_odds'])} ({coverage:.0%})")
    if stats.get("upcoming") is not None:
        lines.append(f"Yaklaşan maç    : {_tr_int(stats['upcoming'])}")
    lines.append("")
    lines.append(f"{'Lig':<8}{'Maç':>9}   Son maç")
    for row in stats["per_league"][:25]:
        lines.append(f"{row['league']:<8}{_tr_int(row['n']):>9}   {row['last']}")
    return "\n".join(lines)
