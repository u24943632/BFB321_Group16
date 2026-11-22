"""Simply Garlic product catalogue.

Each product has a three-letter SKU code. The code is embedded in every box ID
(see :func:`models.tracking.make_box_id`), so operators can read the product
off a label even when the QR code is damaged.

``perishable`` products (fresh pastes and sprouts) get tighter stage-dwell
alert thresholds in :mod:`models.alerts`.

``PRODUCTS`` holds the 10 launch SKUs used to seed the database. At run time
the catalogue is read from the ``products`` table (see
:func:`catalogue_from_rows`), so seasonal products added through the API are
recognised without changing this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

SKU_PATTERN = re.compile(r"^[A-Z]{3}$")  # same rule as ck_products_sku in schema.sql


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


def catalogue_from_rows(rows: Iterable[dict], *, active_only: bool = True) -> dict[str, Product]:
    """Build a SKU -> Product map from ``products JOIN product_categories`` rows.

    With ``active_only`` (used for registering boxes) inactive products are
    left out, so no new boxes can be made for them; existing boxes of an
    inactive product still need the full catalogue (e.g. for alerts).
    """
    return {row["sku"]: Product(row["sku"], row["product_name"], row["category_name"],
                                bool(row["is_perishable"]))
            for row in rows if row.get("is_active", True) or not active_only}


def get_product(sku: str, catalogue: Optional[Mapping[str, Product]] = None) -> Product:
    """Return the product for ``sku`` (case-insensitive).

    Args:
        catalogue: SKU -> Product map to search; defaults to the launch catalogue.

    Raises:
        UnknownProductError: if the SKU is not in the catalogue.
    """
    key = (sku or "").strip().upper()
    try:
        return (catalogue if catalogue is not None else _BY_SKU)[key]
    except KeyError:
        raise UnknownProductError(f"Unknown or inactive product SKU '{sku}'.") from None
