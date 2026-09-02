"""Unit tests for normalization utilities."""

from macloader.detection.normalize import (
    extract_machine_type,
    normalize_dmi_string,
    normalize_hex_id,
)


def test_normalize_hex_id() -> None:
    assert normalize_hex_id("0x8086") == "8086"
    assert normalize_hex_id("8086") == "8086"
    assert normalize_hex_id(" 0x5917 ") == "5917"
    assert normalize_hex_id("1d10") == "1d10"
    assert normalize_hex_id("0x1D10") == "1d10"
    assert normalize_hex_id("1") == "0001"
    assert normalize_hex_id("030000", length=6) == "030000"
    assert normalize_hex_id("") is None
    assert normalize_hex_id(None) is None
    assert normalize_hex_id("invalid_hex") is None


def test_normalize_hex_id_truncates_leading_chars_when_overlong() -> None:
    # Overlong hex IDs must be truncated from the right, keeping the
    # leading (most significant) characters — not the trailing ones.
    assert normalize_hex_id("12345", length=4) == "1234"
    assert normalize_hex_id("0012a8086", length=4) == "0012"


def test_normalize_dmi_string() -> None:
    assert normalize_dmi_string("  LENOVO  ") == "LENOVO"
    assert normalize_dmi_string("ThinkPad T480s\x00\n") == "ThinkPad T480s"
    assert normalize_dmi_string(None) == ""


def test_extract_machine_type() -> None:
    assert extract_machine_type("ThinkPad T480s", "20L7CTO1WW") == "20L7"
    assert extract_machine_type("20L8001WUS", "ThinkPad T480s") == "20L8"
    assert extract_machine_type("ThinkPad T480", "20L5CTO1WW") == "20L5"
    assert extract_machine_type("ThinkPad T480", "20L6001VUS") == "20L6"
    assert extract_machine_type("ThinkPad X1 Carbon 6th", "20KH002JUS") == "20KH"
    assert extract_machine_type("1.0", "XPS 13 9370") is None
    assert extract_machine_type(None, None) is None
