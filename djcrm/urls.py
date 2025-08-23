from django.contrib import admin
from django.urls import path, include, reverse_lazy
from django.contrib.auth import views as auth_views
from django.conf import settings

from leads.views import LandingPageView, SignupView, DashboardView

# Përdor të njëjtin view për të dyja rrotat (password-reset dhe reset-password)
password_reset_view = auth_views.PasswordResetView.as_view(
    template_name='registration/password_reset_form.html',
    email_template_name='registration/password_reset_email.html',
    subject_template_name='registration/password_reset_subject.txt',
    success_url=reverse_lazy('password_reset_done'),
    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
)

urlpatterns = [
    path('admin/', admin.site.urls),

    path('', LandingPageView.as_view(), name='landing-page'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    path('leads/', include('leads.urls', namespace="leads")),
    path('agents/', include('agents.urls', namespace="agents")),
    path('signup/', SignupView.as_view(), name='signup'),

    # ----- Password reset flow -----
    path('password-reset/', password_reset_view, name='password_reset'),
    # ALIAS që kërkon template-i yt (për të shmangur NoReverseMatch)
    path('reset-password/',  password_reset_view, name='reset-password'),

    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html'
        ),
        name='password_reset_done',
    ),
    path(
        'password-reset-confirm/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset-complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html'
        ),
        name='password_reset_complete',
    ),

    # Login / Logout
    path('login/',  auth_views.LoginView.as_view(),  name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]
