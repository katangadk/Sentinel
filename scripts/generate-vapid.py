"""Generate Web Push credentials in an owner-only directory; don't print secrets."""
import base64
import os
import sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

directory = Path(sys.argv[1] if len(sys.argv) > 1 else "data/vapid")
os.umask(0o077)
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
private_path = directory / "private.pem"
public_path = directory / "public.txt"
if private_path.exists() or public_path.exists():
    sys.exit("Refusing to overwrite existing VAPID keys")
vapid = Vapid()
vapid.generate_keys()
vapid.save_key(str(private_path))
encoded = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
public_path.write_text(base64.urlsafe_b64encode(encoded).rstrip(b"=").decode())
private_path.chmod(0o600)
public_path.chmod(0o600)
print(f"Created {private_path} and {public_path}. Set the private path and public value in your server environment.")
