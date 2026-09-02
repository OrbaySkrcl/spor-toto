"""Veri kaynakları. `get_source(name)` ile seçilir."""

from .base import Source, SourceError, season_codes
from .footballdata_uk import FootballDataUK
from .github_mirror import GithubMirror
from .local import LocalCSV
from .synthetic import SyntheticSource

_REGISTRY = {
    "footballdata": FootballDataUK,
    "mirror": GithubMirror,
    "local": LocalCSV,
    "synthetic": SyntheticSource,
}


def get_source(name: str, settings) -> Source:
    key = (name or "footballdata").strip().lower()
    if key not in _REGISTRY:
        raise SourceError(
            f"Bilinmeyen kaynak: {name!r}. Seçenekler: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[key](settings)


__all__ = ["Source", "SourceError", "get_source", "season_codes"]
