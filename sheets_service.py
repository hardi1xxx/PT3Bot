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


def get_row_snapshot(row_num: int):
    """Return current Z, AA and the keterangan cell content for the row's current Z status."""
    ws = get_worksheet()
    z_val = ws.acell(f"{config.COL_STATUS_Z}{row_num}").value or ""
    aa_val = ws.acell(f"{config.COL_STATUS_AA}{row_num}").value or ""
    note_preview = ""
    if z_val in config.STATUS_COLUMN_MAP:
        note_col = config.STATUS_COLUMN_MAP[z_val]["note_col"]
        note_preview = ws.acell(f"{note_col}{row_num}").value or ""
    return {"row": row_num, "status_z": z_val, "status_aa": aa_val, "note_preview": note_preview}


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
