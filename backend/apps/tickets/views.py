import csv
import json
from datetime import timedelta

from django.contrib import messages
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Q, Sum
from django.db.models.functions import TruncDate
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import ListView, CreateView, TemplateView, View

from apps.accounts.models import User, AuditLog, log_action
from apps.companies.models import Company
from .forms import TicketForm, MessageForm, TimeEntryForm, TicketStatusForm, CompanyForm, CategoryForm
from .mixins import TechRequiredMixin
from .models import Ticket, Message, TimeEntry, Attachment, Category
from .notifications import notify_new_reply, notify_ticket_assigned, notify_status_changed, notify_ticket_created


# ── Tech portal ──────────────────────────────────────────────────────────────

class TechDashboard(TechRequiredMixin, ListView):
    template_name = "tech/dashboard.html"
    context_object_name = "tickets"
    paginate_by = 25

    def get_queryset(self):
        qs = Ticket.objects.select_related("company", "assigned_to", "created_by", "category")
        status = self.request.GET.get("status")
        company = self.request.GET.get("company")
        assignee = self.request.GET.get("assignee")
        priority = self.request.GET.get("priority")
        category = self.request.GET.get("category")
        q = self.request.GET.get("q")

        if status:
            qs = qs.filter(status=status)
        if company:
            qs = qs.filter(company_id=company)
        if assignee == "me":
            qs = qs.filter(assigned_to=self.request.user)
        elif assignee == "unassigned":
            qs = qs.filter(assigned_to__isnull=True)
        if priority:
            qs = qs.filter(priority=priority)
        if category:
            qs = qs.filter(category_id=category)
        if q:
            qs = qs.filter(Q(subject__icontains=q) | Q(ticket_number__icontains=q))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["companies"] = Company.objects.filter(is_active=True)
        ctx["techs"] = User.objects.filter(role=User.Role.TECH, is_active=True)
        ctx["categories"] = Category.objects.filter(is_active=True)
        ctx["status_choices"] = Ticket.Status.choices
        ctx["priority_choices"] = Ticket.Priority.choices
        ctx["open_count"] = Ticket.objects.filter(status=Ticket.Status.OPEN).count()
        ctx["in_progress_count"] = Ticket.objects.filter(status=Ticket.Status.IN_PROGRESS).count()
        ctx["waiting_count"] = Ticket.objects.filter(status=Ticket.Status.WAITING_CLIENT).count()
        ctx["my_count"] = Ticket.objects.filter(
            assigned_to=self.request.user,
            status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS]
        ).count()
        ctx["filters"] = self.request.GET
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["filter_params"] = params.urlencode()
        ctx["breached_count"] = Ticket.objects.filter(
            sla_resolve_deadline__lt=timezone.now(),
            status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.WAITING_CLIENT],
        ).count()
        return ctx


class TechTicketDetail(TechRequiredMixin, View):
    template_name = "tech/ticket_detail.html"

    def get_ticket(self, pk):
        return get_object_or_404(
            Ticket.objects.select_related("company", "assigned_to", "created_by")
                         .prefetch_related("messages__author", "messages__attachments", "time_entries__tech"),
            pk=pk
        )

    def get(self, request, pk):
        ticket = self.get_ticket(pk)
        all_attachments = Attachment.objects.filter(
            message__ticket=ticket
        ).select_related("message__author").order_by("uploaded_at")
        return render(request, self.template_name, {
            "ticket": ticket,
            "message_form": MessageForm(is_tech=True),
            "time_form": TimeEntryForm(),
            "status_form": TicketStatusForm(instance=ticket),
            "all_attachments": all_attachments,
        })

    def post(self, request, pk):
        ticket = self.get_ticket(pk)
        action = request.POST.get("action")

        if action == "message":
            form = MessageForm(request.POST, is_tech=True)
            if form.is_valid():
                msg = form.save(commit=False)
                msg.ticket = ticket
                msg.author = request.user
                msg.save()

                # Save any uploaded files
                for f in request.FILES.getlist("attachments"):
                    Attachment.objects.create(
                        message=msg,
                        file=f,
                        filename=f.name,
                    )

                # Record first tech response time for SLA tracking
                if not msg.is_internal and not ticket.first_response_at:
                    ticket.first_response_at = msg.created_at
                    ticket.save(update_fields=["first_response_at"])

                log_action(request, AuditLog.Action.MESSAGE_ADD, target=ticket.ticket_number,
                           detail=f"internal={msg.is_internal}")
                notify_new_reply(msg)

                if request.htmx:
                    all_attachments = Attachment.objects.filter(
                        message__ticket=ticket
                    ).select_related("message__author").order_by("uploaded_at")
                    return render(request, "tech/partials/message.html", {
                        "message": msg,
                        "all_attachments": all_attachments,
                        "ticket": ticket,
                    })
                messages.success(request, "Reply added.")
            else:
                messages.error(request, "Could not add reply.")

        elif action == "time":
            form = TimeEntryForm(request.POST)
            if form.is_valid():
                entry = form.save(commit=False)
                entry.ticket = ticket
                entry.tech = request.user
                entry.save()
                log_action(request, AuditLog.Action.TIME_LOG, target=ticket.ticket_number,
                           detail=f"{entry.minutes}min — {entry.description}")
                if request.htmx:
                    return render(request, "tech/partials/time_entry.html", {
                        "entry": entry,
                        "ticket": ticket,
                    })
                messages.success(request, "Time logged.")
            else:
                messages.error(request, "Invalid time entry.")

        elif action == "status":
            form = TicketStatusForm(request.POST, instance=ticket)
            if form.is_valid():
                old_status = ticket.status
                old_assignee = ticket.assigned_to_id
                form.save()
                ticket.refresh_from_db()
                if ticket.status != old_status:
                    log_action(request, AuditLog.Action.TICKET_STATUS, target=ticket.ticket_number,
                               detail=f"{old_status} → {ticket.status}")
                if ticket.assigned_to_id != old_assignee:
                    log_action(request, AuditLog.Action.TICKET_ASSIGN, target=ticket.ticket_number,
                               detail=f"assigned to {ticket.assigned_to}")
                    notify_ticket_assigned(ticket)
                if ticket.status != old_status:
                    notify_status_changed(ticket, old_status)
                messages.success(request, "Ticket updated.")

        return redirect("tickets:tech_ticket_detail", pk=pk)


class TechTicketCreate(TechRequiredMixin, CreateView):
    model = Ticket
    form_class = TicketForm
    template_name = "tech/ticket_create.html"

    def form_valid(self, form):
        ticket = form.save(commit=False)
        ticket.created_by = self.request.user
        ticket.save()
        description = form.cleaned_data.get("description")
        if description:
            Message.objects.create(
                ticket=ticket,
                author=self.request.user,
                body=description,
                is_internal=False,
            )
        log_action(self.request, AuditLog.Action.TICKET_CREATE, target=ticket.ticket_number,
                   detail=f"Subject: {ticket.subject}")
        notify_ticket_created(ticket)
        messages.success(self.request, f"Ticket {ticket.ticket_number} created.")
        return redirect("tickets:tech_ticket_detail", pk=ticket.pk)


class TechReports(TechRequiredMixin, TemplateView):
    template_name = "tech/reports.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=6)
        thirty_days_ago = now - timedelta(days=30)

        active_statuses = [Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.WAITING_CLIENT]
        closed_statuses = [Ticket.Status.RESOLVED, Ticket.Status.CLOSED]

        all_tickets = Ticket.objects.all()

        # ── Summary cards ──
        ctx["total_this_month"] = all_tickets.filter(created_at__gte=month_start).count()
        ctx["open_total"] = all_tickets.filter(status__in=active_statuses).count()
        ctx["breached_total"] = all_tickets.filter(
            sla_resolve_deadline__lt=now,
            status__in=active_statuses,
        ).count()

        resolved_qs = all_tickets.filter(
            status__in=closed_statuses,
            resolved_at__isnull=False,
            created_at__gte=thirty_days_ago,
        )
        avg_minutes = resolved_qs.annotate(
            duration=ExpressionWrapper(
                F("resolved_at") - F("created_at"),
                output_field=FloatField(),
            )
        ).aggregate(avg=Avg("duration"))["avg"]

        if avg_minutes:
            total_secs = avg_minutes / 1_000_000  # Django returns microseconds as float
            h = int(total_secs // 3600)
            m = int((total_secs % 3600) // 60)
            ctx["avg_resolution"] = f"{h}h {m}m" if h else f"{m}m"
        else:
            ctx["avg_resolution"] = "—"

        # ── SLA compliance (last 30 days resolved) ──
        resolved_30 = all_tickets.filter(
            status__in=closed_statuses,
            resolved_at__isnull=False,
            created_at__gte=thirty_days_ago,
        )
        total_resolved = resolved_30.count()
        met_sla = resolved_30.filter(
            resolved_at__lte=F("sla_resolve_deadline")
        ).count()
        ctx["sla_compliance"] = (
            f"{round(met_sla / total_resolved * 100)}%" if total_resolved else "—"
        )

        # ── Tickets per day (last 30 days) ──
        daily = (
            all_tickets.filter(created_at__gte=thirty_days_ago)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )
        ctx["chart_daily_labels"] = json.dumps([str(r["date"]) for r in daily])
        ctx["chart_daily_data"] = json.dumps([r["count"] for r in daily])

        # ── Tickets by status ──
        by_status = (
            all_tickets.values("status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        status_map = dict(Ticket.Status.choices)
        ctx["chart_status_labels"] = json.dumps([status_map.get(r["status"], r["status"]) for r in by_status])
        ctx["chart_status_data"] = json.dumps([r["count"] for r in by_status])

        # ── Tickets by company (top 10) ──
        by_company = (
            all_tickets.values("company__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        ctx["chart_company_labels"] = json.dumps([r["company__name"] for r in by_company])
        ctx["chart_company_data"] = json.dumps([r["count"] for r in by_company])

        # ── Workload by tech ──
        by_tech = (
            all_tickets.filter(assigned_to__isnull=False)
            .values("assigned_to__first_name", "assigned_to__last_name")
            .annotate(
                open_count=Count("id", filter=Q(status__in=active_statuses)),
                total=Count("id"),
            )
            .order_by("-open_count")
        )
        ctx["tech_workload"] = by_tech

        # ── Recent SLA breaches ──
        ctx["recent_breaches"] = (
            all_tickets.filter(
                sla_resolve_deadline__lt=now,
                status__in=active_statuses,
            )
            .select_related("company", "assigned_to")
            .order_by("sla_resolve_deadline")[:10]
        )

        ctx["companies"] = Company.objects.filter(is_active=True).order_by("name")

        # ── Tickets by category ──
        by_category = (
            all_tickets.values("category__name", "category__color")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        ctx["chart_category_labels"] = json.dumps(
            [r["category__name"] or "Uncategorized" for r in by_category]
        )
        ctx["chart_category_data"] = json.dumps([r["count"] for r in by_category])
        ctx["chart_category_colors"] = json.dumps(
            [r["category__color"] or "#6b7280" for r in by_category]
        )

        return ctx


class TechTimeExport(TechRequiredMixin, View):
    def get(self, request):
        company_id = request.GET.get("company")
        qs = (
            TimeEntry.objects
            .select_related("ticket", "ticket__company", "tech")
            .order_by("ticket__company__name", "ticket__ticket_number", "created_at")
        )
        if company_id:
            qs = qs.filter(ticket__company_id=company_id)

        filename = "time-report.csv"
        if company_id:
            try:
                co = Company.objects.get(pk=company_id)
                filename = f"time-report-{co.slug}.csv"
            except Company.DoesNotExist:
                pass

        def rows():
            yield ["Company", "Ticket #", "Subject", "Tech", "Minutes", "Hours", "Description", "Date"]
            for entry in qs:
                h, m = divmod(entry.minutes, 60)
                yield [
                    entry.ticket.company.name,
                    entry.ticket.ticket_number,
                    entry.ticket.subject,
                    entry.tech.get_full_name() if entry.tech else "",
                    entry.minutes,
                    f"{h}h {m}m" if h else f"{m}m",
                    entry.description,
                    entry.created_at.strftime("%Y-%m-%d"),
                ]

        import io

        def generate():
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows():
                writer.writerow(row)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        response = StreamingHttpResponse(generate(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ── Company management ────────────────────────────────────────────────────────

class TechCompanyList(TechRequiredMixin, ListView):
    template_name = "tech/company_list.html"
    context_object_name = "companies"

    def get_queryset(self):
        return (
            Company.objects
            .annotate(
                ticket_count=Count("tickets"),
                open_count=Count(
                    "tickets",
                    filter=Q(tickets__status__in=[
                        Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.WAITING_CLIENT
                    ]),
                ),
            )
            .order_by("name")
        )


class TechCompanyDetail(TechRequiredMixin, View):
    template_name = "tech/company_detail.html"

    def get_company(self, pk):
        return get_object_or_404(Company, pk=pk)

    def get(self, request, pk):
        company = self.get_company(pk)
        tickets = (
            Ticket.objects.filter(company=company)
            .select_related("assigned_to")
            .order_by("-created_at")
        )
        return render(request, self.template_name, {
            "company": company,
            "tickets": tickets,
            "form": CompanyForm(instance=company),
        })

    def post(self, request, pk):
        company = self.get_company(pk)
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            log_action(request, AuditLog.Action.COMPANY_UPDATE, target=company.name)
            messages.success(request, f"{company.name} updated.")
            return redirect("tickets:tech_company_detail", pk=pk)
        tickets = (
            Ticket.objects.filter(company=company)
            .select_related("assigned_to")
            .order_by("-created_at")
        )
        return render(request, self.template_name, {
            "company": company,
            "tickets": tickets,
            "form": form,
        })


class TechCompanyCreate(TechRequiredMixin, View):
    template_name = "tech/company_create.html"

    def get(self, request):
        return render(request, self.template_name, {"form": CompanyForm()})

    def post(self, request):
        form = CompanyForm(request.POST)
        if form.is_valid():
            company = form.save()
            log_action(request, AuditLog.Action.COMPANY_CREATE, target=company.name)
            messages.success(request, f"{company.name} created.")
            return redirect("tickets:tech_company_detail", pk=company.pk)
        return render(request, self.template_name, {"form": form})


# ── Category management ───────────────────────────────────────────────────────

class TechBulkAction(TechRequiredMixin, View):
    def post(self, request):
        ticket_ids = request.POST.getlist("ticket_ids")
        action = request.POST.get("action", "")
        return_url = request.POST.get("return_url", "")

        if not ticket_ids:
            messages.warning(request, "No tickets selected.")
            return self._redirect(return_url)

        tickets = Ticket.objects.filter(pk__in=ticket_ids)
        count = tickets.count()

        if action == "close":
            tickets.update(status=Ticket.Status.CLOSED, resolved_at=None)
            messages.success(request, f"{count} ticket(s) closed.")

        elif action == "resolve":
            tickets.update(status=Ticket.Status.RESOLVED, resolved_at=timezone.now())
            messages.success(request, f"{count} ticket(s) resolved.")

        elif action == "set_status":
            status = request.POST.get("status")
            if status in dict(Ticket.Status.choices):
                update = {"status": status}
                if status == Ticket.Status.RESOLVED:
                    update["resolved_at"] = timezone.now()
                elif status != Ticket.Status.RESOLVED:
                    update["resolved_at"] = None
                tickets.update(**update)
                messages.success(request, f"{count} ticket(s) updated.")

        elif action == "assign":
            assigned_to_id = request.POST.get("assigned_to") or None
            tickets.update(assigned_to_id=assigned_to_id)
            messages.success(request, f"{count} ticket(s) reassigned.")

        elif action == "set_priority":
            priority = request.POST.get("priority")
            if priority in dict(Ticket.Priority.choices):
                tickets.update(priority=priority)
                messages.success(request, f"{count} ticket(s) reprioritized.")

        elif action == "set_category":
            category_id = request.POST.get("category") or None
            tickets.update(category_id=category_id)
            messages.success(request, f"{count} ticket(s) recategorized.")

        else:
            messages.error(request, "Unknown action.")
            return self._redirect(return_url)

        log_action(
            request, AuditLog.Action.TICKET_STATUS,
            target=f"{count} tickets",
            detail=f"bulk {action} on: {', '.join(t.ticket_number for t in tickets[:10])}",
        )
        return self._redirect(return_url)

    def _redirect(self, return_url):
        if return_url and return_url.startswith("/tech/"):
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(return_url)
        return redirect("tickets:tech_dashboard")


class TechCategoryList(TechRequiredMixin, View):
    template_name = "tech/category_list.html"

    _suggestions = [
        "Network & Connectivity", "Hardware", "Microsoft 365",
        "Software", "Security", "Server / Infrastructure", "General",
    ]

    def _ctx(self, form):
        return {
            "categories": Category.objects.annotate(ticket_count=Count("tickets")).order_by("name"),
            "form": form,
            "suggestions": self._suggestions,
        }

    def get(self, request):
        return render(request, self.template_name, self._ctx(CategoryForm()))

    def post(self, request):
        form = CategoryForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category '{cat.name}' created.")
            return redirect("tickets:tech_category_list")
        return render(request, self.template_name, self._ctx(form))


class TechCategoryDetail(TechRequiredMixin, View):
    template_name = "tech/category_detail.html"

    def get(self, request, pk):
        cat = get_object_or_404(Category, pk=pk)
        return render(request, self.template_name, {"cat": cat, "form": CategoryForm(instance=cat)})

    def post(self, request, pk):
        cat = get_object_or_404(Category, pk=pk)
        form = CategoryForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
            return redirect("tickets:tech_category_list")
        return render(request, self.template_name, {"cat": cat, "form": form})
