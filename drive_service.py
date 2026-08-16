"""
Google Drive integration for per-LOP document upload & revisi
(BAST, Foto Instalasi, Berita Acara Perijinan).

Pakai SERVICE ACCOUNT YANG SAMA dengan sheets_service.py (lihat
sheets_service._get_credentials()) -- cuma API-nya beda (Drive v3, bukan
Sheets). Supaya nggak circular-import, sheets_service.py mengimpor modul
ini secara normal di bagian atas, tapi modul ini mengimpor sheets_service
secara LOKAL (di dalam fungsi) hanya saat butuh credentials-nya.

Prasyarat sebelum fitur ini bisa jalan:
  1. Folder tujuan di Drive (DRIVE_FOLDER_ID di config.py, dari env var)
     sudah di-share sebagai Editor ke email service account
     (lihat /debug/sheet-check -> "client_email").
  2. Google Drive API sudah di-enable di project Google Cloud yang sama
     dengan yang dipakai Sheets API.
"""
import re
import threading

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

import config

_drive_lock = threading.Lock()
_drive_client = None

FILE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")


def get_drive_client():
    global _drive_client
    with _drive_lock:
        if _drive_client is None:
            import sheets_service  # local import -- hindari circular import saat modul ini dimuat
            creds = sheets_service._get_credentials()
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
            "Tambahkan Folder ID Google Drive di Railway sebelum upload dokumen."
        )
    drive = get_drive_client()
    folder_name = f"Baris {row_num} - {label}".strip()
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"'{config.DRIVE_FOLDER_ID}' in parents and "
        f"name = '{safe_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    res = drive.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    created = drive.files().create(
        body={
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [config.DRIVE_FOLDER_ID],
        },
        fields="id",
    ).execute()
    return created["id"]


def upload_document(row_num: int, lop_label: str, doc_label: str, filename: str,
                     file_stream, mimetype: str):
    """Upload satu file ke subfolder LOP (dibuat kalau belum ada).
    Returns (file_id, view_url)."""
    drive = get_drive_client()
    folder_id = _get_or_create_lop_folder(row_num, lop_label)
    media = MediaIoBaseUpload(file_stream, mimetype=mimetype, resumable=False)
    created = drive.files().create(
        body={"name": f"{doc_label} - {filename}", "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
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
            raise
