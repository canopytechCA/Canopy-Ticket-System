from django.contrib import admin
from .models import Ticket, Message, TimeEntry


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("author", "created_at")


class TimeEntryInline(admin.TabularInline):
    model = TimeEntry
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "subject", "company", "assigned_to", "status", "priority", "created_at")
    list_filter = ("status", "priority", "company", "assigned_to")
    search_fields = ("ticket_number", "subject", "description")
    readonly_fields = ("ticket_number", "created_at", "updated_at", "resolved_at")
    inlines = [MessageInline, TimeEntryInline]


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("ticket", "tech", "minutes", "description", "created_at")
    list_filter = ("tech",)
