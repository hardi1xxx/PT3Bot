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

# Order Prioritas (mis. "P1 Agustus") -- opsional, ditampilkan sebagai badge
# di sebelah IHLD/Lokasi pada list "Lokasi Sedang Berjalan & Update Status".
# Kosong -> badge tidak ditampilkan sama sekali.
COL_ORDER_PRIORITAS = "BX"

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
    "DROP MOM": ["01. DROP MOM"],
}

# Urutan kolom di tabel "Rekap Port & LOP per Batch": DROP MOM & DROP
# ditaruh di SEBELUM Perijinan, GOLIVE ditaruh persis SETELAH Finish
# Instalasi -- bukan digabung di ujung seperti sebelumnya (dulu inline di
# sheets_service.py: DASHBOARD_STATUSES + list(PIVOT_STATUS_GROUPS.keys())).
# "08. PEMBERKASAN" sengaja TIDAK dimasukkan (rencananya jadi halaman
# terpisah nanti).
PIVOT_COLUMNS = ["DROP MOM", "DROP"] + DASHBOARD_STATUSES + ["GOLIVE"]

# Nilai di kolom BH yang tidak ditampilkan di "Kategori Drop" (nyasar dari
# input manual, duplikat status Z).
BH_EXCLUDE_VALUES = ["10. BAST"]

# ── Z (Status Fisik) dropdown options ─────────────────────────────────
Z_OPTIONS = [
    "00. DROP",
    "01. DROP MOM",
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
# Diupdate supaya persis mengikuti tabel pengelompokan per kategori (lihat
# STATUS_AA_GROUPS di bawah): "3 Material Delivery" dipecah jadi
# "3.1 Pickup Material" + "3.2. Material Delivery"; "4.7. Terminasi"
# ditambahkan (baru); "Selesai Fisik" dinomori ulang dari 4.7 -> 4.8
# (sekarang satu blok nomor "4.8." dengan "Valins" -- dua item beda, bukan
# duplikat, dibiarkan apa adanya sesuai permintaan).
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
    "3.1 Pickup Material",
    "3.2. Material Delivery",
    "4.1. Galian",
    "4.2. Tanam Tiang",
    "4.3. Tarik Kabel",
    "4.4. instalasi ODP",
    "4.5. instalasi ODC",
    "4.6. Perapihan",
    "4.7. Terminasi",
    "4.8. Selesai Fisik",
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

# Sub Status (AA) dikelompokkan per Status (Z), URUT sesuai tahapan kerja
# di lapangan. Dipakai di form update.html untuk:
#   1. Filter dropdown Sub Status supaya tidak perlu cari di antara 32
#      opsi -- cuma yang relevan dengan Status Z yang lagi dipilih.
#   2. Hitung progress % lebih halus (lihat compute_progress di
#      sheets_service.py): posisi sub status DALAM list ini menentukan
#      seberapa jauh progress di dalam tahap itu, bukan cuma lompat penuh
#      begitu Z pindah tahap seperti sebelumnya.
# Z value yang TIDAK ada key-nya di sini (mis. "0.1 SURVEI") -> dropdown
# AA tidak difilter, progress tetap pakai logic lama (lompat per-tahap).
STATUS_AA_GROUPS = {
    "00. DROP": ["0. DROP", "0.1. Plan Drop"],
    "01. DROP MOM": ["0. DROP", "0.1. Plan Drop"],
    "01. PERIJINAN": [
        "1.2. Survey", "2.1. Negosiasi", "7.1 Kendala", "7.2 HOLD",
    ],
    "02. PERSIAPAN": [
        "1.1 Persiapan", "1.3. Review ED", "1.4. Pengajuan PR-PO", "1.5. Juskeb Swakelola",
        "1.6. Juskeb Permit", "1.7. Revisi Juskeb", "1.8. Kenaikan CC",
        "2.2. Pembayaran Permit TA", "2.3. Pembayaran Permit Mitra", "6. Redesign",
    ],
    "03. MATDEV": ["3.1 Pickup Material", "3.2. Material Delivery"],
    "04. INSTALASI": [
        "4.1. Galian", "4.2. Tanam Tiang", "4.3. Tarik Kabel",
        "4.4. instalasi ODP", "4.5. instalasi ODC", "4.6. Perapihan", "4.7. Terminasi",
    ],
    "05. FINISH INSTALASI": ["4.8. Selesai Fisik", "4.8. Valins", "4.9. Dok GOLIVE"],
    # Semua Z yang masuk stage "golive" di PROGRESS_STAGES (lihat z_values
    # di bawah) pakai urutan sub status yang sama.
    "06. GOLIVE": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
    "07. UT": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
    "08. PEMBERKASAN": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
    "09. REKON": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
    "10. BAST": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
    "10.1 BAST 2025": ["5 GOLIVE", "5.1 Uji Terima", "5.2 Rekon", "5.3 BAST", "5.4 BAST 2025"],
}

# Kategori Drop (kolom BH) -- muncul di form update HANYA saat Status Z
# dipilih "00. DROP" atau "01. DROP MOM" (lihat PROGRESS_DROP_STATUSES).
# Beda dari kolom BH yang dipakai dashboard (rekap "Kategori Drop" di
# index.html, cuma dibaca) -- ini yang MENULIS ke kolom BH itu juga.
KATEGORI_DROP_OPTIONS = [
    "Drop by Tsel",
    "Duplikat Order",
    "Kendala Feeder",
    "Kendala izin lingkungan",
    "Sudah tercover project lain",
    "Sudah tercover Provider lain",
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
    # ASUMSI: "01. DROP MOM" pakai kolom yang sama seperti Drop biasa (BF/BG).
    # Kalau ternyata harus beda, kabari saya untuk saya ganti.
    "01. DROP MOM":          {"date_col": "BF", "note_col": "BG"},
}

# ── Aging per-LOP (panel detail dashboard PT3) ─────────────────────────
# Beda dari COL_TANGGAL_NDE (dipakai khusus di halaman /aging) -- ini
# aging yang tampil di panel "Update Status" waktu sebuah LOP dipilih di
# dashboard PT3, dihitung dari WO terbit (kolom D) sampai hari ini.
COL_WO_TERBIT = "D"

# ── Target Finish Instalasi (kolom AK) — tanggal komit manual ──────────
# Diisi field/PIC sebagai target tanggal SELESAI Instalasi (mulai Finish
# Instalasi). Begitu terisi, dipakai GANTI estimasi otomatis (WO terbit +
# akumulasi target_days) untuk deadline tahap Instalasi dan seterusnya
# (Finish Instalasi, Golive) -- karena komitmen lapangan lebih akurat
# daripada estimasi statis. PINDAH dari kolom AK ke AK (permintaan user).
COL_TARGET_FI = "AK"

# Status Z di mana kolom AK WAJIB terisi setiap kali update (boleh nilai
# lama yang sudah ada di sheet -- tidak harus diisi ulang tiap update,
# tapi TIDAK BOLEH kosong sama sekali). Begitu status sudah mencapai
# Finish Instalasi (atau lebih), target ini sudah tidak relevan lagi
# (bukan wajib).
PRE_FINISH_INSTALL_STATUSES = [
    "0.1 SURVEI",
    "01. PERIJINAN",
    "02. PERSIAPAN",
    "03. MATDEV",
    "04. INSTALASI",
]

# ── Progress % & deadline per tahapan pekerjaan LOP ────────────────────
# Dipakai di panel detail LOP dashboard PT3 untuk menghitung:
#   - progress_percent: berapa % LOP ini sudah selesai
#   - deadline per tahapan: WO_terbit + akumulasi target_days s/d tahapan itu
#
# Aturan progress (persis seperti disepakati): bobot suatu tahap baru
# dihitung SETELAH status pindah ke tahap berikutnya (bukan saat masih di
# tahap itu) -- KECUALI Golive, yang bobotnya langsung dihitung begitu
# status dipilih ke Golive (karena Golive adalah tahap terakhir).
# NDE selalu otomatis 5% begitu order/baris muncul, terlepas dari kolom Z.
#
# target_days = estimasi lama pengerjaan tahap ini (hari kalender), dipakai
# untuk menghitung deadline kumulatif dari WO terbit. z_values = None berarti
# "NDE", tidak terikat ke satu nilai kolom Z tertentu (selalu tercapai begitu
# baris ada). Kalau target_days ternyata maksudnya hari KERJA (bukan hari
# kalender), atau bobotnya mau diubah, tinggal edit angka di sini -- tidak
# ada logic lain yang perlu disentuh.
PROGRESS_STAGES = [
    {"key": "nde",            "label": "NDE",               "weight": 5,  "target_days": 1, "z_values": None},
    {"key": "survey",         "label": "Survey",            "weight": 5,  "target_days": 1, "z_values": ["0.1 SURVEI"]},
    {"key": "permit",         "label": "Permit",            "weight": 10, "target_days": 2, "z_values": ["01. PERIJINAN"]},
    {"key": "prepair",        "label": "Prepair",           "weight": 20, "target_days": 4, "z_values": ["02. PERSIAPAN"]},
    {"key": "matdev",         "label": "Matdev",            "weight": 10, "target_days": 2, "z_values": ["03. MATDEV"]},
    {"key": "instalasi",      "label": "Instalasi",         "weight": 30, "target_days": 6, "z_values": ["04. INSTALASI"]},
    {"key": "finish_install", "label": "Finish Instalasi",  "weight": 15, "target_days": 3, "z_values": ["05. FINISH INSTALASI"]},
    {"key": "golive",         "label": "Golive",            "weight": 5,  "target_days": 1,
     "z_values": ["06. GOLIVE", "07. UT", "08. PEMBERKASAN", "09. REKON", "10. BAST", "10.1 BAST 2025"]},
]
# Status Z yang dianggap "Drop" -- tidak masuk sequence progress di atas,
# ditampilkan terpisah (bukan %) di panel detail.
PROGRESS_DROP_STATUSES = ["00. DROP", "01. DROP MOM"]

# ── Aging (halaman /aging, terpisah dari aging per-LOP di atas) ────────
COL_TANGGAL_NDE = "AP"   # tanggal awal (start) untuk hitung aging
AGING_WARNING_DAYS = 35   # > segini = kuning ("Perhatian")
AGING_CRITICAL_DAYS = 60  # > segini = merah ("Kritis")

# Status Z yang aging-nya dihitung sampai tanggal TETAP (bukan sampai hari
# ini) -> AP s/d kolom di bawah ini. Fallback ke hari ini kalau kolomnya
# kosong/tidak valid. Status lain di luar daftar ini pakai "hari ini".
AGING_FIXED_END_COLUMNS = {
    "00. DROP":        "BF",
    "10.1 BAST 2025":  "BF",
    "01. DROP MOM":    "BF",
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
COL_SEMESTA_STATUS_LOP = "I"    # posisi aktual di sheet
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
# Potensi (di ringkasan FBB) = lokasi yang sudah di status ini (siap/berpotensi Golive).
STATUS_POTENSI = "3.OGP DEPLOY"

# ── Sheet "TARGET" ───────────────────────────────────────────────────
SHEET_NAME_TARGET = os.environ.get("SHEET_NAME_TARGET", "TARGET")
HEADER_ROW_TARGET = 1
DATA_START_ROW_TARGET = 2
# Urutan kolom sebenarnya: REGIONAL, PROGRAM, BULAN, TARGET PORT (1 baris
# = 1 Regional + 1 Program (PT2/PT3) + 1 Bulan -- BUKAN kolom PT2/PT3
# terpisah seperti asumsi sebelumnya).
COL_TARGET_REGIONAL = "A"
COL_TARGET_PROGRAM = "B"
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

# ── Dokumen per-LOP (upload ke Google Drive) ────────────────────────────
# Folder Drive tujuan (di-share Editor ke service account -- lihat
# /debug/sheet-check untuk email-nya). Diambil dari env var Railway.
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# Kredensial OAuth akun Drive PRIBADI (dipakai drive_service.py untuk semua
# upload -- dokumen wajib maupun KML). Sebelumnya env var ini sudah di-set
# di Railway tapi TIDAK PERNAH dibaca di config.py, jadi drive_service.py
# selalu menganggapnya kosong (getattr(config, name) -> None) walaupun
# env var-nya sendiri sudah benar. Ditambahkan di sini supaya benar-benar
# ke-load dari environment.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_REFRESH_TOKEN = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")

# Tiap jenis dokumen wajib diupload begitu status Z mencapai
# required_status (boleh sudah ada dari update sebelumnya -- tidak perlu
# upload ulang tiap update, lihat sheets_service.validate_documents_for_status).
# link_col menyimpan URL Drive file terkini; log_col menyimpan riwayat
# catatan revisi (apa yang diubah/dihapus/ditambah), terbaru di baris
# paling atas -- MIRIP pola catatan status di STATUS_COLUMN_MAP, tapi
# revisi dokumen MENIMPA file lamanya (bukan menyimpan semua versi).
#
# required_status DI SINI JUGA menentukan kapan tombol Upload muncul di
# panel detail LOP (lihat index.html renderDocumentsSection): SEBELUM
# required_status -> slot disembunyikan sepenuhnya, PAS di required_status
# -> tombol upload muncul, SETELAH required_status -> jadi view-only
# (cuma link file, tanpa tombol upload). Urutan "sebelum/pas/setelah"
# dihitung dari PROGRESS_STAGES di bawah.
DOCUMENT_TYPES = {
    "bast": {
        "label": "BAST",
        "required_status": "06. GOLIVE",
        "link_col": "BR",
        "log_col": "BS",
    },
    "foto_instalasi": {
        "label": "Foto Instalasi",
        "required_status": "05. FINISH INSTALASI",
        "link_col": "BT",
        "log_col": "BU",
    },
    "berita_acara_perijinan": {
        "label": "Berita Acara Perijinan",
        # Diubah dari "03. MATDEV" ke "01. PERIJINAN" -- upload-nya sekarang
        # HANYA muncul di status Perijinan (bukan Matdev), jadi wajib-nya
        # ikut dipindah ke situ juga supaya tidak ada status yang butuh
        # dokumen ini tapi tombol upload-nya sudah disembunyikan.
        "required_status": "01. PERIJINAN",
        "link_col": "BV",
        "log_col": "BW",
    },
}

# Status Z yang panel dokumennya TIDAK ditampilkan sama sekali di halaman
# ini (BAST rencananya dikelola di halaman terpisah nanti, mirip
# Pemberkasan) -- jadi juga dikeluarkan dari pengecekan wajib supaya tidak
# memblokir simpan status Golive padahal tidak ada cara upload-nya di sini.
DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE = ["bast"]

# Sementara DIMATIKAN: upload masih error (HttpError) di Railway, jadi
# jangan sampai LOP kepending gara-gara dokumen wajib yang gagal keupload.
# Upload tetap bisa dicoba (fitur tidak dihapus) -- ini cuma matiin
# BLOKIR-nya kalau kosong. Set True lagi begitu penyebab HttpError-nya
# sudah ketemu & fix.
DOCUMENT_UPLOAD_REQUIRED = False

# ── KML per-LOP (opsional, TIDAK wajib & TIDAK memblokir simpan status) ─
# Folder Drive KHUSUS KML -- SENGAJA TERPISAH dari DRIVE_FOLDER_ID (BAST/
# Foto Instalasi/Berita Acara) supaya rapi per-kategori. Diambil dari env
# var Railway. Berbeda dari dokumen wajib: KML tidak disimpan sebagai
# link_col/log_col di sheet -- Drive folder itu sendiri jadi sumber
# datanya (folder dibaca langsung tiap kali panel dibuka), jadi tidak ada
# tulis-ke-sheet sama sekali -> upload & baca jadi cepat & sederhana, dan
# LOP boleh punya lebih dari 1 file KML (bukan slot revisi tunggal).
KML_FOLDER_ID = os.environ.get("KML_FOLDER_ID", "")

# Status Z di mana slot upload KML ditampilkan di panel update (cuma soal
# tampilan tombol upload -- list file yang SUDAH ada tetap kelihatan di
# status manapun). Tidak ada validasi wajib apapun terkait KML.
KML_VISIBLE_STATUSES = ["01. PERIJINAN", "02. PERSIAPAN", "03. MATDEV", "04. INSTALASI"]

# Reverse index: status Z -> daftar doc_key yang wajib di status itu
# (BAST dikecualikan -- lihat DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE di atas).
DOCUMENT_TYPES_BY_STATUS = {}
for _dkey, _dmeta in DOCUMENT_TYPES.items():
    if _dkey in DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE:
        continue
    DOCUMENT_TYPES_BY_STATUS.setdefault(_dmeta["required_status"], []).append(_dkey)

# ── Env / secrets ──────────────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
PORT = int(os.environ.get("PORT", 5000))

# ── Spreadsheet MBB (All Node B) & OLO ──────────────────────────────────
# Spreadsheet TERPISAH dari "Detail PT3" -- tetap private, dibaca lewat
# service account yang sama (GOOGLE_SERVICE_ACCOUNT_JSON di atas). Service
# account itu harus di-Share ke spreadsheet ini (akses Viewer cukup) --
# lihat client_email di endpoint /debug/sheet-check.
MBB_OLO_SPREADSHEET_ID = os.environ.get(
    "MBB_OLO_SPREADSHEET_ID", "1h1NBs7k4rCibwFvNVu9t0rIlq-TuF7sh6YZvxhu9VqQ"
)
MBB_SHEET_GID = os.environ.get("MBB_SHEET_GID", "212134262")   # tab: All Node B
OLO_SHEET_GID = os.environ.get("OLO_SHEET_GID", "487400008")    # tab: OLO

# Kolom MBB: A-V, X-AA, AB (Tanggal LI), AD-AU, BB-BC, BP, BY, CM, CZ
# (label harus SEJAJAR urutan dengan MBB_RANGES kalau di-expand satu-satu).
MBB_RANGES = [("A", "V"), ("X", "AA"), ("AB", "AB"), ("AD", "AU"), ("BB", "BC"), ("BP", "BP"), ("BY", "BY"), ("CM", "CM"), ("CZ", "CZ")]
MBB_LABELS = [
    "TAHUN", "Plan Deploy", "Tipe Order", "Sub Sistem", "SITE ID", "WITEL", "STO", "SITE NAME", "Lat", "Long",
    "Jarak PO", "Catuan PO", "Nilai PO", "DASAR KERJA", "REG TSEL", "Tower Provider", "Status Tsel", "Status Recti", "Target RFI", "BULAN PLAN",
    "Status Pekerjaan", "Note Progress",
    "Waspang", "Mitra", "Skema Kemitraan", "Target FI/L0",
    "Tanggal LI",
    "Panjang Kabel", "Jenis Kabel", "Kapasitas Kabel", "Tiang", "Nilai BoQ (Survey)", "Kategori Comcase", "Nilai Comcase", "CC/BoQ", "Nilai Survey + CC", "MoM PO",
    "Tanggal NIM", "NIM", "Surat Permohonan ONT", "Tgl Submit", "NDE Pemenuhan", "Tgl Approve", "Merk", "Tipe ONT",
    "SP SMILE", "Nilai BAST",
    "ID iHLD",
    "NAMA WASPANG TA",
    "STATUS DRM",
    "Status Pekerjaan H-1",
]
MBB_HEADER_CHECK_COL = "A"
MBB_HEADER_CHECK_VALUE = "TAHUN"
MBB_KEY_COL = "E"  # SITE ID -- baris tanpa ini dibuang (bukan data asli)

# Kolom OLO: F-AX
OLO_RANGES = [("F", "AX")]
OLO_LABELS = [
    "SUB SISTEM", "Type Order", "REGION", "BRANCH", "STO", "STATUS IHLD", "ID IHLD", "NAMA iHLD", "NO AO", "NAMA PROYEK",
    "LOKASI", "STATUS PEKERJAAN", "NILAI PO", "RESUME", "PLAN GOLIVE", "REALISASI GOLIVE", "NAMA ALPRO", "ID SW", "KETERANGAN DROP", "NDE ORDER",
    "END DATE / TODAY", "SLA (AGING ORDER)", "GROUPING SLA", "MITRA", "WASPANG", "NIK WASPANG", "NILAI IHLD", "NILAI BAST", "TANGGAL SUBMITED EPROP", "REGION (JABO JABAR)",
    "Column 1", "PLAN RFS", "REALISASI RFS", "KOLOM HO", "PM", "NILAI SURVEY ( ONSITE )", "Nilai Comcase", "Kategori Comcase", "Status Approcal Comcase", "NDE UT PERMOHONAN",
    "NDE UT PENUNJUKKAN", "TIM UT NDE", "STATUS UT", "BA DROP", "NO SP",
]
OLO_HEADER_CHECK_COL = "F"
OLO_HEADER_CHECK_VALUE = "SUB SISTEM"
OLO_KEY_COL = "O"  # NAMA PROYEK -- baris tanpa ini dibuang