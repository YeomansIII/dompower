"""Tests for DompowerClient."""

import json

import pytest
from aiohttp import ClientSession
from aresponses import ResponsesMockServer

from dompower import DompowerClient, InvalidAuthError


class TestDompowerClient:
    """Tests for the DompowerClient class."""

    async def test_client_without_tokens(self) -> None:
        """Test that client without tokens raises error on API call."""
        async with ClientSession() as session:
            client = DompowerClient(session)
            assert not client.has_tokens

    async def test_client_with_tokens(
        self, sample_tokens: dict[str, str]
    ) -> None:
        """Test that client with tokens is properly initialized."""
        async with ClientSession() as session:
            client = DompowerClient(
                session,
                access_token=sample_tokens["access_token"],
                refresh_token=sample_tokens["refresh_token"],
            )
            assert client.has_tokens

    async def test_set_tokens(self, sample_tokens: dict[str, str]) -> None:
        """Test setting tokens after initialization."""
        async with ClientSession() as session:
            client = DompowerClient(session)
            assert not client.has_tokens

            await client.async_set_tokens(
                sample_tokens["access_token"],
                sample_tokens["refresh_token"],
            )
            assert client.has_tokens

    async def test_context_manager(self, sample_tokens: dict[str, str]) -> None:
        """Test async context manager."""
        async with ClientSession() as session:
            async with DompowerClient(
                session,
                access_token=sample_tokens["access_token"],
                refresh_token=sample_tokens["refresh_token"],
            ) as client:
                assert client.has_tokens

    async def test_token_callback(self) -> None:
        """Test that token callback is invoked on token set."""
        callback_called = False
        received_tokens: dict[str, str] = {}

        def token_callback(access: str, refresh: str) -> None:
            nonlocal callback_called, received_tokens
            callback_called = True
            received_tokens = {"access": access, "refresh": refresh}

        async with ClientSession() as session:
            client = DompowerClient(
                session,
                token_update_callback=token_callback,
            )

            await client.async_set_tokens("new_access", "new_refresh")

            assert callback_called
            assert received_tokens["access"] == "new_access"
            assert received_tokens["refresh"] == "new_refresh"


class TestTokenRefresh:
    """Tests for token refresh functionality."""

    async def test_refresh_tokens(
        self,
        aresponses: ResponsesMockServer,
        sample_tokens: dict[str, str],
        sample_refresh_response: dict,
    ) -> None:
        """Test successful token refresh."""
        aresponses.add(
            "prodsvc-dominioncip.smartcmobile.com",
            "/UsermanagementAPI/api/1/login/auth/refresh",
            "POST",
            response=aresponses.Response(
                body=json.dumps(sample_refresh_response),
                content_type="application/json",
            ),
        )

        callback_tokens: dict[str, str] = {}

        def token_callback(access: str, refresh: str) -> None:
            callback_tokens["access"] = access
            callback_tokens["refresh"] = refresh

        async with ClientSession() as session:
            client = DompowerClient(
                session,
                access_token=sample_tokens["access_token"],
                refresh_token=sample_tokens["refresh_token"],
                token_update_callback=token_callback,
            )

            await client.async_refresh_tokens()

            assert callback_tokens["access"] == "new_access_token_abc"
            assert callback_tokens["refresh"] == "new_refresh_token_xyz"

    async def test_refresh_without_tokens(self) -> None:
        """Test that refresh without tokens raises error."""
        async with ClientSession() as session:
            client = DompowerClient(session)

            with pytest.raises(InvalidAuthError):
                await client.async_refresh_tokens()
