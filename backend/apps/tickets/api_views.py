import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.companies.models import Company
from .models import Ticket, Message


def _authorized(request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:]
    key = getattr(settings, "CANOPY_API_KEY", "")
    return bool(key) and token == key


@csrf_exempt
@require_POST
def create_ticket(request):
    if not _authorized(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    subject = (body.get("subject") or "").strip()
    description = (body.get("description") or "").strip()
    company_slug = (body.get("company_slug") or "").strip()
    priority = (body.get("priority") or Ticket.Priority.MEDIUM).upper()
    submitter_name = (body.get("submitter_name") or "").strip()
    submitter_email = (body.get("submitter_email") or "").strip()

    if not subject or not description or not company_slug:
        return JsonResponse(
            {"error": "subject, description, and company_slug are required"},
            status=400,
        )

    if priority not in dict(Ticket.Priority.choices):
        priority = Ticket.Priority.MEDIUM

    try:
        company = Company.objects.get(slug=company_slug, is_active=True)
    except Company.DoesNotExist:
        return JsonResponse({"error": f"Company '{company_slug}' not found"}, status=404)

    ticket = Ticket.objects.create(
        company=company,
        subject=subject,
        description=description,
        priority=priority,
        created_by=None,
    )

    # Build the opening message body, prepending submitter info if provided
    if submitter_name or submitter_email:
        header = f"Submitted via Chat Agent by: {submitter_name}"
        if submitter_email:
            header += f" <{submitter_email}>"
        message_body = f"{header}\n\n{description}"
    else:
        message_body = description

    Message.objects.create(
        ticket=ticket,
        author=None,
        body=message_body,
        is_internal=False,
    )

    return JsonResponse(
        {"ticket_number": ticket.ticket_number, "id": ticket.pk},
        status=201,
    )
