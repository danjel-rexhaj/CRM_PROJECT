#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from django.core.mail import send_mail
from django.conf import settings

def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djcrm.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()



# Shfaq konfigurimin aktual për kontroll
print("Backend:", settings.EMAIL_BACKEND)
print("Host:", settings.EMAIL_HOST)
print("User:", settings.EMAIL_HOST_USER)

# Testo dërgimin
result = send_mail(
    subject="TEST EMAIL",
    message="Ky është një test për reset password ose dërgim të zakonshëm.",
    from_email=None,  # nëse lë None përdor DEFAULT_FROM_EMAIL
    recipient_list=["testuser@icloud.com"],  # vendos emailin ku do testosh
    fail_silently=False,
)

print("Emails sent:", result)