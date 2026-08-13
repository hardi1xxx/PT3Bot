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

# ── Kolom tambahan BL-BQ (isian opsional, muncul tergantung status Z) ──
COL_NILAI_PERIJINAN = "BL"  # rupiah
COL_NILAI_BOQ = "BM"        # rupiah
COL_IDSW = "BN"             # wajib ada '#', contoh: 9671760#9671766
COL_ODP_GOLIVE = "BO"       # wajib ada '-' dan '/', contoh: FBE/D08/068 - FBE/D08/071
COL_JUMLAH_ODP = "BP"       # angka saja
COL_JUMLAH_PORT = "BQ"      # angka saja

# Status Z -> field tambahan mana yang dimunculkan di form update.
# Semua field ini OPSIONAL: kalau dikosongkan saat update, nilai lama di
# sheet TIDAK ditimpa (beda dari kolom Z/AA/keterangan yang selalu ditulis).
EXTRA_FIELDS_BY_STATUS = {
    "01. PERIJINAN": ["nilai_perijinan", "nilai_boq"],
    "05. FINISH INSTALASI": ["jumlah_odp", "jumlah_port"],
    "06. GOLIVE": ["jumlah_odp", "jumlah_port", "idsw", "odp_golive"],
}

EXTRA_FIELD_COLUMNS = {
    "nilai_perijinan": COL_NILAI_PERIJINAN,
    "nilai_boq": COL_NILAI_BOQ,
    "idsw": COL_IDSW,
    "odp_golive": COL_ODP_GOLIVE,
    "jumlah_odp": COL_JUMLAH_ODP,
    "jumlah_port": COL_JUMLAH_PORT,
}

# Metadata per field supaya frontend tinggal render, tidak perlu hardcode.
EXTRA_FIELD_META = {
    "nilai_perijinan": {"label": "Nilai Perijinan", "col": COL_NILAI_PERIJINAN, "type": "currency", "placeholder": "misal: 5000000"},
    "nilai_boq":       {"label": "Nilai BOQ", "col": COL_NILAI_BOQ, "type": "currency", "placeholder": "misal: 3500000"},
    "idsw":            {"label": "IDSW", "col": COL_IDSW, "type": "text", "placeholder": "9671760#9671766"},
    "odp_golive":      {"label": "ODP GOLIVE", "col": COL_ODP_GOLIVE, "type": "text", "placeholder": "FBE/D08/068 - FBE/D08/071"},
    "jumlah_odp":      {"label": "Jumlah ODP", "col": COL_JUMLAH_ODP, "type": "number", "placeholder": "misal: 24"},
    "jumlah_port":     {"label": "Jumlah Port", "col": COL_JUMLAH_PORT, "type": "number", "placeholder": "misal: 96"},
}

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

# Status yang dianggap "sedang berjalan" -> dipakai untuk KPI "Lokasi
# Sedang Berjalan", chart distribusi status, dan notifikasi harian.
# JANGAN tambahkan GOLIVE/DROP ke sini — itu status akhir, bukan "berjalan".
DASHBOARD_STATUSES = [
    "01. PERIJINAN",
    "02. PERSIAPAN",
    "03. MATDEV",
    "04. INSTALASI",
    "05. FINISH INSTALASI",
]

# Kolom ke-6 & ke-7 KHUSUS di tabel "Rekap Port & LOP per Batch" — beberapa
# nilai Z digabung jadi satu kolom. Tidak memengaruhi DASHBOARD_STATUSES/KPI
# "Lokasi Sedang Berjalan" di atas, cuma dipakai di tabel pivot ini saja.
PIVOT_STATUS_GROUPS = {
    "GOLIVE": ["06. GOLIVE", "07. UT", "09. REKON", "10. BAST"],
    "DROP": ["00. DROP", "10.1 BAST 2025"],
}

# Nilai di kolom BH yang tidak ditampilkan di "Kategori Drop" (nyasar dari
# input manual, duplikat status Z).
BH_EXCLUDE_VALUES = ["10. BAST"]

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
# Kolom tanggal yang HANYA ditulis sekali (saat pertama kali status masuk
# ke sini, kalau kolomnya masih kosong) — bukan setiap kali ada update.
# Sejauh ini cuma AZ (04. INSTALASI): mencatat tanggal MULAI masuk
# instalasi, bukan tanggal update paling akhir.
DATE_COLS_WRITE_ONCE = {"AZ"}

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

# ── Notifikasi "belum update hari ini" ─────────────────────────────────
# Status Z yang dipantau + kolom tanggal pasangannya. Kalau status LOP ada
# di sini dan kolom tanggalnya BUKAN hari ini (dan tidak kosong — kosong
# dianggap lokasi baru, wajar, tidak dihitung), LOP itu muncul di notifikasi.
NOTIFY_STATUS_DATE_MAP = {
    "0.1 SURVEI":     "AR",
    "01. PERIJINAN":  "AT",
    "02. PERSIAPAN":  "AV",
    "03. MATDEV":     "AX",
    "04. INSTALASI":  "AZ",
}

# ── Sheet "Semesta" (gabungan PT2+PT3, dipakai halaman FBB) ────────────
SHEET_NAME_SEMESTA = os.environ.get("SHEET_NAME_SEMESTA", "Semesta")
HEADER_ROW_SEMESTA = 1     # asumsi: header di baris 1 (beda dari Detail PT3 yg di baris 2)
DATA_START_ROW_SEMESTA = 2  # asumsi: data mulai baris 2 -- konfirmasi & ubah kalau beda

# Kolom A-O di sheet Semesta
COL_SEMESTA_TANGGAL_NDE = "A"
COL_SEMESTA_PROGRAM = "B"       # PT2 / PT3
COL_SEMESTA_ID_IHLD = "C"
COL_SEMESTA_NAMA_LOKASI = "D"
COL_SEMESTA_STO = "E"
COL_SEMESTA_BATCH = "F"
COL_SEMESTA_BRANCH = "G"
COL_SEMESTA_REGIONAL = "H"      # BANTEN / JAKARTA / Eastern Jabotabek / Jabar / dst
COL_SEMESTA_STATUS_LOP = "V"    # diubah dari I -> V
COL_SEMESTA_POTENSI = "W"       # baru: isian "P1 AUG", "P2 SEP", dst
COL_SEMESTA_FINAL_PORT = "J"
COL_SEMESTA_TGL_FI = "K"
COL_SEMESTA_TGL_GOLIVE = "L"
COL_SEMESTA_UMUR = "M"
COL_SEMESTA_ODP_GOLIVE = "N"
COL_SEMESTA_KETERANGAN = "O"

# Status LOP (kolom V) yang jadi acuan formula FBB — dicocokkan case-insensitive.
STATUS_LOP_GOLIVE = "5. Golive"
STATUS_LOP_DROP_MOM = "6.3. Drop MOM"

# ── Sheet "TARGET" ───────────────────────────────────────────────────
SHEET_NAME_TARGET = os.environ.get("SHEET_NAME_TARGET", "TARGET")
HEADER_ROW_TARGET = 1
DATA_START_ROW_TARGET = 2
# 1 baris = 1 Regional + 1 Program (PT2/PT3) + 1 Bulan + TARGET PORT-nya.
# PENTING: kalau Regional "JABAR" dan "JAKARTA" ditarget terpisah, harus
# jadi 2 baris sendiri-sendiri di sheet ini (bukan digabung "JABAR&JAKARTA"),
# karena data aktual (sheet Semesta) memisahkan kedua Regional itu.
COL_TARGET_REGIONAL = "A"
COL_TARGET_PROGRAM = "B"   # PT2 / PT3
COL_TARGET_BULAN = "C"
COL_TARGET_PORT = "D"

MONTH_NAMES_ID = [
    "januari", "februari", "maret", "april", "mei", "juni",
    "juli", "agustus", "september", "oktober", "november", "desember",
]
MONTH_LABEL_ID = [m.capitalize() for m in MONTH_NAMES_ID]
MONTH_ABBR_EN = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Ambang warna ACH (%) — hijau >90, kuning >50, oranye >25, sisanya merah.
ACH_THRESHOLD_GREEN = 90
ACH_THRESHOLD_YELLOW = 50
ACH_THRESHOLD_ORANGE = 25

# ── Sheet "Detail PT2" ───────────────────────────────────────────────
SHEET_NAME_PT2 = os.environ.get("SHEET_NAME_PT2", "Detail PT2")
HEADER_ROW_PT2 = 1     # asumsi -- konfirmasi & ubah kalau beda
DATA_START_ROW_PT2 = 2  # asumsi -- konfirmasi & ubah kalau beda

COL_PT2_ID_IHLD = "A"
COL_PT2_LOKASI = "B"
COL_PT2_STO = "C"
COL_PT2_SOURCE_ORDER = "D"
COL_PT2_BATCH = "H"
COL_PT2_BRANCH = "J"        # BRANCH TA
COL_PT2_REGIONAL = "L"      # REGIONAL TA
COL_PT2_STATUS_LOP = "M"
COL_PT2_KLASIFIKASI_CANCEL = "P"
COL_PT2_DETAIL_CANCEL = "Q"
COL_PT2_ODP_GOLIVE = "U"
COL_PT2_FINAL_PORT = "AC"
COL_PT2_TGL_CLOSE_WO = "AE"
COL_PT2_PROGRESS_H1 = "AH"  # snapshot status per baris di H-1 (kemarin)
COL_PT2_PROGRESS_H1 = "AH"      # status LOP versi kemarin (H-1), buat baris pembanding di tabel

# Urutan status LOP persis seperti dikonfirmasi (loncat dari 3 ke 5 memang disengaja).
PT2_STATUSES = ["0.DROP", "0.KENDALA", "1.DESIGN", "2.APPROVAL", "3.OGP DEPLOY", "5.GOLIVE"]
PT2_GOLIVE_STATUS = "5.GOLIVE"
PT2_EXCLUDE_FROM_DENOM = ["0.DROP", "0.KENDALA"]
PT2_STATUS_COLORS = {
    "0.DROP": "#dc2626",
    "0.KENDALA": "#f97316",
    "1.DESIGN": "#3b82f6",
    "2.APPROVAL": "#8b5cf6",
    "3.OGP DEPLOY": "#eab308",
    "5.GOLIVE": "#16a34a",
}

# ── Env / secrets ──────────────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
PORT = int(os.environ.get("PORT", 5000))