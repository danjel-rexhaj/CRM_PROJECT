from django.apps import AppConfig
import sys

class LeadsConfig(AppConfig):
    name = 'leads'

    def ready(self):
        from django.db.models.signals import post_save
        from leads.models import Lead
        from leads.views import notify_new_lead   # ← import direkt

        # Gjatë loaddata, çaktivizo signalin
        if 'loaddata' in sys.argv:
            try:
                post_save.disconnect(notify_new_lead, sender=Lead)
            except Exception:
                pass
