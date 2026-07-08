#!/usr/bin/env python3
import getpass
import hashlib
import secrets

password = getpass.getpass("Portal password: ")
iterations = 260000
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
print(f"pbkdf2_sha256${iterations}${salt}${digest}")
