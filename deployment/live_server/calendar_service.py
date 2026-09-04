"""Organizer-owned NYSE calendar provisioning for the live competition."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .store import CompetitionStore


DECISION_CUTOFF_BEFORE_OPEN = timedelta(minutes=30)


def decision_deadline(next_market_open: datetime) -> datetime:
    """Freeze the decision cutoff relative to the exchange-provided next open."""
    if next_market_open.tzinfo is None or next_market_open.utcoffset() is None:
        raise ValueError("next_market_open must include a timezone")
    return next_market_open - DECISION_CUTOFF_BEFORE_OPEN


def ensure_trading_days(
    store: CompetitionStore,
    start: date,
    end: date,
    *,
    calendar_name: str = "XNYS",
) -> list[date]:
    """Create sessions in [start, end], with deadlines at the next session open.

    One extra exchange session is loaded internally because the final signal day
    also needs an explicit execution day. Re-running this function is safe.
    """
    if end < start:
        raise ValueError("end must not be before start")
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - installation concern
        raise RuntimeError("install the 'exchange-calendars' package") from exc

    calendar = xcals.get_calendar(calendar_name)
    # Seven calendar days is sufficient to cross an ordinary weekend/holiday.
    schedule = calendar.schedule.loc[
        start.isoformat():(end + timedelta(days=7)).isoformat()
    ]
    sessions: list[tuple[date, datetime, datetime]] = []
    for label, row in schedule.iterrows():
        session_date = label.date()
        market_open = row["open"].to_pydatetime().astimezone(timezone.utc)
        market_close = row["close"].to_pydatetime().astimezone(timezone.utc)
        sessions.append((session_date, market_open, market_close))

    wanted = [item for item in sessions if start <= item[0] <= end]
    if not wanted:
        return []
    if wanted and sessions.index(wanted[-1]) + 1 >= len(sessions):
        raise RuntimeError("calendar query did not include the next execution session")

    # Create the extra next session first so the final signal day is actionable.
    last_index = sessions.index(wanted[-1]) if wanted else -1
    to_create = sessions[:last_index + 2]
    created: list[date] = []
    for index, (session_date, market_open, market_close) in enumerate(to_create):
        if index + 1 >= len(sessions):
            raise RuntimeError("calendar query did not include a following session")
        next_open = sessions[index + 1][1]
        submission_open = market_close
        deadline = decision_deadline(next_open)
        store.create_trading_day(
            session_date, market_open, market_close, submission_open, deadline
        )
        if start <= session_date <= end:
            created.append(session_date)
    return created


def require_daily_live_calendar(store: CompetitionStore, trading_date: date) -> date:
    """Validate a pre-provisioned signal day and its next execution session.

    Daily jobs are deliberately read-only with respect to the official calendar.
    Missing or stale calendar rows require an explicit organizer action.
    """
    with store._connect() as connection:
        current = connection.execute(
            """SELECT id, trading_date, market_open_at, market_close_at,
                      submission_open_at, submission_deadline_at
                 FROM trading_days WHERE trading_date=%s""",
            (trading_date,),
        ).fetchone()
        if not current:
            raise RuntimeError(
                f"trading day {trading_date} is not provisioned; organizer action required"
            )
        following = connection.execute(
            """SELECT id, trading_date, market_open_at
                 FROM trading_days
                WHERE trading_date>%s ORDER BY trading_date LIMIT 1""",
            (trading_date,),
        ).fetchone()
        if not following:
            raise RuntimeError(
                f"execution day after {trading_date} is not provisioned; "
                "organizer action required"
            )

    expected_deadline = decision_deadline(following["market_open_at"])
    if current["submission_open_at"] != current["market_close_at"]:
        raise RuntimeError(
            f"trading day {trading_date} has an invalid submission open time; "
            "organizer action required"
        )
    if current["submission_deadline_at"] != expected_deadline:
        raise RuntimeError(
            f"trading day {trading_date} has deadline "
            f"{current['submission_deadline_at']}; expected {expected_deadline}; "
            "organizer action required"
        )
    return following["trading_date"]


def latest_closed_session(*, now: datetime | None = None,
                          calendar_name: str = "XNYS") -> date:
    """Resolve the latest exchange session whose official close has passed."""
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - installation concern
        raise RuntimeError("install the 'exchange-calendars' package") from exc
    now = now or datetime.now(timezone.utc)
    calendar = xcals.get_calendar(calendar_name)
    schedule = calendar.schedule.loc[
        (now.date() - timedelta(days=14)).isoformat():now.date().isoformat()
    ]
    closed = [label.date() for label, row in schedule.iterrows()
              if row["close"].to_pydatetime().astimezone(timezone.utc) <= now]
    if not closed:
        raise RuntimeError("no recently closed exchange session was found")
    return closed[-1]
