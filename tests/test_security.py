from ai_workspace.core.security import hash_password, verify_password


def test_hash_password_and_verify_correct_and_wrong_passwords() -> None:
    password = "a-test-password"

    password_hash = hash_password(password)

    assert isinstance(password_hash, str)
    assert password_hash != password
    assert verify_password(password, password_hash) is True
    assert verify_password("a-wrong-password", password_hash) is False


def test_hash_password_uses_a_random_salt() -> None:
    password = "the-same-test-password"

    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash != second_hash
    assert verify_password(password, first_hash) is True
    assert verify_password(password, second_hash) is True
