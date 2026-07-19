from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.companies.models import Company
from apps.tickets.models import Ticket
from apps.tickets.notifications import notify_status_changed


def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_client_user(email="client@example.com", company=None):
    return User.objects.create_user(
        email=email, password="testpass123",
        first_name="Client", last_name="User",
        role=User.Role.CLIENT,
        company=company,
    )


def make_ticket(company, created_by, **kwargs):
    defaults = {"subject": "Test", "description": "desc", "priority": Ticket.Priority.MEDIUM}
    defaults.update(kwargs)
    return Ticket.objects.create(company=company, created_by=created_by, **defaults)


class NotifyStatusChangedTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.client_user = make_client_user(company=self.company)

    def test_resolved_notifies_client_with_resolved_wording(self):
        ticket = make_ticket(self.company, self.client_user, status=Ticket.Status.RESOLVED)
        with patch("apps.tickets.notifications.send_email") as mock_send:
            notify_status_changed(ticket, Ticket.Status.OPEN)
        mock_send.assert_called_once()
        to_email, to_name, subject, html = mock_send.call_args[0]
        self.assertEqual(to_email, self.client_user.email)
        self.assertIn("resolved", html.lower())

    def test_closed_notifies_client_with_closed_wording(self):
        ticket = make_ticket(self.company, self.client_user, status=Ticket.Status.CLOSED)
        with patch("apps.tickets.notifications.send_email") as mock_send:
            notify_status_changed(ticket, Ticket.Status.OPEN)
        mock_send.assert_called_once()
        to_email, to_name, subject, html = mock_send.call_args[0]
        self.assertEqual(to_email, self.client_user.email)
        self.assertIn("closed", html.lower())

    def test_no_notification_for_internal_status_moves(self):
        ticket = make_ticket(self.company, self.client_user, status=Ticket.Status.IN_PROGRESS)
        with patch("apps.tickets.notifications.send_email") as mock_send:
            notify_status_changed(ticket, Ticket.Status.OPEN)
        mock_send.assert_not_called()

    def test_no_notification_when_client_has_no_email_relationship(self):
        ticket = make_ticket(self.company, created_by=None, status=Ticket.Status.RESOLVED)
        with patch("apps.tickets.notifications.send_email") as mock_send:
            notify_status_changed(ticket, Ticket.Status.OPEN)
        mock_send.assert_not_called()
