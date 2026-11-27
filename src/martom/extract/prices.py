"""Turn a price as the listing writes it ("44,00 KM") into a number, in plain Python.

The model copies the price text exactly (`ExtractedVariant.price`); the number is parsed
here, never by the model, so a model can't invent or "round" a price. Anything that isn't
clearly one number becomes None, and the deterministic checks (M2-2) flag it for Toni.
"""

import re
from decimal import Decimal, InvalidOperation

# A run of digits with dots, commas or spaces inside it ("1.250,00", "44", "12 500").
_NUMBER = re.compile(r"\d[\d.,\s]*\d|\d")


def parse_price_km(text: str | None) -> Decimal | None:
    """The single price in `text` as a Decimal, or None when there is none or several.

    Bosnian prices use a comma for decimals and a dot for thousands ("1.250,00 KM"). A dot
    followed by exactly one or two digits at the end ("13.50") is read as a decimal point.
    """
    if not text:
        return None
    numbers = _NUMBER.findall(text)
    if len(numbers) != 1:
        return None  # no number, or "27,00 / 31,00 KM": ambiguous, don't guess
    number = numbers[0].replace(" ", "")
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+\.\d{1,2}", number):
        pass  # "13.50": a decimal point
    else:
        number = number.replace(".", "")  # "1.250": thousands
    try:
        return Decimal(number)
    except InvalidOperation:
        return None
