"""
Run this once on the server to generate a self-signed certificate.
Requires: pip install pyopenssl
Output: cert.pem and key.pem in the same directory as your Flask app.
"""
from OpenSSL import crypto
import os

CERT_FILE = "server_cert.pem"
KEY_FILE  = "key.pem"

def generate_self_signed_cert():
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 2048)

    cert = crypto.X509()
    cert.get_subject().C  = "CZ"
    cert.get_subject().O  = "SoftwareInventory"
    cert.get_subject().CN = "localhost"
    cert.set_serial_number(1)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)  # 10 years
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, "sha256")

    with open(CERT_FILE, "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
    with open(KEY_FILE, "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))

    print(f"Generated {CERT_FILE} and {KEY_FILE}")
    print("Copy both files to your Flask app directory.")

if __name__ == "__main__":
    generate_self_signed_cert()