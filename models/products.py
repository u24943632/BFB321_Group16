"""Simply Garlic product catalogue.

Each product has a three-letter SKU code. The code is embedded in every box ID
(see :func:`models.tracking.make_box_id`), so operators can read the product
off a label even when the QR code is damaged.

``perishable`` products (fresh pastes and sprouts) get tighter stage-dwell
alert thresholds in :mod:`models.alerts`.
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownProductError(KeyError):
    """Raised when a SKU code is not in the catalogue."""


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    perishable: bool = False


PRODUCTS: tuple[Product, ...] = (
    Product("DON", "Dried onions", "Dried"),
    Product("PON", "Pickled onions", "Pickled"),
    Product("BGA", "Black garlic", "Black garlic"),
    Product("BGP", "Black garlic paste", "Black garlic"),
    Product("GGT", "Garlic & ginger tubs", "Fresh paste", perishable=True),
    Product("GST", "Ginger squeeze tubes", "Fresh paste", perishable=True),
    Product("GIT", "Ginger tubs", "Fresh paste", perishable=True),
    Product("DGA", "Dried garlic", "Dried"),
    Product("SDT", "Sundried tomatoes", "Dried"),
    Product("MSP", "Mixed sprouts", "Fresh produce", perishable=True),
)

_BY_SKU = {p.sku: p for p in PRODUCTS}


def get_product(sku: str) -> Product:
    """Return the product for ``sku`` (case-insensitive).

    Raises:
        UnknownProductError: if the SKU is not in the catalogue.
    """
    key = (sku or "").strip().upper()
    try:
        return _BY_SKU[key]
    except KeyError:
        raise UnknownProductError(f"Unknown product SKU '{sku}'.") from None
