#!/usr/bin/env python3
"""Shopping Magic local JSON-in/JSON-out SQLite storage boundary (Python 3.10+)."""
from __future__ import annotations

import argparse
import calendar
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from typing import Any, Callable
import unicodedata
import uuid

VERSION = "0.1.0"
SCHEMA_VERSION = 1

LIST_KINDS = ("supermarket", "home", "gifts", "pharmacy", "personal_care", "custom")
PRIORITIES = ("low", "normal", "high", "urgent")
ITEM_OPEN_STATUSES = ("idea", "planned", "needed", "partially_bought")
ITEM_ALL_STATUSES = ITEM_OPEN_STATUSES + ("bought", "cancelled")
ITEM_ADD_STATUSES = ("idea", "planned", "needed")
REPLENISHMENT_MODES = ("none", "interval_days", "calendar_months")
REPLENISHMENT_SOURCES = ("none", "user_explicit", "user_corrected")
FULFILLMENT_MODES = ("auto", "mark_bought", "keep_open")

CORE_TABLES = frozenset({
    "lists",
    "products",
    "category_preferences",
    "shopping_items",
    "purchases",
    "mutation_requests",
})
EXPORT_TABLES = ("lists", "products", "category_preferences", "shopping_items", "purchases")

# Exact measurement conversion table for partial purchase arithmetic only.
# Keys are folded unit names -> (dimension_group, Decimal_multiplier_to_base).
_UNIT_ALIASES: dict[str, tuple[str, Decimal]] = {
    "": ("dim:unitless", Decimal("1")),
    "mg": ("dim:mass", Decimal("0.001")),
    "milligram": ("dim:mass", Decimal("0.001")),
    "milligrams": ("dim:mass", Decimal("0.001")),
    "g": ("dim:mass", Decimal("1")),
    "gr": ("dim:mass", Decimal("1")),
    "gram": ("dim:mass", Decimal("1")),
    "grams": ("dim:mass", Decimal("1")),
    "γρ": ("dim:mass", Decimal("1")),
    "γραμ": ("dim:mass", Decimal("1")),
    "γραμμαριο": ("dim:mass", Decimal("1")),
    "γραμμαρια": ("dim:mass", Decimal("1")),
    "kg": ("dim:mass", Decimal("1000")),
    "kilo": ("dim:mass", Decimal("1000")),
    "kilos": ("dim:mass", Decimal("1000")),
    "kilogram": ("dim:mass", Decimal("1000")),
    "kilograms": ("dim:mass", Decimal("1000")),
    "κιλο": ("dim:mass", Decimal("1000")),
    "κιλα": ("dim:mass", Decimal("1000")),
    "κγ": ("dim:mass", Decimal("1000")),
    "ml": ("dim:volume", Decimal("1")),
    "milliliter": ("dim:volume", Decimal("1")),
    "milliliters": ("dim:volume", Decimal("1")),
    "millilitre": ("dim:volume", Decimal("1")),
    "millilitres": ("dim:volume", Decimal("1")),
    "cl": ("dim:volume", Decimal("10")),
    "dl": ("dim:volume", Decimal("100")),
    "l": ("dim:volume", Decimal("1000")),
    "lt": ("dim:volume", Decimal("1000")),
    "ltr": ("dim:volume", Decimal("1000")),
    "liter": ("dim:volume", Decimal("1000")),
    "liters": ("dim:volume", Decimal("1000")),
    "litre": ("dim:volume", Decimal("1000")),
    "litres": ("dim:volume", Decimal("1000")),
    "λιτρο": ("dim:volume", Decimal("1000")),
    "λιτρα": ("dim:volume", Decimal("1000")),
}

for _group, _aliases in (
    ("count:piece", ("piece", "pieces", "pc", "pcs", "unit", "units", "item", "items",
                      "τεμ", "τεμαχιο", "τεμαχια", "κομματι", "κομματια")),
    ("count:bottle", ("bottle", "bottles", "μπουκαλι", "μπουκαλια")),
    ("count:carton", ("carton", "cartons", "κουτι", "κουτια")),
    ("count:pack", ("pack", "packs", "packet", "packets", "package", "packages", "πακετο", "πακετα")),
    ("count:box", ("box", "boxes")),
    ("count:can", ("can", "cans", "tin", "tins", "κουτακι", "κουτακια")),
    ("count:jar", ("jar", "jars", "βαζο", "βαζα", "βαζακι", "βαζακια")),
    ("count:bag", ("bag", "bags", "σακουλα", "σακουλες", "σακουλακι")),
    ("count:roll", ("roll", "rolls", "ρολο", "ρολα")),
    ("count:pill", ("pill", "pills", "χαπι", "χαπια")),
    ("count:tablet", ("tablet", "tablets", "ταμπλετα", "ταμπλετες", "δισκιο", "δισκια")),
    ("count:capsule", ("capsule", "capsules", "καψουλα", "καψουλες")),
    ("count:sachet", ("sachet", "sachets", "φακελακι", "φακελακια")),
    ("count:tube", ("tube", "tubes", "σωληναριο", "σωληναρια")),
    ("count:bar", ("bar", "bars", "μπαρα", "μπαρες")),
    ("count:loaf", ("loaf", "loaves", "φραντζολα", "φραντζολες")),
    ("count:bunch", ("bunch", "bunches", "ματσακι", "ματσακια")),
    ("count:pair", ("pair", "pairs", "ζευγαρι", "ζευγαρια")),
    ("count:set", ("set", "sets", "σετ")),
):
    for _alias in _aliases:
        _UNIT_ALIASES[_alias] = (_group, Decimal("1"))


class StoreError(Exception):
    """Machine-readable validation, conflict, or storage boundary error."""


def default_data_dir() -> Path:
    """Resolve user data independently of working or install directories."""
    override = os.environ.get("SHOPPING_MAGIC_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path.home() / ".shopping-magic" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/ShoppingMagic"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "shopping-magic"


def checked_path(path: Path) -> Path:
    """Refuse symlinks and Windows reparse points without following them."""
    path = Path(os.path.abspath(path.expanduser()))
    spelling = str(path)
    volume_root = None
    if os.name == "nt" and spelling.casefold().startswith("\\\\?\\volume{") and "}" in spelling:
        volume_root = spelling[:spelling.index("}") + 1].casefold() + "\\"
    for part in (path, *path.parents):
        if volume_root is not None and not str(part).casefold().startswith(volume_root):
            continue
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StoreError("redirected_profile_path")
    return path


def physical_path(path: Path) -> str | None:
    """Report the OS-opened file path rather than a virtualized Windows spelling."""
    if not path.exists():
        return None
    if sys.platform != "win32":
        return str(path.resolve())
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    with path.open("rb") as handle:
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(
            msvcrt.get_osfhandle(handle.fileno()), buffer, len(buffer), 0
        )
        if not size or size >= len(buffer):
            raise StoreError("profile_physical_path_unavailable")
        return buffer.value


def sqlite_uri(path: Path, mode: str = "ro") -> str:
    """Encode a filesystem path cleanly as an SQLite URI."""
    from urllib.parse import quote

    return "file:" + quote(str(path), safe="") + "?mode=" + mode


def _valid_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


@contextmanager
def profile_lock(directory: Path):
    """Serialize initialization and writes across local agent processes."""
    checked_path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = checked_path(directory / ".startup.lock")
    with lock_path.open("a+b") as lock:
        deadline = time.monotonic() + 10.0
        if os.name == "nt":
            import msvcrt

            def acquire() -> None:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)

            def release() -> None:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(lock, fcntl.LOCK_UN)

        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise StoreError("profile_busy_retry") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            release()


def read_origins(marker: Path) -> dict[str, Any] | None:
    """Read and strictly validate the Windows shared-profile identity guard."""
    if not marker.exists():
        return None
    if marker.is_symlink() or getattr(marker.lstat(), "st_file_attributes", 0) & 0x400:
        raise StoreError("invalid_profile_origins")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))

        def hex_str(value: Any, length: int) -> bool:
            return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)

        allowed = {"schema", "profile_id", "state", "database_identity"}
        if (
            not isinstance(data, dict)
            or set(data) != allowed
            or type(data["schema"]) is not int
            or data["schema"] != 1
            or not hex_str(data["profile_id"], 32)
            or data["state"] not in ("publishing", "ready")
        ):
            raise ValueError
        identity = data["database_identity"]
        if identity is not None and (
            not isinstance(identity, list)
            or len(identity) != 2
            or any(type(v) is not int or v < 0 for v in identity)
        ):
            raise ValueError
        return data
    except (OSError, ValueError, UnicodeError):
        raise StoreError("invalid_profile_origins") from None


def write_origins(marker: Path, data: dict[str, Any]) -> None:
    """Atomically write the Windows shared-profile identity marker."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".origins-",
            suffix=".tmp",
            dir=marker.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def normalized(value: str) -> str:
    """Normalize text for literal matching and uniqueness (case, Greek accents, final sigma)."""
    folded = unicodedata.normalize("NFD", value.casefold())
    stripped = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.split())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def text_field(req: dict[str, Any], key: str, *, limit: int = 2000, allow_empty: bool = False) -> str:
    value = req.get(key)
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise StoreError(f"invalid_{key}")
    return value.strip()


def optional_text(
    req: dict[str, Any],
    key: str,
    default: str = "",
    *,
    limit: int = 2000,
) -> str:
    if key not in req:
        return default
    value = req[key]
    if not isinstance(value, str) or len(value) > limit:
        raise StoreError(f"invalid_{key}")
    return value.strip()


def boolean_field(req: dict[str, Any], key: str, default: bool = False) -> bool:
    value = req.get(key, default)
    if type(value) is not bool:
        raise StoreError(f"invalid_{key}")
    return value


def int_field(
    req: dict[str, Any],
    key: str,
    *,
    minimum: int = 1,
    maximum: int = 2**63 - 1,
) -> int:
    value = req.get(key)
    if type(value) is not int or not (minimum <= value <= maximum):
        raise StoreError(f"invalid_{key}")
    return value


def limit_field(req: dict[str, Any], default: int = 50, maximum: int = 100) -> int:
    value = req.get("limit", default)
    if type(value) is not int or not (1 <= value <= maximum):
        raise StoreError("invalid_limit")
    return value


def offset_field(req: dict[str, Any]) -> int:
    offset = req.get("offset", 0)
    if type(offset) is not int or not (0 <= offset < 2**63):
        raise StoreError("invalid_offset")
    return offset


def date_field(req: dict[str, Any], key: str) -> date:
    value = text_field(req, key, limit=10)
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        raise StoreError(f"invalid_{key}") from None
    return parsed


def optional_date_field(req: dict[str, Any], key: str, default: str | None = None) -> str | None:
    if key not in req:
        return default
    if req[key] is None:
        return None
    return date_field(req, key).isoformat()


def format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def parse_decimal(value: Any, key: str, *, allow_zero: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise StoreError(f"invalid_{key}")
    if isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise StoreError(f"invalid_{key}")
        dec = Decimal(str(value))
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise StoreError(f"invalid_{key}")
        try:
            dec = Decimal(raw)
        except InvalidOperation:
            raise StoreError(f"invalid_{key}") from None
    else:
        raise StoreError(f"invalid_{key}")
    if not dec.is_finite():
        raise StoreError(f"invalid_{key}")
    if allow_zero and dec < 0:
        raise StoreError(f"invalid_{key}")
    if not allow_zero and dec <= 0:
        raise StoreError(f"invalid_{key}")
    formatted = format_decimal(dec)
    if len(formatted) > 32:
        raise StoreError(f"invalid_{key}")
    return formatted


def optional_decimal(
    req: dict[str, Any],
    key: str,
    default: str | None = None,
    *,
    allow_zero: bool = False,
) -> str | None:
    if key not in req:
        return default
    return parse_decimal(req[key], key, allow_zero=allow_zero)


def unit_spec(unit: str) -> tuple[str, Decimal]:
    key = normalized(unit)
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    return (f"custom:{key}", Decimal("1"))


def convert_quantity(qty: Decimal, from_unit: str, to_unit: str) -> Decimal:
    from_group, from_factor = unit_spec(from_unit)
    to_group, to_factor = unit_spec(to_unit)
    if from_group != to_group:
        raise StoreError("incompatible_units")
    return (qty * from_factor) / to_factor


def add_months(start: date, months: int) -> date:
    total_months = (start.year * 12 + (start.month - 1)) + months
    year, zero_month = divmod(total_months, 12)
    month = zero_month + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def validate_aliases(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > 30:
        raise StoreError("invalid_aliases")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise StoreError("invalid_aliases")
        clean = item.strip()
        key = normalized(clean)
        if key and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def canonical_payload(req: dict[str, Any]) -> str:
    try:
        return json.dumps(req, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise StoreError("invalid_request_json") from None


SCHEMA = (
    "CREATE TABLE lists ("
    "id TEXT PRIMARY KEY, "
    "name TEXT NOT NULL, "
    "normalized_name TEXT NOT NULL UNIQUE, "
    "kind TEXT NOT NULL DEFAULT 'custom' CHECK(kind IN ('supermarket','home','gifts','pharmacy','personal_care','custom')), "
    "notes TEXT NOT NULL DEFAULT '', "
    "archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL"
    ")",
    "CREATE TABLE products ("
    "id TEXT PRIMARY KEY, "
    "name TEXT NOT NULL, "
    "normalized_name TEXT NOT NULL UNIQUE, "
    "aliases TEXT NOT NULL DEFAULT '[]', "
    "search_text TEXT NOT NULL, "
    "default_list_id TEXT REFERENCES lists(id) ON DELETE SET NULL, "
    "default_category TEXT NOT NULL DEFAULT '', "
    "default_unit TEXT NOT NULL DEFAULT '', "
    "default_brand TEXT NOT NULL DEFAULT '', "
    "default_store TEXT NOT NULL DEFAULT '', "
    "notes TEXT NOT NULL DEFAULT '', "
    "replenishment_mode TEXT NOT NULL DEFAULT 'none' CHECK(replenishment_mode IN ('none','interval_days','calendar_months')), "
    "replenishment_value INTEGER CHECK(replenishment_value IS NULL OR replenishment_value >= 1), "
    "replenishment_note TEXT NOT NULL DEFAULT '', "
    "replenishment_source TEXT NOT NULL DEFAULT 'none' CHECK(replenishment_source IN ('none','user_explicit','user_corrected')), "
    "revision INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL"
    ")",
    "CREATE TABLE category_preferences ("
    "id TEXT PRIMARY KEY, "
    "topic TEXT NOT NULL, "
    "normalized_topic TEXT NOT NULL UNIQUE, "
    "list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE, "
    "category TEXT NOT NULL DEFAULT '', "
    "notes TEXT NOT NULL DEFAULT '', "
    "updated_at TEXT NOT NULL"
    ")",
    "CREATE TABLE shopping_items ("
    "id TEXT PRIMARY KEY, "
    "list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE RESTRICT, "
    "product_id TEXT REFERENCES products(id) ON DELETE SET NULL, "
    "name TEXT NOT NULL, "
    "search_text TEXT NOT NULL, "
    "category TEXT NOT NULL DEFAULT '', "
    "quantity TEXT, "
    "remaining_quantity TEXT, "
    "unit TEXT NOT NULL DEFAULT '', "
    "brand TEXT NOT NULL DEFAULT '', "
    "preferred_store TEXT NOT NULL DEFAULT '', "
    "notes TEXT NOT NULL DEFAULT '', "
    "priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')), "
    "status TEXT NOT NULL DEFAULT 'needed' CHECK(status IN ('idea','planned','needed','partially_bought','bought','cancelled')), "
    "target_date TEXT, "
    "recipient TEXT NOT NULL DEFAULT '', "
    "occasion TEXT NOT NULL DEFAULT '', "
    "occasion_recurring INTEGER NOT NULL DEFAULT 0 CHECK(occasion_recurring IN (0,1)), "
    "budget_amount TEXT, "
    "budget_currency TEXT NOT NULL DEFAULT '', "
    "revision INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "resolved_at TEXT"
    ")",
    "CREATE TABLE purchases ("
    "id TEXT PRIMARY KEY, "
    "product_id TEXT REFERENCES products(id) ON DELETE SET NULL, "
    "shopping_item_id TEXT REFERENCES shopping_items(id) ON DELETE SET NULL, "
    "list_id TEXT REFERENCES lists(id) ON DELETE SET NULL, "
    "product_name TEXT NOT NULL, "
    "search_text TEXT NOT NULL, "
    "category TEXT NOT NULL DEFAULT '', "
    "bought_on TEXT NOT NULL, "
    "quantity TEXT, "
    "unit TEXT NOT NULL DEFAULT '', "
    "brand TEXT NOT NULL DEFAULT '', "
    "store TEXT NOT NULL DEFAULT '', "
    "price TEXT, "
    "currency TEXT NOT NULL DEFAULT '', "
    "notes TEXT NOT NULL DEFAULT '', "
    "expected_next_on TEXT, "
    "finished_on TEXT, "
    "actual_duration_days INTEGER CHECK(actual_duration_days IS NULL OR actual_duration_days >= 1), "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL"
    ")",
    "CREATE TABLE mutation_requests ("
    "request_key TEXT PRIMARY KEY, "
    "action TEXT NOT NULL, "
    "request_hash TEXT NOT NULL, "
    "response TEXT NOT NULL, "
    "created_at TEXT NOT NULL"
    ")",
    "CREATE INDEX products_list ON products(default_list_id)",
    "CREATE INDEX items_list_status ON shopping_items(list_id, status, target_date, updated_at DESC, id)",
    "CREATE INDEX items_status_target ON shopping_items(status, target_date, updated_at DESC, id)",
    "CREATE INDEX items_product ON shopping_items(product_id, status)",
    "CREATE INDEX purchases_date ON purchases(bought_on DESC, created_at DESC, id)",
    "CREATE INDEX purchases_product_date ON purchases(product_id, bought_on DESC, created_at DESC, id)",
    "CREATE INDEX purchases_store_date ON purchases(store, bought_on DESC, id)",
)

MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def validate_snapshot(data: Any) -> dict[str, Any]:
    """Validate an exported Shopping Magic snapshot as inert data without inferring or repairing."""
    def check_fields(obj: Any, expected: set[str]) -> None:
        if not isinstance(obj, dict) or set(obj) != expected:
            raise StoreError("invalid_import_fields")

    def check_str(value: Any, maximum: int = 2000, empty: bool = False) -> str:
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
            raise StoreError("invalid_import_string")
        return value.strip()

    def check_stamp(value: Any) -> None:
        raw = check_str(value, 100)
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError:
            raise StoreError("invalid_import_timestamp") from None

    def check_date(value: Any, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        raw = check_str(value, 10)
        try:
            if date.fromisoformat(raw).isoformat() != raw:
                raise ValueError
        except ValueError:
            raise StoreError("invalid_import_date") from None

    def check_enum(value: Any, allowed: tuple[str, ...]) -> None:
        if not isinstance(value, str) or value not in allowed:
            raise StoreError("invalid_import_enum")

    def check_ref(value: Any, valid_ids: set[str], nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if not isinstance(value, str) or value not in valid_ids:
            raise StoreError("invalid_import_reference")

    def check_bool(value: Any) -> None:
        if type(value) is not bool:
            raise StoreError("invalid_import_boolean")

    def check_int(value: Any, minimum: int = 1, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if type(value) is not int or value < minimum:
            raise StoreError("invalid_import_integer")

    def check_dec(value: Any, *, allow_zero: bool = False, nullable: bool = True) -> None:
        if value is None and nullable:
            return
        if not isinstance(value, str):
            raise StoreError("invalid_import_decimal")
        parse_decimal(value, "import_decimal", allow_zero=allow_zero)

    check_fields(data, {"schema_version", *EXPORT_TABLES})
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise StoreError("unsupported_import_schema")

    shapes = {
        "lists": {"id", "name", "kind", "notes", "archived", "created_at", "updated_at"},
        "products": {
            "id", "name", "aliases", "default_list_id", "default_category", "default_unit",
            "default_brand", "default_store", "notes", "replenishment_mode",
            "replenishment_value", "replenishment_note", "replenishment_source",
            "revision", "created_at", "updated_at",
        },
        "category_preferences": {"id", "topic", "list_id", "category", "notes", "updated_at"},
        "shopping_items": {
            "id", "list_id", "product_id", "name", "category", "quantity", "remaining_quantity",
            "unit", "brand", "preferred_store", "notes", "priority", "status", "target_date",
            "recipient", "occasion", "occasion_recurring", "budget_amount", "budget_currency",
            "revision", "created_at", "updated_at", "resolved_at",
        },
        "purchases": {
            "id", "product_id", "shopping_item_id", "list_id", "product_name", "category",
            "bought_on", "quantity", "unit", "brand", "store", "price", "currency", "notes",
            "expected_next_on", "finished_on", "actual_duration_days", "created_at", "updated_at",
        },
    }

    ids: dict[str, set[str]] = {}
    for table in EXPORT_TABLES:
        records = data[table]
        if not isinstance(records, list) or len(records) > 10000:
            raise StoreError("invalid_import_records")
        ids[table] = set()
        for rec in records:
            check_fields(rec, shapes[table])
            ident = check_str(rec["id"], 200)
            if ident != rec["id"] or ident in ids[table]:
                raise StoreError("invalid_import_id")
            ids[table].add(ident)
            for ts_key in ("created_at", "updated_at"):
                if ts_key in rec:
                    check_stamp(rec[ts_key])

    list_names: set[str] = set()
    for row in data["lists"]:
        name = check_str(row["name"], 200)
        norm = normalized(name)
        if norm in list_names:
            raise StoreError("invalid_import_duplicate_list")
        list_names.add(norm)
        check_enum(row["kind"], LIST_KINDS)
        check_str(row["notes"], 4000, empty=True)
        check_bool(row["archived"])

    product_names: set[str] = set()
    for row in data["products"]:
        name = check_str(row["name"], 500)
        norm = normalized(name)
        if norm in product_names:
            raise StoreError("invalid_import_duplicate_product")
        product_names.add(norm)
        validate_aliases(row["aliases"])
        check_ref(row["default_list_id"], ids["lists"], nullable=True)
        for field_key in ("default_category", "default_unit", "default_brand", "default_store"):
            check_str(row[field_key], 200, empty=True)
        check_str(row["notes"], 4000, empty=True)
        check_enum(row["replenishment_mode"], REPLENISHMENT_MODES)
        check_enum(row["replenishment_source"], REPLENISHMENT_SOURCES)
        if row["replenishment_mode"] == "none":
            if row["replenishment_value"] is not None:
                raise StoreError("invalid_import_replenishment")
        else:
            check_int(row["replenishment_value"], minimum=1, nullable=False)
        check_str(row["replenishment_note"], 2000, empty=True)
        check_int(row["revision"], minimum=1)

    topics: set[str] = set()
    for row in data["category_preferences"]:
        topic = check_str(row["topic"], 200)
        norm = normalized(topic)
        if norm in topics:
            raise StoreError("invalid_import_duplicate_topic")
        topics.add(norm)
        check_ref(row["list_id"], ids["lists"])
        check_str(row["category"], 200, empty=True)
        check_str(row["notes"], 4000, empty=True)

    for row in data["shopping_items"]:
        check_ref(row["list_id"], ids["lists"])
        check_ref(row["product_id"], ids["products"], nullable=True)
        check_str(row["name"], 500)
        for field_key in ("category", "unit", "brand", "preferred_store", "recipient", "occasion"):
            check_str(row[field_key], 200, empty=True)
        check_str(row["budget_currency"], 20, empty=True)
        check_str(row["notes"], 4000, empty=True)
        check_dec(row["quantity"], allow_zero=False, nullable=True)
        if row["quantity"] is None:
            if row["remaining_quantity"] is not None:
                raise StoreError("invalid_import_quantity")
        else:
            check_dec(row["remaining_quantity"], allow_zero=True, nullable=False)
        check_dec(row["budget_amount"], allow_zero=False, nullable=True)
        check_enum(row["priority"], PRIORITIES)
        check_enum(row["status"], ITEM_ALL_STATUSES)
        check_date(row["target_date"], nullable=True)
        check_bool(row["occasion_recurring"])
        check_int(row["revision"], minimum=1)
        if row["resolved_at"] is not None:
            check_stamp(row["resolved_at"])

    for row in data["purchases"]:
        check_ref(row["product_id"], ids["products"], nullable=True)
        check_ref(row["shopping_item_id"], ids["shopping_items"], nullable=True)
        check_ref(row["list_id"], ids["lists"], nullable=True)
        check_str(row["product_name"], 500)
        for field_key in ("category", "unit", "brand", "store"):
            check_str(row[field_key], 200, empty=True)
        check_str(row["currency"], 20, empty=True)
        check_str(row["notes"], 4000, empty=True)
        check_date(row["bought_on"], nullable=False)
        check_dec(row["quantity"], allow_zero=False, nullable=True)
        check_dec(row["price"], allow_zero=True, nullable=True)
        check_date(row["expected_next_on"], nullable=True)
        check_date(row["finished_on"], nullable=True)
        check_int(row["actual_duration_days"], minimum=1, nullable=True)

    return data


def _validate_existing_database(path: Path) -> None:
    """Verify an existing SQLite file is a valid Shopping Magic store before trusting it."""
    checked_path(path)
    if not path.is_file() or not _valid_sqlite_header(path):
        raise StoreError("unrecognized_database")
    try:
        with closing(sqlite3.connect(sqlite_uri(path, "ro"), uri=True, timeout=10)) as ro:
            version = ro.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in ro.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version == 0 or not (CORE_TABLES <= tables):
                raise StoreError("unrecognized_database")
            if version > SCHEMA_VERSION:
                raise StoreError("unsupported_schema_version")
            if ro.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise StoreError("invalid_database")
            if ro.execute("PRAGMA foreign_key_check").fetchone():
                raise StoreError("invalid_database")
    except sqlite3.DatabaseError as exc:
        if not isinstance(exc, sqlite3.OperationalError):
            raise StoreError("invalid_database") from None
        raise


def prepare_shared_profile(target: Path) -> None:
    """Safely initialize or verify the Windows shared default profile without reset or replacement."""
    target = checked_path(target)
    marker = checked_path(target.parent / ".origins.json")
    origins = read_origins(marker)

    if origins is not None and origins["state"] == "ready" and target.is_file():
        info = target.stat()
        if origins.get("database_identity") == [info.st_dev, info.st_ino] and _valid_sqlite_header(target):
            return
        if origins.get("database_identity") != [info.st_dev, info.st_ino]:
            raise StoreError("shared_profile_identity_changed")
        raise StoreError("unrecognized_database")

    if origins is not None and origins["state"] == "ready" and not target.exists():
        raise StoreError("shared_profile_missing")

    if target.exists():
        if not target.is_file():
            raise StoreError("unrecognized_database")
        _validate_existing_database(target)
        info = target.stat()
        if origins is not None and origins.get("database_identity") is not None:
            if origins["database_identity"] != [info.st_dev, info.st_ino]:
                raise StoreError("shared_profile_identity_changed")
        record = {
            "schema": 1,
            "profile_id": origins["profile_id"] if origins else uuid.uuid4().hex,
            "state": "ready",
            "database_identity": [info.st_dev, info.st_ino],
        }
        write_origins(marker, record)
        return

    profile_id = origins["profile_id"] if origins else uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".shopping-magic-init-", dir=target.parent) as temporary:
        stage_dir = Path(temporary)
        stage = stage_dir / "shopping-magic.sqlite3"
        candidate = Store(stage_dir)
        con = candidate._connect(use_wal=False)
        try:
            con.execute("COMMIT")
        finally:
            con.close()
        with stage.open("r+b") as staged:
            os.fsync(staged.fileno())
        info = stage.stat()
        record = {
            "schema": 1,
            "profile_id": profile_id,
            "state": "publishing",
            "database_identity": [info.st_dev, info.st_ino],
        }
        write_origins(marker, record)
        os.link(stage, target)
        record["state"] = "ready"
        write_origins(marker, record)


class Store:
    """Single-user local SQLite store shared across compatible AI agent hosts."""

    def __init__(self, data_dir: Path | str | None = None):
        self.shared_windows = (
            data_dir is None
            and not os.environ.get("SHOPPING_MAGIC_DATA_DIR")
            and sys.platform == "win32"
        )
        selected = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.data_dir = (
            checked_path(selected)
            if (self.shared_windows or sys.platform == "win32")
            else selected.resolve()
        )
        install = Path(__file__).resolve().parents[1]
        if self.data_dir == install or install in self.data_dir.parents:
            raise StoreError("data_directory_inside_skill")
        self.path = self.data_dir / "shopping-magic.sqlite3"

    def _storage_info(self) -> dict[str, Any]:
        """Return read-only storage diagnostics without creating files or directories."""
        checked_path(self.data_dir)
        checked_path(self.path)
        marker = self.data_dir / ".origins.json"
        profile_id = None
        if marker.exists():
            origins = read_origins(marker)
            if origins:
                profile_id = origins["profile_id"]
        counts = None
        exists = self.path.is_file()
        if exists and _valid_sqlite_header(self.path):
            try:
                with closing(sqlite3.connect(sqlite_uri(self.path, "ro"), uri=True, timeout=5)) as ro:
                    tables = {r[0] for r in ro.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if CORE_TABLES <= tables:
                        counts = {
                            table: ro.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                            for table in EXPORT_TABLES
                        }
            except sqlite3.Error:
                counts = None
        return {
            "directory": str(self.data_dir),
            "database": str(self.path),
            "physical_database": physical_path(self.path),
            "exists": exists,
            "selection": "shared_windows_default" if self.shared_windows else "explicit_or_platform_default",
            "profile_id": profile_id,
            "counts": counts,
        }

    @staticmethod
    def _run_migrations(con: sqlite3.Connection, version: int) -> int:
        while version < SCHEMA_VERSION:
            migrator = MIGRATIONS.get(version)
            if migrator is None:
                raise StoreError("unsupported_schema_version")
            migrator(con)
            version += 1
            con.execute(f"PRAGMA user_version = {int(version)}")
        return version

    def _connect(self, *, use_wal: bool = True) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        checked_path(self.path)
        if self.path.exists():
            if not self.path.is_file() or not _valid_sqlite_header(self.path):
                raise StoreError("unrecognized_database")
        con = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout = 10000")
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise StoreError("unrecognized_database")
                for statement in SCHEMA:
                    con.execute(statement)
                con.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
                version = SCHEMA_VERSION
                initialized_here = True
            else:
                initialized_here = False
                if version > SCHEMA_VERSION:
                    raise StoreError("unsupported_schema_version")
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not (CORE_TABLES <= tables):
                    raise StoreError("unrecognized_database")

            if use_wal:
                mode_row = con.execute("PRAGMA journal_mode").fetchone()
                if mode_row and str(mode_row[0]).lower() != "wal":
                    if not initialized_here:
                        qc = [r[0] for r in con.execute("PRAGMA quick_check").fetchall()]
                        if qc != ["ok"]:
                            raise StoreError("invalid_database")
                        if con.execute("PRAGMA foreign_key_check").fetchone():
                            raise StoreError("invalid_database")
                    # Persist initial schema or release validation lock before enabling WAL,
                    # since SQLite forbids changing journal_mode inside an active transaction.
                    con.execute("COMMIT" if initialized_here else "ROLLBACK")
                    con.execute("PRAGMA journal_mode = WAL")
                    con.execute("BEGIN IMMEDIATE")
                    version = con.execute("PRAGMA user_version").fetchone()[0]
                    if version > SCHEMA_VERSION:
                        raise StoreError("unsupported_schema_version")
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if version == 0 or not (CORE_TABLES <= tables):
                        raise StoreError("unrecognized_database")

            version = self._run_migrations(con, version)
            if version != SCHEMA_VERSION:
                raise StoreError("unsupported_schema_version")
            return con
        except sqlite3.DatabaseError as exc:
            try:
                if con.in_transaction:
                    con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            con.close()
            if not isinstance(exc, sqlite3.OperationalError):
                raise StoreError("invalid_database") from None
            raise
        except Exception:
            try:
                if con.in_transaction:
                    con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            con.close()
            raise

    def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute a single explicit structured JSON request."""
        actions: dict[str, tuple[Callable[[sqlite3.Connection, dict[str, Any]], Any] | None, set[str]]] = {
            "storage-info": (None, set()),
            "context": (self._context, {"as_of", "horizon_days", "recent_purchases_limit"}),
            "list-save": (self._list_save, {"id", "name", "kind", "notes", "archived"}),
            "list-list": (self._list_list, {"include_archived"}),
            "product-save": (
                self._product_save,
                {
                    "id", "name", "aliases", "default_list_id", "default_list_name",
                    "default_list_kind", "default_category", "default_unit",
                    "default_brand", "default_store", "notes", "expected_revision",
                },
            ),
            "product-search": (self._product_search, {"query", "list_id", "limit", "offset"}),
            "category-pref-set": (
                self._category_pref_set,
                {"topic", "list_id", "list_name", "list_kind", "category", "notes"},
            ),
            "category-pref-remove": (self._category_pref_remove, {"id"}),
            "item-add": (
                self._item_add,
                {
                    "request_key", "name", "list_id", "list_name", "list_kind", "product_id",
                    "category", "quantity", "unit", "brand", "preferred_store", "notes",
                    "priority", "status", "target_date", "recipient", "occasion",
                    "occasion_recurring", "budget_amount", "budget_currency",
                },
            ),
            "item-get": (self._item_get, {"id"}),
            "item-list": (
                self._item_list,
                {
                    "list_id", "list_name", "list_kind", "category", "status",
                    "recipient", "product_id", "due_before", "due_after",
                    "gifts_only", "query", "limit", "offset",
                },
            ),
            "item-update": (
                self._item_update,
                {
                    "id", "expected_revision", "request_key", "name", "list_id", "list_name",
                    "list_kind", "category", "quantity", "remaining_quantity", "unit", "brand",
                    "preferred_store", "notes", "priority", "status", "target_date",
                    "recipient", "occasion", "occasion_recurring", "budget_amount",
                    "budget_currency", "remember_list_preference",
                },
            ),
            "item-remove": (self._item_remove, {"id", "request_key", "expected_revision"}),
            "list-clear": (self._list_clear, {"list_id", "list_name", "statuses", "confirm", "request_key"}),
            "purchase-log": (
                self._purchase_log,
                {
                    "request_key", "bought_on", "item_id", "expected_item_revision",
                    "product_id", "product_name", "list_id", "list_name", "list_kind",
                    "category", "quantity", "unit", "brand", "store", "price",
                    "currency", "notes", "fulfillment_mode", "expected_next_on",
                    "replenishment_days", "replenishment_months", "replenishment_note",
                },
            ),
            "purchase-update": (
                self._purchase_update,
                {
                    "id", "request_key", "bought_on", "quantity", "unit", "brand",
                    "store", "price", "currency", "notes", "expected_next_on",
                    "finished_on", "actual_duration_days",
                },
            ),
            "purchase-list": (
                self._purchase_list,
                {"product_id", "query", "store", "list_id", "list_name", "category", "on", "from_date", "to_date", "limit", "offset"},
            ),
            "purchase-remove": (self._purchase_remove, {"id", "request_key", "confirm"}),
            "spending-summary": (
                self._spending_summary,
                {"store", "list_id", "list_name", "category", "product_id", "on", "from_date", "to_date"},
            ),
            "replenishment-set": (
                self._replenishment_set,
                {"product_id", "product_name", "mode", "value", "note", "source", "request_key"},
            ),
            "replenishment-suggestions": (
                self._replenishment_suggestions,
                {"as_of", "horizon_days", "product_id", "query", "list_id", "list_name", "include_all"},
            ),
            "export": (self._export, set()),
            "import": (self._import, {"data", "request_key"}),
        }
        if not isinstance(req, dict) or not isinstance(req.get("action"), str):
            raise StoreError("request_must_have_action")
        action = req["action"]
        if action not in actions:
            raise StoreError("unknown_action")
        fn, allowed = actions[action]
        if set(req) - allowed - {"action"}:
            raise StoreError("unknown_fields")
        if action == "storage-info":
            return {"ok": True, "result": self._storage_info()}
        if self.shared_windows:
            checked_path(self.data_dir)
            with profile_lock(self.data_dir):
                prepare_shared_profile(self.path)
                return self._execute(fn, req)
        return self._execute(fn, req)

    def _execute(self, fn: Callable[[sqlite3.Connection, dict[str, Any]], Any], req: dict[str, Any]) -> dict[str, Any]:
        con = self._connect(use_wal=True)
        try:
            result = fn(con, req)
            con.execute("COMMIT")
            output: dict[str, Any] = {"ok": True, "result": result}
            if req["action"] == "context":
                output["storage"] = self._storage_info()
            return output
        except Exception:
            try:
                if con.in_transaction:
                    con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Row hydration & search text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _product_search_text(name: str, aliases: list[str], brand: str, category: str, notes: str) -> str:
        return normalized(" ".join([name, *aliases, brand, category, notes]))

    @staticmethod
    def _item_search_text(
        name: str,
        category: str,
        brand: str,
        store: str,
        recipient: str,
        occasion: str,
        notes: str,
    ) -> str:
        return normalized(" ".join([name, category, brand, store, recipient, occasion, notes]))

    @staticmethod
    def _purchase_search_text(
        product_name: str,
        category: str,
        brand: str,
        store: str,
        notes: str,
    ) -> str:
        return normalized(" ".join([product_name, category, brand, store, notes]))

    @staticmethod
    def _hydrate_list(row: sqlite3.Row, open_count: int = 0, total_count: int | None = None) -> dict[str, Any]:
        item = {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "notes": row["notes"],
            "archived": bool(row["archived"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "open_count": open_count,
        }
        if total_count is not None:
            item["total_count"] = total_count
        return item

    @staticmethod
    def _hydrate_product(row: sqlite3.Row, list_name: str | None = None) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "aliases": json.loads(row["aliases"]),
            "default_list_id": row["default_list_id"],
            "default_list_name": list_name,
            "default_category": row["default_category"],
            "default_unit": row["default_unit"],
            "default_brand": row["default_brand"],
            "default_store": row["default_store"],
            "notes": row["notes"],
            "replenishment_mode": row["replenishment_mode"],
            "replenishment_value": row["replenishment_value"],
            "replenishment_note": row["replenishment_note"],
            "replenishment_source": row["replenishment_source"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _hydrate_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "list_id": row["list_id"],
            "list_name": row["list_name"],
            "list_kind": row["list_kind"],
            "product_id": row["product_id"],
            "name": row["name"],
            "category": row["category"],
            "quantity": row["quantity"],
            "remaining_quantity": row["remaining_quantity"],
            "unit": row["unit"],
            "brand": row["brand"],
            "preferred_store": row["preferred_store"],
            "notes": row["notes"],
            "priority": row["priority"],
            "status": row["status"],
            "target_date": row["target_date"],
            "recipient": row["recipient"],
            "occasion": row["occasion"],
            "occasion_recurring": bool(row["occasion_recurring"]),
            "budget_amount": row["budget_amount"],
            "budget_currency": row["budget_currency"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
        }

    @staticmethod
    def _hydrate_purchase(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "shopping_item_id": row["shopping_item_id"],
            "list_id": row["list_id"],
            "list_name": row["list_name"],
            "product_name": row["product_name"],
            "category": row["category"],
            "bought_on": row["bought_on"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "brand": row["brand"],
            "store": row["store"],
            "price": row["price"],
            "currency": row["currency"],
            "notes": row["notes"],
            "expected_next_on": row["expected_next_on"],
            "finished_on": row["finished_on"],
            "actual_duration_days": row["actual_duration_days"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------
    # Idempotency receipts
    # ------------------------------------------------------------------

    @staticmethod
    def _replay_receipt(
        con: sqlite3.Connection,
        action: str,
        key: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        req_hash = hashlib.sha256(canonical_payload({"action": action, "payload": payload}).encode("utf-8")).hexdigest()
        previous = con.execute(
            "SELECT action, request_hash, response FROM mutation_requests WHERE request_key=?",
            (key,),
        ).fetchone()
        if previous:
            if previous["action"] != action or previous["request_hash"] != req_hash:
                raise StoreError("request_key_conflict")
            return req_hash, json.loads(previous["response"])
        return req_hash, None

    @staticmethod
    def _record_receipt(
        con: sqlite3.Connection,
        key: str,
        action: str,
        req_hash: str,
        response: Any,
    ) -> Any:
        con.execute(
            "INSERT INTO mutation_requests (request_key, action, request_hash, response, created_at) "
            "VALUES (?,?,?,?,?)",
            (key, action, req_hash, json.dumps(response, ensure_ascii=False), now()),
        )
        return response

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_or_create_list(
        con: sqlite3.Connection,
        name: str,
        *,
        kind: str = "custom",
        notes: str = "",
    ) -> sqlite3.Row:
        if kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        norm = normalized(name)
        existing = con.execute("SELECT * FROM lists WHERE normalized_name=?", (norm,)).fetchone()
        if existing:
            return existing
        ident = uuid.uuid4().hex
        stamp = now()
        con.execute(
            "INSERT INTO lists (id, name, normalized_name, kind, notes, archived, created_at, updated_at) "
            "VALUES (?,?,?,?,?,0,?,?)",
            (ident, name.strip(), norm, kind, notes.strip(), stamp, stamp),
        )
        return con.execute("SELECT * FROM lists WHERE id=?", (ident,)).fetchone()

    def _resolve_list(
        self,
        con: sqlite3.Connection,
        *,
        list_id: str | None = None,
        list_name: str | None = None,
        list_kind: str = "custom",
        create_missing_name: bool = True,
    ) -> sqlite3.Row | None:
        by_id = None
        if list_id is not None:
            by_id = con.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
            if not by_id:
                raise StoreError("list_not_found")
        if list_name is not None:
            norm = normalized(list_name)
            by_name = con.execute("SELECT * FROM lists WHERE normalized_name=?", (norm,)).fetchone()
            if by_id is not None:
                if by_name is not None and by_name["id"] != by_id["id"]:
                    raise StoreError("invalid_list_reference")
                if by_name is None and by_id["normalized_name"] != norm:
                    raise StoreError("invalid_list_reference")
                return by_id
            if by_name is not None:
                return by_name
            if not create_missing_name:
                return None
            return con.execute(
                "SELECT * FROM lists WHERE id=?",
                (self._get_or_create_list(con, list_name, kind=list_kind)["id"],),
            ).fetchone()
        return by_id

    @staticmethod
    def _find_product_by_name_or_alias(con: sqlite3.Connection, name: str) -> sqlite3.Row | None:
        norm = normalized(name)
        row = con.execute("SELECT * FROM products WHERE normalized_name=?", (norm,)).fetchone()
        if row:
            return row
        for candidate in con.execute("SELECT * FROM products ORDER BY created_at ASC, id"):
            aliases = json.loads(candidate["aliases"])
            if any(normalized(alias) == norm for alias in aliases):
                return candidate
        return None

    def _get_or_create_product(
        self,
        con: sqlite3.Connection,
        name: str,
        *,
        default_list_id: str | None = None,
        default_category: str = "",
        default_unit: str = "",
        default_brand: str = "",
        default_store: str = "",
    ) -> sqlite3.Row:
        existing = self._find_product_by_name_or_alias(con, name)
        if existing:
            return existing
        ident = uuid.uuid4().hex
        stamp = now()
        clean_name = name.strip()
        norm = normalized(clean_name)
        search_text = self._product_search_text(
            clean_name, [], default_brand, default_category, ""
        )
        con.execute(
            "INSERT INTO products (id, name, normalized_name, aliases, search_text, "
            "default_list_id, default_category, default_unit, default_brand, default_store, "
            "notes, replenishment_mode, replenishment_value, replenishment_note, replenishment_source, "
            "revision, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'','none',NULL,'','none',1,?,?)",
            (
                ident, clean_name, norm, "[]", search_text,
                default_list_id, default_category, default_unit, default_brand, default_store,
                stamp, stamp,
            ),
        )
        return con.execute("SELECT * FROM products WHERE id=?", (ident,)).fetchone()

    def _fetch_item_row(self, con: sqlite3.Connection, ident: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT i.*, l.name AS list_name, l.kind AS list_kind "
            "FROM shopping_items i JOIN lists l ON l.id = i.list_id WHERE i.id=?",
            (ident,),
        ).fetchone()
        if not row:
            raise StoreError("item_not_found")
        return row

    def _fetch_purchase_row(self, con: sqlite3.Connection, ident: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT p.*, l.name AS list_name "
            "FROM purchases p LEFT JOIN lists l ON l.id = p.list_id WHERE p.id=?",
            (ident,),
        ).fetchone()
        if not row:
            raise StoreError("purchase_not_found")
        return row

    def _fetch_product_hydrated(self, con: sqlite3.Connection, ident: str) -> dict[str, Any]:
        row = con.execute(
            "SELECT p.*, l.name AS default_list_name "
            "FROM products p LEFT JOIN lists l ON l.id = p.default_list_id WHERE p.id=?",
            (ident,),
        ).fetchone()
        if not row:
            raise StoreError("product_not_found")
        return self._hydrate_product(row, row["default_list_name"])

    # ------------------------------------------------------------------
    # Lists, Products, Category Preferences
    # ------------------------------------------------------------------

    def _list_save(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        name = text_field(req, "name", limit=200)
        norm = normalized(name)
        previous = None
        if "id" in req:
            ident = text_field(req, "id", limit=200)
            previous = con.execute("SELECT * FROM lists WHERE id=?", (ident,)).fetchone()
            if not previous:
                raise StoreError("list_not_found")
            collision = con.execute(
                "SELECT id FROM lists WHERE normalized_name=? AND id<>?",
                (norm, ident),
            ).fetchone()
            if collision:
                raise StoreError("list_name_in_use")
        else:
            previous = con.execute("SELECT * FROM lists WHERE normalized_name=?", (norm,)).fetchone()
            ident = previous["id"] if previous else uuid.uuid4().hex

        kind = req.get("kind", previous["kind"] if previous else "custom")
        if kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        notes = optional_text(req, "notes", previous["notes"] if previous else "", limit=4000)
        archived = boolean_field(req, "archived", bool(previous["archived"]) if previous else False)
        stamp = now()
        if previous:
            con.execute(
                "UPDATE lists SET name=?, normalized_name=?, kind=?, notes=?, archived=?, updated_at=? "
                "WHERE id=?",
                (name, norm, kind, notes, int(archived), stamp, ident),
            )
        else:
            con.execute(
                "INSERT INTO lists (id, name, normalized_name, kind, notes, archived, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ident, name, norm, kind, notes, int(archived), stamp, stamp),
            )
        row = con.execute("SELECT * FROM lists WHERE id=?", (ident,)).fetchone()
        open_count = con.execute(
            "SELECT count(*) FROM shopping_items WHERE list_id=? AND status IN ('idea','planned','needed','partially_bought')",
            (ident,),
        ).fetchone()[0]
        total_count = con.execute("SELECT count(*) FROM shopping_items WHERE list_id=?", (ident,)).fetchone()[0]
        return self._hydrate_list(row, open_count=open_count, total_count=total_count)

    def _list_list(self, con: sqlite3.Connection, req: dict[str, Any]) -> list[dict[str, Any]]:
        include_archived = boolean_field(req, "include_archived", False)
        where = "" if include_archived else "WHERE l.archived = 0 "
        rows = con.execute(
            "SELECT l.*, "
            "SUM(CASE WHEN i.status IN ('idea','planned','needed','partially_bought') THEN 1 ELSE 0 END) AS open_count, "
            "COUNT(i.id) AS total_count "
            f"FROM lists l LEFT JOIN shopping_items i ON i.list_id = l.id {where}"
            "GROUP BY l.id ORDER BY l.archived ASC, l.name COLLATE NOCASE ASC, l.id"
        ).fetchall()
        return [
            self._hydrate_list(r, open_count=r["open_count"] or 0, total_count=r["total_count"] or 0)
            for r in rows
        ]

    def _product_save(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        name = text_field(req, "name", limit=500)
        norm = normalized(name)
        previous = None
        if "id" in req:
            ident = text_field(req, "id", limit=200)
            previous = con.execute("SELECT * FROM products WHERE id=?", (ident,)).fetchone()
            if not previous:
                raise StoreError("product_not_found")
            if "expected_revision" in req:
                if int_field(req, "expected_revision", minimum=1) != previous["revision"]:
                    raise StoreError("revision_conflict")
            if con.execute("SELECT id FROM products WHERE normalized_name=? AND id<>?", (norm, ident)).fetchone():
                raise StoreError("product_name_in_use")
        else:
            if "expected_revision" in req:
                raise StoreError("revision_requires_product_id")
            previous = con.execute("SELECT * FROM products WHERE normalized_name=?", (norm,)).fetchone()
            ident = previous["id"] if previous else uuid.uuid4().hex

        aliases = (
            validate_aliases(req["aliases"])
            if "aliases" in req
            else (json.loads(previous["aliases"]) if previous else [])
        )
        list_kind = req.get("default_list_kind", "custom")
        if list_kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        default_list_id = previous["default_list_id"] if previous else None
        if "default_list_id" in req or "default_list_name" in req:
            if req.get("default_list_id") is None and "default_list_name" not in req:
                default_list_id = None
            else:
                req_lid = text_field(req, "default_list_id", limit=200) if req.get("default_list_id") is not None else None
                req_lname = text_field(req, "default_list_name", limit=200) if req.get("default_list_name") is not None else None
                resolved = self._resolve_list(
                    con,
                    list_id=req_lid,
                    list_name=req_lname,
                    list_kind=list_kind,
                    create_missing_name=True,
                )
                default_list_id = resolved["id"] if resolved else None

        default_category = optional_text(req, "default_category", previous["default_category"] if previous else "", limit=200)
        default_unit = optional_text(req, "default_unit", previous["default_unit"] if previous else "", limit=200)
        default_brand = optional_text(req, "default_brand", previous["default_brand"] if previous else "", limit=200)
        default_store = optional_text(req, "default_store", previous["default_store"] if previous else "", limit=200)
        notes = optional_text(req, "notes", previous["notes"] if previous else "", limit=4000)
        search_text = self._product_search_text(name, aliases, default_brand, default_category, notes)
        stamp = now()
        if previous:
            con.execute(
                "UPDATE products SET name=?, normalized_name=?, aliases=?, search_text=?, "
                "default_list_id=?, default_category=?, default_unit=?, default_brand=?, default_store=?, "
                "notes=?, revision=revision+1, updated_at=? WHERE id=?",
                (
                    name, norm, json.dumps(aliases, ensure_ascii=False), search_text,
                    default_list_id, default_category, default_unit, default_brand, default_store,
                    notes, stamp, ident,
                ),
            )
        else:
            con.execute(
                "INSERT INTO products (id, name, normalized_name, aliases, search_text, "
                "default_list_id, default_category, default_unit, default_brand, default_store, "
                "notes, replenishment_mode, replenishment_value, replenishment_note, replenishment_source, "
                "revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'none',NULL,'','none',1,?,?)",
                (
                    ident, name, norm, json.dumps(aliases, ensure_ascii=False), search_text,
                    default_list_id, default_category, default_unit, default_brand, default_store,
                    notes, stamp, stamp,
                ),
            )
        return self._fetch_product_hydrated(con, ident)

    def _product_search(self, con: sqlite3.Connection, req: dict[str, Any]) -> list[dict[str, Any]]:
        query = req.get("query", "")
        if not isinstance(query, str) or len(query) > 2000:
            raise StoreError("invalid_query")
        limit = limit_field(req, default=20)
        offset = offset_field(req)
        clauses: list[str] = []
        params: list[Any] = []
        if "list_id" in req and req["list_id"] is not None:
            lid = text_field(req, "list_id", limit=200)
            clauses.append("p.default_list_id = ?")
            params.append(lid)
        for term in normalized(query).split():
            clauses.append("INSTR(p.search_text, ?) > 0")
            params.append(term)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        sql = (
            "SELECT p.*, l.name AS default_list_name "
            f"FROM products p LEFT JOIN lists l ON l.id = p.default_list_id {where}"
            "ORDER BY p.name COLLATE NOCASE ASC, p.id LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        return [
            self._hydrate_product(r, r["default_list_name"])
            for r in con.execute(sql, params).fetchall()
        ]

    def _category_pref_set(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        topic = text_field(req, "topic", limit=200)
        norm_topic = normalized(topic)
        if "list_id" not in req and "list_name" not in req:
            raise StoreError("list_required")
        list_kind = req.get("list_kind", "custom")
        if list_kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        req_lid = text_field(req, "list_id", limit=200) if req.get("list_id") is not None else None
        req_lname = text_field(req, "list_name", limit=200) if req.get("list_name") is not None else None
        resolved_list = self._resolve_list(
            con,
            list_id=req_lid,
            list_name=req_lname,
            list_kind=list_kind,
            create_missing_name=True,
        )
        if resolved_list is None:
            raise StoreError("list_required")
        existing = con.execute("SELECT * FROM category_preferences WHERE normalized_topic=?", (norm_topic,)).fetchone()
        ident = existing["id"] if existing else uuid.uuid4().hex
        category = optional_text(req, "category", existing["category"] if existing else "", limit=200)
        notes = optional_text(req, "notes", existing["notes"] if existing else "", limit=4000)
        stamp = now()
        if existing:
            con.execute(
                "UPDATE category_preferences SET topic=?, list_id=?, category=?, notes=?, updated_at=? WHERE id=?",
                (topic, resolved_list["id"], category, notes, stamp, ident),
            )
        else:
            con.execute(
                "INSERT INTO category_preferences (id, topic, normalized_topic, list_id, category, notes, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (ident, topic, norm_topic, resolved_list["id"], category, notes, stamp),
            )
        row = con.execute(
            "SELECT cp.*, l.name AS list_name, l.kind AS list_kind "
            "FROM category_preferences cp JOIN lists l ON l.id = cp.list_id WHERE cp.id=?",
            (ident,),
        ).fetchone()
        return dict(row)

    def _category_pref_remove(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        ident = text_field(req, "id", limit=200)
        cur = con.execute("DELETE FROM category_preferences WHERE id=?", (ident,))
        if not cur.rowcount:
            raise StoreError("record_not_found")
        return {"id": ident, "removed": True}

    # ------------------------------------------------------------------
    # Shopping Items
    # ------------------------------------------------------------------

    def _item_add(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        key = text_field(req, "request_key", limit=200)
        name = text_field(req, "name", limit=500)
        list_kind = req.get("list_kind", "custom")
        if list_kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        priority = req.get("priority", "normal")
        if priority not in PRIORITIES:
            raise StoreError("invalid_priority")
        status = req.get("status", "needed")
        if status not in ITEM_ADD_STATUSES:
            raise StoreError("invalid_item_status")
        quantity = optional_decimal(req, "quantity", None, allow_zero=False)
        unit = optional_text(req, "unit", "", limit=200)
        category = optional_text(req, "category", "", limit=200)
        brand = optional_text(req, "brand", "", limit=200)
        preferred_store = optional_text(req, "preferred_store", "", limit=200)
        notes = optional_text(req, "notes", "", limit=4000)
        target_date = optional_date_field(req, "target_date", None)
        recipient = optional_text(req, "recipient", "", limit=200)
        occasion = optional_text(req, "occasion", "", limit=200)
        occasion_recurring = boolean_field(req, "occasion_recurring", False)
        budget_amount = optional_decimal(req, "budget_amount", None, allow_zero=False)
        budget_currency = optional_text(req, "budget_currency", "", limit=20)
        req_lid = text_field(req, "list_id", limit=200) if req.get("list_id") is not None else None
        req_lname = text_field(req, "list_name", limit=200) if req.get("list_name") is not None else None
        req_pid = text_field(req, "product_id", limit=200) if req.get("product_id") is not None else None

        payload = {
            "name": name,
            "list_id": req_lid,
            "list_name": req_lname,
            "list_kind": list_kind,
            "product_id": req_pid,
            "category": category,
            "quantity": quantity,
            "unit": unit,
            "brand": brand,
            "preferred_store": preferred_store,
            "notes": notes,
            "priority": priority,
            "status": status,
            "target_date": target_date,
            "recipient": recipient,
            "occasion": occasion,
            "occasion_recurring": occasion_recurring,
            "budget_amount": budget_amount,
            "budget_currency": budget_currency,
        }
        req_hash, replayed = self._replay_receipt(con, "item-add", key, payload)
        if replayed is not None:
            return replayed

        product = None
        if req_pid is not None:
            product = con.execute("SELECT * FROM products WHERE id=?", (req_pid,)).fetchone()
            if not product:
                raise StoreError("product_not_found")
        else:
            product = self._find_product_by_name_or_alias(con, name)

        resolved_list = self._resolve_list(
            con,
            list_id=req_lid,
            list_name=req_lname,
            list_kind=list_kind,
            create_missing_name=True,
        )
        if resolved_list is None:
            if product is not None and product["default_list_id"]:
                resolved_list = con.execute("SELECT * FROM lists WHERE id=?", (product["default_list_id"],)).fetchone()
            if resolved_list is None:
                raise StoreError("list_required")

        if product is None:
            product = self._get_or_create_product(
                con,
                name,
                default_list_id=resolved_list["id"],
                default_category=category,
                default_unit=unit,
                default_brand=brand,
                default_store=preferred_store,
            )

        ident = uuid.uuid4().hex
        stamp = now()
        search_text = self._item_search_text(
            name, category, brand, preferred_store, recipient, occasion, notes
        )
        con.execute(
            "INSERT INTO shopping_items ("
            "id, list_id, product_id, name, search_text, category, quantity, remaining_quantity, "
            "unit, brand, preferred_store, notes, priority, status, target_date, recipient, "
            "occasion, occasion_recurring, budget_amount, budget_currency, revision, "
            "created_at, updated_at, resolved_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,NULL)",
            (
                ident, resolved_list["id"], product["id"], name, search_text, category,
                quantity, quantity, unit, brand, preferred_store, notes, priority, status,
                target_date, recipient, occasion, int(occasion_recurring), budget_amount,
                budget_currency, stamp, stamp,
            ),
        )
        saved = self._hydrate_item(self._fetch_item_row(con, ident))
        return self._record_receipt(con, key, "item-add", req_hash, saved)

    def _item_get(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        return self._hydrate_item(self._fetch_item_row(con, text_field(req, "id", limit=200)))

    def _item_list(self, con: sqlite3.Connection, req: dict[str, Any]) -> list[dict[str, Any]]:
        limit = limit_field(req, default=50)
        offset = offset_field(req)
        clauses: list[str] = []
        params: list[Any] = []

        if "list_id" in req and req["list_id"] is not None:
            lid = text_field(req, "list_id", limit=200)
            if not con.execute("SELECT 1 FROM lists WHERE id=?", (lid,)).fetchone():
                raise StoreError("list_not_found")
            clauses.append("i.list_id = ?")
            params.append(lid)
        if "list_name" in req and req["list_name"] is not None:
            resolved = self._resolve_list(
                con,
                list_name=text_field(req, "list_name", limit=200),
                create_missing_name=False,
            )
            if resolved is None:
                return []
            clauses.append("i.list_id = ?")
            params.append(resolved["id"])
        if "list_kind" in req and req["list_kind"] is not None:
            kind = text_field(req, "list_kind", limit=50)
            if kind not in LIST_KINDS:
                raise StoreError("invalid_list_kind")
            clauses.append("l.kind = ?")
            params.append(kind)
        cat_filter = None
        if "category" in req and req["category"] is not None:
            cat_filter = normalized(text_field(req, "category", limit=200))
            clauses.append("i.category <> ''")

        status = req.get("status", "open")
        if status == "open":
            clauses.append("i.status IN ('idea','planned','needed','partially_bought')")
        elif status == "all":
            pass
        elif status in ITEM_ALL_STATUSES:
            clauses.append("i.status = ?")
            params.append(status)
        else:
            raise StoreError("invalid_item_status")

        recipient_filter = None
        if "recipient" in req and req["recipient"] is not None:
            recipient_filter = normalized(text_field(req, "recipient", limit=200))
            clauses.append("i.recipient <> ''")

        if "product_id" in req and req["product_id"] is not None:
            clauses.append("i.product_id = ?")
            params.append(text_field(req, "product_id", limit=200))

        if "due_before" in req and req["due_before"] is not None:
            due_before = date_field(req, "due_before").isoformat()
            clauses.append("i.target_date IS NOT NULL AND i.target_date <= ?")
            params.append(due_before)
        if "due_after" in req and req["due_after"] is not None:
            due_after = date_field(req, "due_after").isoformat()
            clauses.append("i.target_date IS NOT NULL AND i.target_date >= ?")
            params.append(due_after)
        if boolean_field(req, "gifts_only", False):
            clauses.append("(l.kind = 'gifts' OR i.recipient <> '' OR i.occasion <> '')")

        if "query" in req and req["query"] is not None:
            query = req["query"]
            if not isinstance(query, str) or len(query) > 2000:
                raise StoreError("invalid_query")
            for term in normalized(query).split():
                clauses.append("INSTR(i.search_text, ?) > 0")
                params.append(term)

        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        sql = (
            "SELECT i.*, l.name AS list_name, l.kind AS list_kind "
            f"FROM shopping_items i JOIN lists l ON l.id = i.list_id {where}"
            "ORDER BY "
            "CASE i.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
            "CASE WHEN i.target_date IS NULL THEN 1 ELSE 0 END, "
            "i.target_date ASC, i.updated_at DESC, i.id"
        )
        rows = con.execute(sql, params).fetchall()
        items = []
        for row in rows:
            if cat_filter is not None and normalized(row["category"]) != cat_filter:
                continue
            if recipient_filter is not None and recipient_filter not in normalized(row["recipient"]):
                continue
            items.append(self._hydrate_item(row))
        return items[offset:offset + limit]

    def _item_update(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        ident = text_field(req, "id", limit=200)
        expected_revision = int_field(req, "expected_revision", minimum=1)
        key = text_field(req, "request_key", limit=200)

        # Validate supplied field types before receipt lookup.
        payload: dict[str, Any] = {"id": ident, "expected_revision": expected_revision}
        if "name" in req:
            payload["name"] = text_field(req, "name", limit=500)
        if "list_id" in req:
            payload["list_id"] = text_field(req, "list_id", limit=200) if req["list_id"] is not None else None
        if "list_name" in req:
            payload["list_name"] = text_field(req, "list_name", limit=200) if req["list_name"] is not None else None
        if "list_kind" in req:
            lk = req["list_kind"]
            if lk not in LIST_KINDS:
                raise StoreError("invalid_list_kind")
            payload["list_kind"] = lk
        if "category" in req:
            payload["category"] = optional_text(req, "category", limit=200)
        if "quantity" in req:
            payload["quantity"] = optional_decimal(req, "quantity", allow_zero=False)
        if "remaining_quantity" in req:
            payload["remaining_quantity"] = optional_decimal(req, "remaining_quantity", allow_zero=True)
        if "unit" in req:
            payload["unit"] = optional_text(req, "unit", limit=200)
        if "brand" in req:
            payload["brand"] = optional_text(req, "brand", limit=200)
        if "preferred_store" in req:
            payload["preferred_store"] = optional_text(req, "preferred_store", limit=200)
        if "notes" in req:
            payload["notes"] = optional_text(req, "notes", limit=4000)
        if "priority" in req:
            if req["priority"] not in PRIORITIES:
                raise StoreError("invalid_priority")
            payload["priority"] = req["priority"]
        if "status" in req:
            if req["status"] not in ITEM_ALL_STATUSES:
                raise StoreError("invalid_item_status")
            payload["status"] = req["status"]
        if "target_date" in req:
            payload["target_date"] = optional_date_field(req, "target_date")
        if "recipient" in req:
            payload["recipient"] = optional_text(req, "recipient", limit=200)
        if "occasion" in req:
            payload["occasion"] = optional_text(req, "occasion", limit=200)
        if "occasion_recurring" in req:
            payload["occasion_recurring"] = boolean_field(req, "occasion_recurring")
        if "budget_amount" in req:
            payload["budget_amount"] = optional_decimal(req, "budget_amount", allow_zero=False)
        if "budget_currency" in req:
            payload["budget_currency"] = optional_text(req, "budget_currency", limit=20)
        if "remember_list_preference" in req:
            payload["remember_list_preference"] = boolean_field(req, "remember_list_preference")

        if set(payload) == {"id", "expected_revision"}:
            raise StoreError("item_update_requires_fields")

        req_hash, replayed = self._replay_receipt(con, "item-update", key, payload)
        if replayed is not None:
            return replayed

        current = self._fetch_item_row(con, ident)
        if current["revision"] != expected_revision:
            raise StoreError("revision_conflict")

        list_id = current["list_id"]
        if "list_id" in payload or "list_name" in payload:
            resolved = self._resolve_list(
                con,
                list_id=payload.get("list_id"),
                list_name=payload.get("list_name"),
                list_kind=payload.get("list_kind", "custom"),
                create_missing_name=True,
            )
            if resolved is None:
                raise StoreError("list_required")
            list_id = resolved["id"]

        name = payload.get("name", current["name"])
        category = payload.get("category", current["category"])
        unit = payload.get("unit", current["unit"])
        brand = payload.get("brand", current["brand"])
        preferred_store = payload.get("preferred_store", current["preferred_store"])
        notes = payload.get("notes", current["notes"])
        priority = payload.get("priority", current["priority"])
        target_date = payload["target_date"] if "target_date" in payload else current["target_date"]
        recipient = payload.get("recipient", current["recipient"])
        occasion = payload.get("occasion", current["occasion"])
        occasion_recurring = int(payload.get("occasion_recurring", bool(current["occasion_recurring"])))
        budget_amount = payload["budget_amount"] if "budget_amount" in payload else current["budget_amount"]
        budget_currency = payload.get("budget_currency", current["budget_currency"])

        product_id = current["product_id"]
        if "name" in payload and (
            product_id is None or normalized(name) != normalized(current["name"])
        ):
            product_row = self._get_or_create_product(
                con,
                name,
                default_list_id=list_id,
                default_category=category,
                default_unit=unit,
                default_brand=brand,
                default_store=preferred_store,
            )
            product_id = product_row["id"]

        qty = payload["quantity"] if "quantity" in payload else current["quantity"]
        status = payload.get("status", current["status"])
        if "remaining_quantity" in payload:
            rem = payload["remaining_quantity"]
            if (qty is None) != (rem is None):
                raise StoreError("invalid_remaining_quantity")
        elif "quantity" in payload:
            if qty is None:
                rem = None
            elif (
                current["status"] in ITEM_OPEN_STATUSES
                and current["quantity"] is not None
                and current["remaining_quantity"] is not None
            ):
                already_bought = max(
                    Decimal("0"),
                    Decimal(current["quantity"]) - Decimal(current["remaining_quantity"]),
                )
                if already_bought > Decimal("0") and unit != current["unit"]:
                    already_bought = convert_quantity(already_bought, current["unit"], unit)
                new_rem = max(Decimal("0"), Decimal(qty) - already_bought)
                rem = format_decimal(new_rem)
                if "status" not in payload and current["status"] == "partially_bought" and new_rem == 0:
                    status = "bought"
            else:
                rem = qty
        else:
            rem = current["remaining_quantity"]

        if (
            current["status"] in ("bought", "cancelled")
            and status in ITEM_OPEN_STATUSES
            and "remaining_quantity" not in payload
            and "quantity" not in payload
            and qty is not None
            and (rem is None or Decimal(rem) <= Decimal("0"))
        ):
            if status == "partially_bought":
                raise StoreError("invalid_remaining_quantity")
            rem = qty

        if rem is not None and status in ITEM_OPEN_STATUSES and Decimal(rem) <= Decimal("0"):
            raise StoreError("invalid_remaining_quantity")

        stamp = now()
        resolved_at = current["resolved_at"]
        if status in ("bought", "cancelled"):
            if resolved_at is None or status != current["status"]:
                resolved_at = stamp
            if status == "bought" and qty is not None and "remaining_quantity" not in payload:
                rem = "0"
        else:
            resolved_at = None

        if (qty is None) != (rem is None):
            raise StoreError("invalid_remaining_quantity")

        search_text = self._item_search_text(
            name, category, brand, preferred_store, recipient, occasion, notes
        )
        con.execute(
            "UPDATE shopping_items SET "
            "list_id=?, product_id=?, name=?, search_text=?, category=?, quantity=?, remaining_quantity=?, "
            "unit=?, brand=?, preferred_store=?, notes=?, priority=?, status=?, target_date=?, "
            "recipient=?, occasion=?, occasion_recurring=?, budget_amount=?, budget_currency=?, "
            "revision=revision+1, updated_at=?, resolved_at=? WHERE id=?",
            (
                list_id, product_id, name, search_text, category, qty, rem, unit, brand, preferred_store,
                notes, priority, status, target_date, recipient, occasion, occasion_recurring,
                budget_amount, budget_currency, stamp, resolved_at, ident,
            ),
        )

        if payload.get("remember_list_preference") and product_id:
            prod = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if prod:
                prod_search = self._product_search_text(
                    prod["name"], json.loads(prod["aliases"]), prod["default_brand"], category, prod["notes"]
                )
                con.execute(
                    "UPDATE products SET default_list_id=?, default_category=?, search_text=?, "
                    "revision=revision+1, updated_at=? WHERE id=?",
                    (list_id, category, prod_search, stamp, prod["id"]),
                )

        updated = self._hydrate_item(self._fetch_item_row(con, ident))
        return self._record_receipt(con, key, "item-update", req_hash, updated)

    def _item_remove(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        ident = text_field(req, "id", limit=200)
        key = text_field(req, "request_key", limit=200)
        expected_revision = int_field(req, "expected_revision", minimum=1) if "expected_revision" in req else None
        payload: dict[str, Any] = {"id": ident}
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        req_hash, replayed = self._replay_receipt(con, "item-remove", key, payload)
        if replayed is not None:
            return replayed
        row = self._fetch_item_row(con, ident)
        if expected_revision is not None and row["revision"] != expected_revision:
            raise StoreError("revision_conflict")
        con.execute("DELETE FROM shopping_items WHERE id=?", (ident,))
        result = {"id": ident, "removed": True}
        return self._record_receipt(con, key, "item-remove", req_hash, result)

    def _list_clear(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        key = text_field(req, "request_key", limit=200)
        if not boolean_field(req, "confirm", False):
            raise StoreError("list_clear_requires_confirmation")
        if "list_id" not in req and "list_name" not in req:
            raise StoreError("list_required")
        req_lid = text_field(req, "list_id", limit=200) if req.get("list_id") is not None else None
        req_lname = text_field(req, "list_name", limit=200) if req.get("list_name") is not None else None
        statuses = req.get("statuses", list(ITEM_OPEN_STATUSES))
        if (
            not isinstance(statuses, list)
            or not statuses
            or any(not isinstance(s, str) or s not in ITEM_ALL_STATUSES for s in statuses)
        ):
            raise StoreError("invalid_statuses")
        statuses = sorted(set(statuses))
        payload = {"list_id": req_lid, "list_name": req_lname, "statuses": statuses, "confirm": True}
        req_hash, replayed = self._replay_receipt(con, "list-clear", key, payload)
        if replayed is not None:
            return replayed

        resolved = self._resolve_list(con, list_id=req_lid, list_name=req_lname, create_missing_name=False)
        if resolved is None:
            raise StoreError("list_not_found")
        placeholders = ",".join("?" for _ in statuses)
        cur = con.execute(
            f"DELETE FROM shopping_items WHERE list_id=? AND status IN ({placeholders})",
            [resolved["id"], *statuses],
        )
        result = {
            "list_id": resolved["id"],
            "list_name": resolved["name"],
            "removed_count": cur.rowcount,
            "purchases_preserved": True,
        }
        return self._record_receipt(con, key, "list-clear", req_hash, result)

    # ------------------------------------------------------------------
    # Purchases & Spending
    # ------------------------------------------------------------------

    def _purchase_log(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        key = text_field(req, "request_key", limit=200)
        bought_on = date_field(req, "bought_on").isoformat()
        fulfillment_mode = req.get("fulfillment_mode", "auto")
        if fulfillment_mode not in FULFILLMENT_MODES:
            raise StoreError("invalid_fulfillment_mode")
        if "expected_item_revision" in req and req.get("item_id") is None:
            raise StoreError("item_id_required")
        expected_item_revision = (
            int_field(req, "expected_item_revision", minimum=1)
            if "expected_item_revision" in req
            else None
        )
        if "replenishment_days" in req and "replenishment_months" in req:
            raise StoreError("conflicting_replenishment_fields")
        rep_days = int_field(req, "replenishment_days", minimum=1, maximum=3650) if "replenishment_days" in req else None
        rep_months = int_field(req, "replenishment_months", minimum=1, maximum=120) if "replenishment_months" in req else None
        rep_note = optional_text(req, "replenishment_note", "", limit=2000)

        item_id = text_field(req, "item_id", limit=200) if req.get("item_id") is not None else None
        product_id = text_field(req, "product_id", limit=200) if req.get("product_id") is not None else None
        product_name = text_field(req, "product_name", limit=500) if req.get("product_name") is not None else None
        if item_id is None and product_id is None and product_name is None:
            raise StoreError("purchase_subject_required")

        list_kind = req.get("list_kind", "custom")
        if list_kind not in LIST_KINDS:
            raise StoreError("invalid_list_kind")
        req_lid = text_field(req, "list_id", limit=200) if req.get("list_id") is not None else None
        req_lname = text_field(req, "list_name", limit=200) if req.get("list_name") is not None else None
        quantity = optional_decimal(req, "quantity", None, allow_zero=False)
        unit = optional_text(req, "unit", "", limit=200)
        category = optional_text(req, "category", "", limit=200)
        brand = optional_text(req, "brand", "", limit=200)
        store = optional_text(req, "store", "", limit=200)
        price = optional_decimal(req, "price", None, allow_zero=True)
        currency = optional_text(req, "currency", "", limit=20)
        notes = optional_text(req, "notes", "", limit=4000)
        expected_next_on = optional_date_field(req, "expected_next_on", None)

        payload = {
            "bought_on": bought_on,
            "item_id": item_id,
            "expected_item_revision": expected_item_revision,
            "product_id": product_id,
            "product_name": product_name,
            "list_id": req_lid,
            "list_name": req_lname,
            "list_kind": list_kind,
            "category": category,
            "quantity": quantity,
            "unit": unit,
            "brand": brand,
            "store": store,
            "price": price,
            "currency": currency,
            "notes": notes,
            "fulfillment_mode": fulfillment_mode,
            "expected_next_on": expected_next_on,
            "replenishment_days": rep_days,
            "replenishment_months": rep_months,
            "replenishment_note": rep_note,
        }
        req_hash, replayed = self._replay_receipt(con, "purchase-log", key, payload)
        if replayed is not None:
            return replayed

        item_row = self._fetch_item_row(con, item_id) if item_id is not None else None
        if item_row is not None and expected_item_revision is not None:
            if item_row["revision"] != expected_item_revision:
                raise StoreError("revision_conflict")

        if item_row is not None and item_row["status"] in ("bought", "cancelled") and fulfillment_mode != "keep_open":
            raise StoreError("item_already_resolved")

        resolved_list = None
        if req_lid is not None or req_lname is not None:
            resolved_list = self._resolve_list(
                con,
                list_id=req_lid,
                list_name=req_lname,
                list_kind=list_kind,
                create_missing_name=True,
            )
        elif item_row is not None:
            resolved_list = con.execute("SELECT * FROM lists WHERE id=?", (item_row["list_id"],)).fetchone()

        product_row = None
        if product_id is not None:
            product_row = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not product_row:
                raise StoreError("product_not_found")
        elif item_row is not None and item_row["product_id"]:
            product_row = con.execute("SELECT * FROM products WHERE id=?", (item_row["product_id"],)).fetchone()

        effective_name = product_name or (product_row["name"] if product_row else item_row["name"])
        effective_category = category or (item_row["category"] if item_row else (product_row["default_category"] if product_row else ""))
        effective_brand = brand or (item_row["brand"] if item_row else "")
        effective_store = store or (item_row["preferred_store"] if item_row else "")
        effective_list_id = (
            resolved_list["id"]
            if resolved_list is not None
            else (product_row["default_list_id"] if product_row else None)
        )

        if product_row is None:
            product_row = self._get_or_create_product(
                con,
                effective_name,
                default_list_id=effective_list_id,
                default_category=effective_category,
                default_unit=unit,
                default_brand=effective_brand,
                default_store=effective_store,
            )
        if effective_list_id is None and product_row["default_list_id"]:
            effective_list_id = product_row["default_list_id"]

        stamp = now()
        if rep_days is not None or rep_months is not None:
            rep_mode = "interval_days" if rep_days is not None else "calendar_months"
            rep_val = rep_days if rep_days is not None else rep_months
            con.execute(
                "UPDATE products SET replenishment_mode=?, replenishment_value=?, replenishment_note=?, "
                "replenishment_source='user_explicit', revision=revision+1, updated_at=? WHERE id=?",
                (rep_mode, rep_val, rep_note or product_row["replenishment_note"], stamp, product_row["id"]),
            )

        updated_item = None
        if item_row is not None and fulfillment_mode != "keep_open":
            if fulfillment_mode == "mark_bought":
                new_rem = "0" if item_row["quantity"] is not None else None
                con.execute(
                    "UPDATE shopping_items SET remaining_quantity=?, status='bought', resolved_at=?, "
                    "revision=revision+1, updated_at=? WHERE id=?",
                    (new_rem, stamp, stamp, item_row["id"]),
                )
            else:
                # Auto fulfillment: subtract only with verified compatible units when both quantities are known.
                if item_row["remaining_quantity"] is not None and quantity is not None:
                    bought_in_item_unit = convert_quantity(
                        Decimal(quantity), unit, item_row["unit"]
                    )
                    remaining = Decimal(item_row["remaining_quantity"]) - bought_in_item_unit
                    if remaining > Decimal("0"):
                        new_status = "partially_bought"
                        new_rem = format_decimal(remaining)
                        resolved_at = None
                    else:
                        new_status = "bought"
                        new_rem = "0"
                        resolved_at = stamp
                    con.execute(
                        "UPDATE shopping_items SET remaining_quantity=?, status=?, resolved_at=?, "
                        "revision=revision+1, updated_at=? WHERE id=?",
                        (new_rem, new_status, resolved_at, stamp, item_row["id"]),
                    )
                else:
                    new_rem = "0" if item_row["quantity"] is not None else None
                    con.execute(
                        "UPDATE shopping_items SET remaining_quantity=?, status='bought', resolved_at=?, "
                        "revision=revision+1, updated_at=? WHERE id=?",
                        (new_rem, stamp, stamp, item_row["id"]),
                    )
            updated_item = self._hydrate_item(self._fetch_item_row(con, item_row["id"]))

        purchase_id = uuid.uuid4().hex
        search_text = self._purchase_search_text(
            effective_name, effective_category, effective_brand, effective_store, notes
        )
        con.execute(
            "INSERT INTO purchases ("
            "id, product_id, shopping_item_id, list_id, product_name, search_text, category, "
            "bought_on, quantity, unit, brand, store, price, currency, notes, "
            "expected_next_on, finished_on, actual_duration_days, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
            (
                purchase_id, product_row["id"], item_row["id"] if item_row else None,
                effective_list_id, effective_name, search_text, effective_category,
                bought_on, quantity, unit, effective_brand, effective_store, price,
                currency, notes, expected_next_on, stamp, stamp,
            ),
        )
        hydrated_product = self._fetch_product_hydrated(con, product_row["id"])
        suggested_next_on: str | None = expected_next_on
        if suggested_next_on is None:
            pmode = hydrated_product["replenishment_mode"]
            pval = hydrated_product["replenishment_value"]
            if pmode == "interval_days" and pval is not None:
                suggested_next_on = (date.fromisoformat(bought_on) + timedelta(days=pval)).isoformat()
            elif pmode == "calendar_months" and pval is not None:
                suggested_next_on = add_months(date.fromisoformat(bought_on), pval).isoformat()
        result = {
            "purchase": self._hydrate_purchase(self._fetch_purchase_row(con, purchase_id)),
            "updated_item": updated_item,
            "product": hydrated_product,
            "suggested_next_on": suggested_next_on,
        }
        return self._record_receipt(con, key, "purchase-log", req_hash, result)

    def _purchase_update(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        ident = text_field(req, "id", limit=200)
        key = text_field(req, "request_key", limit=200)
        payload: dict[str, Any] = {"id": ident}
        if "bought_on" in req:
            payload["bought_on"] = date_field(req, "bought_on").isoformat()
        if "quantity" in req:
            payload["quantity"] = optional_decimal(req, "quantity", allow_zero=False)
        if "unit" in req:
            payload["unit"] = optional_text(req, "unit", limit=200)
        if "brand" in req:
            payload["brand"] = optional_text(req, "brand", limit=200)
        if "store" in req:
            payload["store"] = optional_text(req, "store", limit=200)
        if "price" in req:
            payload["price"] = optional_decimal(req, "price", allow_zero=True)
        if "currency" in req:
            payload["currency"] = optional_text(req, "currency", limit=20)
        if "notes" in req:
            payload["notes"] = optional_text(req, "notes", limit=4000)
        if "expected_next_on" in req:
            payload["expected_next_on"] = optional_date_field(req, "expected_next_on")
        if "finished_on" in req:
            payload["finished_on"] = optional_date_field(req, "finished_on")
        if "actual_duration_days" in req:
            payload["actual_duration_days"] = (
                None
                if req["actual_duration_days"] is None
                else int_field(req, "actual_duration_days", minimum=1, maximum=3650)
            )
        if set(payload) == {"id"}:
            raise StoreError("purchase_update_requires_fields")

        req_hash, replayed = self._replay_receipt(con, "purchase-update", key, payload)
        if replayed is not None:
            return replayed

        current = self._fetch_purchase_row(con, ident)
        bought_on = payload.get("bought_on", current["bought_on"])
        finished_on = payload["finished_on"] if "finished_on" in payload else current["finished_on"]
        actual_duration_days = (
            payload["actual_duration_days"]
            if "actual_duration_days" in payload
            else current["actual_duration_days"]
        )
        if finished_on is not None:
            diff_days = (date.fromisoformat(finished_on) - date.fromisoformat(bought_on)).days
            if diff_days < 1:
                raise StoreError("invalid_finished_on")
            if "actual_duration_days" not in payload:
                actual_duration_days = diff_days

        quantity = payload["quantity"] if "quantity" in payload else current["quantity"]
        unit = payload.get("unit", current["unit"])
        brand = payload.get("brand", current["brand"])
        store = payload.get("store", current["store"])
        price = payload["price"] if "price" in payload else current["price"]
        currency = payload.get("currency", current["currency"])
        notes = payload.get("notes", current["notes"])
        expected_next_on = (
            payload["expected_next_on"] if "expected_next_on" in payload else current["expected_next_on"]
        )
        search_text = self._purchase_search_text(
            current["product_name"], current["category"], brand, store, notes
        )
        con.execute(
            "UPDATE purchases SET bought_on=?, quantity=?, unit=?, brand=?, store=?, price=?, "
            "currency=?, notes=?, search_text=?, expected_next_on=?, finished_on=?, "
            "actual_duration_days=?, updated_at=? WHERE id=?",
            (
                bought_on, quantity, unit, brand, store, price, currency, notes, search_text,
                expected_next_on, finished_on, actual_duration_days, now(), ident,
            ),
        )
        updated = self._hydrate_purchase(self._fetch_purchase_row(con, ident))
        return self._record_receipt(con, key, "purchase-update", req_hash, updated)

    def _purchase_remove(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        ident = text_field(req, "id", limit=200)
        key = text_field(req, "request_key", limit=200)
        if not boolean_field(req, "confirm", False):
            raise StoreError("purchase_remove_requires_confirmation")
        payload = {"id": ident, "confirm": True}
        req_hash, replayed = self._replay_receipt(con, "purchase-remove", key, payload)
        if replayed is not None:
            return replayed
        self._fetch_purchase_row(con, ident)
        con.execute("DELETE FROM purchases WHERE id=?", (ident,))
        return self._record_receipt(con, key, "purchase-remove", req_hash, {"id": ident, "removed": True})

    @staticmethod
    def _date_range_args(req: dict[str, Any]) -> tuple[str | None, str | None]:
        if "on" in req and req["on"] is not None:
            if req.get("from_date") is not None or req.get("to_date") is not None:
                raise StoreError("invalid_date_filter")
            day = date_field(req, "on").isoformat()
            return day, day
        from_d = date_field(req, "from_date").isoformat() if req.get("from_date") is not None else None
        to_d = date_field(req, "to_date").isoformat() if req.get("to_date") is not None else None
        if from_d and to_d and from_d > to_d:
            raise StoreError("invalid_date_range")
        return from_d, to_d

    def _purchase_query_rows(
        self,
        con: sqlite3.Connection,
        req: dict[str, Any],
    ) -> list[sqlite3.Row]:
        from_d, to_d = self._date_range_args(req)
        clauses: list[str] = []
        params: list[Any] = []
        if req.get("product_id") is not None:
            clauses.append("p.product_id = ?")
            params.append(text_field(req, "product_id", limit=200))
        if req.get("list_id") is not None:
            lid = text_field(req, "list_id", limit=200)
            if not con.execute("SELECT 1 FROM lists WHERE id=?", (lid,)).fetchone():
                raise StoreError("list_not_found")
            clauses.append("p.list_id = ?")
            params.append(lid)
        if req.get("list_name") is not None:
            resolved = self._resolve_list(
                con,
                list_name=text_field(req, "list_name", limit=200),
                create_missing_name=False,
            )
            if resolved is None:
                return []
            clauses.append("p.list_id = ?")
            params.append(resolved["id"])
        if from_d is not None:
            clauses.append("p.bought_on >= ?")
            params.append(from_d)
        if to_d is not None:
            clauses.append("p.bought_on <= ?")
            params.append(to_d)
        if req.get("query") is not None:
            query = req["query"]
            if not isinstance(query, str) or len(query) > 2000:
                raise StoreError("invalid_query")
            for term in normalized(query).split():
                clauses.append("INSTR(p.search_text, ?) > 0")
                params.append(term)

        store_filter = normalized(text_field(req, "store", limit=200)) if req.get("store") is not None else None
        cat_filter = normalized(text_field(req, "category", limit=200)) if req.get("category") is not None else None
        if store_filter is not None:
            clauses.append("p.store <> ''")
        if cat_filter is not None:
            clauses.append("p.category <> ''")

        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        sql = (
            "SELECT p.*, l.name AS list_name "
            f"FROM purchases p LEFT JOIN lists l ON l.id = p.list_id {where}"
            "ORDER BY p.bought_on DESC, p.created_at DESC, p.id"
        )
        rows = []
        for row in con.execute(sql, params).fetchall():
            if store_filter is not None and store_filter not in normalized(row["store"]):
                continue
            if cat_filter is not None and normalized(row["category"]) != cat_filter:
                continue
            rows.append(row)
        return rows

    def _purchase_list(self, con: sqlite3.Connection, req: dict[str, Any]) -> list[dict[str, Any]]:
        limit = limit_field(req, default=50)
        offset = offset_field(req)
        rows = self._purchase_query_rows(con, req)
        return [self._hydrate_purchase(row) for row in rows[offset:offset + limit]]

    def _spending_summary(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        from_d, to_d = self._date_range_args(req)
        rows = self._purchase_query_rows(con, req)
        totals: dict[str, Decimal] = {}
        by_store: dict[str, dict[str, Any]] = {}
        priced_count = 0
        unpriced_count = 0
        for row in rows:
            store_label = row["store"] or "Unspecified"
            store_entry = by_store.setdefault(
                store_label,
                {"store": store_label, "purchase_count": 0, "priced_count": 0, "unpriced_count": 0, "totals": {}},
            )
            store_entry["purchase_count"] += 1
            if row["price"] is None:
                unpriced_count += 1
                store_entry["unpriced_count"] += 1
            else:
                priced_count += 1
                store_entry["priced_count"] += 1
                cur = row["currency"] or "UNSPECIFIED"
                amount = Decimal(row["price"])
                totals[cur] = totals.get(cur, Decimal("0")) + amount
                store_entry["totals"][cur] = store_entry["totals"].get(cur, Decimal("0")) + amount

        formatted_stores = []
        for s in sorted(by_store.values(), key=lambda x: (-x["purchase_count"], x["store"].casefold())):
            s["totals_by_currency"] = {k: format_decimal(v) for k, v in sorted(s.pop("totals").items())}
            formatted_stores.append(s)

        return {
            "period": {"from_date": from_d, "to_date": to_d},
            "purchase_count": len(rows),
            "priced_count": priced_count,
            "unpriced_count": unpriced_count,
            "is_complete": unpriced_count == 0,
            "totals_by_currency": {k: format_decimal(v) for k, v in sorted(totals.items())},
            "by_store": formatted_stores,
        }

    # ------------------------------------------------------------------
    # Intelligent Replenishment (read-only estimates)
    # ------------------------------------------------------------------

    def _replenishment_set(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        mode = text_field(req, "mode", limit=50)
        if mode not in REPLENISHMENT_MODES:
            raise StoreError("invalid_replenishment_mode")
        if mode == "none":
            if req.get("value") is not None:
                raise StoreError("invalid_replenishment_value")
            value = None
            source = "none"
        else:
            maximum = 3650 if mode == "interval_days" else 120
            value = int_field(req, "value", minimum=1, maximum=maximum)
            source = req.get("source", "user_explicit")
            if source not in ("user_explicit", "user_corrected"):
                raise StoreError("invalid_replenishment_source")
        note = optional_text(req, "note", "", limit=2000)

        product_id = text_field(req, "product_id", limit=200) if req.get("product_id") is not None else None
        product_name = text_field(req, "product_name", limit=500) if req.get("product_name") is not None else None
        if product_id is None and product_name is None:
            raise StoreError("product_required")

        key = text_field(req, "request_key", limit=200) if "request_key" in req else None
        payload = {
            "product_id": product_id,
            "product_name": product_name,
            "mode": mode,
            "value": value,
            "note": note,
            "source": source,
        }
        req_hash = None
        if key is not None:
            req_hash, replayed = self._replay_receipt(con, "replenishment-set", key, payload)
            if replayed is not None:
                return replayed

        if product_id is not None:
            product = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not product:
                raise StoreError("product_not_found")
        else:
            product = self._get_or_create_product(con, product_name)

        con.execute(
            "UPDATE products SET replenishment_mode=?, replenishment_value=?, replenishment_note=?, "
            "replenishment_source=?, revision=revision+1, updated_at=? WHERE id=?",
            (mode, value, note, source, now(), product["id"]),
        )
        hydrated = self._fetch_product_hydrated(con, product["id"])
        if key is not None and req_hash is not None:
            return self._record_receipt(con, key, "replenishment-set", req_hash, hydrated)
        return hydrated

    def _compute_product_suggestion(
        self,
        con: sqlite3.Connection,
        product: sqlite3.Row,
        purchases: list[sqlite3.Row],
        as_of: date,
        horizon_days: int,
        open_item_count: int,
    ) -> dict[str, Any]:
        last_purchase = purchases[-1] if purchases else None
        last_bought_on = last_purchase["bought_on"] if last_purchase else None
        mode = product["replenishment_mode"]
        value = product["replenishment_value"]

        feedback_by_date: dict[date, int] = {}
        for p in purchases:
            if p["actual_duration_days"] is not None:
                feedback_by_date[date.fromisoformat(p["bought_on"])] = p["actual_duration_days"]
        distinct_dates = sorted({date.fromisoformat(p["bought_on"]) for p in purchases})
        date_gaps = [
            (d2 - d1).days for d1, d2 in zip(distinct_dates, distinct_dates[1:]) if (d2 - d1).days > 0
        ]
        feedback_samples = [p["actual_duration_days"] for p in purchases if p["actual_duration_days"] is not None]
        observed_samples: list[int] = []
        for d1, d2 in zip(distinct_dates, distinct_dates[1:]):
            if d1 in feedback_by_date:
                observed_samples.append(feedback_by_date[d1])
            elif (d2 - d1).days > 0:
                observed_samples.append((d2 - d1).days)
        if distinct_dates and distinct_dates[-1] in feedback_by_date:
            observed_samples.append(feedback_by_date[distinct_dates[-1]])

        suggested_next_on: str | None = None
        estimated_interval_days: int | None = None
        basis = "none"
        confidence = "none"
        regularity = "unknown"
        outliers_ignored = 0
        latest_feedback_days = last_purchase["actual_duration_days"] if last_purchase else None
        explanation = ""

        if last_purchase is not None and last_purchase["expected_next_on"] is not None:
            suggested_next_on = last_purchase["expected_next_on"]
            basis = "purchase_expectation"
            confidence = "explicit"
            regularity = "explicit"
            estimated_interval_days = (date.fromisoformat(suggested_next_on) - date.fromisoformat(last_bought_on)).days
            explanation = f"Explicit expectation set on purchase from {last_bought_on}."
        elif mode in ("interval_days", "calendar_months"):
            confidence = "explicit"
            regularity = "explicit"
            if last_bought_on is not None and value is not None:
                last_dt = date.fromisoformat(last_bought_on)
                next_dt = last_dt + timedelta(days=value) if mode == "interval_days" else add_months(last_dt, value)
                suggested_next_on = next_dt.isoformat()
                estimated_interval_days = (next_dt - last_dt).days
                basis = f"explicit_{mode}"
                explanation = (
                    f"User-defined {value}-day interval from {last_bought_on}."
                    if mode == "interval_days"
                    else f"User-defined {value}-month interval from {last_bought_on}."
                )
            else:
                basis = f"explicit_{mode}"
                explanation = "User-defined replenishment interval saved; awaiting first purchase date."
        else:
            # Historical statistical estimate: median with extreme-outlier safeguard.
            samples: list[int]
            if len(observed_samples) >= 2:
                samples = list(observed_samples)
                basis = "historical_median"
            elif len(observed_samples) == 1 and feedback_samples:
                samples = list(observed_samples)
                basis = "user_feedback"
            else:
                samples = []

            if samples and last_bought_on is not None:
                raw_med = statistics.median(samples)
                filtered = samples
                if len(samples) >= 4 and raw_med > 0:
                    candidate = [g for g in samples if (raw_med / 3.0) <= g <= (raw_med * 3.0)]
                    if len(candidate) >= 2:
                        outliers_ignored = len(samples) - len(candidate)
                        filtered = candidate
                estimated_interval_days = max(1, round(statistics.median(filtered)))
                next_dt = date.fromisoformat(last_bought_on) + timedelta(days=estimated_interval_days)
                suggested_next_on = next_dt.isoformat()
                spread = max(filtered) - min(filtered)
                if basis == "user_feedback" and len(samples) < 2:
                    confidence = "low"
                    regularity = "single_feedback"
                elif len(filtered) >= 3 and spread <= max(2.0, estimated_interval_days * 0.25):
                    confidence = "high"
                    regularity = "regular"
                elif spread <= max(4.0, estimated_interval_days * 0.5):
                    confidence = "medium"
                    regularity = "moderate"
                else:
                    confidence = "low"
                    regularity = "irregular"
                explanation = (
                    f"Estimated {estimated_interval_days}-day median from {len(filtered)} observed "
                    f"interval(s) (confidence: {confidence})."
                )
            elif purchases:
                basis = "insufficient_history"
                explanation = "Only one purchase date and no explicit interval recorded yet."

        days_until_due: int | None = None
        if suggested_next_on is not None:
            days_until_due = (date.fromisoformat(suggested_next_on) - as_of).days
            if days_until_due < 0:
                status = "overdue_estimate"
            elif days_until_due <= horizon_days:
                status = "due_soon_estimate"
            else:
                status = "ok"
        elif mode != "none" and not purchases:
            status = "awaiting_purchase_history"
        elif purchases:
            status = "insufficient_history"
        else:
            status = "no_pattern"

        return {
            "product_id": product["id"],
            "product_name": product["name"],
            "default_list_id": product["default_list_id"],
            "default_list_name": product["default_list_name"],
            "is_estimate": True,
            "status": status,
            "basis": basis,
            "confidence": confidence,
            "regularity": regularity,
            "last_bought_on": last_bought_on,
            "suggested_next_on": suggested_next_on,
            "days_until_due": days_until_due,
            "estimated_interval_days": estimated_interval_days,
            "observed_intervals_count": len(date_gaps),
            "purchase_count": len(purchases),
            "outliers_ignored": outliers_ignored,
            "latest_feedback_days": latest_feedback_days,
            "replenishment_mode": mode,
            "replenishment_value": value,
            "replenishment_note": product["replenishment_note"],
            "confirmed_need_exists": open_item_count > 0,
            "open_item_count": open_item_count,
            "explanation": explanation,
        }

    def _replenishment_suggestions(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        as_of = date_field(req, "as_of") if req.get("as_of") is not None else date.today()
        horizon_days = (
            int_field(req, "horizon_days", minimum=1, maximum=365)
            if "horizon_days" in req
            else 14
        )
        include_all = boolean_field(req, "include_all", False)
        product_id = text_field(req, "product_id", limit=200) if req.get("product_id") is not None else None
        query = req.get("query")
        if query is not None and (not isinstance(query, str) or len(query) > 2000):
            raise StoreError("invalid_query")

        clauses: list[str] = []
        params: list[Any] = []
        if product_id is not None:
            if not con.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                raise StoreError("product_not_found")
            clauses.append("p.id = ?")
            params.append(product_id)
        if req.get("list_id") is not None:
            lid = text_field(req, "list_id", limit=200)
            if not con.execute("SELECT 1 FROM lists WHERE id=?", (lid,)).fetchone():
                raise StoreError("list_not_found")
            clauses.append("p.default_list_id = ?")
            params.append(lid)
        if req.get("list_name") is not None:
            resolved = self._resolve_list(
                con,
                list_name=text_field(req, "list_name", limit=200),
                create_missing_name=False,
            )
            if resolved is None:
                return {"as_of": as_of.isoformat(), "horizon_days": horizon_days, "is_estimate": True, "suggestions": []}
            clauses.append("p.default_list_id = ?")
            params.append(resolved["id"])
        if query is not None:
            for term in normalized(query).split():
                clauses.append("INSTR(p.search_text, ?) > 0")
                params.append(term)

        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        products = con.execute(
            "SELECT p.*, l.name AS default_list_name "
            f"FROM products p LEFT JOIN lists l ON l.id = p.default_list_id {where}"
            "ORDER BY p.name COLLATE NOCASE ASC, p.id",
            params,
        ).fetchall()

        purchases_by_product: dict[str, list[sqlite3.Row]] = {}
        for row in con.execute(
            "SELECT * FROM purchases WHERE product_id IS NOT NULL ORDER BY bought_on ASC, created_at ASC, id"
        ):
            purchases_by_product.setdefault(row["product_id"], []).append(row)

        open_counts = {
            row["product_id"]: row["cnt"]
            for row in con.execute(
                "SELECT product_id, count(*) AS cnt FROM shopping_items "
                "WHERE product_id IS NOT NULL AND status IN ('idea','planned','needed','partially_bought') "
                "GROUP BY product_id"
            )
        }

        explicit_target = product_id is not None or (query is not None and bool(query.strip()))
        results = []
        for prod in products:
            purchases = purchases_by_product.get(prod["id"], [])
            if not purchases and prod["replenishment_mode"] == "none" and not explicit_target:
                continue
            item = self._compute_product_suggestion(
                con, prod, purchases, as_of, horizon_days, open_counts.get(prod["id"], 0)
            )
            if include_all or explicit_target or item["status"] in ("overdue_estimate", "due_soon_estimate"):
                results.append(item)

        rank = {
            "overdue_estimate": 0,
            "due_soon_estimate": 1,
            "ok": 2,
            "awaiting_purchase_history": 3,
            "insufficient_history": 4,
            "no_pattern": 5,
        }
        results.sort(
            key=lambda x: (
                rank[x["status"]],
                x["days_until_due"] if x["days_until_due"] is not None else 10**9,
                x["product_name"].casefold(),
            )
        )
        return {
            "as_of": as_of.isoformat(),
            "horizon_days": horizon_days,
            "is_estimate": True,
            "suggestions": results,
        }

    # ------------------------------------------------------------------
    # Context, Export & Import
    # ------------------------------------------------------------------

    def _context(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        as_of = date_field(req, "as_of") if req.get("as_of") is not None else date.today()
        horizon_days = (
            int_field(req, "horizon_days", minimum=1, maximum=90)
            if "horizon_days" in req
            else 7
        )
        recent_limit = (
            int_field(req, "recent_purchases_limit", minimum=1, maximum=100)
            if "recent_purchases_limit" in req
            else 20
        )

        context_limit = 100
        lists = self._list_list(con, {"include_archived": False})
        open_items = self._item_list(con, {"status": "open", "limit": context_limit})
        total_open_items = con.execute(
            "SELECT count(*) FROM shopping_items WHERE status IN ('idea','planned','needed','partially_bought')"
        ).fetchone()[0]
        upcoming_gifts = self._item_list(
            con,
            {"status": "open", "gifts_only": True, "limit": context_limit},
        )
        total_upcoming_gifts = con.execute(
            "SELECT count(*) FROM shopping_items i JOIN lists l ON l.id = i.list_id "
            "WHERE i.status IN ('idea','planned','needed','partially_bought') "
            "AND (l.kind = 'gifts' OR i.recipient <> '' OR i.occasion <> '')"
        ).fetchone()[0]
        category_prefs = [
            dict(r)
            for r in con.execute(
                "SELECT cp.*, l.name AS list_name, l.kind AS list_kind "
                "FROM category_preferences cp JOIN lists l ON l.id = cp.list_id "
                "ORDER BY cp.topic COLLATE NOCASE ASC, cp.id"
            ).fetchall()
        ]
        products = self._product_search(con, {"limit": context_limit})
        total_products = con.execute("SELECT count(*) FROM products").fetchone()[0]
        recent_purchases = self._purchase_list(con, {"limit": recent_limit})
        total_purchases = con.execute("SELECT count(*) FROM purchases").fetchone()[0]
        suggestions = self._replenishment_suggestions(
            con,
            {"as_of": as_of.isoformat(), "horizon_days": horizon_days},
        )["suggestions"]

        def page_meta(rows: list[Any], total: int) -> dict[str, Any]:
            truncated = len(rows) < total
            return {
                "returned": len(rows),
                "total": total,
                "truncated": truncated,
                "next_offset": len(rows) if truncated else None,
            }

        return {
            "as_of": as_of.isoformat(),
            "lists": lists,
            "open_items": open_items,
            "upcoming_gifts": upcoming_gifts,
            "category_preferences": category_prefs,
            "products": products,
            "recent_purchases": recent_purchases,
            "replenishment_suggestions": suggestions,
            "pagination": {
                "open_items": page_meta(open_items, total_open_items),
                "upcoming_gifts": page_meta(upcoming_gifts, total_upcoming_gifts),
                "products": page_meta(products, total_products),
                "recent_purchases": page_meta(recent_purchases, total_purchases),
            },
        }

    def _export(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        lists = [
            self._hydrate_list(r)
            for r in con.execute("SELECT * FROM lists ORDER BY id").fetchall()
        ]
        for entry in lists:
            entry.pop("open_count", None)
        products = [
            self._hydrate_product(r)
            for r in con.execute("SELECT * FROM products ORDER BY id").fetchall()
        ]
        for entry in products:
            entry.pop("default_list_name", None)
        category_prefs = [
            dict(r)
            for r in con.execute(
                "SELECT id, topic, list_id, category, notes, updated_at "
                "FROM category_preferences ORDER BY id"
            ).fetchall()
        ]
        items = [
            self._hydrate_item(r)
            for r in con.execute(
                "SELECT i.*, '' AS list_name, '' AS list_kind FROM shopping_items i ORDER BY i.id"
            ).fetchall()
        ]
        for entry in items:
            entry.pop("list_name", None)
            entry.pop("list_kind", None)
        purchases = [
            self._hydrate_purchase(r)
            for r in con.execute(
                "SELECT p.*, '' AS list_name FROM purchases p ORDER BY p.id"
            ).fetchall()
        ]
        for entry in purchases:
            entry.pop("list_name", None)
        return {
            "schema_version": SCHEMA_VERSION,
            "lists": lists,
            "products": products,
            "category_preferences": category_prefs,
            "shopping_items": items,
            "purchases": purchases,
        }

    def _import(self, con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
        key = text_field(req, "request_key", limit=200)
        data = req.get("data")
        try:
            encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise StoreError("invalid_import_json") from None
        if len(encoded) > 20 * 1024 * 1024:
            raise StoreError("invalid_import_size")
        digest = hashlib.sha256(encoded).hexdigest()
        req_hash, replayed = self._replay_receipt(con, "import", key, {"snapshot_hash": digest})
        if replayed is not None:
            return replayed

        data = validate_snapshot(data)
        occupied = con.execute(
            "SELECT 1 FROM lists UNION ALL SELECT 1 FROM products UNION ALL "
            "SELECT 1 FROM category_preferences UNION ALL SELECT 1 FROM shopping_items UNION ALL "
            "SELECT 1 FROM purchases UNION ALL SELECT 1 FROM mutation_requests LIMIT 1"
        ).fetchone()
        if occupied:
            raise StoreError("import_requires_empty_profile")

        for row in data["lists"]:
            con.execute(
                "INSERT INTO lists VALUES (?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["name"], normalized(row["name"]), row["kind"],
                    row["notes"], int(row["archived"]), row["created_at"], row["updated_at"],
                ),
            )
        for row in data["products"]:
            search_text = self._product_search_text(
                row["name"], row["aliases"], row["default_brand"], row["default_category"], row["notes"]
            )
            con.execute(
                "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["name"], normalized(row["name"]),
                    json.dumps(row["aliases"], ensure_ascii=False), search_text,
                    row["default_list_id"], row["default_category"], row["default_unit"],
                    row["default_brand"], row["default_store"], row["notes"],
                    row["replenishment_mode"], row["replenishment_value"],
                    row["replenishment_note"], row["replenishment_source"],
                    row["revision"], row["created_at"], row["updated_at"],
                ),
            )
        for row in data["category_preferences"]:
            con.execute(
                "INSERT INTO category_preferences VALUES (?,?,?,?,?,?,?)",
                (
                    row["id"], row["topic"], normalized(row["topic"]),
                    row["list_id"], row["category"], row["notes"], row["updated_at"],
                ),
            )
        for row in data["shopping_items"]:
            search_text = self._item_search_text(
                row["name"], row["category"], row["brand"], row["preferred_store"],
                row["recipient"], row["occasion"], row["notes"],
            )
            con.execute(
                "INSERT INTO shopping_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["list_id"], row["product_id"], row["name"], search_text,
                    row["category"], row["quantity"], row["remaining_quantity"], row["unit"],
                    row["brand"], row["preferred_store"], row["notes"], row["priority"],
                    row["status"], row["target_date"], row["recipient"], row["occasion"],
                    int(row["occasion_recurring"]), row["budget_amount"], row["budget_currency"],
                    row["revision"], row["created_at"], row["updated_at"], row["resolved_at"],
                ),
            )
        for row in data["purchases"]:
            search_text = self._purchase_search_text(
                row["product_name"], row["category"], row["brand"], row["store"], row["notes"]
            )
            con.execute(
                "INSERT INTO purchases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["product_id"], row["shopping_item_id"], row["list_id"],
                    row["product_name"], search_text, row["category"], row["bought_on"],
                    row["quantity"], row["unit"], row["brand"], row["store"], row["price"],
                    row["currency"], row["notes"], row["expected_next_on"],
                    row["finished_on"], row["actual_duration_days"],
                    row["created_at"], row["updated_at"],
                ),
            )

        result = {
            "imported": True,
            "counts": {table: len(data[table]) for table in EXPORT_TABLES},
        }
        return self._record_receipt(con, key, "import", req_hash, result)


def main() -> int:
    """Read one UTF-8 JSON object from stdin or --request-file and emit one JSON envelope."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Override persistent user data directory")
    parser.add_argument("--request-file", type=Path, help="Read UTF-8 JSON from this file instead of stdin")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raw = (
            args.request_file.read_text(encoding="utf-8-sig")
            if args.request_file
            else sys.stdin.buffer.read().decode("utf-8-sig")
        )
        req = json.loads(raw)
        output = Store(args.data_dir).handle(req)
        code = 0
    except (StoreError, OSError, sqlite3.Error, ValueError) as exc:
        error = str(exc) if isinstance(exc, StoreError) else "storage_or_input_error"
        output, code = {"ok": False, "error": error}, 1
    print(json.dumps(output, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
