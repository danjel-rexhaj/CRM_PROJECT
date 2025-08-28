from django.core.management.base import BaseCommand
from django.utils.timezone import now, timedelta
from leads.models import AgentLoginLog

class Command(BaseCommand):
    help = "Fshin logimet e agjentëve më të vjetra se 7 ditë (vetëm ato brenda orarit të lejuar)."

    def handle(self, *args, **options):
        cutoff = now() - timedelta(days=7)
        deleted, _ = AgentLoginLog.objects.filter(
            outside_allowed_hours=False,   # mos prek ato jashtë orarit
            timestamp__lt=cutoff
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"✅ U fshinë {deleted} logime të vjetra."))
