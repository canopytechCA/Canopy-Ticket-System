from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.tickets.models import Message, Ticket


def make_company(name="Client Co"):
    return Company.objects.create(name=name)


def make_tech(email="tech@example.com"):
    return User.objects.create_user(
        email=email, password="testpass123",
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
    defaults = {"subject": "Issue", "description": "desc", "priority": Ticket.Priority.MEDIUM}
    defaults.update(kwargs)
    return Ticket.objects.create(company=company, created_by=created_by, **defaults)


class ClientDashboardTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.other_company = make_company("Other Co")
        self.client_user = make_client_user(company=self.company)
        self.c = Client()

    def test_anonymous_redirected_to_login(self):
        r = self.c.get(reverse("portal:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_tech_user_gets_403(self):
        tech = make_tech()
        self.c.force_login(tech)
        r = self.c.get(reverse("portal:dashboard"))
        self.assertEqual(r.status_code, 403)

    def test_client_sees_200(self):
        self.c.force_login(self.client_user)
        r = self.c.get(reverse("portal:dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_client_sees_only_own_company_tickets(self):
        t1 = make_ticket(self.company, self.client_user)
        other_client = make_client_user(email="other@co.com", company=self.other_company)
        t2 = make_ticket(self.other_company, other_client)
        self.c.force_login(self.client_user)
        r = self.c.get(reverse("portal:dashboard"))
        ticket_list = list(r.context["tickets"])
        self.assertIn(t1, ticket_list)
        self.assertNotIn(t2, ticket_list)


class ClientTicketCreateTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.client_user = make_client_user(company=self.company)
        self.c = Client()
        self.c.force_login(self.client_user)

    def test_post_creates_ticket_for_own_company(self):
        self.c.post(reverse("portal:ticket_create"), {
            "subject": "My problem",
            "description": "It is broken",
            "priority": Ticket.Priority.LOW,
        })
        self.assertEqual(Ticket.objects.count(), 1)
        t = Ticket.objects.first()
        self.assertEqual(t.company, self.company)
        self.assertEqual(t.created_by, self.client_user)

    def test_post_redirects_to_ticket_detail(self):
        r = self.c.post(reverse("portal:ticket_create"), {
            "subject": "Redirect check",
            "description": "desc",
            "priority": Ticket.Priority.MEDIUM,
        })
        t = Ticket.objects.first()
        self.assertRedirects(r, reverse("portal:ticket_detail", kwargs={"pk": t.pk}))

    def test_first_message_created_from_description(self):
        self.c.post(reverse("portal:ticket_create"), {
            "subject": "Issue",
            "description": "Please help with this.",
            "priority": Ticket.Priority.MEDIUM,
        })
        t = Ticket.objects.first()
        msg = Message.objects.get(ticket=t)
        self.assertEqual(msg.body, "Please help with this.")
        self.assertFalse(msg.is_internal)


class ClientTicketDetailTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.other_company = make_company("Other Co")
        self.client_user = make_client_user(company=self.company)
        self.other_client = make_client_user(email="other@co.com", company=self.other_company)
        self.ticket = make_ticket(self.company, self.client_user)
        self.c = Client()
        self.c.force_login(self.client_user)

    def test_get_returns_200(self):
        r = self.c.get(reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk}))
        self.assertEqual(r.status_code, 200)

    def test_cannot_view_other_company_ticket(self):
        other_ticket = make_ticket(self.other_company, self.other_client)
        r = self.c.get(reverse("portal:ticket_detail", kwargs={"pk": other_ticket.pk}))
        self.assertEqual(r.status_code, 404)

    def test_internal_messages_excluded_from_context(self):
        Message.objects.create(ticket=self.ticket, body="Public reply", is_internal=False)
        Message.objects.create(ticket=self.ticket, body="Internal note", is_internal=True)
        r = self.c.get(reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk}))
        public = list(r.context["public_messages"])
        bodies = [m.body for m in public]
        self.assertIn("Public reply", bodies)
        self.assertNotIn("Internal note", bodies)

    def test_reply_creates_public_message(self):
        url = reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk})
        self.c.post(url, {"body": "Need more info"})
        msg = Message.objects.get(ticket=self.ticket)
        self.assertEqual(msg.body, "Need more info")
        self.assertFalse(msg.is_internal)

    def test_reply_auto_reopens_waiting_client_ticket(self):
        self.ticket.status = Ticket.Status.WAITING_CLIENT
        self.ticket.save(update_fields=["status"])
        url = reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk})
        self.c.post(url, {"body": "Here is the info"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.OPEN)

    def test_reply_does_not_change_open_ticket_status(self):
        self.ticket.status = Ticket.Status.OPEN
        self.ticket.save(update_fields=["status"])
        url = reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk})
        self.c.post(url, {"body": "Update"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.OPEN)

    def test_closed_ticket_cannot_receive_reply(self):
        self.ticket.status = Ticket.Status.CLOSED
        self.ticket.save(update_fields=["status", "updated_at"])
        url = reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk})
        self.c.post(url, {"body": "Reply on closed"})
        self.assertEqual(Message.objects.filter(ticket=self.ticket).count(), 0)

    def test_resolved_ticket_cannot_receive_reply(self):
        self.ticket.status = Ticket.Status.RESOLVED
        self.ticket.save()
        url = reverse("portal:ticket_detail", kwargs={"pk": self.ticket.pk})
        self.c.post(url, {"body": "Reply on resolved"})
        self.assertEqual(Message.objects.filter(ticket=self.ticket).count(), 0)
