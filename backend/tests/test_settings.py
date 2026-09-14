from src.settings import Settings


def test_missing_configuration_lists_names_without_secret_values():
    settings = Settings(
        _env_file=None, fmp_api_key="", sec_api_key="", voyage_api_key="",
        anthropic_api_key="", openai_api_key="",
        edgar_user_agent="AlphaAnalyst contact@example.com",
    )
    errors = settings.analysis_configuration_errors()
    assert "FMP_API_KEY" in errors
    assert "ANTHROPIC_API_KEY" in errors
    assert any("EDGAR_USER_AGENT" in error for error in errors)


def test_default_provider_configuration_accepts_filled_values():
    settings = Settings(
        _env_file=None, fmp_api_key="test", sec_api_key="test", voyage_api_key="test",
        anthropic_api_key="test", openai_api_key="test",
        edgar_user_agent="AlphaAnalyst analyst@example.test",
    )
    assert settings.analysis_configuration_errors() == []
