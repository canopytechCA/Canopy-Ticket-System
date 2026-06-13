"""
Dev seed: creates demo companies, users, and tickets.
Run: python manage.py seed_dev
"""
from django.core.management.base import BaseCommand
from apps.accounts.models import User
from apps.companies.models import Company
from apps.tickets.models import Message, Ticket, TimeEntry


DEMO_PASSWORD = "ChangeMe123!"


class Command(BaseCommand):
    help = "Seed demo data for local development"

    def handle(self, *args, **options):
        self.stdout.write("Seeding...")

        # Companies
        acme, _ = Company.objects.get_or_create(name="Acme Corp", defaults={"phone": "780-555-0101"})
        globex, _ = Company.objects.get_or_create(name="Globex Industries", defaults={"phone": "780-555-0202"})

        # Techs
        marc, _ = User.objects.get_or_create(
            email="marc.gullo@canopytech.ca",
            defaults={
                "first_name": "Marc",
                "last_name": "Gullo",
                "role": User.Role.TECH,
                "is_staff": True,
                "is_superuser": True,
            }
        )
        marc.set_password(DEMO_PASSWORD)
        marc.save()

        tech2, _ = User.objects.get_or_create(
            email="tech2@canopytech.ca",
            defaults={
                "first_name": "Alex",
                "last_name": "Smith",
                "role": User.Role.TECH,
                "is_staff": True,
            }
        )
        tech2.set_password(DEMO_PASSWORD)
        tech2.save()

        # Client users
        client1, _ = User.objects.get_or_create(
            email="jane@acmecorp.com",
            defaults={
                "first_name": "Jane",
                "last_name": "Doe",
                "role": User.Role.CLIENT,
                "company": acme,
            }
        )
        client1.set_password(DEMO_PASSWORD)
        client1.save()

        client2, _ = User.objects.get_or_create(
            email="bob@globex.com",
            defaults={
                "first_name": "Bob",
                "last_name": "Burns",
                "role": User.Role.CLIENT,
                "company": globex,
            }
        )
        client2.set_password(DEMO_PASSWORD)
        client2.save()

        # Sample tickets
        if not Ticket.objects.exists():
            t1 = Ticket.objects.create(
                company=acme,
                created_by=client1,
                assigned_to=marc,
                subject="Cannot connect to VPN from home",
                description="Since the Windows update last Tuesday I can't connect to the VPN. Error says 'authentication failed'.",
                priority=Ticket.Priority.HIGH,
                status=Ticket.Status.IN_PROGRESS,
            )
            Message.objects.create(ticket=t1, author=client1, body=t1.description)
            Message.objects.create(ticket=t1, author=marc, body="Hi Jane, can you send me a screenshot of the exact error message? I'll look into the VPN logs on our end.")
            TimeEntry.objects.create(ticket=t1, tech=marc, minutes=30, description="Reviewed VPN logs, waiting on screenshot")

            t2 = Ticket.objects.create(
                company=globex,
                created_by=client2,
                subject="Printer on 2nd floor not responding",
                description="The HP LaserJet on the 2nd floor has been offline since this morning. Tried turning it off and on.",
                priority=Ticket.Priority.MEDIUM,
                status=Ticket.Status.OPEN,
            )
            Message.objects.create(ticket=t2, author=client2, body=t2.description)

            t3 = Ticket.objects.create(
                company=acme,
                created_by=client1,
                assigned_to=tech2,
                subject="New employee laptop setup — Sarah K.",
                description="We have a new hire starting Monday. Please set up a laptop with standard Acme Corp software.",
                priority=Ticket.Priority.LOW,
                status=Ticket.Status.OPEN,
            )
            Message.objects.create(ticket=t3, author=client1, body=t3.description)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Login at http://localhost:8000/auth/login/\n"
            f"  Tech:   marc.gullo@canopytech.ca / {DEMO_PASSWORD}\n"
            f"  Tech:   tech2@canopytech.ca / {DEMO_PASSWORD}\n"
            f"  Client: jane@acmecorp.com / {DEMO_PASSWORD}\n"
            f"  Client: bob@globex.com / {DEMO_PASSWORD}\n"
        ))
