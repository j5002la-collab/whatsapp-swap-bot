"""BIP340 Schnorr signature implementation (RFC 340).
Uses coincurve for secp256k1 point operations.
"""

import hashlib

# secp256k1 curve order
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def tagged_hash(tag: str, msg: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


def _int_from_bytes(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _bytes_from_int(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _has_even_y(pubkey_bytes: bytes) -> bool:
    """Check if the public key (33-byte compressed or 64-byte uncompressed) has even Y."""
    from coincurve import PublicKey
    pk = PublicKey(pubkey_bytes)
    uncompressed = pk.format(compressed=False)
    # uncompressed is 04 || X || Y (65 bytes)
    y = uncompressed[33:]
    return (y[-1] & 1) == 0


def schnorr_sign(msg: bytes, seckey: bytes, aux_rand: bytes | None = None) -> bytes:
    """BIP340 Schnorr signature.
    
    Returns 64-byte signature: R.x (32 bytes) || s (32 bytes).
    
    Args:
        msg: 32-byte message hash to sign
        seckey: 32-byte secret key
        aux_rand: 32-byte auxiliary randomness (default: zeros)
    """
    from coincurve import PrivateKey, PublicKey

    if len(seckey) != 32:
        raise ValueError("seckey must be 32 bytes")
    if len(msg) != 32:
        raise ValueError("msg must be 32 bytes")

    d0 = _int_from_bytes(seckey)
    if d0 == 0 or d0 >= N:
        raise ValueError("secret key out of range")

    # Public key P = d0 * G
    key = PrivateKey(seckey)
    pk_bytes = key.public_key.format(compressed=True)  # 33 bytes

    if aux_rand is None:
        aux_rand = b"\x00" * 32

    # t = d0 XOR tagged_hash("BIP0340/aux", aux_rand)
    t = d0 ^ _int_from_bytes(tagged_hash("BIP0340/aux", aux_rand))
    if t >= N:
        t %= N

    # k0 = tagged_hash("BIP0340/nonce", t_bytes || P_x || msg) mod N
    P_x = pk_bytes[1:]  # strip prefix
    k0_bytes = tagged_hash("BIP0340/nonce", _bytes_from_int(t) + P_x + msg)
    k0 = _int_from_bytes(k0_bytes) % N
    if k0 == 0:
        k0 = 1  # BIP340: if k0 == 0, set to 1

    # R = k0 * G
    try:
        R_key = PrivateKey(_bytes_from_int(k0))
    except Exception:
        k0 = 1
        R_key = PrivateKey(_bytes_from_int(k0))

    R_pub = R_key.public_key  # type: PublicKey

    # If R has odd Y, negate k
    if not _has_even_y(R_pub.format(compressed=True)):
        k = (N - k0) % N
    else:
        k = k0

    R_x = R_pub.format(compressed=True)[1:]

    # e = tagged_hash("BIP0340/challenge", R_x || P_x || msg)
    e = _int_from_bytes(tagged_hash("BIP0340/challenge", R_x + P_x + msg)) % N

    # s = (k + e * d) mod N
    s = (k + e * d0) % N

    return R_x + _bytes_from_int(s)
