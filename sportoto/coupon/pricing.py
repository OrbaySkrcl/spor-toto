"""Spor Toto kolon adedi ve kupon bedeli hesabı.

Sistemli oyunda üretilen kolon sayısı, işaretlenen tahmin sayılarının
çarpımıdır. Tek tahminli maçlar 1 ile çarptığı için sonucu değiştirmez:

    kolon = 2^(iki ihtimalli maç sayısı) × 3^(üç ihtimalli maç sayısı)

Kupon bedeli = kolon × kolon birim fiyatı. Birim fiyat zaman zaman
güncellendiğinden `SPORTOTO_COLUMN_PRICE` ortam değişkeninden okunur.
"""

from __future__ import annotations

MAX_MATCHES = 15


def columns_for(doubles: int, triples: int) -> int:
    """İki ve üç ihtimalli maç sayısından üretilen kolon adedi."""
    if doubles < 0 or triples < 0:
        raise ValueError("Maç sayıları negatif olamaz")
    if doubles + triples > MAX_MATCHES:
        raise ValueError(f"Toplam en fazla {MAX_MATCHES} maç işaretlenebilir")
    return (2**doubles) * (3**triples)


def coupon_cost(columns: int, column_price: float) -> float:
    """Kolon adedi ve birim fiyattan toplam kupon bedeli."""
    return columns * column_price


def columns_table(max_doubles: int = 10, max_triples: int = 6) -> list[list[int]]:
    """Rehberdeki "üretilen kolon adedi" tablosu.

    Satır: iki ihtimalli maç sayısı, sütun: üç ihtimalli maç sayısı.
    """
    return [
        [columns_for(d, t) for t in range(max_triples + 1)]
        for d in range(max_doubles + 1)
        if d + max_triples <= MAX_MATCHES or True
    ]


def cost_table(
    column_price: float, max_doubles: int = 10, max_triples: int = 6
) -> list[list[float]]:
    """Rehberdeki "toplam kupon bedeli" tablosu."""
    return [
        [coupon_cost(c, column_price) for c in row]
        for row in columns_table(max_doubles, max_triples)
    ]


def largest_feasible(max_columns: int, n_matches: int = MAX_MATCHES) -> tuple[int, int, int]:
    """`max_columns` sınırını aşmayan en büyük 2^a·3^b ayrışımını bulur.

    Döner: (kolon, iki ihtimalli sayısı, üç ihtimalli sayısı).
    """
    best = (1, 0, 0)
    for triples in range(n_matches + 1):
        for doubles in range(n_matches - triples + 1):
            columns = columns_for(doubles, triples)
            if columns <= max_columns and columns > best[0]:
                best = (columns, doubles, triples)
    return best


def describe_cost(columns: int, column_price: float) -> str:
    return f"{columns:,} kolon × {column_price:,.2f} TL = {columns * column_price:,.2f} TL".replace(
        ",", "."
    )
