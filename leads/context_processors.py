from .models import Notification

def notifications(request):
    if request.user.is_authenticated:
        unread_count = Notification.objects.filter(user=request.user, read=False).count()
        unread_notifications = Notification.objects.filter(user=request.user, read=False).order_by("-created_at")[:5]
        user_assigned_leads = request.user.leads_assigned.count() if hasattr(request.user, "leads_assigned") else 0
        user_total_leads = request.user.leads.count() if hasattr(request.user, "leads") else 0
    else:
        unread_count = 0
        unread_notifications = []
        user_assigned_leads = 0
        user_total_leads = 0

    return {
        "unread_count": unread_count,
        "unread_notifications": unread_notifications,
        "user_assigned_leads": user_assigned_leads,
        "user_total_leads": user_total_leads,
    }
