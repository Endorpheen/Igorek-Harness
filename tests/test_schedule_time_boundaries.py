"""Schedule clocks are deterministic and only future eligible instants are returned."""

from datetime import datetime, timezone

import pytest

from factory.core import scheduler as s


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 1, 15, 10, 30, 20, tzinfo=timezone.utc)
        return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(s, 'datetime', Clock)


def schedule(kind, at='11:00', **fields):
    return s.Schedule({'schedule_type': kind, 'time_value': at, 'timezone': 'UTC', 'enabled': 1, **fields})


def stamp(day, hour, minute=0):
    return int(datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize('kind', ['daily', 'once', 'hourly', 'cron'])
def test_disabled_schedules_have_no_next_run(kind):
    assert schedule(kind, enabled=0, cron_expression='0 11 * * *').calculate_next_run() is None


@pytest.mark.parametrize('kind,at,expected', [('daily', '11:00', stamp(15, 11)), ('daily', '10:30', stamp(16, 10, 30)), ('daily', '09:00', stamp(16, 9)), ('once', '11:00', stamp(15, 11)), ('once', '10:30', None), ('once', '09:00', None)])
def test_daily_rolls_forward_but_once_never_reschedules(kind, at, expected):
    assert schedule(kind, at).calculate_next_run() == expected


@pytest.mark.parametrize('kind', ['daily', 'once', 'hourly'])
@pytest.mark.parametrize('at', ['24:00', '12:60', 'broken'])
def test_bad_time_cannot_schedule_work(kind, at):
    assert schedule(kind, at).calculate_next_run() is None


@pytest.mark.parametrize('kind', ['daily', 'once'])
def test_non_utc_local_time_is_converted_to_absolute_timestamp(kind):
    assert schedule(kind, '14:00', timezone='Europe/Moscow').calculate_next_run() == stamp(15, 11)


@pytest.mark.parametrize('expression,expected', [('0 11 * * *', stamp(15, 11)), ('0 9 * * *', stamp(16, 9)), ('* 11 * * *', stamp(15, 11)), ('0 * * * *', stamp(16, 10)), ('bad', None), ('x 11 * * *', None), ('0 25 * * *', None)])
def test_supported_daily_cron_and_malformed_forms(expression, expected):
    assert schedule('cron', cron_expression=expression).calculate_next_run() == expected


@pytest.mark.parametrize('fields', [{'interval_hours': 0}, {'interval_hours': True}, {'interval_hours': '2'}, {'interval_hours': 8761}, {'interval_hours': 2, 'interval_anchor_at': 0}, {'interval_hours': 2, 'interval_anchor_at': 'invalid'}])
def test_hourly_interval_metadata_must_be_bounded_typed_values(fields):
    assert schedule('hourly', metadata=fields).calculate_next_run() is None


def test_hourly_anchor_is_absolute_and_advances_strictly_past_now():
    past = stamp(15, 8)
    assert schedule('hourly', metadata={'interval_hours': 2, 'interval_anchor_at': past}).calculate_next_run() == stamp(15, 12)
    future = stamp(16, 8)
    assert schedule('hourly', metadata={'interval_hours': 2, 'interval_anchor_at': future}).calculate_next_run() == future
    assert schedule('hourly', '08:00', metadata={'interval_hours': 2}, created_at=stamp(15, 9)).calculate_next_run() == stamp(16, 8)


@pytest.mark.parametrize('kind,at', [('daily', ''), ('once', ''), ('hourly', ''), ('unknown', '12:00')])
def test_missing_or_unknown_schedule_type_is_not_due(kind, at):
    assert schedule(kind, at).calculate_next_run() is None
