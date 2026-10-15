import commitment as C

KEY = bytes(range(32))


def test_vector():
    c = C.commitment(KEY, 42000, "Food & Dining", 1791800142, "Swiggy", "EXPENSE")
    assert c.hex() == "c7f1ca2643e8c475d4d47cd47fbb831357de0178a834d56d797e1eed91f973d9"


def test_tamper_and_wrong_key():
    c = C.commitment(KEY, 42000, "Food & Dining", 1791800142, "Swiggy", "EXPENSE")
    assert C.verify(KEY, c, 42000, "Food & Dining", 1791800142, "Swiggy", "EXPENSE")
    assert not C.verify(KEY, c, 42100, "Food & Dining", 1791800142, "Swiggy", "EXPENSE")
    assert not C.verify(bytes(32), c, 42000, "Food & Dining", 1791800142, "Swiggy", "EXPENSE")


def test_encoding_unambiguous():
    assert C.canonical_message(1, "ab", 2, "c", "T") != C.canonical_message(1, "a", 2, "bc", "T")
