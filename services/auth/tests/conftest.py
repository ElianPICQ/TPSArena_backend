"""Throwaway Postgres and Redis containers for this test run (needs Docker; nothing to start by hand).

Started at import, before the app is imported: settings and clients are created at import time.
"""

import atexit
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from testcontainers.community.redis import RedisContainer
from testcontainers.community.postgres import PostgresContainer

SERVICE_DIR = Path(__file__).resolve().parent.parent

postgres = PostgresContainer("postgres:17", username="auth_svc", password="test", dbname="auth", driver="asyncpg")
redis_container = RedisContainer("redis:7")
postgres.start()
redis_container.start()
atexit.register(postgres.stop)
atexit.register(redis_container.stop)

key_file = Path(tempfile.mkdtemp()) / "jwt_private.pem"
key_file.write_bytes(
    Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)

os.environ["DATABASE_URL"] = postgres.get_connection_url()
os.environ["REDIS_URL"] = (
    f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
)
os.environ["JWT_PRIVATE_KEY_PATH"] = str(key_file)

subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE_DIR, check=True, env=os.environ)
