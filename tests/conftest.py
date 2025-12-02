"""Pytest configuration and fixtures for dompower tests."""

import pytest


@pytest.fixture
def sample_tokens() -> dict[str, str]:
    """Sample tokens for testing."""
    return {
        "access_token": "test_access_token_12345",
        "refresh_token": "test_refresh_token_67890",
    }


@pytest.fixture
def sample_refresh_response() -> dict:
    """Sample response from token refresh endpoint."""
    return {
        "status": {
            "type": "success",
            "code": 200,
            "message": "success",
            "error": False,
        },
        "data": {
            "accessToken": "new_access_token_abc",
            "refreshToken": "new_refresh_token_xyz",
            "expiresIn": 30,
        },
    }
