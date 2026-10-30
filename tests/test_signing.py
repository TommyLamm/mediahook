from app.signing import sign, verify

KEY = b"k" * 32


def test_valid_signature():
    signature = sign(KEY, "job12345", "a_01.jpg", 2000)
    assert verify(KEY, "job12345", "a_01.jpg", 2000, signature, now=1000)


def test_expired_signature():
    signature = sign(KEY, "job12345", "a_01.jpg", 2000)
    assert not verify(KEY, "job12345", "a_01.jpg", 2000, signature, now=2001)


def test_tampered_fields():
    signature = sign(KEY, "job12345", "a_01.jpg", 2000)
    assert not verify(KEY, "job12345", "a_02.jpg", 2000, signature, now=1000)
    assert not verify(KEY, "job99999", "a_01.jpg", 2000, signature, now=1000)
    assert not verify(KEY, "job12345", "a_01.jpg", 3000, signature, now=1000)
    assert not verify(b"other", "job12345", "a_01.jpg", 2000, signature, now=1000)
