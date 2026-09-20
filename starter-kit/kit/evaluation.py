"""Standalone decision-period metrics; no backend runtime dependency."""

from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

CALCULATION_VERSION = 'decision-period-sharpe1764-turnover-mean-shared-ties-decimal40-v2'
CONTEXT = Context(prec=40, rounding=ROUND_HALF_EVEN)
FIELDS = ('nav_before', 'nav_after_period', 'traded_notional')
ZERO, ONE = Decimal(0), Decimal(1)


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('NAV and notional must be finite decimal numbers or decimal strings')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('NAV and notional must be finite decimal numbers or decimal strings') from error
    if not result.is_finite():
        raise ValueError('NAV and notional must be finite')
    return result


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('Evaluation timestamps must be timezone-aware ISO 8601 strings') from error


def calculate_metrics(periods, valuation_points, initial_nav=Decimal('1000000')):
    """Calculate from ordered completed periods and initial/end/close NAV points.

    Each endpoint precedes the next rebalance's fee. Intermediate closes add no
    return. Turnover averages each round's notional divided by its pre-fee NAV.
    """
    with localcontext(CONTEXT):
        initial = _decimal(initial_nav)
        if initial <= ZERO or not isinstance(periods, list) or not periods:
            raise ValueError('Positive initial NAV and at least one completed period are required')
        if not isinstance(valuation_points, list) or not valuation_points:
            raise ValueError('A valuation trajectory including initial NAV is required')
        points = [_decimal(value) for value in valuation_points]
        if points[0] != initial or any(value <= ZERO for value in points):
            raise ValueError('Valuations must be positive and start with initial NAV')
        previous, returns, ratios, endpoints = initial, [], [], []
        for period in periods:
            if not isinstance(period, dict) or any(field not in period for field in FIELDS):
                raise ValueError('Each period needs nav_before, nav_after_period, and traded_notional')
            before, end, notional = (_decimal(period[field]) for field in FIELDS)
            if before <= ZERO or end <= ZERO or notional < ZERO:
                raise ValueError('Period NAV must be positive and notional nonnegative')
            if before != previous:
                raise ValueError('Period NAV boundaries must be continuous, starting at initial NAV')
            returns.append(end / before - ONE)
            ratios.append(notional / before)
            endpoints.append(end)
            previous = end
        if points[-1] != endpoints[-1]:
            raise ValueError('Valuations must end at the last completed endpoint')
        cursor = 0
        for value in points[1:]:
            if cursor < len(endpoints) and value == endpoints[cursor]:
                cursor += 1
        if cursor != len(endpoints):
            raise ValueError('Valuations must include every period endpoint in order')
        peak, drawdown = initial, ZERO
        for value in points[1:]:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        count = Decimal(len(periods))
        mean = sum(returns, ZERO) / count
        sharpe = ZERO
        if len(periods) > 1:
            variance = sum(((value - mean) ** 2 for value in returns), ZERO) / (count - ONE)
            if variance > ZERO:
                sharpe = Decimal(1764).sqrt() * mean / variance.sqrt()
        return {'cumulative_return': endpoints[-1] / initial - ONE, 'sharpe_ratio': sharpe,
                'maximum_drawdown': drawdown, 'turnover': sum(ratios, ZERO) / count}


def _timestamped_points(periods, points, payload):
    if any(not isinstance(point, dict) or not {'as_of', 'nav'} <= point.keys() for point in points):
        raise ValueError('Timestamped valuations each need as_of and nav; do not mix input formats')
    times = [_timestamp(point['as_of']) for point in points]
    if any(left >= right for left, right in zip(times, times[1:])):
        raise ValueError('Valuation timestamps must be strictly chronological and unique')
    values = [_decimal(point['nav']) for point in points]
    by_time = dict(zip(times, values))
    previous_end = None
    for index, period in enumerate(periods):
        if not {'start_time', 'end_time'} <= period.keys():
            raise ValueError('Timestamped inputs require start_time and end_time on every period')
        start, end = _timestamp(period['start_time']), _timestamp(period['end_time'])
        if start >= end or (previous_end is not None and start != previous_end):
            raise ValueError('Period timestamps must be ordered, contiguous intervals')
        if index == 0 and start != times[0]:
            raise ValueError('Initial valuation timestamp must match the first period start')
        if end not in by_time or by_time[end] != _decimal(period['nav_after_period']):
            raise ValueError('Each endpoint timestamp must have its exact period NAV valuation')
        previous_end = end
    if times[-1] != previous_end:
        raise ValueError('Valuations must stop at the latest completed period endpoint')
    if 'as_of' in payload and _timestamp(payload['as_of']) != previous_end:
        raise ValueError('Response as_of must match the last completed period endpoint')
    return values


def evaluate(payload):
    """Accept raw input or a saved metrics API response; always recompute metrics.

    Numeric-only points are assumed to be supplied in chronological order and
    checked against period endpoints. Timestamped API inputs additionally verify
    actual chronological order and exact timestamp/NAV alignment. Equal NAV at
    different timestamps is retained. latest_valuation and cached metrics are not
    inputs to completed-period calculations.
    """
    if not isinstance(payload, dict):
        raise ValueError('Evaluation input must be an object')
    periods, points = payload.get('periods'), payload.get('valuation_points')
    if not isinstance(periods, list) or not periods or any(
            not isinstance(period, dict) or any(field not in period for field in FIELDS) for period in periods):
        raise ValueError('Provide at least one completed period with all three NAV/notional fields')
    if not isinstance(points, list) or not points:
        raise ValueError('Provide initial, completed endpoint, and intermediate close valuation points')
    if payload.get('calculation_version', CALCULATION_VERSION) != CALCULATION_VERSION:
        raise ValueError('Input calculation_version does not match this kit policy')
    for field in ('completed_periods', 'scheduled_periods'):
        if field in payload and (type(payload[field]) is not int or payload[field] < len(periods)):
            raise ValueError('Period count metadata is inconsistent with the supplied trajectory')
    if 'completed_periods' in payload and payload['completed_periods'] != len(periods):
        raise ValueError('completed_periods must equal the number of supplied periods')
    if payload.get('provisional') is False and payload.get('scheduled_periods', len(periods)) != len(periods):
        raise ValueError('A final trajectory must include every scheduled period')
    has_timestamps = any(isinstance(point, dict) for point in points) or any(
        'start_time' in period or 'end_time' in period for period in periods)
    if has_timestamps:
        points = _timestamped_points(periods, points, payload)
    elif 'as_of' in payload:
        raise ValueError('An as_of response requires timestamped periods and valuation points')
    metrics = calculate_metrics(periods, points, payload.get('initial_nav', '1000000'))
    return {'calculation_version': CALCULATION_VERSION,
            'metrics': {key: '0' if value == ZERO else str(value) for key, value in metrics.items()}}
