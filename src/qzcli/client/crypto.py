"""Browser-compatible custom RSA used for the CAS login password field.

This is a non-standard RSA implementation that mirrors the JavaScript the
platform's login page runs in the browser. It must match bit-for-bit or login
silently fails, so this is a near-verbatim port of the reference Python and is
covered by a known-answer test (see tests/test_crypto.py).

Key behaviours that must not drift:
- plaintext bytes are taken as ``ord(c)`` per character (not UTF-8 encoding);
- blocks are ``2 * high_index(modulus)`` bytes, zero-padded at the tail;
- each 16-bit little-endian digit packs as ``b0 + (b1 << 8)`` into the block;
- ciphertext hex is left-zero-padded to ``4 * (high_index + 1)`` chars;
- multiple blocks join with spaces, then spaces are stripped from the result.
"""

from __future__ import annotations


def hex2int(hex_string: str) -> int:
    hex_string = hex_string.strip()
    if hex_string.startswith(("0x", "0X")):
        hex_string = hex_string[2:]
    return int(hex_string, 16)


def int2hex(number: int, min_length: int = 0) -> str:
    hex_str = format(number, "x")
    if min_length > 0:
        hex_str = hex_str.zfill(min_length)
    return hex_str


class CustomRSA:
    def __init__(self, modulus_hex: str, exponent_hex: str):
        self.modulus = hex2int(modulus_hex)
        self.exponent = hex2int(exponent_hex)
        self.chunk_size = 2 * self._bi_high_index(self.modulus)
        # Match the browser RSA implementation, which renders each 16-bit digit
        # as a 4-character hex chunk and preserves leading zeros within the
        # highest non-zero digit.
        self.ciphertext_hex_length = 4 * (self._bi_high_index(self.modulus) + 1)

    @staticmethod
    def _bi_high_index(n: int) -> int:
        if n == 0:
            return 0
        bit_length = n.bit_length()
        return (bit_length + 15) // 16 - 1

    @staticmethod
    def _pow_mod(base: int, exp: int, mod: int) -> int:
        return pow(base, exp, mod)

    @staticmethod
    def _encode_block(byte_array: list[int], start: int, chunk_size: int) -> int:
        block = 0
        digit_index = 0
        for k in range(start, start + chunk_size, 2):
            byte1 = byte_array[k] if k < len(byte_array) else 0
            byte2 = byte_array[k + 1] if k + 1 < len(byte_array) else 0
            digit = byte1 + (byte2 << 8)
            block += digit << (16 * digit_index)
            digit_index += 1
        return block

    def encrypt_string(self, plaintext: str) -> str:
        if not plaintext:
            return ""

        byte_array = [ord(c) for c in plaintext]
        while len(byte_array) % self.chunk_size != 0:
            byte_array.append(0)

        result_parts = []
        for i in range(0, len(byte_array), self.chunk_size):
            block = self._encode_block(byte_array, i, self.chunk_size)
            encrypted = self._pow_mod(block, self.exponent, self.modulus)
            result_parts.append(int2hex(encrypted, self.ciphertext_hex_length))

        return " ".join(result_parts)


class PasswordEncryptor:
    EXPONENT = "010001"
    MODULUS = (
        "008aed7e057fe8f14c73550b0e6467b023616ddc8fa91846d2613cdb7f7621e3"
        "cada4cd5d812d627af6b87727ade4e26d26208b7326815941492b2204c3167ab"
        "2d53df1e3a2c9153bdb7c8c2e968df97a5e7e01cc410f92c4c2c2fba529b3ee98"
        "8ebc1fca99ff5119e036d732c368acf8beba01aa2fdafa45b21e4de4928d0d403"
    )

    def __init__(self):
        self.rsa = CustomRSA(self.MODULUS, self.EXPONENT)

    def encrypt(self, password: str) -> str:
        if self.is_encrypted(password):
            return password
        return self.rsa.encrypt_string(password).replace(" ", "")

    @staticmethod
    def is_encrypted(password: str) -> bool:
        return 254 <= len(password) <= 256 and all(
            c in "0123456789abcdefABCDEF" for c in password
        )


def encrypt_password(password: str) -> str:
    return PasswordEncryptor().encrypt(password)
