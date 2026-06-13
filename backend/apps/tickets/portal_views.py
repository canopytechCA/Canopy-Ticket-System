from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView, CreateView, DetailView, View

from .forms import ClientTicketForm, MessageForm
from .mixins import ClientRequiredMixin
from .models import Ticket, Message, Attachment


class ClientDashboard(ClientRequiredMixin, ListView):
    template_name = "portal/dashboard.html"
    context_object_name = "tickets"
    paginate_by = 20

    def get_queryset(self):
        return Ticket.objects.filter(
            company=self.request.user.company
        ).select_related("assigned_to").order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base_qs = Ticket.objects.filter(company=self.request.user.company)
        ctx["open_count"] = base_qs.filter(status__in=[
            Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.WAITING_CLIENT
        ]).count()
        ctx["resolved_count"] = base_qs.filter(status__in=[
            Ticket.Status.RESOLVED, Ticket.Status.CLOSED
        ]).count()
        return ctx


class ClientTicketCreate(ClientRequiredMixin, CreateView):
    model = Ticket
    form_class = ClientTicketForm
    template_name = "portal/ticket_create.html"

    def form_valid(self, form):
        ticket = form.save(commit=False)
        ticket.created_by = self.request.user
        ticket.company = self.request.user.company
        ticket.save()
        Message.objects.create(
            ticket=ticket,
            author=self.request.user,
            body=form.cleaned_data["description"],
            is_internal=False,
        )
        messages.success(self.request, f"Your ticket {ticket.ticket_number} has been submitted. We'll be in touch soon.")
        return redirect("portal:ticket_detail", pk=ticket.pk)


class ClientTicketDetail(ClientRequiredMixin, View):
    template_name = "portal/ticket_detail.html"

    def get_ticket(self, request, pk):
        return get_object_or_404(
            Ticket.objects.select_related("company", "assigned_to")
                         .prefetch_related("messages__author"),
            pk=pk,
            company=request.user.company,
        )

    def get(self, request, pk):
        ticket = self.get_ticket(request, pk)
        return render(request, self.template_name, {
            "ticket": ticket,
            "message_form": MessageForm(is_tech=False),
            "public_messages": ticket.messages.filter(is_internal=False).prefetch_related("attachments"),
        })

    def post(self, request, pk):
        ticket = self.get_ticket(request, pk)
        if not ticket.is_open:
            messages.error(request, "This ticket is closed and cannot receive replies.")
            return redirect("portal:ticket_detail", pk=pk)

        form = MessageForm(request.POST, is_tech=False)
        if form.is_valid():
            msg = form.save(commit=False)
            msg.ticket = ticket
            msg.author = request.user
            msg.is_internal = False
            msg.save()

            for f in request.FILES.getlist("attachments"):
                Attachment.objects.create(message=msg, file=f, filename=f.name)

            # If ticket was waiting on client, move it back to open
            if ticket.status == Ticket.Status.WAITING_CLIENT:
                ticket.status = Ticket.Status.OPEN
                ticket.save(update_fields=["status", "updated_at"])

            if request.htmx:
                return render(request, "portal/partials/message.html", {"message": msg})
            messages.success(request, "Reply sent.")
        else:
            messages.error(request, "Could not send reply.")

        return redirect("portal:ticket_detail", pk=pk)
