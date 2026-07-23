from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import httpx
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.email_service import _TOKEN_CACHE_KEY
from apps.tickets.management.commands import process_email_replies as cmd_module
from apps.tickets.models import EmailPollState

_GRAPH_SETTINGS = dict(
    GRAPH_CLIENT_ID="id", GRAPH_CLIENT_SECRET="secret", GRAPH_TENANT_ID="tenant",
)

# A launch time that's already in the past relative to whenever the test
# suite actually runs - the real _LAUNCH_AT (Aug 3, 2026) is a fixed future
# date, so tests exercising "after launch" behavior patch it to this instead.
_LAUNCH = timezone.now() - timedelta(hours=1)


class ProcessEmailRepliesConfigTests(TestCase):
    def test_skips_when_graph_not_configured(self):
        out = StringIO()
        call_command("process_email_replies", stderr=out)
        self.assertIn("GRAPH_* settings not configured", out.getvalue())


@override_settings(**_GRAPH_SETTINGS)
class LaunchGateTests(TestCase):
    """This app launches Aug 3, 2026 12:01am Eastern - nothing should become
    a ticket before that instant, including the backlog that piled up in the
    mailbox while Graph permissions were broken pre-launch."""

    def test_before_real_launch_date_does_nothing(self):
        # Uses the real _LAUNCH_AT, unpatched - relies on "now" genuinely
        # being before Aug 3, 2026, which is true for as long as this test
        # suite runs before that date.
        out = StringIO()
        call_command("process_email_replies", stdout=out)
        self.assertIn("Before launch", out.getvalue())
        self.assertFalse(EmailPollState.objects.filter(pk=1).exists())

    def test_first_run_after_launch_clamps_cutoff_to_launch_not_now(self):
        with patch.object(cmd_module, "_LAUNCH_AT", _LAUNCH):
            out = StringIO()
            call_command("process_email_replies", stdout=out)
        self.assertIn("Cutoff established at launch time", out.getvalue())
        state = EmailPollState.objects.get(pk=1)
        self.assertEqual(state.last_received_at, _LAUNCH)

    def test_stale_pre_launch_cutoff_gets_clamped_forward(self):
        """Simulates the real production scenario: EmailPollState already
        holds a stale cutoff from before Graph permissions were fixed. Once
        launch passes, that stale cutoff must not be used as-is — it would
        sweep the entire pre-launch backlog in as tickets on the next poll —
        so it needs to be clamped up to the launch instant instead."""
        EmailPollState.objects.create(pk=1, last_received_at=_LAUNCH - timedelta(days=30))

        with patch.object(cmd_module, "_LAUNCH_AT", _LAUNCH):
            out = StringIO()
            call_command("process_email_replies", stdout=out)

        self.assertIn("Cutoff established at launch time", out.getvalue())
        state = EmailPollState.objects.get(pk=1)
        self.assertEqual(state.last_received_at, _LAUNCH)

    def test_normal_polling_resumes_once_cutoff_is_at_launch(self):
        EmailPollState.objects.create(pk=1, last_received_at=_LAUNCH)

        with patch.object(cmd_module, "_LAUNCH_AT", _LAUNCH), \
             patch.object(cmd_module, "_get_token", return_value="tok"), \
             patch.object(cmd_module, "_get_unread_messages", return_value=[]) as mock_fetch:
            out = StringIO()
            call_command("process_email_replies", stdout=out)

        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args.kwargs["since"], _LAUNCH)
        self.assertIn("Done", out.getvalue())


@override_settings(**_GRAPH_SETTINGS)
class ProcessEmailRepliesAuthFailureTests(TestCase):
    def setUp(self):
        cache.clear()
        # Establish the cutoff first (past launch), same as a real second
        # run would need before it starts actually fetching mail.
        self._launch_patch = patch.object(cmd_module, "_LAUNCH_AT", _LAUNCH)
        self._launch_patch.start()
        self.addCleanup(self._launch_patch.stop)
        call_command("process_email_replies", stdout=StringIO())

    def test_401_from_graph_clears_cached_token(self):
        cache.set(_TOKEN_CACHE_KEY, "stale-token", timeout=3600)

        response = MagicMock(status_code=401)
        error = httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=response)

        with patch("apps.tickets.management.commands.process_email_replies._get_unread_messages",
                   side_effect=error):
            out = StringIO()
            call_command("process_email_replies", stderr=out)

        self.assertIsNone(cache.get(_TOKEN_CACHE_KEY))
        self.assertIn("Failed to fetch emails", out.getvalue())

    def test_non_auth_error_leaves_cached_token_alone(self):
        cache.set(_TOKEN_CACHE_KEY, "still-good-token", timeout=3600)

        response = MagicMock(status_code=500)
        error = httpx.HTTPStatusError("server error", request=MagicMock(), response=response)

        with patch("apps.tickets.management.commands.process_email_replies._get_unread_messages",
                   side_effect=error):
            call_command("process_email_replies", stderr=StringIO())

        self.assertEqual(cache.get(_TOKEN_CACHE_KEY), "still-good-token")
