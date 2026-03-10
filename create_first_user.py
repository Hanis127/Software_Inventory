"""
Run this once after creating the users table to create your first admin account.
Usage: python create_first_user.py
"""
import hashlib
import os
import sys
sys.path.insert(0, '.')
from db import query

def hash_password(password):
    salt = os.urandom(32)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return salt.hex() + ':' + key.hex()

username = input("Enter username: ").strip().lower()
password = input("Enter password: ").strip()

if len(password) < 6:
    print("Password must be at least 6 characters")
    sys.exit(1)

existing = query("SELECT id FROM users WHERE username = %s", (username,), fetch='one')
if existing:
    print(f"User '{username}' already exists")
    sys.exit(1)

pw_hash = hash_password(password)
query("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, pw_hash))
print(f"User '{username}' created successfully.")