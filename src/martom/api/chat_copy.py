"""Fixed Bosnian texts of the chat stream's "Savjetnik radi" steps (#21).

Customer-facing copy, so it lives here, in one place; a change is a product decision
(Tomislav). The bom text matches the frontend fixtures (web/src/api/fixtures/fence.ts).
"""

# Fixed texts, also the fallback when nothing was parsed or nothing was found.
STEP_PARSE = "Čitam upit"
STEP_SEARCH = "Pretražujem katalog"
STEP_BOM = "Izračunao popis materijala"
STEP_CLARIFY = "Trebam još podataka"

# Dynamic texts (Tomislav, 28 Nov 2026): only values Python parsed, never model text.
PARSED = "Razumijem: {parts}"  # e.g. "Razumijem: panelna ograda · 25 m · 150 cm"
PARSED_SEPARATOR = " · "
KIND_PANEL = "panelna ograda"
KIND_ROLL = "ograda u roli"
# "Pronađen 1 proizvod", "Pronađena 2 proizvoda", "Pronađeno 5 proizvoda".
FOUND_ONE = "Pronađen {count} proizvod"
FOUND_FEW = "Pronađena {count} proizvoda"
FOUND_MANY = "Pronađeno {count} proizvoda"
