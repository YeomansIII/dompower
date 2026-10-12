"""Diagnostic report for Dominion Energy South Carolina accounts.

SC users run ``dompower sc-report`` and attach the text file to a GitHub
issue. The maintainer has no SC account, so the report is the main source
of real SC data.

The report must be safe to post in public:

- JSON responses show the field names and the value types only. Values are
  shown only for booleans, date strings, and an allowlist of keys.
- HTML pages show the size only.
- Green Button XML shows element names, readings, and the numeric codes of
  ReadingType, LocalTimeParameters, and the UsagePoint service kind.
  UsagePoint IDs are replaced with "register-1", "register-2". Entry
  titles are shown only for an allowlist of generic Green Button titles.
- Query values are shown only for an allowlist of parameters.
- Error messages show quoted values only if they are numbers or dates.
- At the end, known secrets, email addresses, and phone numbers are
  replaced in the full text.
"""

from __future__ import annotations

import json
import platform
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from yarl import URL

from ..exceptions import TFARequiredError
from ..models import MeterType
from ._http import RESPONSE_RECORDER
from .client import DominionSCClient, registers_to_intervals
from .const import BIDGELY_PILOT_ID, SC_TIMEZONE
from .greenbutton import _NS, _usage_point_id, bidgely_epoch_to_local

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from .models import SCRegisterUsage

ISSUE_URL = "https://github.com/YeomansIII/ha-dominion-energy/issues/19"

_SAFE_STRING_KEYS = frozenset({"status", "measurementType"})
_SAFE_QUERY_PARAMS = frozenset(
    {"start", "end", "measurement-type", "file-type", "service", "serviceAccountType"}
)
_MAX_LIST_ITEMS = 5
_MAX_LEAVES = 40
_RAW_READS = 3

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+)?(Z|[+-]\d{2}:?\d{2})?$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_LONG_DIGITS_RE = re.compile(r"\d{4,}")
_USER_PATH_RE = re.compile(r"/users/[^/]+/")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

REDACTED = "<redacted>"

# Generic Atom entry titles that a real Bidgely export used. Other titles
# (for example a UsagePoint or ServiceDeliveryPoint title) can contain a
# service address or a meter number, so the report shows only their length.
# The report compares the text before the first '.' or ':'.
_SAFE_TITLES = frozenset(
    {
        "Green Button Data File",
        "Interval Consumption",
        "Time Configuration",
        "Type of Meter Reading Data",
    }
)

# Green Button elements that show numeric values. Other elements show only
# their field names.
_ALL_FIELDS = "*"
_XML_VALUE_FIELDS: dict[str, frozenset[str]] = {
    "ReadingType": frozenset({_ALL_FIELDS}),
    "LocalTimeParameters": frozenset({_ALL_FIELDS}),
    "UsagePoint": frozenset({"ServiceCategory.kind", "status"}),
}


@dataclass
class _Response:
    """One recorded HTTP response. The body stays in memory only."""

    method: str
    url: str
    status: int
    body: str


@dataclass
class _ReportState:
    """Data that the report collects while it runs."""

    responses: list[_Response] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def record(self, method: str, url: str, status: int, body: str) -> None:
        """Record one response. Used as the RESPONSE_RECORDER callback."""
        self.responses.append(_Response(method, url, status, body))

    def label(self, usage_point_id: str) -> str:
        """Return a stable, anonymous label for a UsagePoint ID."""
        return self.labels.setdefault(
            usage_point_id, f"register-{len(self.labels) + 1}"
        )


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """Replace secrets, email addresses, and phone numbers in the text.

    Secrets shorter than 4 characters are not replaced, because a short
    value would also replace normal text.
    """
    values = {s for s in secrets if s and len(s) >= 4}
    for secret in sorted(values, key=len, reverse=True):
        text = re.sub(re.escape(secret), REDACTED, text, flags=re.IGNORECASE)
    text = _EMAIL_RE.sub("<email>", text)
    return _PHONE_RE.sub("<phone>", text)


def json_shape(value: Any, key: str | None = None) -> Any:
    """Return the structure of a JSON value without the private values."""
    if isinstance(value, dict):
        return {
            (k if _is_safe_key(k) else f"<key {i}>"): json_shape(v, k)
            for i, (k, v) in enumerate(value.items(), 1)
        }
    if isinstance(value, list):
        items = [json_shape(v, key) for v in value[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            items.append(f"<{len(value) - _MAX_LIST_ITEMS} more items>")
        return items
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return "<number>"
    if isinstance(value, str):
        if key in _SAFE_STRING_KEYS or _DATE_RE.match(value):
            return value
        return f"<string, {len(value)} chars>"
    return f"<{type(value).__name__}>"


def _is_safe_key(key: str) -> bool:
    """Return True if a JSON key looks like a field name, not like data."""
    return bool(_SAFE_KEY_RE.match(key)) and not _LONG_DIGITS_RE.search(key)


def _describe_title(title: str) -> str:
    """Return an entry title if it is a generic title, or a placeholder."""
    prefix = re.split(r"[.:]", title, maxsplit=1)[0].strip()
    if not prefix:
        return "(no title)"
    if prefix in _SAFE_TITLES:
        return prefix
    return f"<other title, {len(title)} chars>"


def _number_or_size(text: str) -> str:
    """Return a numeric XML value, or a placeholder for other text."""
    text = text.strip()
    return text if _NUMBER_RE.match(text) else f"<string, {len(text)} chars>"


def describe_error(err: Exception) -> str:
    """Return an error for the report, without quoted response values.

    Some error messages contain a value from a response, for example
    "invalid literal for int() with base 10: '...'". The report shows a
    quoted value only if it is a number or a date.
    """

    def mask(match: re.Match[str]) -> str:
        value = match.group(0)[1:-1]
        if _NUMBER_RE.match(value) or _DATE_RE.match(value):
            return match.group(0)
        return f"<string, {len(value)} chars>"

    return f"{type(err).__name__}: {_QUOTED_RE.sub(mask, str(err))}"


def _local_name(tag: str) -> str:
    """Return an XML tag name without the namespace."""
    return tag.rsplit("}", 1)[-1]


def _describe_request(response: _Response) -> str:
    """Return one line for a request, without private query values."""
    url = URL(response.url)
    path = _USER_PATH_RE.sub("/users/<user-id>/", url.path)
    params = [
        f"{k}={v if k in _SAFE_QUERY_PARAMS else REDACTED}"
        for k, v in url.query.items()
        if k != "_"
    ]
    query = f"?{'&'.join(params)}" if params else ""
    return f"{response.method} {url.host}{path}{query} -> HTTP {response.status}"


def _describe_body(body: str) -> list[str]:
    """Return the lines that describe a response body."""
    stripped = body.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            shape = json_shape(json.loads(body))
        except ValueError:
            return [f"not valid JSON, {len(body)} chars"]
        return [f"JSON, {len(body)} chars:", *json.dumps(shape, indent=2).splitlines()]
    head = stripped[:1000].lower()
    if stripped.startswith("<?xml") or "<feed" in head:
        return [f"XML, {len(body)} chars (see the Green Button sections)"]
    if "<html" in head or "<!doctype html" in head:
        token = "__RequestVerificationToken" in body
        found = "found" if token else "NOT found"
        return [f"HTML, {len(body)} chars, verification token {found}"]
    if not body:
        return ["empty body"]
    return [f"text, {len(body)} chars"]


def _leaf_fields(element: ET.Element) -> dict[str, str]:
    """Return the leaf fields of an XML element.

    Values are shown only for numeric codes in the allowlisted fields.
    Other elements (for example ServiceDeliveryPoint) can contain account
    data, so the report shows only their field names.
    """
    allowed = _XML_VALUE_FIELDS.get(_local_name(element.tag), frozenset())
    fields: dict[str, str] = {}

    def walk(node: ET.Element, prefix: str) -> None:
        for child in node:
            name = f"{prefix}{_local_name(child.tag)}"
            if len(child):
                walk(child, f"{name}.")
            elif len(fields) < _MAX_LEAVES:
                text = (child.text or "").strip()
                visible = _ALL_FIELDS in allowed or name in allowed
                show = visible and _NUMBER_RE.match(text)
                fields[name] = text if show else "<hidden>"

    walk(element, "")
    return fields


def summarize_green_button(xml_text: str, state: _ReportState) -> list[str]:
    """Return the structure of a raw Green Button download."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - see greenbutton.py
    except ET.ParseError as err:
        return [f"XML parse error: {err}"]

    lines = ["Element counts:"]
    tags = Counter(_local_name(el.tag) for el in root.iter())
    lines += [f"  {tag}: {count}" for tag, count in sorted(tags.items())]

    titles: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    details: list[str] = []
    raw_reads: dict[str, list[str]] = {}
    for entry in root.findall("atom:entry", _NS):
        title = entry.findtext("atom:title", default="", namespaces=_NS)
        titles[_describe_title(title)] += 1
        up_id = _usage_point_id(entry)
        label = state.label(up_id) if up_id else "(no register)"
        content = entry.find("atom:content", _NS)
        for child in content if content is not None else []:
            name = _local_name(child.tag)
            if name == "IntervalBlock":
                reads = raw_reads.setdefault(label, [])
                for reading in child.iterfind("espi:IntervalReading", _NS):
                    if len(reads) < _RAW_READS:
                        reads.append(_describe_raw_read(reading))
                continue
            if (label, name) in seen:
                continue
            seen.add((label, name))
            details.append(f"  {label} {name}:")
            details += [f"    {k}: {v}" for k, v in _leaf_fields(child).items()]

    lines.append("Entry titles (text before the first '.' or ':', generic only):")
    lines += [f"  {title}: {count}" for title, count in sorted(titles.items())]
    lines.append("Other content (first entry of each kind):")
    lines += details or ["  (none)"]
    lines.append(f"First {_RAW_READS} raw readings of each register:")
    for label, reads in raw_reads.items():
        lines.append(f"  {label}:")
        lines += [f"    {read}" for read in reads]
    return lines


def _describe_raw_read(reading: ET.Element) -> str:
    """Return a raw reading with both ways to read its epoch value."""
    start = _number_or_size(reading.findtext("espi:timePeriod/espi:start", "", _NS))
    duration = _number_or_size(
        reading.findtext("espi:timePeriod/espi:duration", "", _NS)
    )
    value = _number_or_size(reading.findtext("espi:value", "", _NS))
    try:
        epoch = int(start)
    except ValueError:
        return f"start={start} duration={duration} value={value}"
    as_wall = bidgely_epoch_to_local(epoch).strftime("%Y-%m-%d %H:%M")
    as_utc = datetime.fromtimestamp(epoch, SC_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"start={epoch} duration={duration} value={value} | "
        f"wall-time reading: {as_wall} | real-epoch reading: {as_utc}"
    )


def _summarize_registers(
    registers: list[SCRegisterUsage],
    meter_type: MeterType,
    state: _ReportState,
) -> list[str]:
    """Return the parsed register data, daily totals, and hourly totals."""
    lines = [f"Registers after date filter: {len(registers)}"]
    for register in registers:
        reads = register.reads
        values = [r.value for r in reads]
        lines.append(f"  {state.label(register.usage_point_id)}:")
        if not reads:
            lines.append("    no readings")
            continue
        lines += [
            f"    readings: {len(reads)}",
            f"    interval lengths (s): {sorted({r.duration_seconds for r in reads})}",
            f"    first: {reads[0].start.isoformat()}",
            f"    last: {reads[-1].start.isoformat()}",
            f"    positive: {sum(v > 0 for v in values)}, "
            f"negative: {sum(v < 0 for v in values)}, "
            f"zero: {sum(v == 0 for v in values)}",
            f"    min: {min(values)}, max: {max(values)}, sum: {round(sum(values), 3)}",
        ]
        per_day = Counter(r.start.date() for r in reads)
        first_time: dict[date, str] = {}
        for read in reads:
            first_time.setdefault(read.start.date(), read.start.strftime("%H:%M"))
        lines.append("    readings per day (first reading time):")
        lines += [
            f"      {day}: {count} ({first_time[day]})"
            for day, count in sorted(per_day.items())
        ]

    intervals = registers_to_intervals(registers, meter_type)
    if not intervals:
        return lines
    unit = intervals[0].unit
    daily: dict[date, list[float]] = {}
    for interval in intervals:
        totals = daily.setdefault(interval.timestamp.date(), [0.0, 0.0])
        totals[0] += interval.consumption
        totals[1] += interval.generation
    lines.append(f"Daily totals ({unit}), compare with the website:")
    lines += [
        f"  {day}: consumption {c:.3f}, generation {g:.3f}"
        for day, (c, g) in sorted(daily.items())
    ]

    last_day = max(daily)
    # Key by the UTC hour. On the DST fall-back day, the two 01:00 hours
    # have the same local hour. The time zone name tells them apart.
    hourly: dict[datetime, list[float]] = {}
    for interval in intervals:
        if interval.timestamp.date() == last_day:
            hour = interval.timestamp.astimezone(UTC).replace(minute=0, second=0)
            totals = hourly.setdefault(hour, [0.0, 0.0])
            totals[0] += interval.consumption
            totals[1] += interval.generation
    lines.append(f"Hourly totals for {last_day} ({unit}):")
    lines += [
        f"  {hour.astimezone(SC_TIMEZONE):%H:%M %Z} "
        f"consumption {c:.3f}, generation {g:.3f}"
        for hour, (c, g) in sorted(hourly.items())
    ]
    return lines


def _package_version() -> str:
    """Return the installed dompower version."""
    try:
        return version("dompower")
    except PackageNotFoundError:
        return "unknown"


def _header(start_date: date, end_date: date) -> list[str]:
    """Return the report header that tells the user what the file contains."""
    now = datetime.now().astimezone()
    return [
        "dompower South Carolina diagnostic report",
        "=========================================",
        f"Created: {now:%Y-%m-%d %H:%M %Z}",
        f"dompower {_package_version()}, Python {platform.python_version()}, "
        f"{platform.system()}",
        f"Date range: {start_date} to {end_date}",
        f"Bidgely pilot ID: {BIDGELY_PILOT_ID}",
        "",
        f"This file is for {ISSUE_URL}",
        "Read all of it before you post it.",
        "",
        "It does NOT contain your username, password, TFA token, cookies,",
        "email address, phone number, service address, account number,",
        "or Bidgely user ID. These are replaced with <redacted>, <email>,",
        "or <phone>. Other text values show only as <string, N chars>.",
        "",
        "It DOES contain: HTTP status codes, JSON field names, dates,",
        "meter readings (Wh or ft³) for the date range, and bill amounts ($).",
        "Remove anything that you do not want to share.",
    ]


async def async_build_report(
    session: ClientSession,
    username: str,
    password: str,
    tfa_token: str | None,
    *,
    days: int = 3,
    today: date | None = None,
) -> str:
    """Log in, fetch SC data, and return a redacted report.

    Errors do not stop the report. Each error is added to the report, because
    the failed step is the most useful information.

    Args:
        session: aiohttp session with ``create_sc_cookie_jar()``.
        username: SC portal username.
        password: SC portal password.
        tfa_token: TFA token from ``dompower sc-login``.
        days: Number of complete past days to include.
        today: Today's date (for tests).

    Returns:
        The report text. It is safe to post after the user reads it.
    """
    end_date = (today or date.today()) - timedelta(days=1)
    start_date = end_date - timedelta(days=max(days, 1) - 1)
    state = _ReportState()
    secrets: list[str | None] = [username, password, tfa_token]
    summary: list[str] = []
    sections: list[tuple[str, list[str]]] = []

    client = DominionSCClient(session, username, password, tfa_token=tfa_token)
    recorder_token = RESPONSE_RECORDER.set(state.record)
    try:
        try:
            sc_session = await client.async_login()
        except TFARequiredError:
            sc_session = None
            summary.append("Saved TFA token accepted: no (run 'dompower sc-login')")
        except Exception as err:  # The report shows every error.
            sc_session = None
            state.errors.append(f"login: {describe_error(err)}")

        if sc_session is not None:
            account = sc_session.account
            secrets += [account.user_id, account.service_address]
            summary += [
                "Saved TFA token accepted: yes",
                "Login complete: yes",
                f"Meter types: {', '.join(t.value for t in account.meter_types)}",
            ]
            meter_types = account.meter_types or [MeterType.ELECTRIC]
            for meter_type in meter_types:
                sections.append(
                    await _async_meter_section(
                        client, meter_type, start_date, end_date, state
                    )
                )
            sections.append(await _async_bill_section(client, state))
    finally:
        RESPONSE_RECORDER.reset(recorder_token)

    secrets += list(state.labels)

    lines = _header(start_date, end_date)
    lines += ["", "1. Summary", "----------"]
    lines += summary or ["Login complete: no (see Errors)"]
    lines += ["", "2. Requests and response fields", "-------------------------------"]
    for response in state.responses:
        lines.append(_describe_request(response))
        lines += [f"    {line}" for line in _describe_body(response.body)]
    for number, (title, body) in enumerate(sections, 3):
        heading = f"{number}. {title}"
        lines += ["", heading, "-" * len(heading), *body]
    heading = f"{len(sections) + 3}. Errors"
    lines += ["", heading, "-" * len(heading)]
    lines += state.errors or ["(none)"]
    return redact("\n".join(lines) + "\n", secrets)


async def _async_meter_section(
    client: DominionSCClient,
    meter_type: MeterType,
    start_date: date,
    end_date: date,
    state: _ReportState,
) -> tuple[str, list[str]]:
    """Return the report section for one meter type."""
    title = f"{meter_type.value.capitalize()} data"
    first_response = len(state.responses)
    try:
        registers = await client.async_get_register_usage(
            start_date, end_date, meter_type
        )
    except Exception as err:  # The report shows every error.
        state.errors.append(f"{meter_type.value} data: {describe_error(err)}")
        return title, ["Failed, see Errors."]

    lines = _summarize_registers(registers, meter_type, state)
    downloads = [
        r
        for r in state.responses[first_response:]
        if "gb-download" in r.url and r.status == 200
    ]
    if downloads:
        lines += ["", "Raw Green Button download (includes one extra day each side):"]
        lines += summarize_green_button(downloads[-1].body, state)
    return title, lines


async def _async_bill_section(
    client: DominionSCClient, state: _ReportState
) -> tuple[str, list[str]]:
    """Return the report section for the bill forecast."""
    title = "Bill forecast (compare with the website)"
    try:
        forecast = await client.async_get_bill_forecast()
    except Exception as err:  # The report shows every error.
        state.errors.append(f"bill forecast: {describe_error(err)}")
        return title, ["Failed, see Errors."]
    return title, [
        f"period: {forecast.period_start} to {forecast.period_end}",
        f"data through: {forecast.data_through}",
        f"cost to date: ${forecast.cost_to_date:.2f}",
        f"forecasted cost: ${forecast.forecasted_cost:.2f}",
        "last year cost: "
        + (
            f"${forecast.last_year_cost:.2f}"
            if forecast.last_year_cost is not None
            else "not available"
        ),
    ]
