"""
Generate hash password untuk ditaruh di kolom "password_hash" di users.xlsx.

Dijalankan LOKAL di komputer Anda (bukan di server) -- supaya password asli
tidak pernah ketik/terkirim ke mana-mana selain terminal Anda sendiri.

Cara pakai:
    pip install werkzeug
    python3 hash_password.py

Lalu ketik password yang diinginkan, hash-nya akan dicetak -- copy hasilnya
ke kolom password_hash di baris user yang sesuai di users.xlsx.
"""
import getpass
from werkzeug.security import generate_password_hash

password = getpass.getpass("Ketik password (tidak akan ditampilkan di layar): ")
confirm = getpass.getpass("Ulangi password: ")

if password != confirm:
    print("\nPassword tidak sama, coba lagi.")
else:
    print("\nHash password (copy baris di bawah ini ke kolom password_hash):\n")
    print(generate_password_hash(password))
