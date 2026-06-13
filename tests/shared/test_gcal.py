"""Tests for the shared Google Calendar client factory."""

from unittest.mock import MagicMock, patch

from shared.gcal import build_calendar_service


@patch("shared.gcal.build")
@patch("shared.gmail.Credentials")
def test_build_calendar_service_uses_calendar_v3(
    creds_cls: MagicMock, build_mock: MagicMock
) -> None:
    sentinel_creds = object()
    creds_cls.return_value = sentinel_creds
    sentinel_service = object()
    build_mock.return_value = sentinel_service

    service = build_calendar_service()

    assert service is sentinel_service
    build_mock.assert_called_once_with(
        "calendar", "v3", credentials=sentinel_creds, cache_discovery=False
    )


@patch("shared.gcal.build")
@patch("shared.gmail.Credentials")
def test_build_calendar_service_passes_oauth_settings(
    creds_cls: MagicMock, build_mock: MagicMock
) -> None:
    build_calendar_service()

    build_mock.assert_called_once()
    creds_cls.assert_called_once()
    kwargs = creds_cls.call_args.kwargs
    # Reuses the shared Gmail OAuth grant; token is minted from the refresh token.
    assert kwargs["token"] is None
    assert kwargs["token_uri"] == "https://oauth2.googleapis.com/token"
    assert kwargs["refresh_token"]
    assert kwargs["client_id"]
    assert kwargs["client_secret"]
