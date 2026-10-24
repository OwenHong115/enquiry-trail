import base64
import hashlib
from app.security.passwords import hash_password, verify_password
from app.api.auth import _DUMMY_PASSWORD_HASH


def test_new_hashes_and_dummy_use_the_selected_work_factor():
    stored = hash_password("correct-example-password")
    assert int(stored.split("$")[1]) == 600_000
    assert int(_DUMMY_PASSWORD_HASH.split("$")[1]) == 600_000
    assert verify_password("correct-example-password", stored)
    assert not verify_password("incorrect-example-password", stored)


def test_existing_hashes_remain_verifiable():
    salt = b"legacy-test-salt!"
    value = hashlib.pbkdf2_hmac("sha256", b"legacy-example-password", salt, 210_000)
    stored = "pbkdf2_sha256$210000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(value).decode()
    assert verify_password("legacy-example-password", stored)
