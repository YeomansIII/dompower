"""Tests for library constants."""

from __future__ import annotations

import re

from dompower.const import DEFAULT_HEADERS, USER_AGENT


class TestUserAgent:
    """The user agent must stay shaped like a real Chrome on macOS.

    Dominion sits behind an Incapsula WAF that fingerprints this header. A
    malformed or truncated string is a reason to be blocked, and the failure
    shows up as an opaque 403 on every endpoint.
    """

    def test_matches_real_chrome_format(self) -> None:
        """The string parses as a full, well-formed Chrome user agent."""
        pattern = (
            r"^Mozilla/5\.0 \(Macintosh; Intel Mac OS X 10_15_7\) "
            r"AppleWebKit/537\.36 \(KHTML, like Gecko\) "
            r"Chrome/\d+\.0\.0\.0 Safari/537\.36$"
        )
        assert re.match(pattern, USER_AGENT), USER_AGENT

    def test_webkit_and_safari_versions_are_frozen(self) -> None:
        """Chrome always reports 537.36. 537.00 is not a real build."""
        assert USER_AGENT.count("537.36") == 2
        assert "537.00" not in USER_AGENT

    def test_chrome_version_is_not_ancient(self) -> None:
        """Guard against the version falling far behind stable.

        Chrome 152 was stable in August 2026. This is a floor, not a pin -
        raise the constant when it drifts, and this keeps passing.
        """
        match = re.search(r"Chrome/(\d+)\.", USER_AGENT)
        assert match is not None
        assert int(match.group(1)) >= 152

    def test_default_headers_use_the_shared_constant(self) -> None:
        """A second hand-written copy would drift out of sync."""
        assert DEFAULT_HEADERS["User-Agent"] == USER_AGENT
