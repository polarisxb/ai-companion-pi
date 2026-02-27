def test_hello_world():
    assert "hello" == "hello"


def test_greeting():
    greeting = "Hello, World!"
    assert greeting.startswith("Hello")
    assert "World" in greeting
