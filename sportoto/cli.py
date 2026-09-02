"""Komut satırı arayüzü.

    python -m sportoto ingest            # veriyi indir
    python -m sportoto train             # modeli eğit ve kalibre et
    python -m sportoto coupon kupon.txt  # 15 maçlık kuponu optimize et
    python -m sportoto backtest          # geçmişe dönük değerlendirme
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_LEAGUES, LEAGUES, load_settings
from .coupon.optimizer import budget_frontier, optimize_coupon
from .report import (
    format_coupon,
    format_frontier,
    format_predictions,
    format_quality,
    format_stats,
    format_tables,
    format_weekly,
)
from .storage import Database
from .teams import TeamResolver, parse_coupon

log = logging.getLogger("sportoto")


def _settings_from_args(args):
    overrides = {}
    if getattr(args, "source", None):
        overrides["source"] = args.source
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = Path(args.data_dir)
    if getattr(args, "leagues", None):
        overrides["leagues"] = [c.strip().upper() for c in args.leagues.split(",") if c.strip()]
    if getattr(args, "seasons_back", None):
        overrides["seasons_back"] = args.seasons_back
    settings = load_settings(**overrides)
    if getattr(args, "column_price", None):
        settings.coupon.column_price = args.column_price
    settings.ensure_dirs()
    return settings


def _load_predictor(settings, leagues=None, retrain=False, quiet=False):
    """Eğitilmiş tahminciyi döner; kayıtlı kalibrasyon varsa yeniden kullanır."""
    from .predictor import Predictor

    predictor = Predictor(settings)
    reused = (not retrain) and predictor.load_blend()
    report = predictor.train(
        leagues=leagues, calibrate=not reused, progress=not quiet
    )
    if not reused:
        predictor.save_blend()
    return predictor, report


# --------------------------------------------------------------------------
# komutlar
# --------------------------------------------------------------------------
def cmd_ingest(args) -> int:
    from .ingest import ingest

    settings = _settings_from_args(args)
    leagues = settings.leagues
    result = ingest(settings, leagues=leagues, source_name=settings.source,
                    with_fixtures=not args.no_fixtures)
    print(result.summary())
    print()
    print(format_stats(Database(settings.db_path).stats()))
    return 0


def cmd_stats(args) -> int:
    settings = _settings_from_args(args)
    print(format_stats(Database(settings.db_path).stats()))
    return 0


def cmd_train(args) -> int:
    settings = _settings_from_args(args)
    predictor, report = _load_predictor(settings, retrain=True, quiet=args.quiet)
    path = predictor.save_blend()
    print(f"Eğitim tamam: {report['matches']} maç, {report['first_date']} → {report['last_date']}")
    print(f"Dixon-Coles kestirilen ligler: {', '.join(report['dc_leagues'])}")
    print(f"Blend {predictor.blend.describe()}")
    print(f"Kalibrasyon kaydedildi: {path}")
    return 0


def cmd_predict(args) -> int:
    settings = _settings_from_args(args)
    fixtures = _read_fixtures(args.file)
    if not fixtures:
        print("Maç bulunamadı. Her satıra 'Ev Sahibi - Deplasman' yazın.", file=sys.stderr)
        return 1
    predictor, _ = _load_predictor(settings, retrain=args.retrain, quiet=True)
    predictions = predictor.predict(fixtures)
    print(format_predictions(predictions, show_components=args.components))
    return 0


def cmd_coupon(args) -> int:
    settings = _settings_from_args(args)
    fixtures = _read_fixtures(args.file)
    if not fixtures:
        print("Maç bulunamadı. Her satıra 'Ev Sahibi - Deplasman' yazın.", file=sys.stderr)
        return 1
    if len(fixtures) != settings.coupon.n_matches:
        print(
            f"⚠ {len(fixtures)} maç okundu (Spor Toto kuponu {settings.coupon.n_matches} maçtır). "
            "Optimizasyon yine de yapılacak.\n",
            file=sys.stderr,
        )

    predictor, _ = _load_predictor(settings, retrain=args.retrain, quiet=True)
    predictions = predictor.predict(fixtures)
    print(format_predictions(predictions, show_components=args.components))
    print()

    price = settings.coupon.column_price
    plan = optimize_coupon(
        predictions,
        max_columns=args.columns,
        budget=args.budget if args.columns is None else None,
        column_price=price,
        max_triples=args.max_triples,
    )
    print(format_coupon(plan))
    if args.frontier:
        print()
        print(format_frontier(budget_frontier(predictions, price, args.frontier_max), price))
    return 0


def cmd_week(args) -> int:
    """Yaklaşan maçlar için tahmin listesi; istenirse otomatik kupon."""
    settings = _settings_from_args(args)
    predictor, _ = _load_predictor(settings, retrain=args.retrain, quiet=True)
    predictions = predictor.upcoming(days=args.days)
    if not predictions:
        print(
            "Yaklaşan maç bulunamadı. Veri kaynağı fikstürleri henüz yayınlamamış "
            "olabilir; `sportoto ingest` ile tazeleyip tekrar deneyin.",
            file=sys.stderr,
        )
        return 1

    print(format_weekly(predictions))
    if predictor.quality:
        print()
        print(format_quality(predictor.quality))

    if args.coupon:
        need = settings.coupon.n_matches
        if len(predictions) < need:
            print(f"\nOtomatik kupon için en az {need} maç gerekiyor.", file=sys.stderr)
            return 0
        chosen = sorted(predictions, key=lambda p: -p.confidence)[:need]
        chosen.sort(key=lambda p: (p.date or "", p.home))
        print()
        print(
            f"Not: aşağıdaki liste resmî Spor Toto kuponu değildir — yaklaşan "
            f"maçlar arasından en tahmin edilebilir {need} tanesidir."
        )
        print()
        plan = optimize_coupon(
            chosen, max_columns=args.columns,
            budget=args.budget if args.columns is None else None,
            column_price=settings.coupon.column_price,
        )
        print(format_coupon(plan, "OTOMATİK SİSTEM KUPONU"))
    return 0


def cmd_quality(args) -> int:
    settings = _settings_from_args(args)
    predictor, _ = _load_predictor(settings, retrain=args.retrain, quiet=True)
    print(format_quality(predictor.quality))
    return 0


def cmd_backtest(args) -> int:
    from .backtest import run_backtest

    settings = _settings_from_args(args)
    result = run_backtest(
        settings,
        leagues=settings.leagues if args.leagues else None,
        start=args.start,
        end=args.end,
        refit_days=args.refit_days,
        calibration_days=args.calibration_days,
        budgets=[int(b) for b in args.budgets.split(",")] if args.budgets else None,
        progress=not args.quiet,
    )
    print(result.report())
    if args.calibration:
        print("\nKALİBRASYON")
        print(result.calibration.to_string(index=False))
    if args.out:
        out = Path(args.out)
        result.rows.to_csv(out, index=False)
        print(f"\nSatır bazında sonuçlar yazıldı: {out}")
    return 0


def cmd_tables(args) -> int:
    settings = _settings_from_args(args)
    print(format_tables(settings.coupon.column_price))
    return 0


def cmd_leagues(args) -> int:
    print(f"{'Kod':<6}{'Ülke':<12}{'Lig':<26}{'Düzen':<8}Varsayılan")
    print("─" * 66)
    for code, league in LEAGUES.items():
        mark = "✓" if code in DEFAULT_LEAGUES else ""
        print(f"{code:<6}{league.country:<12}{league.name:<26}{league.layout:<8}{mark}")
    return 0


def cmd_adjust(args) -> int:
    """Sakatlık/ceza gibi manuel takım düzeltmeleri.

    `attack -0.25`, takımın gol beklentisini exp(-0.25) ≈ %22 düşürür.
    Kaba bir başlangıç noktası: takımın gol üretiminin %X'ini oluşturan bir
    oyuncu yoksa, attack ≈ ln(1 - X) alınabilir.
    """
    from datetime import date, timedelta

    settings = _settings_from_args(args)
    db = Database(settings.db_path)

    if args.list:
        today = args.on or date.today().isoformat()
        active = db.active_adjustments(today)
        if not active:
            print(f"{today} tarihinde geçerli düzeltme yok.")
            return 0
        print(f"{today} tarihinde geçerli düzeltmeler:")
        for team, (atk, dfn) in sorted(active.items()):
            print(f"  {team:<28} atak {atk:+.3f}   defans {dfn:+.3f}")
        return 0

    if not args.team:
        print("Takım adı gerekli. Örnek: sportoto adjust Galatasaray --attack -0.25",
              file=sys.stderr)
        return 1

    match = TeamResolver(db.known_teams()).resolve(args.team)
    if not match.confident:
        alts = ", ".join(t for t, _ in match.alternatives[:3])
        print(
            f"Takım güvenle eşleşmedi: {args.team!r} → {match.team!r} "
            f"(benzerlik {match.score:.2f}). Alternatifler: {alts}",
            file=sys.stderr,
        )
        return 1

    start = args.since or date.today().isoformat()
    end = args.until or (date.fromisoformat(start) + timedelta(days=args.days)).isoformat()
    db.add_adjustment(
        match.team, start, attack=args.attack, defense=args.defense,
        valid_to=end, note=args.note,
    )
    print(
        f"✅ {match.team}: atak {args.attack:+.3f}, defans {args.defense:+.3f} "
        f"({start} → {end})" + (f" — {args.note}" if args.note else "")
    )
    return 0


def cmd_bot(args) -> int:
    from .bot.telegram_bot import run_bot

    settings = _settings_from_args(args)
    return run_bot(settings, token=args.token)


def _read_fixtures(path: str | None) -> list[dict]:
    """Dosyadan ya da stdin'den 'Ev - Deplasman' satırlarını okur."""
    text = Path(path).read_text(encoding="utf-8") if path and path != "-" else sys.stdin.read()
    return [{"home": h, "away": a} for h, a in parse_coupon(text)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sportoto",
        description="Spor Toto 1/0/2 olasılık modeli ve sistem kuponu optimizasyonu",
    )
    parser.add_argument("--source", help="veri kaynağı: footballdata | mirror | local | synthetic")
    parser.add_argument("--data-dir", help="veri klasörü (varsayılan: data)")
    parser.add_argument("--leagues", help="virgülle ayrılmış lig kodları (ör. T1,E0,SP1)")
    parser.add_argument("--seasons-back", type=int, help="kaç sezon geriye inilecek")
    parser.add_argument("--column-price", type=float, help="kolon birim fiyatı (TL)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ayrıntılı günlük")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="veri kaynağından maçları indir")
    p.add_argument("--no-fixtures", action="store_true", help="yaklaşan maçları çekme")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("stats", help="veritabanı durumu")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("train", help="modeli eğit ve blend'i kalibre et")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="maç listesi için 1/0/2 olasılıkları")
    p.add_argument("file", nargs="?", help="maç listesi dosyası ('-' = stdin)")
    p.add_argument("--components", action="store_true", help="bileşen olasılıklarını da göster")
    p.add_argument("--retrain", action="store_true", help="kalibrasyonu yeniden yap")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("coupon", help="15 maçlık sistem kuponunu optimize et")
    p.add_argument("file", nargs="?", help="kupon dosyası ('-' = stdin)")
    p.add_argument("--budget", type=float, help="bütçe (TL)")
    p.add_argument("--columns", type=int, help="kolon üst sınırı (bütçe yerine)")
    p.add_argument("--max-triples", type=int, help="en fazla kaç üçlü tahmin")
    p.add_argument("--components", action="store_true")
    p.add_argument("--frontier", action="store_true", help="bütçe/şans eğrisini yazdır")
    p.add_argument("--frontier-max", type=int, default=200_000, help="eğride en fazla kolon")
    p.add_argument("--retrain", action="store_true")
    p.set_defaults(func=cmd_coupon)

    p = sub.add_parser("hafta", help="yaklaşan maçlar için haftalık tahminler")
    p.add_argument("--days", type=int, default=8, help="kaç gün ileriye bakılsın")
    p.add_argument("--coupon", action="store_true", help="otomatik sistem kuponu da kur")
    p.add_argument("--budget", type=float, help="otomatik kupon bütçesi (TL)")
    p.add_argument("--columns", type=int, help="otomatik kupon kolon sınırı")
    p.add_argument("--retrain", action="store_true")
    p.set_defaults(func=cmd_week)

    p = sub.add_parser("basari", help="modelin ölçülmüş örnek dışı başarısı")
    p.add_argument("--retrain", action="store_true")
    p.set_defaults(func=cmd_quality)

    p = sub.add_parser("backtest", help="sızıntısız yürüyen backtest")
    p.add_argument("--start", help="değerlendirme başlangıcı (YYYY-MM-DD)")
    p.add_argument("--end", help="değerlendirme bitişi (YYYY-MM-DD)")
    p.add_argument("--refit-days", type=int, default=21, help="modeller kaç günde bir yenilensin")
    p.add_argument("--calibration-days", type=int, default=730)
    p.add_argument("--budgets", help="simüle edilecek kolon bütçeleri (virgüllü)")
    p.add_argument("--calibration", action="store_true", help="kalibrasyon tablosunu yazdır")
    p.add_argument("--out", help="satır bazında sonuçları CSV'ye yaz")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("tables", help="kolon adedi ve kupon bedeli tabloları")
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("leagues", help="desteklenen ligler")
    p.set_defaults(func=cmd_leagues)

    p = sub.add_parser("adjust", help="sakatlık/ceza için manuel takım düzeltmesi")
    p.add_argument("team", nargs="?", help="takım adı")
    p.add_argument("--attack", type=float, default=0.0,
                   help="atak düzeltmesi (log ölçek; -0.25 ≈ %%22 daha az gol)")
    p.add_argument("--defense", type=float, default=0.0,
                   help="defans düzeltmesi (negatif = daha kötü savunma)")
    p.add_argument("--since", help="başlangıç tarihi (varsayılan: bugün)")
    p.add_argument("--until", help="bitiş tarihi")
    p.add_argument("--days", type=int, default=14, help="--until yoksa kaç gün geçerli")
    p.add_argument("--note", help="açıklama")
    p.add_argument("--list", action="store_true", help="geçerli düzeltmeleri listele")
    p.add_argument("--on", help="--list için tarih (varsayılan: bugün)")
    p.set_defaults(func=cmd_adjust)

    p = sub.add_parser("bot", help="Telegram botunu çalıştır")
    p.add_argument("--token", help="bot jetonu (yoksa TELEGRAM_BOT_TOKEN)")
    p.set_defaults(func=cmd_bot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nİptal edildi.", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.verbose:
            raise
        print(f"Hata: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
