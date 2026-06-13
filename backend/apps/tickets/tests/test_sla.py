from datetime import timedelta
from unittest.mock import MagicMock

from django.test import TestCase
from django.utils import timezone

from apps.tickets.sla import (
    SLAStatus,
    deadlines_for,
    resolve_status,
    response_status,
    time_remaining_display,
)


class DeadlinesForTests(TestCase):
    def test_critical_policy(self):
        now = timezone.now()
        resp, res = deadlines_for("CRITICAL", now)
        self.assertAlmostEqual((resp - now).total_seconds() / 3600, 1, delta=0.01)
        self.assertAlmostEqual((res - now).total_seconds() / 3600, 4, delta=0.01)

    def test_high_policy(self):
        now = timezone.now()
        resp, res = deadlines_for("HIGH", now)
        self.assertAlmostEqual((resp - now).total_seconds() / 3600, 4, delta=0.01)
        self.assertAlmostEqual((res - now).total_seconds() / 3600, 8, delta=0.01)

    def test_medium_policy(self):
        now = timezone.now()
        resp, res = deadlines_for("MEDIUM", now)
        self.assertAlmostEqual((resp - now).total_seconds() / 3600, 8, delta=0.01)
        self.assertAlmostEqual((res - now).total_seconds() / 3600, 24, delta=0.01)

    def test_low_policy(self):
        now = timezone.now()
        resp, res = deadlines_for("LOW", now)
        self.assertAlmostEqual((resp - now).total_seconds() / 3600, 24, delta=0.01)
        self.assertAlmostEqual((res - now).total_seconds() / 3600, 72, delta=0.01)

    def test_unknown_priority_defaults_to_medium(self):
        now = timezone.now()
        resp_m, res_m = deadlines_for("MEDIUM", now)
        resp_u, res_u = deadlines_for("NONEXISTENT", now)
        self.assertAlmostEqual(resp_m.timestamp(), resp_u.timestamp(), delta=1)
        self.assertAlmostEqual(res_m.timestamp(), res_u.timestamp(), delta=1)


def _mock_ticket(sla_response_deadline=None, first_response_at=None,
                 sla_resolve_deadline=None, resolved_at=None, created_at=None):
    t = MagicMock()
    t.sla_response_deadline = sla_response_deadline
    t.first_response_at = first_response_at
    t.sla_resolve_deadline = sla_resolve_deadline
    t.resolved_at = resolved_at
    t.created_at = created_at or (timezone.now() - timedelta(hours=10))
    return t


class ResponseStatusTests(TestCase):
    def test_no_deadline_returns_none(self):
        t = _mock_ticket()
        self.assertIsNone(response_status(t))

    def test_met_when_responded_before_deadline(self):
        deadline = timezone.now() + timedelta(hours=2)
        responded = timezone.now() - timedelta(minutes=5)
        t = _mock_ticket(sla_response_deadline=deadline, first_response_at=responded)
        self.assertEqual(response_status(t), SLAStatus.MET)

    def test_missed_when_responded_after_deadline(self):
        deadline = timezone.now() - timedelta(hours=2)
        responded = timezone.now() - timedelta(hours=1)
        t = _mock_ticket(sla_response_deadline=deadline, first_response_at=responded)
        self.assertEqual(response_status(t), SLAStatus.MISSED)

    def test_breached_when_no_response_past_deadline(self):
        deadline = timezone.now() - timedelta(hours=1)
        t = _mock_ticket(sla_response_deadline=deadline, first_response_at=None)
        self.assertEqual(response_status(t), SLAStatus.BREACHED)

    def test_ok_when_plenty_of_time_remains(self):
        created = timezone.now() - timedelta(minutes=1)
        deadline = timezone.now() + timedelta(hours=8)
        t = _mock_ticket(sla_response_deadline=deadline, first_response_at=None, created_at=created)
        self.assertEqual(response_status(t), SLAStatus.OK)

    def test_warning_when_close_to_deadline(self):
        created = timezone.now() - timedelta(hours=9, minutes=50)
        deadline = timezone.now() + timedelta(minutes=10)
        t = _mock_ticket(sla_response_deadline=deadline, first_response_at=None, created_at=created)
        self.assertEqual(response_status(t), SLAStatus.WARNING)


class ResolveStatusTests(TestCase):
    def test_no_deadline_returns_none(self):
        t = _mock_ticket()
        self.assertIsNone(resolve_status(t))

    def test_met_when_resolved_before_deadline(self):
        deadline = timezone.now() + timedelta(hours=2)
        resolved = timezone.now() - timedelta(minutes=5)
        t = _mock_ticket(sla_resolve_deadline=deadline, resolved_at=resolved)
        self.assertEqual(resolve_status(t), SLAStatus.MET)

    def test_missed_when_resolved_after_deadline(self):
        deadline = timezone.now() - timedelta(hours=2)
        resolved = timezone.now() - timedelta(hours=1)
        t = _mock_ticket(sla_resolve_deadline=deadline, resolved_at=resolved)
        self.assertEqual(resolve_status(t), SLAStatus.MISSED)

    def test_breached_when_not_resolved_and_past_deadline(self):
        deadline = timezone.now() - timedelta(hours=1)
        t = _mock_ticket(sla_resolve_deadline=deadline, resolved_at=None)
        self.assertEqual(resolve_status(t), SLAStatus.BREACHED)


class TimeRemainingDisplayTests(TestCase):
    def test_future_hours_and_minutes(self):
        deadline = timezone.now() + timedelta(hours=2, minutes=30)
        result = time_remaining_display(deadline)
        self.assertIn("2h", result)
        self.assertNotIn("Breached", result)

    def test_future_minutes_only(self):
        deadline = timezone.now() + timedelta(minutes=45)
        result = time_remaining_display(deadline)
        self.assertIn("m", result)
        self.assertNotIn("Breached", result)

    def test_past_shows_breached(self):
        deadline = timezone.now() - timedelta(hours=1, minutes=10)
        result = time_remaining_display(deadline)
        self.assertIn("Breached", result)
        self.assertIn("1h", result)
