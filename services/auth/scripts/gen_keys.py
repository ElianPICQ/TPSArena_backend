"""Generate the Ed25519 key pair used by Auth to sign JWTs.

Usage, from services/auth: uv run python scripts/gen_keys.py [--force]
Writes keys/jwt_private.pem and keys/jwt_public.pem in services/auth (git-ignored).
"""

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEYS_DIR = Path(__file__).resolve().parent.parent / "keys"


def main() -> None:
    private_path = KEYS_DIR / "jwt_private.pem"
    public_path = KEYS_DIR / "jwt_public.pem"
    if private_path.exists() and "--force" not in sys.argv:
        print(f"{private_path} already exists, use --force to overwrite")
        return

    KEYS_DIR.mkdir(exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(f"Wrote {private_path} and {public_path}")


if __name__ == "__main__":
    main()
