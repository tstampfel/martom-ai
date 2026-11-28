"""Every Bosnian sentence the advisor writes itself (not the model's own words).

The rules behind each text are in docs/domain/fence-rules.md and the #20 comments from
Toni and Tomislav. These texts are appended to the answer after the grounding check, so
Martom's public phone number (036 651 427) is never mistaken for a model-made number.
"""

# Comments and texts use the real "–" dash, as in fence-rules.md.
# ruff: noqa: RUF001, RUF003

MARTOM_PHONE = "036 651 427"
MARTOM_EMAIL = "info@martom-bekaert.com"
CONTACT = f"{MARTOM_PHONE} ili {MARTOM_EMAIL}"

# Tomislav, 28 Nov 2026 (#171, replaces Toni's 26 Nov "out of scope"): Martom makes gates
# to order. Never a price, and no gate line in the BOM.
GATE = f"Kapije radimo po narudžbi, javite se za ponudu: {CONTACT}"
# Tomislav, 26 Nov 2026: a side over 300 m or a total over 1000 m gets no materials list.
FENCE_TOO_LARGE = f"Za ograde ove veličine javite se direktno Martomu: {MARTOM_PHONE}"
# Toni, 26 Nov 2026 14:52: concrete-in-soil posts are not in the BOM.
CONCRETE_POSTS = "Stupovi za betoniranje – po upitu (rade se po narudžbi, 10–15 dana)"
CONCRETE_POSTS_LINE = "Stupovi za betoniranje – po upitu"
CONCRETE_POSTS_LINE_NOTE = "Rade se po narudžbi, 10–15 dana."
# fence-rules.md, "Every BOM".
BOM_NOTE = (
    "Cijene i ponuda su informativne – pribor možete izbaciti ako ga već imate. "
    "Dostava se potvrđuje."
)
# Toni, 28 Nov 2026 (#171): fence-rules.md, "Concrete, installation and availability".
# By arrangement only, within about 40 km; never priced and never in the calculator.
INSTALLATION = f"Montažu radimo po dogovoru u krugu oko 40 km; javite se na {CONTACT}."
# Toni, 28 Nov 2026 (#171): anchors are "prateći materijal", sold separately. The second
# form only when this chat's materials list has an anchor line.
ANCHORS_SOLD_SEPARATELY = "Ne, ankeri se kupuju posebno."
ANCHORS_SOLD_SEPARATELY_IN_LIST = "Ne, ankeri se kupuju posebno; za vaš popis smo ih već uračunali."
# Toni, 28 Nov 2026 (#171, "Anchors, final"): a drilling answer always names the anchor
# type with its bit, never a bare "8 mm" or "10 mm".
DRILLING_PANEL = "Stupovi panelne ograde (s pločicom): tiplo anker M8×80 – svrdlo ø10 mm SDS."
DRILLING_FOOT_PLATE = (
    "Stupovi sa stopom (ograda u roli): anker vijak M8×70 (može i M8×80) – svrdlo ø8 mm."
)
NO_CONCRETE_SALES = "Martom ne prodaje beton i ne izvodi građevinske radove; beton nabavljate sami."
# fence-rules.md, "Welded roll fence": chain link rules are still pending from Toni.
CHAIN_LINK = (
    "Za pletivo (žičanu mrežu) još nemamo pravila za izračun materijala. "
    f"Javite se direktno Martomu: {MARTOM_PHONE}"
)
# fence-rules.md, "Eurofence": there is no 100 cm Eurofence. The offered heights come
# from the synced variants, never from this text.
EUROFENCE_MISSING_HEIGHT = "Eurofence od {height} cm ne postoji. Imamo {options}."
OUT_OF_SCOPE = (
    "Ja sam savjetnik Željezarije Martom i pomažem oko proizvoda koje Martom prodaje: "
    "ograde, alat, okov i ostalo iz ponude. Pitajte me slobodno nešto od toga."
)
# Tomislav, 26 Nov 2026: a side of 0 m or less is an input error; ask for it again.
INVALID_LENGTH = "Dužina svake strane mora biti veća od 0 m. Upišite dužine ponovo."
NOTHING_FOUND = (
    "Nisam našao proizvod koji odgovara upitu. Opišite ga drugačije ili se javite "
    f"direktno Martomu: {MARTOM_PHONE}"
)
# The grounding check failed twice: plain search results, no prices (docs/PLAN.md).
FALLBACK_INTRO = "Evo proizvoda iz ponude koji odgovaraju upitu:"
FALLBACK_NO_RESULTS = NOTHING_FOUND

# --- clarify chips ------------------------------------------------------------------

CLARIFY_INTRO = "Još nekoliko podataka:"
LAYOUT_QUESTION = "Koliko ima kutova?"
LAYOUT_LABEL = "Kutovi"
FENCE_TYPE_QUESTION = "Koja vrsta ograde?"
FENCE_TYPE_LABEL = "Ograda"
FENCE_TYPE_PANEL = "Panelna ograda"
FENCE_TYPE_ROLL = "Varena ograda u roli"
HEIGHT_QUESTION = "Koja visina?"
HEIGHT_LABEL = "Visina"
POSTS_QUESTION = "Gdje idu stupovi?"
POSTS_LABEL = "Stupovi"
POSTS_PLATE = "Na beton, s pločicom"
POSTS_CONCRETE = "U zemlju (betoniram)"
POSTS_CONCRETE_SHORT = "U zemlju · po upitu"
POSTS_CONCRETE_BADGE = "po upitu · po narudžbi"
POSTS_CONCRETE_NOTE = (
    "Stupovi za betoniranje su po upitu i rade se po narudžbi, 10–15 dana. "
    "Beton ne prodajemo, nabavljate ga sami."
)
POSTS_UNKNOWN = "Ne znam"
POSTS_T_POST = "U zemlju ili beton (T-stup)"
POSTS_T_POST_SHORT = "T-stup"
POSTS_FOOT_PLATE = "Na betonski zid ili podlogu (stup sa stopom)"
POSTS_FOOT_PLATE_SHORT = "Stup sa stopom"
COLOUR_QUESTION = "Koja boja?"
COLOUR_LABEL = "Boja"
COLOUR_ANTHRACITE = "Antracit (RAL 7016)"
COLOUR_GREEN = "Zelena (RAL 6005)"
COLOUR_ANY = "Svejedno"
PROJECT_PANEL = "Panelna ograda"
PROJECT_ROLL = "Ograda u roli"
PROJECT_FENCE = "Ograda"
BOM_TITLE = "Popis materijala"

# #147 (Tomislav, 28 Nov 2026): a product not on the materials list goes into the korpa
# as 1 piece; the model never sets a quantity, so the customer changes it there.
CART_QUANTITY = "Količinu možete promijeniti u korpi."
