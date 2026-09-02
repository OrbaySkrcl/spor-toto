"""Sistem kuponu optimizasyonu — bütçe kısıtı altında P(15/15) maksimizasyonu.

Problem
-------
15 maçın her biri için {1}, {1,0}, {1,0,2} gibi bir tahmin kümesi S_i seçiyoruz.
Kupon 15/15 tutar ancak ve ancak her maçın gerçek sonucu S_i içindeyse:

    P(15/15) = Π_i  P(sonuç ∈ S_i)

Maliyet ise kümelerin boyutlarının çarpımı:

    kolon = Π_i |S_i| = 2^a · 3^b       (a = ikili, b = üçlü sayısı)

Yani hedef: **Σ log P(sonuç ∈ S_i)** değerini `2^a·3^b ≤ bütçe` kısıtı altında
maksimize etmek.

Çözüm neden tam (exact)
-----------------------
İki gözlem problemi küçük bir dinamik programa indirger:

1. |S_i| = k sabitken en iyi küme, o maçın **en yüksek k olasılıklı** sonucudur
   (başka bir k'lı küme daha fazla olasılık kapsayamaz).
2. Maliyet yalnızca kaç tane ikili/üçlü seçtiğimize bağlıdır — hangi maçların
   ikili olduğuna değil.

Dolayısıyla durum uzayı (işlenen maç, kullanılan ikili, kullanılan üçlü) =
15 × 16 × 16 ile sınırlıdır ve DP küresel optimumu garanti eder. Yaygın olarak
kullanılan "en belirsiz maçlara ikili ver" sezgiseli bu optimumu genelde
ıskalar; buradaki DP ıskalamaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .pricing import columns_for

OUTCOME_LABELS = ("1", "0", "2")
_NEG_INF = float("-inf")
_EPS = 1e-12


@dataclass
class MatchSelection:
    """Tek bir maç için işaretlenen tahminler."""

    index: int
    home: str
    away: str
    probabilities: tuple[float, float, float]
    picks: tuple[str, ...]
    cover: float                       # P(gerçek sonuç işaretlenenler içinde)

    @property
    def size(self) -> int:
        return len(self.picks)

    @property
    def picks_text(self) -> str:
        return "-".join(self.picks)

    @property
    def risk(self) -> float:
        """İşaretlenmeyen sonucun toplam olasılığı."""
        return 1.0 - self.cover


@dataclass
class CouponPlan:
    """Optimize edilmiş sistem kuponu."""

    selections: list[MatchSelection]
    columns: int
    doubles: int
    triples: int
    p_all_correct: float
    column_price: float = 0.0
    distribution: dict[int, float] = field(default_factory=dict)
    #: Optimizasyonun maksimize ettiği eşik (n = hepsi doğru).
    target: int = 0

    @property
    def cost(self) -> float:
        return self.columns * self.column_price

    @property
    def singles(self) -> int:
        return len(self.selections) - self.doubles - self.triples

    @property
    def p_target(self) -> float:
        """Optimize edilen eşiği tutturma olasılığı."""
        target = self.target or len(self.selections)
        return self.probability_at_least(target)

    def probability_at_least(self, k: int) -> float:
        return sum(p for hits, p in self.distribution.items() if hits >= k)

    def summary(self) -> str:
        n = len(self.selections)
        return (
            f"{self.singles} tekli / {self.doubles} ikili / {self.triples} üçlü → "
            f"{self.columns:,} kolon".replace(",", ".")
            + (f" ({self.cost:,.2f} TL)".replace(",", ".") if self.column_price else "")
            + f" | P({n}/{n}) = {self.p_all_correct:.4%}"
        )


def _cover_options(probs: tuple[float, float, float]) -> tuple[list[float], list[tuple[str, ...]]]:
    """Boyut 1/2/3 için kapsanan olasılık ve karşılık gelen işaretler."""
    order = sorted(range(3), key=lambda i: -probs[i])
    covers, picks = [], []
    running = 0.0
    for k in range(3):
        running += probs[order[k]]
        covers.append(min(running, 1.0))
        chosen = sorted(order[: k + 1])  # 1-0-2 sırasında göster
        picks.append(tuple(OUTCOME_LABELS[i] for i in chosen))
    return covers, picks


def correct_count_distribution(covers: list[float]) -> dict[int, float]:
    """Doğru bilinen maç sayısının dağılımı (Poisson-binom).

    Sistem kuponunda en iyi kolonun tutturduğu maç sayısı, sonucu işaretlenen
    kümede olan maçların sayısıdır; bu da bağımsız Bernoulli'lerin toplamıdır.
    """
    dist = [1.0]
    for c in covers:
        c = min(max(c, 0.0), 1.0)
        nxt = [0.0] * (len(dist) + 1)
        for hits, p in enumerate(dist):
            if p == 0.0:
                continue
            nxt[hits] += p * (1.0 - c)
            nxt[hits + 1] += p * c
        dist = nxt
    return {hits: p for hits, p in enumerate(dist)}


def optimize_coupon(
    predictions,
    max_columns: int | None = None,
    budget: float | None = None,
    column_price: float = 0.0,
    max_triples: int | None = None,
    target: int | None = None,
) -> CouponPlan:
    """Bütçe kısıtı altında en iyi sistem kuponunu döner.

    `predictions`: `p_home/p_draw/p_away` (veya `probs`) taşıyan nesneler ya da
    (p1, p0, p2) üçlüleri.
    `max_columns` verilmezse `budget` ve `column_price`'tan hesaplanır.
    `target`: maksimize edilecek eşik. Varsayılan, maç sayısı (hepsi doğru).
    Spor Toto 12'den itibaren ödeme yaptığı için `target=13` gibi bir eşik
    çoğu bütçede daha anlamlıdır ve **farklı bir dağılım** üretir: hepsini
    tutturmak için en belirsiz maçlar kapatılır, 13 için ise aynı bütçeyle
    daha fazla maçta makul kapsama tercih edilir.
    """
    return _optimize_rows(
        _normalize(predictions), max_columns, budget, column_price, max_triples, target
    )


def _optimize_rows(
    rows: list[tuple[str, str, tuple[float, float, float]]],
    max_columns: int | None,
    budget: float | None,
    column_price: float,
    max_triples: int | None,
    target: int | None = None,
) -> CouponPlan:
    """`optimize_coupon`'ın çekirdeği; girdinin normalize edildiğini varsayar.

    `budget_frontier` aynı satırları yüzlerce kez optimize ettiği için
    normalizasyon oradan ayrıldı — hem tekrar işi önler hem de normalize
    edilmiş satırların yeniden normalize edilmeye çalışılmasını engeller.
    """
    n = len(rows)
    if n == 0:
        raise ValueError("Tahmin listesi boş")

    if max_columns is None:
        if budget is not None and column_price > 0:
            max_columns = max(1, int(budget // column_price))
        else:
            max_columns = 1
    max_columns = max(1, int(max_columns))
    cap_triples = n if max_triples is None else max(0, min(max_triples, n))

    covers_per_match, picks_per_match = [], []
    for _, _, probs in rows:
        covers, picks = _cover_options(probs)
        covers_per_match.append([math.log(max(c, _EPS)) for c in covers])
        picks_per_match.append(picks)

    # dp[a][b] = ilk i maç için a ikili, b üçlü kullanıldığındaki en iyi log-kapsama
    dp = [[_NEG_INF] * (cap_triples + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    choice: list[list[list[int]]] = []

    for i in range(n):
        nxt = [[_NEG_INF] * (cap_triples + 1) for _ in range(n + 1)]
        step = [[0] * (cap_triples + 1) for _ in range(n + 1)]
        log_cover = covers_per_match[i]
        for a in range(i + 1):
            for b in range(min(i - a, cap_triples) + 1):
                base = dp[a][b]
                if base == _NEG_INF:
                    continue
                # tekli
                if base + log_cover[0] > nxt[a][b]:
                    nxt[a][b] = base + log_cover[0]
                    step[a][b] = 1
                # ikili
                if a + 1 <= n and base + log_cover[1] > nxt[a + 1][b]:
                    nxt[a + 1][b] = base + log_cover[1]
                    step[a + 1][b] = 2
                # üçlü
                if b + 1 <= cap_triples and base + log_cover[2] > nxt[a][b + 1]:
                    nxt[a][b + 1] = base + log_cover[2]
                    step[a][b + 1] = 3
        dp = nxt
        choice.append(step)

    # Bütçeye sığan en iyi (a, b)
    best = (_NEG_INF, 0, 0)
    for a in range(n + 1):
        for b in range(cap_triples + 1):
            if a + b > n or dp[a][b] == _NEG_INF:
                continue
            if columns_for(a, b) > max_columns:
                continue
            if dp[a][b] > best[0]:
                best = (dp[a][b], a, b)
    if best[0] == _NEG_INF:  # bütçe 1 kolona bile yetmiyorsa tümü tekli
        best = (dp[0][0], 0, 0)

    _, doubles, triples = best
    sizes = _backtrack_sizes(choice, doubles, triples, n)

    goal = n if target is None else max(1, min(int(target), n))
    if goal < n:
        # P(≥k) çok doğrusal bir amaç değildir; hepsi-doğru için optimal olan
        # dağılım k<n için optimal olmayabilir. Tam DP'nin çözümünden başlayıp
        # bütçeye sığan her (ikili, üçlü) bileşimi için yerel arama yapılır.
        sizes, doubles, triples = _search_threshold(
            [c[:] for c in covers_per_match], max_columns, cap_triples, goal, sizes
        )

    selections = _build_selections(rows, picks_per_match, sizes)
    covers = [s.cover for s in selections]
    return CouponPlan(
        selections=selections,
        columns=columns_for(doubles, triples),
        doubles=doubles,
        triples=triples,
        p_all_correct=math.prod(covers),
        column_price=column_price,
        distribution=correct_count_distribution(covers),
        target=goal,
    )


def _backtrack_sizes(choice, doubles: int, triples: int, n: int) -> list[int]:
    """DP kararlarını geriye izleyerek maç başına işaret sayısını çıkarır."""
    a, b = doubles, triples
    sizes = [1] * n
    for i in range(n - 1, -1, -1):
        size = choice[i][a][b] or 1
        sizes[i] = size
        if size == 2:
            a -= 1
        elif size == 3:
            b -= 1
    return sizes


def _build_selections(rows, picks_per_match, sizes) -> list[MatchSelection]:
    selections = []
    for i, (home, away, probs) in enumerate(rows):
        size = max(1, sizes[i])
        picks = picks_per_match[i][size - 1]
        cover = sum(probs[OUTCOME_LABELS.index(p)] for p in picks)
        selections.append(
            MatchSelection(i, home, away, probs, picks, min(cover, 1.0))
        )
    return selections


def _threshold_probability(covers: list[float], goal: int) -> float:
    """P(en az `goal` maç doğru) — Poisson-binom."""
    dist = [1.0]
    for c in covers:
        c = min(max(c, 0.0), 1.0)
        nxt = [0.0] * (len(dist) + 1)
        for hits, p in enumerate(dist):
            if p:
                nxt[hits] += p * (1.0 - c)
                nxt[hits + 1] += p * c
        dist = nxt
    return sum(dist[goal:])


def _maximal_allocations(n: int, cap_triples: int, max_columns: int) -> list[tuple[int, int]]:
    """Bütçeye sığan ve başkası tarafından baskılanmayan (ikili, üçlü) bileşimleri.

    Daha fazla işaret her maçın kapsamasını zayıf anlamda artırdığı için,
    (a', b') ≥ (a, b) olan ve bütçeye sığan bir bileşim varsa (a, b) denenmeye
    değmez. Bu eleme arama uzayını tipik olarak beşte bire indirir ve eşiğe
    özel (daha pahalı ama daha iyi) açgözlü başlangıcı mümkün kılar.
    """
    feasible = [
        (a, b)
        for b in range(cap_triples + 1)
        for a in range(n - b + 1)
        if columns_for(a, b) <= max_columns
    ]
    feasible_set = set(feasible)
    return [
        (a, b)
        for (a, b) in feasible
        if not any(
            (a2, b2) != (a, b) and a2 >= a and b2 >= b
            for (a2, b2) in feasible_set
        )
    ]


def _search_threshold(log_covers, max_columns: int, cap_triples: int,
                      goal: int, start_sizes: list[int]):
    """P(≥goal) için en iyi işaret dağılımını arar.

    Hepsi-doğru amacının aksine P(≥k) çarpanlarına ayrılamaz, bu yüzden tam DP
    uygulanamaz. Bunun yerine: bütçeye sığan baskın bileşimler taranır, her biri
    için amaç fonksiyonunun marjinal katkısına göre açgözlü bir atama kurulur ve
    ikili takaslarla yerel iyileştirme yapılır. Tam DP'nin hepsi-doğru çözümü de
    aday olarak denendiği için sonuç ondan kötü olamaz.

    Küçük kuponlarda kaba kuvvetle karşılaştırıldığında optimumu bulur
    (bkz. tests/test_optimizer.py).
    """
    n = len(log_covers)
    covers = [[math.exp(v) for v in row] for row in log_covers]

    def value(sizes):
        return _threshold_probability([covers[i][sizes[i] - 1] for i in range(n)], goal)

    # Tam DP'nin hepsi-doğru çözümü de bir başlangıç noktasıdır ve o da yerel
    # aramadan geçmelidir: aynı (ikili, üçlü) bileşimi içinde işaretleri farklı
    # maçlara dağıtmak P(≥k) için daha iyi olabilir.
    best_sizes = _hill_climb(covers, list(start_sizes), goal, value)
    best_value = value(best_sizes)
    best_pair = (
        sum(1 for s in best_sizes if s == 2),
        sum(1 for s in best_sizes if s == 3),
    )

    for doubles, triples in _maximal_allocations(n, cap_triples, max_columns):
        for seed in (
            _greedy_threshold(covers, doubles, triples, goal, value),
            _greedy_assignment(covers, doubles, triples),
        ):
            sizes = _hill_climb(covers, seed, goal, value)
            candidate = value(sizes)
            if candidate > best_value + 1e-15:
                best_value, best_sizes, best_pair = candidate, sizes, (doubles, triples)
    return best_sizes, best_pair[0], best_pair[1]


def _greedy_threshold(covers, doubles: int, triples: int, goal: int, value) -> list[int]:
    """Her adımda amaç fonksiyonunu en çok artıran yükseltmeyi uygular."""
    n = len(covers)
    sizes = [1] * n
    quota = {2: doubles, 3: triples}
    for _ in range(doubles + triples):
        best = (None, -1.0)
        for size in (2, 3):
            if quota[size] <= 0:
                continue
            for i in range(n):
                if sizes[i] != 1:
                    continue
                sizes[i] = size
                score = value(sizes)
                sizes[i] = 1
                if score > best[1]:
                    best = ((i, size), score)
        if best[0] is None:
            break
        i, size = best[0]
        sizes[i] = size
        quota[size] -= 1
    return sizes


def _greedy_assignment(covers, doubles: int, triples: int) -> list[int]:
    """Ham kapsama kazancına göre atama — ikinci bir başlangıç noktası."""
    n = len(covers)
    sizes = [1] * n
    for i in sorted(range(n), key=lambda i: -(covers[i][2] - covers[i][0]))[:triples]:
        sizes[i] = 3
    remaining = [i for i in range(n) if sizes[i] == 1]
    remaining.sort(key=lambda i: -(covers[i][1] - covers[i][0]))
    for i in remaining[:doubles]:
        sizes[i] = 2
    return sizes


def _hill_climb(covers, sizes: list[int], goal: int, value, max_passes: int = 6) -> list[int]:
    """Boyutları maçlar arasında takas ederek yerel iyileştirme yapar.

    Kolon sayısı boyutların çarpımı olduğundan, iki maçın boyutlarını takas
    etmek maliyeti değiştirmez — bütçe kısıtı kendiliğinden korunur.
    """
    n = len(sizes)
    current = value(sizes)
    for _ in range(max_passes):
        improved = False
        for i in range(n):
            for j in range(i + 1, n):
                if sizes[i] == sizes[j]:
                    continue
                sizes[i], sizes[j] = sizes[j], sizes[i]
                candidate = value(sizes)
                if candidate > current + 1e-15:
                    current, improved = candidate, True
                else:
                    sizes[i], sizes[j] = sizes[j], sizes[i]
        if not improved:
            break
    return sizes


def budget_frontier(
    predictions, column_price: float = 0.0, max_columns: int = 200_000
) -> list[CouponPlan]:
    """Her fizibil kolon adedi için en iyi kuponu döner (artan maliyet sırasıyla).

    Kullanıcının "hangi bütçe kaç kat şans getiriyor" sorusunu somut olarak
    yanıtlar: azalan marjinal fayda burada açıkça görülür.
    """
    rows = _normalize(predictions)
    n = len(rows)
    seen: dict[int, CouponPlan] = {}
    for triples in range(n + 1):
        for doubles in range(n - triples + 1):
            columns = columns_for(doubles, triples)
            if columns > max_columns:
                continue
            plan = _optimize_rows(rows, columns, None, column_price, None)
            current = seen.get(plan.columns)
            if current is None or plan.p_all_correct > current.p_all_correct:
                seen[plan.columns] = plan
    return [seen[c] for c in sorted(seen)]


def _normalize(predictions) -> list[tuple[str, str, tuple[float, float, float]]]:
    """Farklı girdi biçimlerini (ev, deplasman, (p1, p0, p2)) hâline getirir."""
    rows = []
    for i, item in enumerate(predictions):
        if isinstance(item, (tuple, list)) and len(item) == 3 and all(
            isinstance(v, (int, float)) for v in item
        ):
            home, away, probs = f"Maç {i + 1}", "", tuple(float(v) for v in item)
        elif hasattr(item, "p_home"):
            home = getattr(item, "home", f"Maç {i + 1}")
            away = getattr(item, "away", "")
            probs = (float(item.p_home), float(item.p_draw), float(item.p_away))
        elif isinstance(item, dict):
            home = item.get("home", f"Maç {i + 1}")
            away = item.get("away", "")
            probs = (
                float(item.get("p_home", item.get("p1", 0.0))),
                float(item.get("p_draw", item.get("p0", 0.0))),
                float(item.get("p_away", item.get("p2", 0.0))),
            )
        else:
            raise TypeError(f"Tanınmayan tahmin biçimi: {type(item)}")
        total = sum(probs)
        if total <= 0:
            raise ValueError(f"Geçersiz olasılıklar: {probs}")
        rows.append((home, away, tuple(p / total for p in probs)))
    return rows


def compare_budgets(
    predictions,
    budgets: list[float],
    column_price: float,
    target: int | None = None,
) -> list[CouponPlan]:
    """Verilen TL bütçeleri için kuponları yan yana hesaplar.

    "Ne kadar koyayım" sorusu tek bir kuponla cevaplanamaz; kullanıcının
    seçenekleri aynı maç listesi üzerinde görmesi gerekir.
    """
    rows = _normalize(predictions)
    plans = []
    seen = set()
    for budget in sorted(set(budgets)):
        columns = max(1, int(budget // column_price)) if column_price > 0 else int(budget)
        plan = _optimize_rows(rows, columns, None, column_price, None, target)
        if plan.columns in seen:
            continue
        seen.add(plan.columns)
        plans.append(plan)
    return plans
