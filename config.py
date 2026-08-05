import os

# ── Google Sheets identity ────────────────────────────────────────────
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "100Sx_DgMZjobEM_Q9xoHXU4O26iHdy_hANfbM7NxWNQ")
SHEET_NAME = os.environ.get("SHEET_NAME", "Detail PT3")

HEADER_ROW = 2          # header labels live on row 2
DATA_START_ROW = 3      # data rows start on row 3

# Unique row identity columns
COL_IHLD = "I"
COL_LOKASI = "J"

# Status columns
COL_STATUS_Z = "Z"    # Status Fisik (main status)
COL_STATUS_AA = "AA"  # SUB Status Fisik (sub status)
COL_KETERANGAN_AB = "AB"  # auto-merge formula, never written by the bot

# ── Dashboard columns ───────────────────────────────────────────────────
COL_ORDER = "A"    # 1 baris = 1 order -> dipakai untuk hitung Total Order
COL_BATCH = "C"    # Batch, dipakai sebagai baris (row) pada tabel pivot
COL_BRANCH = "S"   # Branch, dipakai untuk filter checkbox di atas tabel pivot
COL_PORT = "AG"    # Jumlah port per baris -> dipakai untuk Total Port & tabel "Data / Port"
COL_BH = "BH"      # Kolom bebas isian, direkap jadi tabel jumlah per nilai
COL_BRANCH = "S"   # Branch, dipakai untuk filter checkbox di tabel Rekap Port & LOP

# Kolom tambahan untuk tabel "Detail Lokasi" di bawah chart
COL_MITRA = "Y"    # Nama Mitra
COL_ODP_L = "L"    # ODP
COL_PORT_M = "M"   # PORT
COL_BOQ_N = "N"    # BoQ
COL_CPP_O = "O"    # CPP

# Status yang dianggap "sedang berjalan" -> dipakai sebagai kolom pada
# kedua tabel pivot dashboard (Port & LOP) dan untuk filter list lokasi aktif.
DASHBOARD_STATUSES = [
    "01. PERIJINAN",
    "02. PERSIAPAN",
    "03. MATDEV",
    "04. INSTALASI",
    "05. FINISH INSTALASI",
]

# ── Z (Status Fisik) dropdown options ─────────────────────────────────
Z_OPTIONS = [
    "00. DROP",
    "01. PERIJINAN",
    "02. PERSIAPAN",
    "03. MATDEV",
    "04. INSTALASI",
    "05. FINISH INSTALASI",
    "06. GOLIVE",
    "07. UT",
    "08. PEMBERKASAN",
    "09. REKON",
    "10. BAST",
    "10.1 BAST 2025",
    "0.1 SURVEI",
]

# ── AA (SUB Status Fisik) dropdown options ────────────────────────────
AA_OPTIONS = [
    "1.1 Persiapan",
    "1.2. Survey",
    "1.3. Review ED",
    "1.4. Pengajuan PR-PO",
    "1.5. Juskeb Swakelola",
    "1.6. Juskeb Permit",
    "1.7. Revisi Juskeb",
    "1.8. Kenaikan CC",
    "2.1. Negosiasi",
    "2.2. Pembayaran Permit TA",
    "2.3. Pembayaran Permit Mitra",
    "3 Material Delivery",
    "4.1. Galian",
    "4.2. Tanam Tiang",
    "4.3. Tarik Kabel",
    "4.4. instalasi ODP",
    "4.5. instalasi ODC",
    "4.6. Perapihan",
    "4.7. Selesai Fisik",
    "4.8. Valins",
    "4.9. Dok GOLIVE",
    "5 GOLIVE",
    "5.1 Uji Terima",
    "5.2 Rekon",
    "5.3 BAST",
    "6. Redesign",
    "7.1 Kendala",
    "7.2 HOLD",
    "0. DROP",
    "0.1. Plan Drop",
    "5.4 BAST 2025",
]

# ── Z value -> (date_col, note_col) routing ───────────────────────────
# Several Z values share the same target pair (BE/BD group and BG/BF group).
STATUS_COLUMN_MAP = {
    "0.1 SURVEI":            {"date_col": "AR", "note_col": "AS"},
    "01. PERIJINAN":         {"date_col": "AT", "note_col": "AU"},
    "02. PERSIAPAN":         {"date_col": "AV", "note_col": "AW"},
    "03. MATDEV":            {"date_col": "AX", "note_col": "AY"},
    "04. INSTALASI":         {"date_col": "AZ", "note_col": "BA"},
    "05. FINISH INSTALASI":  {"date_col": "BB", "note_col": "BC"},
    "06. GOLIVE":            {"date_col": "BD", "note_col": "BE"},
    "07. UT":                {"date_col": "BD", "note_col": "BE"},
    "08. PEMBERKASAN":       {"date_col": "BD", "note_col": "BE"},
    "09. REKON":             {"date_col": "BD", "note_col": "BE"},
    "10. BAST":              {"date_col": "BD", "note_col": "BE"},
    "00. DROP":              {"date_col": "BF", "note_col": "BG"},
    "10.1 BAST 2025":        {"date_col": "BF", "note_col": "BG"},
}

# ── Aging ────────────────────────────────────────────────────────────
COL_TANGGAL_NDE = "AP"   # tanggal awal (start) untuk hitung aging
AGING_WARNING_DAYS = 15   # > segini = kuning ("Perhatian")
AGING_CRITICAL_DAYS = 35  # > segini = merah ("Kritis")

# Status Z yang aging-nya dihitung sampai tanggal TETAP (bukan sampai hari
# ini) -> AP s/d kolom di bawah ini. Fallback ke hari ini kalau kolomnya
# kosong/tidak valid. Status lain di luar daftar ini pakai "hari ini".
AGING_FIXED_END_COLUMNS = {
    "00. DROP":        "BF",
    "10.1 BAST 2025":  "BF",
    "06. GOLIVE":      "BD",
    "07. UT":          "BD",
    "09. REKON":       "BD",
    "10. BAST":        "BD",
}

# ── Env / secrets ──────────────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
PORT = int(os.environ.get("PORT", 5000))
