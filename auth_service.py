"""
Login server-side untuk INTRA.

PENTING soal keamanan (supaya jelas kenapa desainnya begini): file
users.xlsx dibaca di SINI, di server -- TIDAK PERNAH dikirim ke browser
dalam bentuk apapun. Jadi user (via Inspect Element / tab Network) tidak
akan pernah bisa melihat isi users.xlsx sama sekali, terlepas dari apakah
file itu sendiri "dienkripsi" atau tidak -- bedanya jauh dari kalau ini
app React/JS yang jalan di browser (di situ SEMUA data yang dipakai buat
cek login otomatis ikut terkirim ke browser, makanya rawan).

Password tetap di-hash (werkzeug generate_password_hash/check_password_hash
-- pakai scrypt, sudah termasuk salt otomatis) sebagai lapisan tambahan,
supaya kalau file users.xlsx ini somehow bocor/ke-commit ke git dsb,
password asli user tetap tidak langsung kebaca.
"""
import functools
import os

import openpyxl
from flask import session, redirect, url_for, request
from werkzeug.security import check_password_hash

USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.xlsx")

ROLE_LABELS = {
    "developer": "Developer",
    "admin": "Admin",
    "manager": "Manager",
    "user": "User",
}


def load_users():
    """Baca users.xlsx tiap kali dipanggil -- SENGAJA tidak di-cache, supaya
    perubahan (tambah/hapus/reset password user) di file langsung kepakai
    tanpa perlu restart server. File-nya kecil (jumlah user terbatas), jadi
    baca ulang tiap request tidak masalah dari sisi performa."""
    if not os.path.exists(USERS_FILE):
        return []
    wb = openpyxl.load_workbook(USERS_FILE, read_only=True, data_only=True)
    ws = wb["users"] if "users" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    users = []
    for r in rows:
        if not r or not r[0]:
            continue
        nik, name, password_hash, role, project = (list(r) + [None] * 5)[:5]
        users.append({
            "nik": str(nik).strip(),
            "name": (name or "").strip(),
            "password_hash": (password_hash or "").strip(),
            "role": (role or "user").strip().lower(),
            "project": (project or "").strip().upper() or "ALL",
        })
    return users


def verify_login(nik, password):
    """Return dict user kalau NIK + password cocok, None kalau tidak."""
    nik = (nik or "").strip()
    if not nik or not password:
        return None
    for u in load_users():
        if u["nik"] == nik and u["password_hash"] and check_password_hash(u["password_hash"], password):
            return u
    return None


def current_user():
    return session.get("user")


def can_access_menu(user, key):
    """Sama aturan dengan draft React sebelumnya: developer/admin/manager
    bebas akses semua menu, role 'user' cuma menu sesuai kolom project-nya
    di users.xlsx ('ALL' juga bebas akses semua)."""
    if not user:
        return False
    if user["role"] in ("developer", "admin", "manager"):
        return True
    return user["project"] == "ALL" or user["project"] == key


def login_required(view_func):
    """Decorator: redirect ke /login kalau belum login. Simpan halaman yang
    dituju di ?next= supaya begitu login sukses, langsung diarahkan balik
    ke situ (bukan selalu ke halaman pilihan project)."""
    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped
