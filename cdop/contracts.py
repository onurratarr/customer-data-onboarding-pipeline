"""Data contracts for the canonical entities (pydantic). Each mapped source row is coerced into the contract;
rows that cannot be coerced go to quarantine with the reasons, instead of failing the load or leaking bad data."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError, field_validator

from .profile import DATE_FORMATS, TS_FORMATS

COUNTRY_TO_ISO2 = {"us": "US", "usa": "US", "united states": "US", "ca": "CA", "can": "CA", "canada": "CA",
                   "gb": "GB", "gbr": "GB", "uk": "GB", "united kingdom": "GB", "de": "DE", "deu": "DE",
                   "germany": "DE", "fr": "FR", "fra": "FR", "france": "FR", "nl": "NL", "nld": "NL",
                   "netherlands": "NL", "se": "SE", "swe": "SE", "sweden": "SE", "no": "NO", "nor": "NO",
                   "norway": "NO"}
SEGMENT_VALUES = {"enterprise": "enterprise", "mid-market": "mid_market", "mid_market": "mid_market",
                  "midmarket": "mid_market", "mid market": "mid_market", "smb": "smb", "small business": "smb"}
STATUS_VALUES = {"placed": "placed", "open": "placed", "new": "placed", "pending": "placed", "created": "placed",
                 "shipped": "shipped", "in transit": "shipped", "delivered": "delivered", "fulfilled": "delivered",
                 "completed": "delivered", "cancelled": "cancelled", "canceled": "cancelled", "void": "cancelled",
                 "returned": "returned", "refunded": "returned"}
TRUE = {"y", "yes", "true", "t", "1", "x", "active"}
FALSE = {"n", "no", "false", "f", "0", "", "inactive"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")


def _blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def parse_date(v) -> date:
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    s = str(v).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return parse_ts(s).date()


def parse_ts(v) -> datetime:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        pass
    for f in TS_FORMATS:
        try:
            d = datetime.strptime(s, f)
            return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            pass
    raise ValueError(f"unparseable timestamp {s!r}")


def parse_bool(v) -> bool:
    s = "" if v is None else str(v).strip().lower()
    if s in TRUE:
        return True
    if s in FALSE:
        return False
    raise ValueError(f"not a boolean: {v!r}")


def parse_money(v) -> Decimal:
    s = re.sub(r"[$€£,\s]", "", str(v))
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {v!r}") from None
    if d < 0:
        raise ValueError("negative amount")
    return d.quantize(Decimal("0.01"))


def _norm(table: dict, what: str):
    def f(v):
        key = str(v).strip().lower().replace("_", " ") if v is not None else ""
        key2 = str(v).strip().lower() if v is not None else ""
        if key2 in table:
            return table[key2]
        if key in table:
            return table[key]
        raise ValueError(f"unknown {what} {v!r}")
    return f


norm_country = _norm(COUNTRY_TO_ISO2, "country")
norm_segment = _norm(SEGMENT_VALUES, "segment")
norm_status = _norm(STATUS_VALUES, "status")


class _Base(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if _blank(v) else v


class Customer(_Base):
    customer_id: str
    email: str
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    phone: Optional[str] = None
    country_code: Optional[str] = None
    segment: Optional[Literal["enterprise", "mid_market", "smb"]] = None
    signup_date: Optional[date] = None
    is_active: Optional[bool] = None

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        if v is None:
            return v
        v = str(v).strip().lower()
        if not EMAIL.match(v):
            raise ValueError(f"invalid email {v!r}")
        return v

    @field_validator("country_code", mode="before")
    @classmethod
    def _country(cls, v):
        return None if v is None else norm_country(v)

    @field_validator("segment", mode="before")
    @classmethod
    def _segment(cls, v):
        return None if v is None else norm_segment(v)

    @field_validator("signup_date", mode="before")
    @classmethod
    def _date(cls, v):
        return None if v is None else parse_date(v)

    @field_validator("is_active", mode="before")
    @classmethod
    def _bool(cls, v):
        return None if v is None else parse_bool(v)


class Product(_Base):
    sku: str
    product_name: str
    category: Optional[str] = None
    unit_price: Decimal
    currency: Optional[Literal["USD", "EUR", "GBP"]] = None
    is_discontinued: Optional[bool] = None

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        return None if v is None else str(v).strip().title()

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, v):
        return v if v is None else parse_money(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _cur(cls, v):
        return None if v is None else str(v).strip().upper()

    @field_validator("is_discontinued", mode="before")
    @classmethod
    def _bool(cls, v):
        return parse_bool(v)


class Order(_Base):
    order_id: str
    customer_id: str
    sku: str
    quantity: int
    unit_price: Decimal
    order_ts: datetime
    status: Literal["placed", "shipped", "delivered", "cancelled", "returned"]
    updated_at: datetime

    @field_validator("quantity", mode="after")
    @classmethod
    def _qty(cls, v):
        if v <= 0:
            raise ValueError("quantity must be positive")
        return v

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, v):
        return v if v is None else parse_money(v)

    @field_validator("order_ts", "updated_at", mode="before")
    @classmethod
    def _ts(cls, v):
        return v if v is None else parse_ts(v)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v):
        return v if v is None else norm_status(v)


CONTRACTS = {"customers": Customer, "products": Product, "orders": Order}
FIELDS = {e: list(m.model_fields) for e, m in CONTRACTS.items()}


def validate_rows(entity: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """rows are already renamed to canonical fields. Returns (valid, quarantined); quarantined keep the raw row."""
    model = CONTRACTS[entity]
    good, bad = [], []
    for i, row in enumerate(rows):
        try:
            good.append(model(**row).model_dump())
        except ValidationError as e:
            errs = [f"{'.'.join(map(str, x['loc'])) or 'row'}: "
                    + ("missing required value" if x.get("input") is None and x["type"] != "value_error"
                       else x["msg"].removeprefix("Value error, ")) for x in e.errors()]
            bad.append({"row_number": i + 1, "raw": row, "errors": errs})
    return good, bad
