"""Parser for the Bidgely Green Button (ESPI) XML export.

This module has no network code. It takes the XML text from the Bidgely
gb-download endpoint and returns one SCRegisterUsage for each meter register.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from ..exceptions import ApiError
from .const import SC_TIMEZONE
from .models import SCIntervalRead, SCRegisterUsage

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "espi": "http://naesb.org/espi",
}
_USAGE_POINT_RE = re.compile(r"/UsagePoint/([^/]+)")
_INTERVAL_TITLE_PREFIX = "Interval Consumption"


def bidgely_epoch_to_local(epoch: int) -> datetime:
    """Convert a Bidgely epoch value to a timezone-aware local datetime.

    Bidgely encodes local (Eastern) wall-clock time as if it were UTC.
    For example, 1786579200 is 2026-08-13 00:00 UTC, but it means
    2026-08-13 00:00 EDT. We read the UTC clock time and attach the
    America/New_York zone.
    """
    return datetime.fromtimestamp(epoch, UTC).replace(tzinfo=SC_TIMEZONE)


def _read_start(epoch: int, repeated: bool) -> datetime:
    """Return the start of a reading as a real local time.

    The epochs are wall time, so DST changes need special handling:

    - Fall-back (November): the two 01:00-01:59 hours have the same wall
      times. The first reading of a wall time is EDT (fold=0). A repeated
      reading of an ambiguous wall time is the second hour, EST (fold=1).
    - Spring-forward (March): 02:00-02:59 does not exist. Bidgely should not
      send these times. If it does, the UTC round trip moves the reading one
      hour later (02:30 becomes 03:30 EDT), so its value adds to that
      interval. The daily total stays correct, and no time is invalid.

    Note: aware datetimes that differ only in ``fold`` compare equal and have
    the same hash. Key dicts and sets by the UTC instant, not by this value.
    """
    local = bidgely_epoch_to_local(epoch)
    if repeated:
        # In the spring-forward gap, fold=1 also changes the offset. Use
        # fold=1 only if the wall time exists, which means it is ambiguous.
        second = local.replace(fold=1).astimezone(UTC).astimezone(SC_TIMEZONE)
        if second.fold == 1:
            return second
    return local.astimezone(UTC).astimezone(SC_TIMEZONE)


def _usage_point_id(entry: ET.Element) -> str:
    """Return the UsagePoint ID from the link hrefs of an entry."""
    for link in entry.findall("atom:link", _NS):
        match = _USAGE_POINT_RE.search(link.get("href", ""))
        if match:
            return match.group(1)
    # Keep readings without a UsagePoint ID together, so no data is lost.
    return ""


def parse_registers(xml_text: str) -> list[SCRegisterUsage]:
    """Parse Green Button XML into one SCRegisterUsage for each register.

    The registers are in first-seen order. Signs are kept as reported, so a
    solar export register has negative values.

    Args:
        xml_text: Raw XML from the gb-download endpoint.

    Returns:
        List of registers. Empty if the feed has no interval data.

    Raises:
        ApiError: If the XML cannot be parsed.
    """
    try:
        # The XML comes from Bidgely over HTTPS. The stdlib expat parser does
        # not load external entities, and expat >= 2.4.1 limits entity expansion.
        root = ET.fromstring(xml_text)  # noqa: S314
    except ET.ParseError as err:
        raise ApiError(
            f"Could not parse Green Button XML: {err}",
            response_text=xml_text[:500],
        ) from err

    registers: dict[str, SCRegisterUsage] = {}
    # Epoch values seen for each register, in reading order. A repeated
    # epoch in the fall-back hour is the second (EST) hour.
    seen: dict[str, set[int]] = {}
    try:
        for entry in root.findall("atom:entry", _NS):
            title = entry.findtext("atom:title", default="", namespaces=_NS)
            if not title.startswith(_INTERVAL_TITLE_PREFIX):
                continue

            usage_point_id = _usage_point_id(entry)
            register = registers.setdefault(
                usage_point_id, SCRegisterUsage(usage_point_id=usage_point_id)
            )
            seen_epochs = seen.setdefault(usage_point_id, set())
            for reading in entry.iterfind(
                "atom:content/espi:IntervalBlock/espi:IntervalReading", _NS
            ):
                start = int(reading.findtext("espi:timePeriod/espi:start", "", _NS))
                duration = int(
                    reading.findtext("espi:timePeriod/espi:duration", "", _NS)
                )
                value = float(reading.findtext("espi:value", "", _NS))
                repeated = start in seen_epochs
                seen_epochs.add(start)
                register.reads.append(
                    SCIntervalRead(
                        start=_read_start(start, repeated),
                        duration_seconds=duration,
                        value=value,
                    )
                )
    except ValueError as err:
        raise ApiError(
            f"Unexpected value in Green Button XML: {err}",
            response_text=xml_text[:500],
        ) from err

    return list(registers.values())
