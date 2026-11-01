import hashlib
import hmac
import secrets


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt:{salt}:{digest}"


def verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split(":")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_crypto_signature(raw, signature, token):
    key = hashlib.sha256(token.encode()).digest()
    return bool(token and signature) and hmac.compare_digest(
        hmac.new(key, raw, hashlib.sha256).hexdigest(), signature
    )
