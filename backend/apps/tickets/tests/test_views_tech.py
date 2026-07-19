from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.tickets.models import Message, Ticket, TimeEntry


def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_tech(email="tech@example.com", password="testpass123"):
    return User.objects.create_user(
        email=email, password=password,
        first_name="Tech", last_name="User",
        role=User.Role.TECH,
    )


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


class TechDashboardAuthTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.client_user = make_client_user(company=self.company)
        self.c = Client()

    def test_anonymous_redirected_to_login(self):
        r = self.c.get(reverse("tickets:tech_dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login/", r["Location"])

    def test_client_user_gets_403(self):
        self.c.force_login(self.client_user)
        r = self.c.get(reverse("tickets:tech_dashboard"))
        self.assertEqual(r.status_code, 403)

    def test_tech_user_gets_200(self):
        self.c.force_login(self.tech)
        r = self.c.get(reverse("tickets:tech_dashboard"))
        self.assertEqual(r.status_code, 200)


class TechDashboardFilterTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.company2 = make_company("Other Co")
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)

    def test_filter_by_status(self):
        t_open = make_ticket(self.company, self.tech, status=Ticket.Status.OPEN)
        t_resolved = make_ticket(self.company, self.tech, status=Ticket.Status.RESOLVED)
        r = self.c.get(reverse("tickets:tech_dashboard") + "?status=OPEN")
        tickets = list(r.context["tickets"])
        self.assertIn(t_open, tickets)
        self.assertNotIn(t_resolved, tickets)

    def test_filter_by_company(self):
        t1 = make_ticket(self.company, self.tech)
        t2 = make_ticket(self.company2, self.tech)
        r = self.c.get(reverse("tickets:tech_dashboard") + f"?company={self.company.pk}")
        tickets = list(r.context["tickets"])
        self.assertIn(t1, tickets)
        self.assertNotIn(t2, tickets)

    def test_filter_by_priority(self):
        t_high = make_ticket(self.company, self.tech, priority=Ticket.Priority.HIGH)
        t_low = make_ticket(self.company, self.tech, priority=Ticket.Priority.LOW)
        r = self.c.get(reverse("tickets:tech_dashboard") + "?priority=HIGH")
        tickets = list(r.context["tickets"])
        self.assertIn(t_high, tickets)
        self.assertNotIn(t_low, tickets)

    def test_filter_assignee_me(self):
        tech2 = make_tech("tech2@example.com")
        t_mine = make_ticket(self.company, self.tech, assigned_to=self.tech)
        t_theirs = make_ticket(self.company, self.tech, assigned_to=tech2)
        r = self.c.get(reverse("tickets:tech_dashboard") + "?assignee=me")
        tickets = list(r.context["tickets"])
        self.assertIn(t_mine, tickets)
        self.assertNotIn(t_theirs, tickets)

    def test_filter_assignee_unassigned(self):
        t_unassigned = make_ticket(self.company, self.tech, assigned_to=None)
        t_assigned = make_ticket(self.company, self.tech, assigned_to=self.tech)
        r = self.c.get(reverse("tickets:tech_dashboard") + "?assignee=unassigned")
        tickets = list(r.context["tickets"])
        self.assertIn(t_unassigned, tickets)
        self.assertNotIn(t_assigned, tickets)

    def test_filter_params_in_context(self):
        r = self.c.get(reverse("tickets:tech_dashboard") + "?status=OPEN")
        self.assertIn("filter_params", r.context)
        self.assertIn("status=OPEN", r.context["filter_params"])

    def test_filter_params_excludes_page(self):
        r = self.c.get(reverse("tickets:tech_dashboard") + "?status=OPEN&page=1")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("page", r.context["filter_params"])


class TechTicketDetailTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.ticket = make_ticket(self.company, self.tech)
        self.c = Client()
        self.c.force_login(self.tech)
        self.url = reverse("tickets:tech_ticket_detail", kwargs={"pk": self.ticket.pk})

    def test_get_returns_200_with_attachments_context(self):
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("all_attachments", r.context)

    def test_post_message_creates_public_reply(self):
        self.c.post(self.url, {"action": "message", "body": "Hello"})
        msg = Message.objects.get(ticket=self.ticket)
        self.assertEqual(msg.body, "Hello")
        self.assertFalse(msg.is_internal)

    def test_post_internal_message(self):
        self.c.post(self.url, {"action": "message", "body": "Private note", "is_internal": "on"})
        msg = Message.objects.get(ticket=self.ticket)
        self.assertTrue(msg.is_internal)

    def test_post_message_sets_first_response_at(self):
        self.assertIsNone(self.ticket.first_response_at)
        self.c.post(self.url, {"action": "message", "body": "Reply"})
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.first_response_at)

    def test_internal_message_does_not_set_first_response(self):
        self.c.post(self.url, {"action": "message", "body": "Internal", "is_internal": "on"})
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.first_response_at)

    def test_post_time_entry_creates_entry(self):
        self.c.post(self.url, {"action": "time", "minutes": 30, "description": "Debugging"})
        entry = TimeEntry.objects.get(ticket=self.ticket)
        self.assertEqual(entry.minutes, 30)
        self.assertEqual(entry.tech, self.tech)

    def test_post_status_update_changes_status(self):
        self.c.post(self.url, {
            "action": "status",
            "status": Ticket.Status.IN_PROGRESS,
            "priority": self.ticket.priority,
            "subject": self.ticket.subject,
            "description": self.ticket.description,
        })
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS)

    def test_post_status_update_changes_assigned_to(self):
        tech2 = make_tech("tech2@example.com")
        self.c.post(self.url, {
            "action": "status",
            "status": self.ticket.status,
            "priority": self.ticket.priority,
            "assigned_to": tech2.pk,
            "subject": self.ticket.subject,
            "description": self.ticket.description,
        })
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, tech2)


class TechTicketCreateTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)

    def test_create_ticket_redirects_to_detail(self):
        r = self.c.post(reverse("tickets:tech_ticket_create"), {
            "company": self.company.pk,
            "subject": "New issue",
            "description": "Details here",
            "priority": Ticket.Priority.HIGH,
        })
        self.assertEqual(Ticket.objects.count(), 1)
        t = Ticket.objects.first()
        self.assertRedirects(r, reverse("tickets:tech_ticket_detail", kwargs={"pk": t.pk}))

    def test_created_ticket_has_correct_fields(self):
        self.c.post(reverse("tickets:tech_ticket_create"), {
            "company": self.company.pk,
            "subject": "Printer offline",
            "description": "Printer on 2nd floor is offline.",
            "priority": Ticket.Priority.LOW,
        })
        t = Ticket.objects.first()
        self.assertEqual(t.subject, "Printer offline")
        self.assertEqual(t.created_by, self.tech)
        self.assertEqual(t.company, self.company)


class TechReportsTests(TestCase):
    def setUp(self):
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)

    def test_returns_200(self):
        r = self.c.get(reverse("tickets:tech_reports"))
        self.assertEqual(r.status_code, 200)

    def test_context_has_companies(self):
        make_company("Acme")
        r = self.c.get(reverse("tickets:tech_reports"))
        self.assertIn("companies", r.context)


class TechTimeExportTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.ticket = make_ticket(self.company, self.tech)
        TimeEntry.objects.create(ticket=self.ticket, tech=self.tech, minutes=60, description="Work")
        self.c = Client()
        self.c.force_login(self.tech)

    def test_returns_csv_content_type(self):
        r = self.c.get(reverse("tickets:tech_time_export"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv")

    def test_csv_contains_header_row(self):
        r = self.c.get(reverse("tickets:tech_time_export"))
        content = b"".join(r.streaming_content).decode()
        self.assertIn("Company", content)
        self.assertIn("Ticket #", content)
        self.assertIn("Minutes", content)

    def test_csv_contains_time_entry_data(self):
        r = self.c.get(reverse("tickets:tech_time_export"))
        content = b"".join(r.streaming_content).decode()
        self.assertIn(self.company.name, content)
        self.assertIn(self.ticket.ticket_number, content)

    def test_filter_by_company_excludes_others(self):
        co2 = make_company("Other Co")
        t2 = make_ticket(co2, self.tech)
        TimeEntry.objects.create(ticket=t2, tech=self.tech, minutes=30, description="")
        r = self.c.get(reverse("tickets:tech_time_export") + f"?company={self.company.pk}")
        content = b"".join(r.streaming_content).decode()
        self.assertIn(self.company.name, content)
        self.assertNotIn("Other Co", content)

    def test_anonymous_redirected(self):
        c = Client()
        r = c.get(reverse("tickets:tech_time_export"))
        self.assertEqual(r.status_code, 302)


class TechCompanyListTests(TestCase):
    def setUp(self):
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)

    def test_returns_200(self):
        make_company("Acme")
        r = self.c.get(reverse("tickets:tech_company_list"))
        self.assertEqual(r.status_code, 200)

    def test_lists_companies(self):
        co = make_company("Acme Corp")
        r = self.c.get(reverse("tickets:tech_company_list"))
        self.assertIn(co, r.context["companies"])

    def test_anonymous_redirected(self):
        c = Client()
        r = c.get(reverse("tickets:tech_company_list"))
        self.assertEqual(r.status_code, 302)


class TechCompanyDetailTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)
        self.url = reverse("tickets:tech_company_detail", kwargs={"pk": self.company.pk})

    def test_get_returns_200(self):
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_get_shows_company_tickets(self):
        t = make_ticket(self.company, self.tech)
        r = self.c.get(self.url)
        self.assertIn(t, r.context["tickets"])

    def test_post_updates_name(self):
        self.c.post(self.url, {
            "name": "Renamed Corp",
            "phone": "",
            "website": "",
            "notes": "",
            "is_active": "on",
        })
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Renamed Corp")

    def test_post_invalid_form_returns_200(self):
        r = self.c.post(self.url, {"name": ""})
        self.assertEqual(r.status_code, 200)


class TechCompanyCreateTests(TestCase):
    def setUp(self):
        self.tech = make_tech()
        self.c = Client()
        self.c.force_login(self.tech)

    def test_get_returns_200(self):
        r = self.c.get(reverse("tickets:tech_company_create"))
        self.assertEqual(r.status_code, 200)

    def test_post_creates_company(self):
        self.c.post(reverse("tickets:tech_company_create"), {
            "name": "New Corp",
            "phone": "780-555-1234",
            "website": "",
            "notes": "Notes here",
            "is_active": "on",
        })
        self.assertEqual(Company.objects.filter(name="New Corp").count(), 1)

    def test_post_redirects_to_detail(self):
        r = self.c.post(reverse("tickets:tech_company_create"), {
            "name": "Redirect Corp",
            "phone": "",
            "website": "",
            "notes": "",
            "is_active": "on",
        })
        co = Company.objects.get(name="Redirect Corp")
        self.assertRedirects(r, reverse("tickets:tech_company_detail", kwargs={"pk": co.pk}))


class TechBulkActionNotificationTests(TestCase):
    """tickets.update() bypasses save()/signals, so bulk resolve/close used to
    silently skip the client notification email that the single-ticket status
    change path already sends."""

    def setUp(self):
        self.company = make_company()
        self.tech = make_tech()
        self.client_user = make_client_user(company=self.company)
        self.c = Client()
        self.c.force_login(self.tech)

    def test_bulk_resolve_notifies_each_client(self):
        t1 = make_ticket(self.company, self.client_user, status=Ticket.Status.OPEN)
        t2 = make_ticket(self.company, self.client_user, status=Ticket.Status.IN_PROGRESS)
        with patch("apps.tickets.views.notify_status_changed") as mock_notify:
            self.c.post(reverse("tickets:tech_bulk_action"), {
                "ticket_ids": [t1.pk, t2.pk],
                "action": "resolve",
            })
        self.assertEqual(mock_notify.call_count, 2)
        mock_notify.assert_any_call(t1, Ticket.Status.OPEN)
        mock_notify.assert_any_call(t2, Ticket.Status.IN_PROGRESS)

    def test_bulk_close_notifies_client(self):
        t1 = make_ticket(self.company, self.client_user, status=Ticket.Status.OPEN)
        with patch("apps.tickets.views.notify_status_changed") as mock_notify:
            self.c.post(reverse("tickets:tech_bulk_action"), {
                "ticket_ids": [t1.pk],
                "action": "close",
            })
        mock_notify.assert_called_once_with(t1, Ticket.Status.OPEN)

    def test_bulk_set_status_notifies_client(self):
        t1 = make_ticket(self.company, self.client_user, status=Ticket.Status.OPEN)
        with patch("apps.tickets.views.notify_status_changed") as mock_notify:
            self.c.post(reverse("tickets:tech_bulk_action"), {
                "ticket_ids": [t1.pk],
                "action": "set_status",
                "status": Ticket.Status.CLOSED,
            })
        mock_notify.assert_called_once_with(t1, Ticket.Status.OPEN)

    def test_bulk_resolve_skips_already_resolved_tickets(self):
        already = make_ticket(self.company, self.client_user, status=Ticket.Status.RESOLVED)
        with patch("apps.tickets.views.notify_status_changed") as mock_notify:
            self.c.post(reverse("tickets:tech_bulk_action"), {
                "ticket_ids": [already.pk],
                "action": "resolve",
            })
        mock_notify.assert_not_called()

    def test_bulk_resolve_actually_updates_status(self):
        t1 = make_ticket(self.company, self.client_user, status=Ticket.Status.OPEN)
        self.c.post(reverse("tickets:tech_bulk_action"), {
            "ticket_ids": [t1.pk],
            "action": "resolve",
        })
        t1.refresh_from_db()
        self.assertEqual(t1.status, Ticket.Status.RESOLVED)
        self.assertIsNotNone(t1.resolved_at)
