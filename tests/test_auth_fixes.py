# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""Auth fixes: the Persona address survives a body-only inquiry."""

from alpha_harness.brain.errors import persona_url


def test_persona_url_resolves_a_relative_location() -> None:
    url = persona_url("https://api.worldquantbrain.com/", "inq_123")
    assert url.startswith("https://api.worldquantbrain.com/")
    assert "inq_123" in url


if __name__ == "__main__":
    test_persona_url_resolves_a_relative_location()
    print("ok")
