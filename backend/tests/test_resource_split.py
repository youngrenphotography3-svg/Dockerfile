# ruff: noqa: S101  -- a test file asserts
"""A batch BRAIN fails for using too much resource is retried in smaller batches."""

import sys

from alpha_harness.engine.packer import (
    MAX_BATCH,
    WorkItem,
    is_resource_failure,
    key_of,
    pack,
    smaller_cap,
)

_PAYLOAD = {
    "type": "REGULAR",
    "settings": {"region": "GLB", "delay": 1, "instrumentType": "EQUITY", "language": "FASTEXPR"},
    "regular": "rank(close)",
}


def test_brain_message_is_recognised() -> None:
    assert is_resource_failure("Your simulation probably took too much resource.")
    assert not is_resource_failure("Unknown variable")
    assert not is_resource_failure(None)


def test_cap_halves_down_to_alone_then_gives_up() -> None:
    assert smaller_cap(10, None) == 5
    assert smaller_cap(5, 5) == 2
    assert smaller_cap(2, 2) == 1
    assert smaller_cap(1, 1) is None
    # The size the batch really had counts, not just the cap: a batch of 4 halves to 2.
    assert smaller_cap(4, None) == 2
    assert smaller_cap(6, 10) == 3


def test_uncapped_key_is_unchanged() -> None:
    assert key_of(_PAYLOAD) == key_of(_PAYLOAD, None)
    assert key_of(_PAYLOAD).max_batch == MAX_BATCH


def test_capped_rows_pack_in_small_batches_apart_from_others() -> None:
    capped = [WorkItem(i, key_of(_PAYLOAD, 2), "t", _PAYLOAD) for i in range(5)]
    plain = [WorkItem(10 + i, key_of(_PAYLOAD), "t", _PAYLOAD) for i in range(3)]
    batches = pack(capped + plain, free_slots=40)
    sizes = sorted(len(b.items) for b in batches)
    assert sizes == [1, 2, 2, 3]
    for batch in batches:
        caps = {item.key.cap for item in batch.items}
        assert len(caps) == 1


if __name__ == "__main__":
    test_brain_message_is_recognised()
    test_cap_halves_down_to_alone_then_gives_up()
    test_uncapped_key_is_unchanged()
    test_capped_rows_pack_in_small_batches_apart_from_others()
    sys.stdout.write("ok\n")
