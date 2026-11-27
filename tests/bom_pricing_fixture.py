"""The extracted variants of the six listings the BOM price map points at.

Copied from the local synced catalogue on 27 Nov 2026 (product_variant, as extraction
wrote them), so the shipped price_map.toml is tested against real field values. Prices are
the OLX prices of that day; fence-rules.md's 905,10 KM example uses the same panel prices.
Imported as a plain module (pytest's `pythonpath` includes tests/).
"""

# ruff: noqa: RUF001

from decimal import Decimal

from martom.extract.store import VariantRow

PANEL_LISTING = 63996799
T_POST_LISTING = 59473597
FOOT_PLATE_LISTING = 81645615
TENSIONER_LISTING = 18129143
WIRE_LISTING = 46677177
WIRE_HOLDER_LISTING = 48475613


def _v(
    label: str,
    price: str | None,
    unit: str | None,
    *,
    height: str | None = None,
    length: str | None = None,
    colour: str | None = None,
    size: str | None = None,
) -> VariantRow:
    price_text = f"{price.replace('.', ',')} KM" if price else None
    return VariantRow(
        label=label,
        height=height,
        length=length,
        colour=colour,
        size=size,
        price_text=price_text,
        price=Decimal(price) if price else None,
        unit=unit,
    )


def _panel(height: int, width: int, price: str) -> VariantRow:
    label = f"3D Panelna ograda {height} x {width} cm (Ø 4,20/4,40 mm PVC)"
    return _v(label, price, "panel", height=str(height), length=f"{width} cm")


def _post(height: int, panel: int, price: str) -> VariantRow:
    label = f"Stup H-{height} cm sa stopom (za panele visine {panel} cm)"
    return _v(label, price, "kom", height=f"H-{height} cm")


def _t_post(height: int, price: str) -> VariantRow:
    label = f"T-stup metalni plastificirani 35 x 35 x 3,50 mm – visina H-{height} cm"
    return _v(label, price, "kom", height=f"H-{height}", colour="zelena")


def _foot_plate(height: int, price: str) -> VariantRow:
    label = f"Stup / kolac sa stopom 40 x 40 mm – visina {height} cm"
    return _v(label, price, "kom", height=f"{height} cm", size="40 x 40 mm")


VARIANTS: dict[int, list[VariantRow]] = {
    PANEL_LISTING: [
        _panel(83, 200, "27.00"),
        _panel(83, 250, "31.00"),
        _panel(103, 200, "31.00"),
        _panel(103, 250, "41.00"),
        _panel(123, 200, "36.00"),
        _panel(123, 250, "44.00"),
        _panel(153, 200, "44.00"),
        _panel(153, 250, "53.00"),
        _panel(203, 200, "56.00"),
        _panel(203, 250, "85.00"),
        _post(85, 83, "16.80"),
        _post(105, 103, "19.00"),
        _post(125, 123, "21.50"),
        _post(155, 153, "24.90"),
        _post(205, 203, "31.00"),
        _v("PVC kopča sa samorezivim vijkom", "1.00", "kom"),
        _v(
            "Tiplo anker FI 10 / M8 x 80 mm (za pričvršćivanje stope stupa u beton)",
            "1.30",
            "kom",
            size="FI 10 / M8 x 80 mm",
        ),
    ],
    T_POST_LISTING: [
        _t_post(100, "11.50"),
        _t_post(125, "10.00"),
        _t_post(150, "12.25"),
        _t_post(175, "14.00"),
        _t_post(200, "17.00"),
    ],
    FOOT_PLATE_LISTING: [
        _foot_plate(60, "10.50"),
        _foot_plate(80, "11.90"),
        _foot_plate(100, "14.00"),
        _foot_plate(125, "16.50"),
        _foot_plate(150, "21.00"),
    ],
    TENSIONER_LISTING: [
        _v("Zatezač za žicu (španer) – Zeleni (plastificirani)", "1.65", "kom", colour="Zeleni"),
        _v(
            "Zatezač za žicu (španer) – Antracit (plastificirani)",
            "1.65",
            "kom",
            colour="Antracit",
        ),
        _v("Zatezač za žicu (španer) – Cinčani (pocinčani čelik)", "1.65", "kom", colour="Cinčani"),
        _v("Zatezač za bodljikavu žicu (ojačani model – slika 2)", "3.50", "kom"),
    ],
    WIRE_LISTING: [
        _v("ŽICA PVC 3,10MM -100M- 20,00KM", "20.00", "100M", length="100M", size="3,10MM"),
        _v("ŽICA PVC 2,70MM -100M- 18,00KM", "18.00", "100M", length="100M", size="2,70MM"),
        _v("ŽICA PVC 2,2MM -100M- 14,000KM", "14.00", "100M", length="100M", size="2,2MM"),
    ],
    WIRE_HOLDER_LISTING: [
        _v("NOSAČ DRŽAČ ŽICE NA STUPU STUBU - 0,40KMKOM", "0.40", "KOM"),
    ],
}
