"""
Run this once on the server to generate a self-signed certificate.
Requires: pip install cryptography
Output: server_cert.pem and key.pem in the same directory as your Flask app.
"""
import datetime
import ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CERT_FILE  = "server_cert.pem"
KEY_FILE   = "key.pem"
SERVER_IP  = "192.168.171.34"   # <-- change if your server IP changes

def generate_self_signed_cert():
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME,        "CZ"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,   "SoftwareInventory"),
        x509.NameAttribute(NameOID.COMMON_NAME,         SERVER_IP),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address(SERVER_IP)),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    print(f"Generated {CERT_FILE} and {KEY_FILE}")
    print(f"Certificate valid for IP: {SERVER_IP} and DNS: localhost")
    print("")
    print("Next steps:")
    print("  1. Keep server_cert.pem and key.pem in your Flask app directory (server uses these)")
    print("  2. Copy server_cert.pem alongside agent.exe or deploy via install.bat to")
    print("     C:\\ProgramData\\DMCPatchAgent\\server_cert.pem")
    print("")
    print("The agent will pin trust to this exact certificate.")
    print("If you regenerate the cert, redeploy server_cert.pem to all machines.")

if __name__ == "__main__":
    generate_self_signed_cert()