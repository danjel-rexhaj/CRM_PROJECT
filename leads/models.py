from django.db import models
from django.db.models.signals import post_save
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.contrib.auth.models import AbstractUser
import phonenumbers
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django_countries import countries
from django.dispatch import receiver


class User(AbstractUser):
    is_organisor = models.BooleanField(default=True)
    is_agent = models.BooleanField(default=False)


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.user.username


class LeadManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset()


class Lead(models.Model):
    first_name = models.CharField(max_length=20)
    last_name = models.CharField(max_length=20)
    age = models.IntegerField(default=0)
    organisation = models.ForeignKey(UserProfile, null=True, blank=True, on_delete=models.CASCADE)
    agent = models.ForeignKey("Agent", null=True, blank=True, on_delete=models.SET_NULL)
    category = models.ForeignKey("Category", related_name="leads", null=True, blank=True, on_delete=models.SET_NULL)
    date_added = models.DateTimeField(auto_now_add=True)
    phone_number = models.CharField(max_length=200)
    email = models.EmailField()
    profile_picture = models.ImageField(null=True, blank=True, upload_to="profile_pictures/")
    converted_date = models.DateTimeField(null=True, blank=True)
    service = models.CharField(max_length=100, blank=True, null=True)
    affiliate = models.CharField(max_length=255, blank=True, null=True)
    forum = models.CharField(max_length=255, blank=True, null=True)
    country = models.CharField(max_length=255, blank=True, null=True)
    car_maker = models.CharField(max_length=100, blank=True, null=True)
    car_model = models.CharField(max_length=100, blank=True, null=True)
    car_engine = models.CharField(max_length=150, blank=True, null=True)


    objects = LeadManager()

    @property
    def resolved_country_code(self):
        if self.country:
            return self.country[:2].upper()
        try:
            num = phonenumbers.parse(self.phone_number, None)
            return phonenumbers.region_code_for_number(num)  # p.sh. "AL"
        except:
            return None


    @property
    def resolved_country_name(self):
        if self.country:
            # përdor dict-in e django_countries për të kthyer emrin e plotë nga kodi ISO
            return countries.name(self.country)
        try:
            num = phonenumbers.parse(self.phone_number, None)
            code = phonenumbers.region_code_for_number(num)  # p.sh. "AL"
            if code:
                return countries.name(code)  # kthen "Albania"
        except:
            pass
        return None




    @property
    def status(self):
        if self.category:
            return self.category.name
        return "Unassigned"

    def last_followup_note(self):
        followup = self.followups.order_by('-date_added').first()
        if followup and followup.notes:
            return followup.notes[:30] + ("..." if len(followup.notes) > 30 else "")
        return "—"


# Ky signal mbetet jashtë klasës
@receiver(pre_save, sender=Lead)
def set_country_from_phone(sender, instance, **kwargs):
    if not instance.country and instance.phone_number:
        try:
            num = phonenumbers.parse(instance.phone_number, None)
            region = phonenumbers.region_code_for_number(num)
            if region:
                instance.country = region
        except:
            pass


def handle_upload_follow_ups(instance, filename):
    return f"lead_followups/lead_{instance.lead.pk}/{filename}"

class FollowUp(models.Model):
    lead = models.ForeignKey(Lead, related_name="followups", on_delete=models.CASCADE)
    agent = models.ForeignKey(User, on_delete=models.CASCADE)  # sigurohemi që nuk është null
    date_added = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    file = models.FileField(null=True, blank=True, upload_to=handle_upload_follow_ups)

    def __str__(self):
        return f"{self.lead.first_name} {self.lead.last_name} - {self.agent.get_full_name()}"


class Agent(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    organisation = models.ForeignKey(UserProfile, on_delete=models.CASCADE)

    def __str__(self):
        return self.user.email


class Category(models.Model):
    name = models.CharField(max_length=30)  # New, Contacted, Converted, Unconverted
    organisation = models.ForeignKey(UserProfile, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


def post_user_created_signal(sender, instance, created, **kwargs):
    if created and not kwargs.get("raw"):
        UserProfile.objects.create(user=instance)


post_save.connect(post_user_created_signal, sender=User)



class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    url = models.CharField(max_length=200, blank=True)  # link për lead ose faqe
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message}"
    

class AgentLoginLog(models.Model):
    agent = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    outside_allowed_hours = models.BooleanField(default=False)  # 🔹 shto këtë

    def __str__(self):
        return f"{self.agent.username} - {self.timestamp}"
    

    from django.apps import AppConfig
    class LeadsConfig(AppConfig):
        name = 'leads'

        def ready(self):
            import leads.signals  # ngarko signalet