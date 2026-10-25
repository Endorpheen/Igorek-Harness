"""Usage counters stay numeric while credential-shaped fields remain redacted."""

import pytest

from factory.core.vault.logging_redaction import redact_mapping


COUNTERS = [
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "prompt_tokens",
    "completion_tokens",
    "max_tokens",
    "num_tokens",
]


@pytest.mark.parametrize("key", COUNTERS)
@pytest.mark.parametrize("value", [0, 7])
def test_known_nonnegative_integer_usage_counts_are_preserved(key, value):
    original = {key: value}
    assert redact_mapping(original) == original
    assert original == {key: value}


@pytest.mark.parametrize("key", COUNTERS)
@pytest.mark.parametrize(
    "value", ["synthetic-secret", -1, True, {"secret": "synthetic"}, [7]]
)
def test_counter_names_cannot_carry_secret_strings_or_arbitrary_values(key, value):
    assert redact_mapping({key: value}) == {key: "[REDACTED]"}


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "refresh_token",
        "password",
        "api_key",
        "private_key",
        "access_token",
        "total_tokens_secret",
        "input_tokens_private",
    ],
)
@pytest.mark.parametrize("value", [7, "synthetic-secret"])
def test_numeric_or_textual_credentials_are_still_redacted(key, value):
    assert redact_mapping({key: value}) == {key: "[REDACTED]"}


def test_nested_usage_counts_and_credential_redaction_coexist_without_mutation():
    original = {
        "usage_ext": {
            "total_tokens": 8,
            "current_usage": {"input_tokens": 3, "output_tokens": 5},
            "api_key": "synthetic-secret",
        },
        "records": [{"total_tokens": 0, "access_token": "synthetic"}],
    }
    result = redact_mapping(original)
    assert result["usage_ext"] == {
        "total_tokens": 8,
        "current_usage": {"input_tokens": 3, "output_tokens": 5},
        "api_key": "[REDACTED]",
    }
    assert result["records"] == [{"total_tokens": 0, "access_token": "[REDACTED]"}]
    assert (
        original["usage_ext"]["api_key"] == "synthetic-secret"
        and original["records"][0]["access_token"] == "synthetic"
    )


def test_counter_allowlist_matches_whole_field_name_case_insensitively():
    assert redact_mapping({"TOTAL_TOKENS": 9, "TOTAL_TOKENS_SECRET": 9}) == {
        "TOTAL_TOKENS": 9,
        "TOTAL_TOKENS_SECRET": "[REDACTED]",
    }
