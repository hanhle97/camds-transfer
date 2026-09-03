from camds_imds_importer.core.logging import sanitize_log_data


def test_sensitive_values_are_redacted_recursively() -> None:
    data = sanitize_log_data({"username": "user", "password": "secret", "nested": {"token": "abc"}})
    assert data == {"username": "user", "password": "***REDACTED***", "nested": {"token": "***REDACTED***"}}
