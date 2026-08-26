"""
Google Drive integration for per-LOP document upload & revisi
(BAST, Foto Instalasi, Berita Acara Perijinan).

Pakai OAuth 2.0 akun Drive PRIBADI (bukan lagi service account seperti
sebelumnya). Kredensial ini TERPISAH dari sheets_service.py -- Sheets
tetap pakai service account seperti biasa lewat
sheets_service._get_credentials(); modul ini punya kredensial sendiri
lewat _get_oauth_credentials() di bawah, jadi tidak ada lagi
ketergantungan (bahkan local import) ke sheets_service.

Prasyarat sebelum fitur ini bisa jalan:
  1. Sudah generate refresh_token dengan menjalankan
     get_drive_refresh_token.py SEKALI di komputer lokal (lihat file itu
     untuk instruksinya).
  2. 3 env var berikut sudah di-set di Railway:
       GOOGLE_OAUTH_CLIENT_ID
       GOOGLE_OAUTH_CLIENT_SECRET
       GOOGLE_OAUTH_REFRESH_TOKEN
  3. DRIVE_FOLDER_ID (di config.py, dari env var) sekarang harus berupa
     ID folder yang ADA DI DRIVE AKUN PRIBADI ANDA SENDIRI (yang dipakai
     login waktu generate refresh_token) -- bukan lagi folder yang
     di-share ke service account.
  4. Google Drive API sudah di-enable di project Google Cloud yang sama
     dengan yang dipakai untuk membuat OAuth client ID di atas.
"""
import json as _json
import re
import threading

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

import config

_drive_lock = threading.Lock()
_drive_client = None

FILE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _describe_http_error(e: HttpError) -> str:
    """HttpError bawaan googleapiclient sering ber-str() jadi cuma
    'HttpError: ' kosong -- tidak nampilin status/alasan aslinya sama
    sekali. Ini ekstrak status code + pesan asli dari Google (e.content,
    JSON) supaya error yang sampai ke user (lewat try/except di app.py)
    ada isinya, bukan string kosong."""
    status = getattr(getattr(e, "resp", None), "status", None)
    reason = None
    try:
        body = e.content.decode("utf-8") if isinstance(e.content, bytes) else str(e.content)
        parsed = _json.loads(body)
        reason = parsed.get("error", {}).get("message")
    except Exception:
        reason = None
    if not reason:
        reason = getattr(e, "reason", None) or "(tidak ada detail dari Google)"
    return f"Google Drive API error {status}: {reason}"


def _get_oauth_credentials() -> Credentials:
    """Bangun Credentials dari refresh_token yang sudah disimpan sebagai
    env var (lihat get_drive_refresh_token.py). google-auth otomatis
    minta access_token baru lewat refresh_token setiap kali kadaluarsa,
    jadi tidak perlu login ulang manual selama refresh_token belum
    dicabut."""
    missing = [
        name for name in
        ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")
        if not getattr(config, name, None)
    ]
    if missing:
        raise RuntimeError(
            "Env var OAuth Drive belum lengkap: " + ", ".join(missing) + ". "
            "Jalankan get_drive_refresh_token.py dulu lalu set env var itu di Railway."
        )
    creds = Credentials(
        token=None,
        refresh_token=config.GOOGLE_OAUTH_REFRESH_TOKEN,
        client_id=config.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=DRIVE_SCOPES,
    )
    creds.refresh(Request())  # gagal cepat & jelas kalau refresh_token invalid/dicabut
    return creds


def get_drive_client():
    global _drive_client
    with _drive_lock:
        if _drive_client is None:
            creds = _get_oauth_credentials()
            _drive_client = build("drive", "v3", credentials=creds, cache_discovery=False)
        return _drive_client


def _extract_file_id(url: str):
    if not url:
        return None
    m = FILE_ID_RE.search(url)
    return m.group(1) if m else None


def _get_or_create_lop_folder(row_num: int, label: str) -> str:
    """Subfolder di dalam DRIVE_FOLDER_ID khusus 1 LOP -- dibuat sekali,
    dipakai ulang untuk semua jenis dokumen LOP itu (BAST, Foto Instalasi,
    Berita Acara Perijinan semua masuk 1 folder yang sama biar rapi)."""
    if not config.DRIVE_FOLDER_ID:
        raise RuntimeError(
            "DRIVE_FOLDER_ID belum di-set di environment variable. "
            "Tambahkan Folder ID Google Drive (di Drive pribadi Anda) di "
            "Railway sebelum upload dokumen."
        )
    drive = get_drive_client()
    folder_name = f"Baris {row_num} - {label}".strip()
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"'{config.DRIVE_FOLDER_ID}' in parents and "
        f"name = '{safe_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    try:
        res = drive.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    try:
        created = drive.files().create(
            body={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [config.DRIVE_FOLDER_ID],
            },
            fields="id",
        ).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    return created["id"]


def upload_document(row_num: int, lop_label: str, doc_label: str, filename: str,
                     file_stream, mimetype: str):
    """Upload satu file ke subfolder LOP (dibuat kalau belum ada).
    Returns (file_id, view_url)."""
    drive = get_drive_client()
    folder_id = _get_or_create_lop_folder(row_num, lop_label)
    media = MediaIoBaseUpload(file_stream, mimetype=mimetype, resumable=False)
    try:
        created = drive.files().create(
            body={"name": f"{doc_label} - {filename}", "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
        ).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    file_id = created["id"]

    # Biar file bisa langsung dibuka dari dashboard tanpa perlu di-share
    # manual satu-satu. Kalau kebijakan org melarang sharing publik, upload
    # tetap berhasil (link tersimpan), cuma view-nya jadi terbatas ke akun
    # yang sudah punya akses ke folder.
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except HttpError:
        pass

    view_url = created.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, view_url


def delete_document(url: str):
    """Hapus file lama di Drive berdasarkan URL yang tersimpan di sheet.
    Aman dipanggil meski file sudah tidak ada (404 diabaikan)."""
    file_id = _extract_file_id(url)
    if not file_id:
        return
    drive = get_drive_client()
    try:
        drive.files().delete(fileId=file_id).execute()
    except HttpError as e:
        if e.resp.status != 404:
            raise RuntimeError(_describe_http_error(e)) from e