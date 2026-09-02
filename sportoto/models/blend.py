"""Bileşen modelleri logaritmik görüş havuzunda (log-opinion pool) birleştirir.

    p ∝ Π_k p_k^{w_k},   Σw_k = 1

Neden aritmetik ortalama değil de log havuz:
  * Aritmetik ortalama, bir model "bu kesinlikle olmaz" derken bile
    olasılığı yüksek tutar; log havuz bu vetoyu korur.
  * Sonuç, bileşenlerin geometrik ortalamasıdır — kalibrasyonu daha iyi
    korur ve fazla-güvenli tahmin üretme eğilimi düşüktür.

Ağırlıklar sabit değil, doğrulama penceresinde log-loss minimize edilerek
kestirilir.

Eksik bileşen sorunu ve "profil" çözümü
---------------------------------------
Ağırlıklar, oranların **her zaman mevcut olduğu** geçmiş veride öğrenilir.
Piyasa en güçlü tek sinyal olduğu için diğer bileşenleri neredeyse sıfıra
ezer. Kullanıcı kupon listesini elle yapıştırdığında ise oran yoktur; geriye
ağırlığı sıfıra yakın bileşenler kalır ve havuz "hiçbir bilgim yok" diyerek
düzgün dağılıma (%33/%33/%33) düşer. Yani model, aslında bildiği maçlarda
bile susar.

Çözüm: tek bir ağırlık vektörü yerine **her bileşen kombinasyonu için ayrı
bir profil** kestirilir. "Yalnızca dc+elo mevcut" profili, tam da o iki
bileşenle, gerçek sonuçlar üzerinde fit edilir. Tahmin anında her maç için
mevcut bileşen kümesine karşılık gelen profil kullanılır.

Ek güvence olarak ağırlıklara bir taban uygulanır: hiçbir bileşen tam olarak
sıfır ağırlık almaz, böylece profil bulunamasa bile havuz sessizce çökmez.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

log = logging.getLogger(__name__)

_EPS = 1e-9
_UNIFORM = np.log(1.0 / 3.0)


def profile_key(names) -> str:
    """Bileşen kümesini kararlı bir anahtara çevirir."""
    return "|".join(sorted(names))


@dataclass
class LogPoolBlend:
    components: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    temperature: float = 1.0
    fitted: bool = False
    fit_log_loss: float | None = None
    #: Bir bileşenin fit'e girebilmesi için gereken en az kapsama oranı.
    min_coverage: float = 0.05
    #: Sıcaklık sınırları. Serbest bırakılırsa dejenere kalibrasyon verisinde
    #: 0'a yaklaşıp olasılıkları 0/1'e iter; bu da kupon optimizasyonunu bozar.
    temp_min: float = 0.6
    temp_max: float = 1.8
    #: Hiçbir bileşenin sıfır ağırlık almaması için taban.
    weight_floor: float = 0.02
    #: Kalibrasyonda her bileşenin kaç maçta kullanılabilir olduğu (0-1).
    coverage: dict[str, float] = field(default_factory=dict)
    #: Bileşen kombinasyonu -> {"weights": {...}, "temperature": float, "n": int}
    profiles: dict[str, dict] = field(default_factory=dict)

    # -- yardımcılar ------------------------------------------------------
    def _temperature_from(self, raw: float) -> float:
        """Serbest parametreyi [temp_min, temp_max] aralığına sıkıştırır."""
        span = self.temp_max - self.temp_min
        return self.temp_min + span * float(0.5 * (1.0 + np.tanh(0.5 * raw)))

    def _weights_from(self, raw: np.ndarray, k: int) -> np.ndarray:
        """Softmax + taban: ağırlıklar ≥ weight_floor ve toplamı 1."""
        logits = np.concatenate([[0.0], raw])
        w = np.exp(logits - logits.max())
        w /= w.sum()
        floor = min(self.weight_floor, 0.9 / max(k, 1))
        return floor + (1.0 - floor * k) * w

    @staticmethod
    def _pool(stack: np.ndarray, mask: np.ndarray, w: np.ndarray, temperature: float) -> np.ndarray:
        """stack: (K, N, 3) bileşen olasılıkları, mask: (K, N) bileşen mevcut mu."""
        log_p = np.log(np.clip(stack, _EPS, 1.0))
        wm = w[:, None] * mask
        norm = wm.sum(axis=0)
        safe = np.where(norm > _EPS, norm, 1.0)
        combined = (wm[:, :, None] * log_p).sum(axis=0) / safe[:, None]
        # Hiçbir bileşen yoksa düzgün dağılım. Bu bir tahmin değil, "bilmiyorum"
        # demektir; çağıran taraf `availability` ile bu satırları ayırt eder.
        combined = np.where(norm[:, None] > _EPS, combined, _UNIFORM)
        combined = combined / max(temperature, 1e-3)
        combined -= combined.max(axis=1, keepdims=True)
        p = np.exp(combined)
        return p / p.sum(axis=1, keepdims=True)

    def _prepare(self, component_probs: dict[str, np.ndarray], names: list[str]):
        """(stack, mask) üretir; eksik bileşen NaN kabul edilir."""
        length = next(
            (len(v) for v in component_probs.values() if v is not None), 0
        )
        arrays = []
        for name in names:
            value = component_probs.get(name)
            if value is None:
                value = np.full((length, 3), np.nan)
            arrays.append(np.asarray(value, dtype=float))
        stack = np.stack(arrays) if arrays else np.zeros((0, length, 3))
        mask = ~np.isnan(stack).any(axis=2) if len(arrays) else np.zeros((0, length), bool)
        return np.nan_to_num(stack, nan=1.0 / 3.0), mask

    # -- kestirim ---------------------------------------------------------
    def fit(self, component_probs: dict[str, np.ndarray], outcomes: np.ndarray,
            sample_weights: np.ndarray | None = None,
            fit_profiles: bool = True) -> "LogPoolBlend":
        """`component_probs`: ad -> (N,3) dizi (NaN = o maç için bileşen yok).

        `outcomes`: 0 = ev (1), 1 = beraberlik (0), 2 = deplasman (2).
        `fit_profiles=False` yalnızca tam ağırlıkları kestirir; başarı ölçümü
        gibi profil gerekmeyen yerlerde işi yarıya indirir.
        """
        candidates = [k for k, v in component_probs.items() if v is not None and len(v)]
        if not candidates:
            raise ValueError("Birleştirilecek bileşen yok")

        all_stack, all_mask = self._prepare(component_probs, candidates)
        self.coverage = {n: float(all_mask[i].mean()) for i, n in enumerate(candidates)}

        names = [n for n in candidates if self.coverage[n] >= self.min_coverage]
        dropped = [n for n in candidates if n not in names]
        if dropped:
            log.warning(
                "Kapsaması yetersiz bileşenler blend dışı bırakıldı: %s",
                ", ".join(f"{n} ({self.coverage[n]:.1%})" for n in dropped),
            )
        if not names:
            raise ValueError("Yeterli kapsamaya sahip bileşen yok")
        self.components = names

        keep = [candidates.index(n) for n in names]
        stack, mask = all_stack[keep], all_mask[keep]
        y = np.asarray(outcomes, dtype=int)
        sw = np.ones(len(y)) if sample_weights is None else np.asarray(sample_weights, float)

        weights, temperature, loss = self._fit_subset(stack, mask, y, sw, list(range(len(names))))
        if weights is None:
            self.weights = {n: 1.0 / len(names) for n in names}
            self.temperature = 1.0
            self.fitted = False
            return self
        self.weights = {n: float(w) for n, w in zip(names, weights)}
        self.temperature = float(temperature)
        self.fit_log_loss = loss
        self.fitted = True

        # --- Alt küme profilleri ---
        if not fit_profiles:
            self.profiles = {}
            return self
        # Her bileşen kombinasyonu için ayrı ağırlık seti. Bunlar, o kombinasyon
        # gerçekten mevcutken (ör. oran yokken) doğru dengeyi verir.
        self.profiles = {}
        for size in range(1, len(names) + 1):
            for combo in itertools.combinations(range(len(names)), size):
                subset = [names[i] for i in combo]
                sub_mask = mask[list(combo)]
                usable = sub_mask.any(axis=0)
                if usable.sum() < 100:
                    continue
                w, t, sub_loss = self._fit_subset(
                    stack[list(combo)][:, usable],
                    sub_mask[:, usable],
                    y[usable],
                    sw[usable],
                    list(range(size)),
                )
                if w is None:
                    continue
                self.profiles[profile_key(subset)] = {
                    "weights": {n: float(x) for n, x in zip(subset, w)},
                    "temperature": float(t),
                    "n": int(usable.sum()),
                    "log_loss": sub_loss,
                }
        return self

    def _fit_subset(self, stack, mask, y, sw, index):
        """Verilen bileşen alt kümesi için ağırlık ve sıcaklık kestirir."""
        k = len(index)
        if k == 0 or len(y) < 30:
            return None, 1.0, None
        usable = mask.any(axis=0)
        if usable.sum() < 30:
            return None, 1.0, None
        stack, mask, y, sw = stack[:, usable], mask[:, usable], y[usable], sw[usable]
        sw = sw / sw.sum()
        rows = np.arange(len(y))

        def nll(params):
            w = self._weights_from(params[: k - 1], k)
            temperature = self._temperature_from(params[k - 1])
            p = self._pool(stack, mask, w, temperature)
            return -np.sum(sw * np.log(np.clip(p[rows, y], _EPS, 1.0)))

        x0 = np.zeros(k)
        target = np.clip(
            (1.0 - self.temp_min) / max(self.temp_max - self.temp_min, 1e-9), 1e-3, 1 - 1e-3
        )
        x0[k - 1] = 2.0 * np.arctanh(2.0 * target - 1.0)
        result = minimize(nll, x0, method="Nelder-Mead",
                          options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-9})
        if not np.all(np.isfinite(result.x)):
            return None, 1.0, None
        return (
            self._weights_from(result.x[: k - 1], k),
            self._temperature_from(result.x[k - 1]),
            float(result.fun),
        )

    # -- tahmin -----------------------------------------------------------
    def availability(self, component_probs: dict[str, np.ndarray]) -> list[list[str]]:
        """Her satır için hangi bileşenlerin kullanılabildiğini döner."""
        if not self.components:
            return []
        _, mask = self._prepare(component_probs, self.components)
        return [
            [n for i, n in enumerate(self.components) if mask[i, row]]
            for row in range(mask.shape[1])
        ]

    def predict(self, component_probs: dict[str, np.ndarray]) -> np.ndarray:
        if not self.components:
            raise RuntimeError("Blend henüz fit edilmedi")
        stack, mask = self._prepare(component_probs, self.components)
        n = mask.shape[1]
        out = np.full((n, 3), 1.0 / 3.0)

        # Satırları mevcut bileşen desenine göre grupla; her desen için o
        # desene özel profil (varsa) kullanılır.
        patterns: dict[tuple, list[int]] = {}
        for row in range(n):
            key = tuple(i for i in range(len(self.components)) if mask[i, row])
            patterns.setdefault(key, []).append(row)

        for pattern, rows in patterns.items():
            idx = np.array(rows)
            if not pattern:
                continue  # bileşen yok: düzgün dağılım kalır
            subset = [self.components[i] for i in pattern]
            profile = self.profiles.get(profile_key(subset))
            if profile:
                w = np.array([profile["weights"].get(n, 0.0) for n in subset])
                temperature = float(profile.get("temperature", self.temperature))
            else:
                # Profil yoksa tam ağırlıkları mevcut bileşenlere yeniden dağıt.
                w = np.array([self.weights.get(n, 0.0) for n in subset])
                if w.sum() <= _EPS:
                    w = np.ones(len(subset))
                temperature = self.temperature
            out[idx] = self._pool(
                stack[list(pattern)][:, idx], mask[list(pattern)][:, idx], w, temperature
            )
        return out

    # -- gösterim / kalıcılık ---------------------------------------------
    def describe(self) -> str:
        parts = []
        for n in self.components:
            cov = self.coverage.get(n)
            suffix = f" (kapsama {cov:.0%})" if cov is not None and cov < 0.999 else ""
            parts.append(f"{n}={self.weights.get(n, 0):.2f}{suffix}")
        text = f"ağırlıklar: {', '.join(parts)} | sıcaklık={self.temperature:.3f}"
        if self.profiles:
            text += f" | {len(self.profiles)} profil"
        return text

    def describe_profile(self, names) -> str:
        """Belirli bir bileşen kümesi için kullanılacak ağırlıkları anlatır."""
        profile = self.profiles.get(profile_key(names))
        if not profile:
            return "profil yok (tam ağırlıklar yeniden normalize edilir)"
        parts = ", ".join(f"{n}={w:.2f}" for n, w in sorted(profile["weights"].items()))
        return f"{parts} | sıcaklık={profile['temperature']:.2f} | n={profile['n']}"

    def to_dict(self) -> dict:
        return {
            "components": self.components,
            "weights": self.weights,
            "temperature": self.temperature,
            "fitted": self.fitted,
            "fit_log_loss": self.fit_log_loss,
            "coverage": self.coverage,
            "profiles": self.profiles,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogPoolBlend":
        return cls(
            components=list(data.get("components", [])),
            weights=dict(data.get("weights", {})),
            temperature=float(data.get("temperature", 1.0)),
            fitted=bool(data.get("fitted", False)),
            fit_log_loss=data.get("fit_log_loss"),
            coverage=dict(data.get("coverage", {})),
            profiles=dict(data.get("profiles", {})),
        )
