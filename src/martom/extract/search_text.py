"""The compact `product.search_text` that full-text search (M3) indexes.

Built in plain Python from the title and the extracted facts: title, Bosnian product type,
brand, colours, heights, mesh, uses, compatibility and synonyms (docs/PLAN.md, "What gets
indexed"), plus the regional synonyms Haiku adds for search (#121: "kolica" for a
"KARIOLA TAČKE" listing). The marketing description is left out on purpose: its
boilerplate ("čvrsta, moderna i dugotrajna") would drown out the facts.

Parts are joined with " | " in a fixed order, empty parts are skipped and a part that
repeats an earlier one (ignoring case) is dropped, so the same input always gives the same
text.
"""

import re

from martom.extract.schema import ExtractedProduct

# Words customers use for the same thing. If one of a pair appears in the title, the
# product type or the listing's own synonyms, the other is added, so "stub" finds a
# listing that only says "stup". Keys are lower case. Only the pairs docs/PLAN.md names;
# add more when Toni or the eval set (M3-1) shows customers need them.
KNOWN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "stup": ("stub",),
    "stub": ("stup",),
    "stupovi": ("stubovi",),
    "stubovi": ("stupovi",),
    "koke": ("kokoši",),
    "kokoši": ("koke",),
}

# The prompt asks Haiku for at most 6 regional synonyms; only the first 6 are indexed, so a
# runaway or prompt-injected reply can't stuff search_text and inflate its rank (#121).
MAX_REGIONAL_SYNONYMS = 6

_WORD = re.compile(r"\w+")
SEPARATOR = " | "


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def _synonyms(title: str, product: ExtractedProduct) -> list[str]:
    """The listing's own synonyms, Haiku's regional synonyms (#121), then known partners
    of words they use."""
    terms = [*product.synonyms, *product.regional_synonyms[:MAX_REGIONAL_SYNONYMS]]
    source_words = _words(" ".join([title, product.product_type, *terms]))
    for word in source_words:
        terms.extend(KNOWN_SYNONYMS.get(word, ()))
    # Drop terms that are already words of the title or product type.
    already = set(_words(f"{title} {product.product_type}"))
    unique: list[str] = []
    for term in terms:
        if term.lower() not in already and term.lower() not in {u.lower() for u in unique}:
            unique.append(term)
    return unique


def build_search_text(title: str, product: ExtractedProduct) -> str:
    heights = [v.height for v in product.variants if v.height]
    parts = [
        title,
        product.product_type,
        product.brand or "",
        " ".join(product.colours),
        " ".join(dict.fromkeys(heights)),  # dict.fromkeys: de-duplicate, keep order
        f"okce {product.mesh}" if product.mesh else "",
        " ".join(product.uses),
        " ".join(product.compatible_with),
        " ".join(_synonyms(title, product)),
    ]
    kept: list[str] = []
    seen: set[str] = set()
    for part in parts:
        part = " ".join(part.split())  # collapse whitespace
        if part and part.lower() not in seen:
            kept.append(part)
            seen.add(part.lower())
    return SEPARATOR.join(kept)
