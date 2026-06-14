from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.tickets.models import Attachment, Message, Ticket, TimeEntry


def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_tech(email="tech@example.com"):
    return User.objects.create_user(
        email=email, password="testpass123",
        first_name="Test", last_name="Tech",
        role=User.Role.TECH,
    )


def make_ticket(company, created_by, **kwargs):
    defaults = {"subject": "Test ticket", "description": "desc", "priority": Ticket.Priority.MEDIUM}
    defaults.update(kwargs)
    return Ticket.objects.create(company=company, created_by=created_by, **defaults)


class TicketNumberTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()

    def test_ticket_number_auto_generated(self):
        from django.utils import timezone
        t = make_ticket(self.company, self.tech)
        year = str(timezone.now().year)
        parts = t.ticket_number.split("-")
        self.assertEqual(parts[0], "T")
        self.assertEqual(parts[1], year)
        self.assertTrue(parts[2].isdigit())
        self.assertGreaterEqual(len(parts[2]), 5)

    def test_ticket_numbers_are_unique(self):
        t1 = make_ticket(self.company, self.tech)
        t2 = make_ticket(self.company, self.tech)
        self.assertNotEqual(t1.ticket_number, t2.ticket_number)

    def test_ticket_number_not_overwritten_on_update(self):
        t = make_ticket(self.company, self.tech)
        original = t.ticket_number
        t.subject = "Changed"
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.ticket_number, original)


class TicketSLATests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()

    def test_sla_deadlines_set_on_create(self):
        t = make_ticket(self.company, self.tech, priority=Ticket.Priority.HIGH)
        self.assertIsNotNone(t.sla_response_deadline)
        self.assertIsNotNone(t.sla_resolve_deadline)

    def test_high_priority_sla_hours(self):
        before = timezone.now()
        t = make_ticket(self.company, self.tech, priority=Ticket.Priority.HIGH)
        self.assertAlmostEqual(
            (t.sla_response_deadline - t.created_at).total_seconds() / 3600, 4, delta=0.01
        )
        self.assertAlmostEqual(
            (t.sla_resolve_deadline - t.created_at).total_seconds() / 3600, 8, delta=0.01
        )

    def test_critical_priority_sla_hours(self):
        t = make_ticket(self.company, self.tech, priority=Ticket.Priority.CRITICAL)
        self.assertAlmostEqual(
            (t.sla_response_deadline - t.created_at).total_seconds() / 3600, 1, delta=0.01
        )
        self.assertAlmostEqual(
            (t.sla_resolve_deadline - t.created_at).total_seconds() / 3600, 4, delta=0.01
        )

    def test_sla_deadlines_not_reset_on_update(self):
        t = make_ticket(self.company, self.tech)
        original_resp = t.sla_response_deadline
        t.subject = "Changed"
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.sla_response_deadline, original_resp)


class TicketStatusTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()

    def test_resolved_at_set_when_resolved(self):
        t = make_ticket(self.company, self.tech)
        self.assertIsNone(t.resolved_at)
        t.status = Ticket.Status.RESOLVED
        t.save()
        self.assertIsNotNone(t.resolved_at)

    def test_resolved_at_cleared_when_reopened(self):
        t = make_ticket(self.company, self.tech, status=Ticket.Status.RESOLVED)
        t.status = Ticket.Status.OPEN
        t.save()
        self.assertIsNone(t.resolved_at)

    def test_is_open_true_for_active_statuses(self):
        for status in (Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.WAITING_CLIENT):
            t = make_ticket(self.company, self.tech, status=status)
            self.assertTrue(t.is_open, status)

    def test_is_open_false_for_terminal_statuses(self):
        for status in (Ticket.Status.RESOLVED, Ticket.Status.CLOSED):
            t = make_ticket(self.company, self.tech)
            t.status = status
            t.save()
            self.assertFalse(t.is_open, status)


class TicketTotalMinutesTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.ticket = make_ticket(self.company, self.tech)

    def test_zero_with_no_entries(self):
        self.assertEqual(self.ticket.total_minutes, 0)

    def test_sums_all_entries(self):
        TimeEntry.objects.create(ticket=self.ticket, tech=self.tech, minutes=30)
        TimeEntry.objects.create(ticket=self.ticket, tech=self.tech, minutes=45)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.total_minutes, 75)


class TimeEntryTests(TestCase):
    def test_hours_display_minutes_only(self):
        e = TimeEntry(minutes=45)
        self.assertEqual(e.hours_display, "45m")

    def test_hours_display_hours_and_minutes(self):
        e = TimeEntry(minutes=90)
        self.assertEqual(e.hours_display, "1h 30m")

    def test_hours_display_zero_minutes(self):
        e = TimeEntry(minutes=120)
        self.assertEqual(e.hours_display, "2h 0m")


class AttachmentTests(TestCase):
    def test_extension_lowercased(self):
        att = Attachment(filename="Report.PDF")
        self.assertEqual(att.extension, ".pdf")

    def test_is_image_for_image_types(self):
        for name in ("photo.jpg", "img.jpeg", "pic.PNG", "anim.gif", "img.webp"):
            att = Attachment(filename=name)
            self.assertTrue(att.is_image, f"{name} should be image")

    def test_is_image_false_for_non_images(self):
        for name in ("doc.pdf", "spreadsheet.xlsx", "script.py", "archive.zip"):
            att = Attachment(filename=name)
            self.assertFalse(att.is_image, f"{name} should not be image")
