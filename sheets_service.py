"""
Core logic for reading/writing the "Detail PT3" Google Sheet.
Used by both the Flask web app and the Telegram bot so the update
logic (routing + prepend + date-overwrite) lives in exactly one place.
"""
import json
import datetime
import threading

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client_lock = threading.Lock()
_gspread_client = None
_worksheet = None


def _col_to_index(col_letters: str) -> int:
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27, etc."""
    result = 0
    for ch in col_letters:
        result = result * 26 + (ord(ch.upper()) - ord("A") + 1)
    return result


def get_client():
    global _gspread_client
    with _client_lock:
        if _gspread_client is None:
            if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON env var is not set. "
                    "Paste the full service-account JSON key content as this variable "
                    "in Railway's environment settings."
                )
            info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            _gspread_client = gspread.authorize(creds)
        return _gspread_client


def get_worksheet():
    global _worksheet
    if _worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _worksheet = sh.worksheet(config.SHEET_NAME)
    return _worksheet


def find_row(ihld: str, lokasi: str):
    """
    Find the sheet row matching IHLD (col I) + LOKASI IHLD (col J).
    Returns the 1-indexed row number, or None if not found.
    Matching is case-insensitive and trims whitespace.
    """
    ws = get_worksheet()
    ihld_col_idx = _col_to_index(config.COL_IHLD)
    lokasi_col_idx = _col_to_index(config.COL_LOKASI)

    ihld_values = ws.col_values(ihld_col_idx)
    lokasi_values = ws.col_values(lokasi_col_idx)

    target_ihld = (ihld or "").strip().lower()
    target_lokasi = (lokasi or "").strip().lower()

    max_len = max(len(ihld_values), len(lokasi_values))
    for i in range(config.DATA_START_ROW - 1, max_len):  # 0-indexed list, rows start at DATA_START_ROW
        row_num = i + 1
        v_ihld = ihld_values[i].strip().lower() if i < len(ihld_values) else ""
        v_lokasi = lokasi_values[i].strip().lower() if i < len(lokasi_values) else ""
        if v_ihld == target_ihld and (not target_lokasi or v_lokasi == target_lokasi):
            return row_num
    return None


def search_rows(query: str, limit: int = 15):
    """
    Loose search across IHLD + LOKASI IHLD for autocomplete-style lookup
    (used when the user isn't sure of the exact IHLD code).
    Returns a list of dicts: {row, ihld, lokasi, status_z, status_aa}
    """
    ws = get_worksheet()
    ihld_col_idx = _col_to_index(config.COL_IHLD)
    lokasi_col_idx = _col_to_index(config.COL_LOKASI)
    z_col_idx = _col_to_index(config.COL_STATUS_Z)
    aa_col_idx = _col_to_index(config.COL_STATUS_AA)

    ihld_values = ws.col_values(ihld_col_idx)
    lokasi_values = ws.col_values(lokasi_col_idx)
    z_values = ws.col_values(z_col_idx)
    aa_values = ws.col_values(aa_col_idx)

    q = (query or "").strip().lower()
    results = []
    max_len = max(len(ihld_values), len(lokasi_values))
    for i in range(config.DATA_START_ROW - 1, max_len):
        row_num = i + 1
        v_ihld = ihld_values[i].strip() if i < len(ihld_values) else ""
        v_lokasi = lokasi_values[i].strip() if i < len(lokasi_values) else ""
        if not v_ihld and not v_lokasi:
            continue
        if q and q not in v_ihld.lower() and q not in v_lokasi.lower():
            continue
        results.append({
            "row": row_num,
            "ihld": v_ihld,
            "lokasi": v_lokasi,
            "status_z": z_values[i].strip() if i < len(z_values) else "",
            "status_aa": aa_values[i].strip() if i < len(aa_values) else "",
        })
        if len(results) >= limit:
            break
    return results


def get_row_label(row_num: int):
    """Return {ihld, lokasi, batch} for the row header shown in the update panel."""
    ws = get_worksheet()
    ihld = ws.acell(f"{config.COL_IHLD}{row_num}").value or ""
    lokasi = ws.acell(f"{config.COL_LOKASI}{row_num}").value or ""
    batch = ws.acell(f"{config.COL_BATCH}{row_num}").value or ""
    return {"ihld": ihld.strip(), "lokasi": lokasi.strip(), "batch": batch.strip()}


def _to_number(raw: str) -> float:
    """Parse a sheet cell into a number. Tolerates '' , '12', '12.5', and the
    Indonesian '1.234,56' thousands/decimal style. Anything unparseable -> 0."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


# Normalized (trimmed + uppercased) lookup so that status values entered in the
# sheet with different case or stray whitespace (e.g. "01. perijinan ") still
# match a DASHBOARD_STATUSES entry instead of being silently dropped from the
# pivot tables / "lokasi sedang berjalan" count.
_STATUS_LOOKUP = {s.strip().upper(): s for s in config.DASHBOARD_STATUSES}


def _match_status(raw: str):
    """Return the canonical DASHBOARD_STATUSES value matching `raw`, or None."""
    return _STATUS_LOOKUP.get((raw or "").strip().upper())


def get_dashboard_data():
    """
    One-shot read of the whole sheet, returned as a flat list of per-row
    records (plus the status/batch/branch option lists). Kept intentionally
    "raw" (no pre-aggregation) so the dashboard can recompute every KPI,
    the chart, the pivot tables, and the Kategori Drop counts from the same
    branch-filtered set on the client — otherwise the branch filter would
    only affect the pivot table like before.
    """
    ws = get_worksheet()
    all_values = ws.get_all_values()

    idx = {
        "order": _col_to_index(config.COL_ORDER) - 1,
        "batch": _col_to_index(config.COL_BATCH) - 1,
        "status_z": _col_to_index(config.COL_STATUS_Z) - 1,
        "status_aa": _col_to_index(config.COL_STATUS_AA) - 1,
        "port": _col_to_index(config.COL_PORT) - 1,
        "bh": _col_to_index(config.COL_BH) - 1,
        "branch": _col_to_index(config.COL_BRANCH) - 1,
        "ihld": _col_to_index(config.COL_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_LOKASI) - 1,
        "mitra": _col_to_index(config.COL_MITRA) - 1,
        "odp_l": _col_to_index(config.COL_ODP_L) - 1,
        "port_m": _col_to_index(config.COL_PORT_M) - 1,
        "boq_n": _col_to_index(config.COL_BOQ_N) - 1,
        "cpp_o": _col_to_index(config.COL_CPP_O) - 1,
    }

    data_rows = all_values[config.DATA_START_ROW - 1:]

    rows = []
    batch_order = []
    seen_batches = set()
    branch_set = set()

    for offset, row in enumerate(data_rows):
        row_num = config.DATA_START_ROW + offset

        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        order_val = cell("order")
        batch_val = cell("batch") or "(Tanpa Batch)"
        status_raw = cell("status_z")
        status_val = _match_status(status_raw)  # canonical DASHBOARD_STATUSES value or None
        ihld_val = cell("ihld")

        if not order_val and not ihld_val and not cell("batch"):
            continue  # fully empty row, skip

        branch_val = cell("branch") or "(Tanpa Branch)"
        branch_set.add(branch_val)
        if batch_val not in seen_batches:
            seen_batches.add(batch_val)
            batch_order.append(batch_val)

        rows.append({
            "row": row_num,
            "has_order": bool(order_val),
            "branch": branch_val,
            "batch": batch_val,
            "status": status_val,          # canonical value, or null if not one of the 5
            "status_raw": status_raw,
            "status_aa": cell("status_aa"),
            "port": _to_number(cell("port")),
            "ihld": ihld_val,
            "lokasi": cell("lokasi"),
            "mitra": cell("mitra"),
            "odp_l": cell("odp_l"),
            "port_m": cell("port_m"),
            "boq_n": cell("boq_n"),
            "cpp_o": cell("cpp_o"),
            "bh": cell("bh"),
        })

    return {
        "statuses": config.DASHBOARD_STATUSES,
        "batches": batch_order,
        "branches": sorted(branch_set, key=lambda b: b.lower()),
        "rows": rows,
    }


def get_row_snapshot(row_num: int):
    """Return current Z, AA and the keterangan cell content for the row's current Z status.
    `last_note` is just the topmost single entry (the most recent one, since
    entries are prepended newest-first) — used by the update panel to show
    "Keterangan sebelumnya" without the whole history."""
    ws = get_worksheet()
    z_val = ws.acell(f"{config.COL_STATUS_Z}{row_num}").value or ""
    aa_val = ws.acell(f"{config.COL_STATUS_AA}{row_num}").value or ""
    note_preview = ""
    if z_val in config.STATUS_COLUMN_MAP:
        note_col = config.STATUS_COLUMN_MAP[z_val]["note_col"]
        note_preview = ws.acell(f"{note_col}{row_num}").value or ""
    last_note = note_preview.split("\n")[0].strip() if note_preview.strip() else ""
    return {
        "row": row_num,
        "status_z": z_val,
        "status_aa": aa_val,
        "note_preview": note_preview,
        "last_note": last_note,
    }


def get_row_detail(row_num: int):
    """
    Return header/value pairs for every column from I to AB (inclusive) of
    the given row, used by the "Kategori Drop" list -> detail view.
    Headers come from HEADER_ROW; falls back to "Kolom N" if a header cell
    is blank.
    """
    ws = get_worksheet()
    start_idx = _col_to_index("I")
    end_idx = _col_to_index("AB")
    header_range = ws.get(f"I{config.HEADER_ROW}:AB{config.HEADER_ROW}")
    value_range = ws.get(f"I{row_num}:AB{row_num}")
    headers = header_range[0] if header_range else []
    values = value_range[0] if value_range else []

    fields = []
    for i in range(end_idx - start_idx + 1):
        h = headers[i].strip() if i < len(headers) and headers[i].strip() else f"Kolom {i + 1}"
        v = values[i] if i < len(values) else ""
        fields.append({"header": h, "value": v})
    return fields


def update_status(row_num: int, z_value: str, aa_value: str, note_text: str, when: datetime.date = None):
    """
    Apply one update to a row:
      1. Write Z and AA dropdown values.
      2. Resolve the (date_col, note_col) pair from Z.
      3. Prepend "DD/MM/YY : note_text" above whatever is already in note_col.
      4. Overwrite date_col with today's date (or `when` if given).
    Returns the (date_col, note_col) pair used.
    """
    if z_value not in config.STATUS_COLUMN_MAP:
        raise ValueError(f"Unknown status Z value: {z_value!r}")

    when = when or datetime.date.today()
    date_str = when.strftime("%d/%m/%y")

    ws = get_worksheet()
    mapping = config.STATUS_COLUMN_MAP[z_value]
    date_col, note_col = mapping["date_col"], mapping["note_col"]

    # 1. Status dropdowns
    ws.update_acell(f"{config.COL_STATUS_Z}{row_num}", z_value)
    if aa_value:
        ws.update_acell(f"{config.COL_STATUS_AA}{row_num}", aa_value)

    # 2. Prepend note (newest on top)
    existing_note = ws.acell(f"{note_col}{row_num}").value or ""
    new_entry = f"{date_str} : {note_text.strip()}"
    if existing_note.strip():
        merged_note = new_entry + "\n" + existing_note
    else:
        merged_note = new_entry
    ws.update_acell(f"{note_col}{row_num}", merged_note)

    # 3. Overwrite the paired date cell with just the latest date
    ws.update_acell(f"{date_col}{row_num}", date_str)

    return date_col, note_col
