from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Agent, Lead

User = get_user_model()

@receiver(pre_delete, sender=Agent)
def reassign_leads_to_admin(sender, instance, **kwargs):
    """
    Kur fshihet një agjent → të gjithë lead-et e tij kalojnë tek admin-i.
    """
    try:
        # gjej user admin
        admin_user = User.objects.get(username="admin")
        admin_agent, _ = Agent.objects.get_or_create(
            user=admin_user,
            organisation=instance.organisation
        )

        # reasign lead-et
        Lead.objects.filter(agent=instance).update(agent=admin_agent)

    except User.DoesNotExist:
        pass
