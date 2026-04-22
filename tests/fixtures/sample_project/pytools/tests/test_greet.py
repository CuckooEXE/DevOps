from mypkg import greet


def test_greet_basic() -> None:
    assert greet("world") == "hello, world!"


def test_greet_uses_name() -> None:
    assert "ada" in greet("ada")
