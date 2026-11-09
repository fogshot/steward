"""Dues computation: the segment model and proration engine (PRD: dues-generation.md).

Pure functions over the dataclasses from model.py — no I/O, no hledger.
The year's dues are computed by splitting at every boundary date (join/leave,
tier change, override start/end), prorating each segment at its applicable
annual rate, summing, and rounding half-up to cents once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .model import Member, MembersFile

CENT = Decimal("0.01")


@dataclass
class Segment:
    """A maximal time span with constant rate inputs."""

    start: date  # inclusive
    end: date  # inclusive
    annual_rate: Decimal
    waived: bool  # True when the rate comes from a fee override


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    return date(year, month % 12 + 1, 1)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def liable_segments(
    member: Member, year: int, proration: str
) -> list[tuple[date, date, date]]:
    """Liable spans within the calendar year: (rounded_start, rounded_end, rate_date).

    monthly: whole calendar months — joining any day in April makes April liable;
    the span is rounded to month starts. rate_date is the unrounded start, used
    for rate resolution so a tier effective on the join date applies.
    daily/none: exact dates.
    """
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    spans: list[tuple[date, date, date]] = []
    for period in member.membership:
        start = max(period.joined, year_start)
        end = min(period.left, year_end) if period.left else year_end
        if start > end:
            continue
        rate_date = start
        if proration == "monthly":
            start = _month_start(start)
            # end is the month start of the month AFTER the last liable month,
            # so month counting is end - start (exclusive end).
            end = _add_months(_month_start(end), 1)
        spans.append((start, end, rate_date))
    return spans


def _rate_on(member: Member, members: MembersFile, day: date) -> tuple[Decimal, bool]:
    """Rate resolution chain: active override → tier history → fee_tiers rate."""
    for override in member.fee_overrides:
        if override.start <= day <= override.end:
            return override.amount, True
    tier_name = None
    for assignment in member.tiers:
        if assignment.effective <= day:
            tier_name = assignment.tier
    if tier_name is None:
        return Decimal(0), False
    rate = Decimal(0)
    for entry in members.fee_tiers.get(tier_name, []):
        if entry.effective <= day:
            rate = entry.amount
    return rate, False


def build_segments(
    member: Member, members: MembersFile, year: int, proration: str
) -> list[Segment]:
    """Split liable spans at every rate boundary into constant-rate segments."""
    segments: list[Segment] = []
    for span_start, span_end, rate_date in liable_segments(member, year, proration):
        boundaries = {span_start}
        for assignment in member.tiers:
            if span_start < assignment.effective <= span_end:
                boundaries.add(
                    _month_start(assignment.effective)
                    if proration == "monthly"
                    else assignment.effective
                )
        for override in member.fee_overrides:
            for boundary in (
                override.start,
                _add_months(_month_start(override.end), 1),
            ):
                if span_start < boundary < span_end:
                    boundaries.add(
                        _month_start(boundary) if proration == "monthly" else boundary
                    )
        points = sorted(boundaries)
        built: list[Segment] = []
        for i, point in enumerate(points):
            end = points[i + 1] if i + 1 < len(points) else None
            if end is None:
                seg_end = span_end
            elif proration == "monthly":
                seg_end = end  # month-start boundary, exclusive
            else:
                seg_end = date.fromordinal(end.toordinal() - 1)
            # Resolve the rate at the unrounded date for the first segment, so a
            # tier effective on the join date applies even when monthly proration
            # rounds the segment start back to the month start.
            rate_day = rate_date if i == 0 else point
            rate, waived = _rate_on(member, members, rate_day)
            built.append(
                Segment(start=point, end=seg_end, annual_rate=rate, waived=waived)
            )
        # Merge adjacent segments with identical rates: a boundary inside a
        # proration-rounded segment (e.g. a tier effective on the join date)
        # is a no-op and must not split it.
        for segment in built:
            if (
                segments
                and segments[-1].annual_rate == segment.annual_rate
                and segments[-1].waived == segment.waived
            ):
                segments[-1].end = segment.end
            else:
                segments.append(segment)
    return segments


def _segment_months(segment: Segment) -> int:
    # end is the exclusive month-start boundary: Apr 1 → Jan 1 is 9 months.
    return (
        (segment.end.year - segment.start.year) * 12
        + segment.end.month
        - segment.start.month
    )


def _segment_days(segment: Segment) -> int:
    return (segment.end - segment.start).days + 1


def compute_dues(
    member: Member, members: MembersFile, year: int, proration: str
) -> Decimal:
    """Total dues for a member in a calendar year, rounded half-up to cents once."""
    segments = build_segments(member, members, year, proration)
    if proration == "none":
        total = segments[0].annual_rate if segments else Decimal(0)
    elif proration == "daily":
        days_in_year = (date(year, 12, 31) - date(year, 1, 1)).days + 1
        total = sum(
            (s.annual_rate * _segment_days(s) / days_in_year for s in segments),
            Decimal(0),
        )
    else:  # monthly
        total = sum(
            (s.annual_rate * _segment_months(s) / 12 for s in segments),
            Decimal(0),
        )
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def is_full_year_waiver(
    member: Member, members: MembersFile, year: int, proration: str
) -> bool:
    """True when every liable segment has a zero override rate."""
    segments = build_segments(member, members, year, proration)
    return bool(segments) and all(s.waived and s.annual_rate == 0 for s in segments)
