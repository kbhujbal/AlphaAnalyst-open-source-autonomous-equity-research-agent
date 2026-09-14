from src.diagnostics import public_diagnostic
from src.settings import settings


def test_public_warning_redacts_provider_keys_and_query_tokens(mocker):
    mocker.patch.object(settings, "fmp_api_key", "private-provider-credential")
    message = "Fetch failed https://provider.test/data?apikey=private-provider-credential&token=another-secret status=403"
    sanitized = public_diagnostic(message)
    assert "private-provider-credential" not in sanitized
    assert "another-secret" not in sanitized
    assert "status=403" in sanitized


def test_public_warning_keeps_actionable_missing_coverage():
    assert public_diagnostic("Earnings transcripts unavailable") == "Earnings transcripts unavailable"
