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


def _get_or_create_lop_folder(row_num: int, label: str, parent_folder_id: str = None) -> str:
    """Subfolder di dalam parent_folder_id (default: config.DRIVE_FOLDER_ID)
    khusus 1 LOP -- dibuat sekali, dipakai ulang untuk semua jenis dokumen
    LOP itu. parent_folder_id dibuat bisa diisi supaya folder KML
    (config.KML_FOLDER_ID) bisa punya struktur subfolder per-LOP yang sama
    tapi TERPISAH dari folder dokumen BAST/Foto/Berita Acara."""
    parent_folder_id = parent_folder_id or config.DRIVE_FOLDER_ID
    if not parent_folder_id:
        raise RuntimeError(
            "Folder ID Drive tujuan belum di-set di environment variable. "
            "Tambahkan Folder ID Google Drive di Railway sebelum upload dokumen."
        )
    drive = get_drive_client()
    folder_name = f"Baris {row_num} - {label}".strip()
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"'{parent_folder_id}' in parents and "
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
                "parents": [parent_folder_id],
            },
            fields="id",
        ).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    return created["id"]


def _find_lop_folder(row_num: int, label: str, parent_folder_id: str):
    """Sama seperti _get_or_create_lop_folder, TAPI tidak membuat folder
    kalau belum ada -- return None saja. Dipakai untuk LISTING (mis. lihat
    KML yang sudah diupload): LOP yang belum pernah upload apa-apa tidak
    perlu bikin folder kosong di Drive, dan baris yang belum ada isinya
    langsung selesai tanpa request 'create' tambahan (tetap cepat)."""
    if not parent_folder_id:
        return None
    drive = get_drive_client()
    folder_name = f"Baris {row_num} - {label}".strip()
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"'{parent_folder_id}' in parents and "
        f"name = '{safe_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    try:
        res = drive.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    files = res.get("files", [])
    return files[0]["id"] if files else None


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


# ── KML per-LOP (opsional, folder TERPISAH dari dokumen wajib) ─────────
# Tidak seperti upload_document() di atas: KML TIDAK menimpa file lama --
# 1 LOP boleh punya banyak file KML sekaligus (bukan 1 slot revisi), jadi
# tidak perlu simpan link ke sheet, cukup baca langsung dari Drive tiap
# panel dibuka.

def upload_kml(row_num: int, lop_label: str, filename: str, file_stream, mimetype: str):
    """Upload 1 file KML ke subfolder LOP di dalam KML_FOLDER_ID (folder
    khusus KML, terpisah dari BAST/Foto Instalasi/Berita Acara Perijinan).
    Returns dict {id, name, url}."""
    if not config.KML_FOLDER_ID:
        raise RuntimeError(
            "KML_FOLDER_ID belum di-set di environment variable. "
            "Tambahkan Folder ID Google Drive khusus KML di Railway sebelum upload."
        )
    drive = get_drive_client()
    folder_id = _get_or_create_lop_folder(row_num, lop_label, parent_folder_id=config.KML_FOLDER_ID)
    media = MediaIoBaseUpload(file_stream, mimetype=mimetype, resumable=False)
    try:
        created = drive.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    file_id = created["id"]

    # Sama seperti dokumen lain: biar bisa langsung dibuka/preview tanpa
    # share manual. Kalau kebijakan org melarang sharing publik, upload
    # tetap berhasil, cuma view-nya terbatas ke akun yang punya akses folder.
    try:
        drive.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    except HttpError:
        pass

    view_url = created.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    return {"id": file_id, "name": created.get("name", filename), "url": view_url}


def list_kml_files(row_num: int, lop_label: str):
    """List semua file KML yang sudah diupload untuk 1 LOP, terbaru duluan.
    Kalau folder LOP ini belum pernah dibuat (belum ada upload sama
    sekali) -> langsung return [] TANPA membuat folder & tanpa request
    'list files' tambahan, supaya baris yang belum ada KML-nya tetap
    cepat dibuka (cuma 1 request 'cari folder', bukan 2)."""
    if not config.KML_FOLDER_ID:
        return []
    folder_id = _find_lop_folder(row_num, lop_label, config.KML_FOLDER_ID)
    if not folder_id:
        return []
    drive = get_drive_client()
    try:
        res = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id, name, webViewLink, createdTime)",
            orderBy="createdTime desc",
            pageSize=50,
        ).execute()
    except HttpError as e:
        raise RuntimeError(_describe_http_error(e)) from e
    files = res.get("files", [])
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view",
            "preview_url": f"https://drive.google.com/file/d/{f['id']}/preview",
            "created": f.get("createdTime"),
        }
        for f in files
    ]