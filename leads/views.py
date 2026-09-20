import logging
import secrets
import time
from datetime import datetime, date, timedelta
from django_countries import countries
from django.contrib import messages
from django.shortcuts import redirect

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import HttpResponseForbidden, JsonResponse
from django.http.response import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.views import generic, View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST 

from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from datetime import datetime
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils.timezone import localtime
from django.core.mail import send_mail
from django.conf import settings
from .models import AgentLoginLog

from agents.mixins import OrganisorAndLoginRequiredMixin
from .forms import (
    LeadModelForm,
    CustomUserCreationForm,
    AssignAgentForm,
    LeadCategoryUpdateForm,
    CategoryModelForm,
    FollowUpModelForm
)
from .models import Lead, Agent, Category, FollowUp, Notification


logger = logging.getLogger(__name__)


# CRUD+L - Create, Retrieve, Update and Delete + List


SIGNUP_SESSION_KEY = "pending_signup"
SIGNUP_CODE_TTL = 10 * 60  # sekonda
SIGNUP_MAX_ATTEMPTS = 5


def _send_signup_code(pending):
    """Gjeneron një kod të ri, e ruan në 'pending' dhe e dërgon me email."""
    pending["code"] = f"{secrets.randbelow(10**6):06d}"
    pending["expires"] = time.time() + SIGNUP_CODE_TTL
    pending["attempts"] = 0
    send_mail(
        subject="Your EagleDrop CRM verification code",
        message=(
            f"Hi {pending['first_name']},\n\n"
            f"Your verification code is: {pending['code']}\n\n"
            f"It expires in {SIGNUP_CODE_TTL // 60} minutes. "
            "If you did not try to sign up, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[pending["email"]],
    )


class SignupView(generic.CreateView):
    """Hapi 1: merr të dhënat, dërgon kodin me email. Llogaria krijohet vetëm pas verifikimit."""
    template_name = "registration/signup.html"
    form_class = CustomUserCreationForm

    def form_valid(self, form):
        cd = form.cleaned_data
        pending = {
            "username": cd["username"],
            "first_name": cd["first_name"],
            "last_name": cd["last_name"],
            "email": cd["email"],
            "password": make_password(cd["password1"]),
        }
        try:
            _send_signup_code(pending)
        except Exception:
            logger.exception("Failed to send signup verification code")
            form.add_error(None, "We couldn't send the verification email. Please check the address and try again.")
            return self.form_invalid(form)
        self.request.session[SIGNUP_SESSION_KEY] = pending
        return redirect("signup-verify")


class SignupVerifyView(generic.TemplateView):
    """Hapi 2: kodi nga emaili; nëse është i saktë krijohet llogaria."""
    template_name = "registration/signup_verify.html"

    def dispatch(self, request, *args, **kwargs):
        if SIGNUP_SESSION_KEY not in request.session:
            return redirect("signup")
        return super().dispatch(request, *args, **kwargs)

    def _render(self, error=None):
        pending = self.request.session[SIGNUP_SESSION_KEY]
        return self.render_to_response({"email": pending["email"], "error": error})

    def post(self, request, *args, **kwargs):
        pending = request.session[SIGNUP_SESSION_KEY]

        if request.POST.get("resend"):
            try:
                _send_signup_code(pending)
            except Exception:
                logger.exception("Failed to resend signup verification code")
                return self._render("We couldn't send the email. Please try again in a moment.")
            request.session[SIGNUP_SESSION_KEY] = pending
            messages.success(request, "A new code has been sent.")
            return redirect("signup-verify")

        if time.time() > pending["expires"]:
            return self._render("This code has expired. Click 'Send a new code'.")
        if pending["attempts"] >= SIGNUP_MAX_ATTEMPTS:
            return self._render("Too many wrong attempts. Click 'Send a new code'.")

        code = request.POST.get("code", "").strip()
        if not constant_time_compare(code, pending["code"]):
            pending["attempts"] += 1
            request.session[SIGNUP_SESSION_KEY] = pending
            return self._render("Incorrect code. Please try again.")

        if (
            User.objects.filter(username=pending["username"]).exists()
            or User.objects.filter(email__iexact=pending["email"]).exists()
        ):
            del request.session[SIGNUP_SESSION_KEY]
            messages.error(request, "That username or email is already registered.")
            return redirect("signup")

        # Kush regjistrohet vetë është pronar i CRM-së së tij (organisor);
        # agjentët krijohen nga organisor-i te faqja Agents.
        User.objects.create(
            username=pending["username"],
            first_name=pending["first_name"],
            last_name=pending["last_name"],
            email=pending["email"],
            password=pending["password"],
            is_organisor=True,
            is_agent=False,
        )
        del request.session[SIGNUP_SESSION_KEY]
        messages.success(request, "Email verified. Your account has been created, you can now log in.")
        return redirect("login")



class LandingPageView(generic.TemplateView):
    template_name = "landing.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


ALBANIAN_MONTHS_SHORT = [
    "Jan", "Shk", "Mar", "Pri", "Maj", "Qer",
    "Kor", "Gsh", "Sht", "Tet", "Nën", "Dhj",
]


class DashboardView(OrganisorAndLoginRequiredMixin, generic.TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = super(DashboardView, self).get_context_data(**kwargs)

        user = self.request.user
        base_qs = Lead.objects.filter(organisation=user.userprofile)

        # How many leads we have in total
        total_lead_count = base_qs.count()

        # How many new leads in the last 30 days
        thirty_days_ago = date.today() - timedelta(days=30)

        total_in_past30 = base_qs.filter(
            date_added__gte=thirty_days_ago
        ).count()

        # How many converted leads in the last 30 days
        converted_category = Category.objects.filter(
            organisation=user.userprofile, name__iexact="Converted"
        ).first()
        if converted_category:
            converted_in_past30 = base_qs.filter(
                category=converted_category,
                converted_date__gte=thirty_days_ago
            ).count()
        else:
            converted_in_past30 = 0

        # Bucket every lead's category into one of 4 statuses for the dashboard cards/chart
        pending_names = ["call back", "voicemail", "no answer", "error call", "live"]
        contacted_count = base_qs.filter(category__name__icontains="convert").count()
        in_process_count = 0
        for name in pending_names:
            in_process_count += base_qs.filter(category__name__icontains=name).count()
        not_interested_count = base_qs.filter(category__name__icontains="not interest").count()
        other_count = total_lead_count - contacted_count - in_process_count - not_interested_count
        if other_count < 0:
            other_count = 0

        # Leads added per day, last 30 days, for the line chart
        chart_labels = []
        chart_data = []
        for i in range(29, -1, -1):
            day = date.today() - timedelta(days=i)
            day_count = base_qs.filter(date_added__date=day).count()
            chart_labels.append(f"{day.day:02d} {ALBANIAN_MONTHS_SHORT[day.month - 1]}")
            chart_data.append(day_count)

        context.update({
            "total_lead_count": total_lead_count,
            "total_in_past30": total_in_past30,
            "converted_in_past30": converted_in_past30,
            "contacted_count": contacted_count,
            "in_process_count": in_process_count,
            "not_interested_count": not_interested_count,
            "other_count": other_count,
            "chart_labels": chart_labels,
            "chart_data": chart_data,
        })
        return context


def landing_page(request):
    return render(request, "landing.html")

class LeadListView(LoginRequiredMixin, generic.ListView):
    template_name = "leads/lead_list.html"
    context_object_name = "leads"
    paginate_by = 10  # default

    def get_paginate_by(self, queryset):
        perpage = self.request.GET.get("perpage")
        if perpage and perpage.isdigit():
            return int(perpage)
        return self.paginate_by

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor:
            queryset = Lead.objects.filter(
                organisation=user.userprofile,
                agent__isnull=False
            )
        elif hasattr(user, "agent"):
            queryset = Lead.objects.filter(
                organisation=user.agent.organisation,
                agent__isnull=False
            ).filter(agent__user=user)
        else:
            return Lead.objects.none()

        # --- filtrat ---
        q = self.request.GET.get("q")
        agent = self.request.GET.get("agent")
        category = self.request.GET.getlist("category")
        affiliates = self.request.GET.getlist("affiliate")
        forums = self.request.GET.getlist("forum")

        if q:
            if q.isdigit():
                queryset = queryset.filter(Q(id=int(q)) | Q(phone_number=q))
            else:
                queryset = queryset.filter(
                    Q(first_name__icontains=q) |
                    Q(last_name__icontains=q) |
                    Q(email__icontains=q)
                )

        agents_filter = self.request.GET.getlist("agent")
        if agents_filter:
            queryset = queryset.filter(agent__id__in=agents_filter)

        if category:
            if "unassigned" in category:
                queryset = queryset.filter(category__isnull=True)
            else:
                queryset = queryset.filter(category__id__in=category)

        if affiliates:
            queryset = queryset.filter(affiliate__in=affiliates)

        if forums:
            queryset = queryset.filter(forum__in=forums)

        countries = self.request.GET.getlist("country")
        if countries:
            # Merr nga DB ato që kanë country
            queryset = queryset.filter(country__in=countries)

            # IDs ekstra nga prefikset
            extra_ids = [
                lead.id for lead in Lead.objects.filter(
                    organisation=user.userprofile if user.is_organisor else user.agent.organisation
                )
                if not lead.country and lead.resolved_country_code in countries
            ]




        # --- renditja ---
        sort = self.request.GET.get("sort")
        if sort == "date_asc":
            queryset = queryset.order_by("date_added")
        elif sort == "date_desc":
            queryset = queryset.order_by("-date_added")
        elif sort == "first_asc":
            queryset = queryset.order_by("first_name")
        elif sort == "first_desc":
            queryset = queryset.order_by("-first_name")
        else:
            queryset = queryset.order_by("-date_added")

        # ✅ ruaj ID e filtruar në session
        self.request.session["visible_leads"] = list(queryset.values_list("id", flat=True))

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        if user.is_organisor:
            categories = Category.objects.filter(organisation=user.userprofile)
        elif hasattr(user, "agent"):
            categories = Category.objects.filter(organisation=user.agent.organisation)
        else:
            categories = Category.objects.none()

        categories = list(categories)
        categories.insert(0, type("obj", (), {"id": "unassigned", "name": "Unassigned"})())

        context["categories"] = categories
        context["agents"] = Agent.objects.filter(
            organisation=user.userprofile if user.is_organisor else user.agent.organisation
        )
        context["unread_notifications"] = user.notifications.filter(read=False)
        context["unread_count"] = context["unread_notifications"].count()

        context["selected_affiliates"] = self.request.GET.getlist("affiliate")
        context["selected_forums"] = self.request.GET.getlist("forum")
        context["affiliates"] = Lead.objects.exclude(
            affiliate__isnull=True
        ).exclude(affiliate="").values_list("affiliate", flat=True).distinct()
        context["forums"] = Lead.objects.exclude(
            forum__isnull=True
        ).exclude(forum="").values_list("forum", flat=True).distinct()
        context["selected_categories"] = self.request.GET.getlist("category")

        context["countries"] = list(countries)  
        context["selected_countries"] = self.request.GET.getlist("country")

        # ✅ ruaj query string pa "page"
        qs = self.request.GET.copy()
        if "page" in qs:
            qs.pop("page")
        context["querystring"] = qs.urlencode()

        # Ruaj query origjinale në session nëse të duhet
        self.request.session["last_leads_query"] = self.request.GET.urlencode()
        context["selected_agents"] = self.request.GET.getlist("agent")

        return context


def lead_list(request):
    leads = Lead.objects.all()

    sort = request.GET.get("sort")
    if sort == "date_asc":
        leads = leads.order_by("date_added")
    elif sort == "date_desc":
        leads = leads.order_by("-date_added")

    paginator = Paginator(leads, request.GET.get("perpage", 10))
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {"leads": page_obj, "page_obj": page_obj}

    # nëse kërkesa është ajax => kthe vetëm tbody
    if request.GET.get("ajax"):
        return render(request, "leads/_leads_table_body.html", context)
    return render(request, "leads/lead_list.html", context)



class LeadDetailView(LoginRequiredMixin, generic.DetailView):
    template_name = "leads/lead_detail.html"
    context_object_name = "lead"

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor:
            queryset = Lead.objects.filter(organisation=user.userprofile)
        else:
            queryset = Lead.objects.filter(organisation=user.agent.organisation)
            queryset = queryset.filter(agent__user=user)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lead = self.get_object()
        context["form"] = LeadCategoryUpdateForm(instance=lead)  # ← ky është select box
        return context



def lead_detail(request, pk):
    lead = Lead.objects.get(id=pk)
    context = {
        "lead": lead
    }
    return render(request, "leads/lead_detail.html", context)


class LeadCreateView(OrganisorAndLoginRequiredMixin, generic.CreateView):
    template_name = "leads/lead_create.html"
    form_class = LeadModelForm

    def get_success_url(self):
        return reverse("leads:lead-list")

    def form_valid(self, form):
        lead = form.save(commit=False)
        lead.organisation = self.request.user.userprofile
        lead.save()
        if self.request.user.email:
            try:
                send_mail(
                    subject="A lead has been created",
                    message=f"Lead {lead.first_name} {lead.last_name} was created. Go to the site to see it.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[self.request.user.email],
                )
            except Exception:
                logger.exception("Failed to send lead-created email")
        messages.success(self.request, "You have successfully created a lead")
        return super(LeadCreateView, self).form_valid(form)


def lead_create(request):
    form = LeadModelForm()
    if request.method == "POST":
        form = LeadModelForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("/leads")
    context = {
        "form": form
    }
    return render(request, "leads/lead_create.html", context)


class LeadUpdateView(OrganisorAndLoginRequiredMixin, generic.UpdateView):
    template_name = "leads/lead_update.html"
    form_class = LeadModelForm

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        return Lead.objects.filter(organisation=user.userprofile)

    def get_success_url(self):
        return reverse("leads:lead-list")

    def form_valid(self, form):
        form.save()
        messages.info(self.request, "You have successfully updated this lead")
        return super(LeadUpdateView, self).form_valid(form)


def lead_update(request, pk):
    lead = Lead.objects.get(id=pk)
    form = LeadModelForm(instance=lead)
    if request.method == "POST":
        form = LeadModelForm(request.POST, instance=lead)
        if form.is_valid():
            form.save()
            return redirect("/leads")
    context = {
        "form": form,
        "lead": lead
    }
    return render(request, "leads/lead_update.html", context)


class LeadDeleteView(OrganisorAndLoginRequiredMixin, generic.DeleteView):
    template_name = "leads/lead_delete.html"

    def get_success_url(self):
        return reverse("leads:lead-list")

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        return Lead.objects.filter(organisation=user.userprofile)


def lead_delete(request, pk):
    lead = Lead.objects.get(id=pk)
    lead.delete()
    return redirect("/leads")


class AssignAgentView(OrganisorAndLoginRequiredMixin, generic.FormView):
    template_name = "leads/assign_agent.html"
    form_class = AssignAgentForm

    def get_form_kwargs(self, **kwargs):
        kwargs = super(AssignAgentView, self).get_form_kwargs(**kwargs)
        kwargs.update({
            "request": self.request
        })
        return kwargs
        
    def get_success_url(self):
        return reverse("leads:lead-list")

    def form_valid(self, form):
        agent = form.cleaned_data["agent"]
        lead = Lead.objects.get(id=self.kwargs["pk"])
        lead.agent = agent

        send_as_new = self.request.POST.get("send_as_new") == "1"

        if send_as_new:
            try:
                new_category = Category.objects.get(
                    organisation=lead.organisation, name="New"
                )
                lead.category = new_category
            except Category.DoesNotExist:
                pass

            lead.followups.all().delete()

        lead.save()
        return super().form_valid(form)



class CategoryListView(OrganisorAndLoginRequiredMixin, generic.ListView):
    template_name = "leads/category_list.html"
    context_object_name = "category_list"

    def get_context_data(self, **kwargs):
        context = super(CategoryListView, self).get_context_data(**kwargs)
        user = self.request.user

        if user.is_organisor:
            queryset = Lead.objects.filter(
                organisation=user.userprofile
            )
        else:
            queryset = Lead.objects.filter(
                organisation=user.agent.organisation
            )

        context.update({
            "unassigned_lead_count": queryset.filter(category__isnull=True).count()
        })
        return context

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        if user.is_organisor:
            queryset = Category.objects.filter(
                organisation=user.userprofile
            )
        else:
            queryset = Category.objects.filter(
                organisation=user.agent.organisation
            )
        return queryset


class CategoryDetailView(OrganisorAndLoginRequiredMixin, generic.DetailView):
    template_name = "leads/category_detail.html"
    context_object_name = "category"

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        if user.is_organisor:
            queryset = Category.objects.filter(
                organisation=user.userprofile
            )
        else:
            queryset = Category.objects.filter(
                organisation=user.agent.organisation
            )
        return queryset


class CategoryCreateView(OrganisorAndLoginRequiredMixin, generic.CreateView):
    template_name = "leads/category_create.html"
    form_class = CategoryModelForm

    def get_success_url(self):
        return reverse("leads:category-list")

    def form_valid(self, form):
        category = form.save(commit=False)
        category.organisation = self.request.user.userprofile
        category.save()
        return super(CategoryCreateView, self).form_valid(form)


class CategoryUpdateView(OrganisorAndLoginRequiredMixin, generic.UpdateView):
    template_name = "leads/category_update.html"
    form_class = CategoryModelForm

    def get_success_url(self):
        return reverse("leads:category-list")

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        if user.is_organisor:
            queryset = Category.objects.filter(
                organisation=user.userprofile
            )
        else:
            queryset = Category.objects.filter(
                organisation=user.agent.organisation
            )
        return queryset


class CategoryDeleteView(OrganisorAndLoginRequiredMixin, generic.DeleteView):
    template_name = "leads/category_delete.html"

    def get_success_url(self):
        return reverse("leads:category-list")

    def get_queryset(self):
        user = self.request.user
        # initial queryset of leads for the entire organisation
        if user.is_organisor:
            queryset = Category.objects.filter(
                organisation=user.userprofile
            )
        else:
            queryset = Category.objects.filter(
                organisation=user.agent.organisation
            )
        return queryset

from django.contrib import messages

class LeadCategoryUpdateView(LoginRequiredMixin, generic.UpdateView):
    template_name = "leads/lead_category_update.html"
    form_class = LeadCategoryUpdateForm

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor:
            queryset = Lead.objects.filter(organisation=user.userprofile)
        else:
            queryset = Lead.objects.filter(organisation=user.agent.organisation)
            queryset = queryset.filter(agent__user=user)
        return queryset

    def get_success_url(self):
        return reverse("leads:lead-detail", kwargs={"pk": self.get_object().id})

    def form_valid(self, form):
        lead_before_update = self.get_object()
        instance = form.save(commit=False)

        user = self.request.user

        if user.is_organisor:
            organisation = user.userprofile
        else:
            organisation = user.agent.organisation

        converted_category = Category.objects.filter(
            name="Converted",
            organisation=organisation
        ).first()

        if converted_category:
            if form.cleaned_data["category"] == converted_category:
                if lead_before_update.category != converted_category:
                    instance.converted_date = datetime.now()

        instance.save()
        messages.success(self.request, "✅ Statusi i lead-it u ndryshua me sukses!")
        return super().form_valid(form)


class FollowUpCreateView(LoginRequiredMixin, generic.CreateView):
    template_name = "leads/followup_create.html"
    form_class = FollowUpModelForm

    def get_success_url(self):
        return reverse("leads:lead-detail", kwargs={"pk": self.kwargs["pk"]})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "lead": Lead.objects.get(pk=self.kwargs["pk"])
        })
        return context

    def form_valid(self, form):
        lead = Lead.objects.get(pk=self.kwargs["pk"])
        followup = form.save(commit=False)
        followup.lead = lead
        followup.agent = self.request.user
        followup.save()
        messages.success(self.request, "📝 Shënimi u shtua me sukses!")
        return super().form_valid(form)


class FollowUpUpdateView(LoginRequiredMixin, generic.UpdateView):
    template_name = "leads/followup_update.html"
    form_class = FollowUpModelForm

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor:
            queryset = FollowUp.objects.filter(lead__organisation=user.userprofile)
        else:
            queryset = FollowUp.objects.filter(lead__organisation=user.agent.organisation)
            queryset = queryset.filter(lead__agent__user=user)
        return queryset

    def get_success_url(self):
        return reverse("leads:lead-detail", kwargs={"pk": self.get_object().lead.id})

    def form_valid(self, form):
        messages.success(self.request, "✏️ Shënimi u përditësua me sukses!")
        return super().form_valid(form)


class FollowUpDeleteView(LoginRequiredMixin, generic.DeleteView):
    template_name = "leads/followup_delete.html"

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor or user.is_superuser:
            return FollowUp.objects.all()
        else:
            return FollowUp.objects.filter(agent=user)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        lead_pk = self.object.lead.pk

        if not request.user.is_organisor and not request.user.is_superuser:
            if self.object.agent != request.user:
                return HttpResponseForbidden("You cannot delete this comment.")

        self.object.delete()
        messages.success(request, "🗑️ Shënimi u fshi me sukses!")
        return redirect("leads:lead-detail", pk=lead_pk)


# def lead_update(request, pk):
#     lead = Lead.objects.get(id=pk)
#     form = LeadForm()
#     if request.method == "POST":
#         form = LeadForm(request.POST)
#         if form.is_valid():
#             first_name = form.cleaned_data['first_name']
#             last_name = form.cleaned_data['last_name']
#             age = form.cleaned_data['age']
#             lead.first_name = first_name
#             lead.last_name = last_name
#             lead.age = age
#             lead.save()
#             return redirect("/leads")
    # context = {
    #     "form": form,
    #     "lead": lead
    # }
#     return render(request, "leads/lead_update.html", context)


# def lead_create(request):
    # form = LeadForm()
    # if request.method == "POST":
    #     form = LeadForm(request.POST)
    #     if form.is_valid():
    #         first_name = form.cleaned_data['first_name']
    #         last_name = form.cleaned_data['last_name']
    #         age = form.cleaned_data['age']
    #         agent = Agent.objects.first()
    #         Lead.objects.create(
    #             first_name=first_name,
    #             last_name=last_name,
    #             age=age,
    #             agent=agent
    #         )
    #         return redirect("/leads")
    # context = {
    #     "form": form
    # }
#     return render(request, "leads/lead_create.html", context)


class LeadJsonView(generic.View):

    def get(self, request, *args, **kwargs):
        
        qs = list(Lead.objects.all().values(
            "first_name", 
            "last_name", 
            "age")
        )

        return JsonResponse({
            "qs": qs,
        })
    

class AssignMultipleAgentsView(OrganisorAndLoginRequiredMixin, generic.ListView):
    template_name = "leads/assign_multiple_agents.html"
    context_object_name = "leads"

    def get_queryset(self):
        user = self.request.user
        if user.is_organisor:
            return Lead.objects.filter(organisation=user.userprofile, agent__isnull=True)
        else:
            return Lead.objects.filter(organisation=user.agent.organisation, agent=user.agent)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_organisor:
            agents = Agent.objects.filter(organisation=user.userprofile)
        else:
            agents = Agent.objects.filter(organisation=user.agent.organisation)
        context['agents'] = agents
        return context

    def post(self, request, *args, **kwargs):
        user = request.user
        lead_ids = request.POST.getlist('lead_ids')
        agent_id = request.POST.get('agent_id')
        send_as_new = request.POST.get('send_as_new') == "1"  # kontrollo checkbox-in

        if not lead_ids:
            messages.error(request, "Zgjidh së paku një lead.")
            return redirect('leads:lead-list')

        if not agent_id:
            messages.error(request, "Zgjidh një agent.")
            return redirect('leads:lead-list')

        try:
            agent = Agent.objects.get(id=agent_id, organisation=user.userprofile)
        except Agent.DoesNotExist:
            messages.error(request, "Agjenti i zgjedhur nuk ekziston.")
            return redirect('leads:lead-list')

        leads = Lead.objects.filter(id__in=lead_ids, organisation=user.userprofile)
        updated_count = 0

        for lead in leads:
            lead.agent = agent

            if send_as_new:
                # Reset statusi dhe fshi komentet
                try:
                    new_category = Category.objects.get(
                        organisation=lead.organisation, name="New"
                    )
                    lead.category = new_category
                except Category.DoesNotExist:
                    pass

                lead.followups.all().delete()

            lead.save()
            updated_count += 1

        if updated_count > 0 and agent.user:
            sample_names = list(leads.values_list("first_name", flat=True)[:3])
            names_str = ", ".join(sample_names)
            extra = f" +{updated_count - 3} të tjerë" if updated_count > 3 else ""
            Notification.objects.create(
                user=agent.user,
                message=f"U caktuan {updated_count} leads tek ju: {names_str}{extra}",
                url=reverse("leads:lead-list"),
            )

        messages.success(request, f"{updated_count} leads janë caktuar te agjenti {agent.user.get_full_name()}.")
        return redirect('leads:lead-list')
    

    
User = get_user_model()

from django.views import View

class PublicLeadCreateView(View):
    template_name = "leads/public_lead_form.html"

    def get(self, request, *args, **kwargs):
        # kur hap faqen me GET → shfaq formën
        return render(request, self.template_name)

    def post(self, request, *args, **kwargs):
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        email = request.POST.get("email")
        phone_number = request.POST.get("phone_number")
        age = request.POST.get("age")
        service = request.POST.get("service")

        affiliate = "MyAff"
        forum = "MyForum"

        if not (first_name and last_name and email):
            messages.error(request, "Plotëso të gjitha fushat e kërkuara.")
            return render(request, self.template_name, {})

        # gjej organizatorin
        organisor_user = User.objects.filter(is_organisor=True).order_by("id").first()
        if not organisor_user:
            messages.error(request, "S'ka organizator të konfiguruar.")
            return render(request, self.template_name, {})
        organisation = organisor_user.userprofile

        # gjej ose krijo agent
        agent, _ = Agent.objects.get_or_create(user=organisor_user, organisation=organisation)

        # gjej ose krijo kategori NEW
        new_category, _ = Category.objects.get_or_create(
            name="New", organisation=organisation
        )

        Lead.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=phone_number,
            age=age,
            organisation=organisation,
            agent=agent,
            category=new_category,
            service=service,
            affiliate=affiliate,
            forum=forum,
        )

        messages.success(request, "✅ New lead just came!")
        return redirect("leads:thank-you")





class ThankYouView(generic.TemplateView):
    template_name = "leads/thank_you.html"
    


@login_required
def lead_prev(request, pk):
    lead_ids = request.session.get("visible_leads", [])
    pk = int(pk)
    if pk not in lead_ids:
        return redirect("leads:lead-list")
    idx = lead_ids.index(pk)
    prev_id = lead_ids[idx - 1] if idx > 0 else lead_ids[-1]
    query = request.session.get("last_leads_query", "")
    return redirect(f"{reverse('leads:lead-detail', args=[prev_id])}?{query}")

@login_required
def lead_next(request, pk):
    lead_ids = request.session.get("visible_leads", [])
    pk = int(pk)
    if pk not in lead_ids:
        return redirect("leads:lead-list")
    idx = lead_ids.index(pk)
    next_id = lead_ids[idx + 1] if idx + 1 < len(lead_ids) else lead_ids[0]
    query = request.session.get("last_leads_query", "")
    return redirect(f"{reverse('leads:lead-detail', args=[next_id])}?{query}")




@login_required
@require_GET
def notifications_feed(request):
    """
    Kthen njoftime të REJA pas 'since' (ISO8601).
    Nëse s’ka 'since', kthen 10 të fundit të palexuara.
    """
    since_iso = request.GET.get("since")
    qs = Notification.objects.filter(user=request.user).order_by("-created_at")

    if since_iso:
        dt = parse_datetime(since_iso)
        if dt:
            qs = qs.filter(created_at__gt=dt, read=False)
        else:
            qs = qs.none()
    else:
        qs = qs.filter(read=False)


    items = [{
        "id": n.id,
        "message": n.message,
        "url": n.url or "",
        "created_at": n.created_at.isoformat(),
        "read": n.read,
    } for n in qs[:10000000000]]

    unread_count = Notification.objects.filter(user=request.user, read=False).count()

    return JsonResponse({
        "items": items,
        "unread_count": unread_count,
        "server_time": now().isoformat(),
    })


@login_required
@require_POST
def notifications_mark_read(request):
    """
    Shenjon si të lexuara njoftimet me ids=[...] ose all=true.
    """
    ids = request.POST.getlist("ids[]")
    mark_all = request.POST.get("all") == "true"

    if mark_all:
        Notification.objects.filter(user=request.user, read=False).update(read=True)
    elif ids:
        Notification.objects.filter(user=request.user, id__in=ids).update(read=True)

    unread_count = Notification.objects.filter(user=request.user, read=False).count()

    return JsonResponse({"ok": True, "unread_count": unread_count})


import sys

@receiver(post_save, sender=Lead)
def notify_new_lead(sender, instance, created, **kwargs):
    # 🚨 mos e ekzekuto gjatë loaddata
    if 'loaddata' in sys.argv:
        return  

    if not created:
        return

    if not instance.organisation:  # mbrojtje shtesë
        return

    admin_user = instance.organisation.user
    Notification.objects.create(
        user=admin_user,
        message=f"New lead: {instance.first_name} {instance.last_name}",
        url=reverse("leads:lead-detail", kwargs={"pk": instance.pk}),
    )




@login_required
def welcome_new_user(request):
    user = request.user
    if user.is_organisor or hasattr(user, "agent"):
        return redirect("dashboard")  # ose tek leads
    return render(request, "registration/welcome.html")


@csrf_exempt
@require_POST
def affiliate_webhook(request, affiliate, forum):
    import json

    secret = request.headers.get("X-Webhook-Secret")
    if secret != settings.WEBHOOK_SECRET:
        return JsonResponse({"ok": False, "error": "invalid secret"}, status=403)

    try:
        data = json.loads(request.body)

        organisor_user = User.objects.filter(is_organisor=True).order_by("id").first()
        if not organisor_user:
            return JsonResponse({"ok": False, "error": "no organisor account configured"}, status=500)
        organisation = organisor_user.userprofile
        agent, _ = Agent.objects.get_or_create(user=organisor_user, organisation=organisation)
        new_category, _ = Category.objects.get_or_create(name="New", organisation=organisation)

        leads_created = []

        # ✅ Nëse vjen listë
        if isinstance(data, list):
            for item in data:
                lead = Lead.objects.create(
                    first_name=item.get("first_name", ""),
                    last_name=item.get("last_name", ""),
                    email=item.get("email", ""),
                    phone_number=item.get("phone", ""),
                    organisation=organisation,
                    agent=agent,
                    category=new_category,
                    age=item.get("age", 0),
                    country=item.get("country", ""),
                    affiliate=affiliate,
                    forum=forum,
                )
                leads_created.append(lead.id)

        # ✅ Nëse vjen vetëm një objekt
        elif isinstance(data, dict):
            lead = Lead.objects.create(
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
                email=data.get("email", ""),
                phone_number=data.get("phone", ""),
                organisation=organisation,
                agent=agent,
                category=new_category,
                age=data.get("age", 0),
                country=data.get("country", ""),
                affiliate=affiliate,
                forum=forum,
            )
            leads_created.append(lead.id)

        return JsonResponse({"ok": True, "lead_ids": leads_created})

    except Exception as e:
        logger.error(f"Affiliate webhook error: {e}")
        return JsonResponse({"ok": False, "error": str(e)}, status=400)





@receiver(user_logged_in)
def notify_agent_login(sender, request, user, **kwargs):
    # vetëm për agjentët
    if hasattr(user, "agent") and user.is_agent:
        login_time = localtime().strftime("%Y-%m-%d %H:%M:%S")

        # gjej IP reale edhe nëse përdor ngrok ose proxy
        ip = request.META.get("HTTP_X_FORWARDED_FOR")
        if ip:
            ip = ip.split(",")[0]  # merr IP-n e parë (klienti real)
        else:
            ip = request.META.get("REMOTE_ADDR")

        ua = request.META.get("HTTP_USER_AGENT", "")

        # 🔹 Orari i lejuar (nga settings.py)
        allowed_start = datetime.strptime(settings.ALLOWED_LOGIN_START, "%H:%M").time()
        allowed_end = datetime.strptime(settings.ALLOWED_LOGIN_END, "%H:%M").time()
        now_time = localtime().time()

        is_outside = not (allowed_start <= now_time <= allowed_end)

        # 🔹 Ruaje logimin në DB (me flag nëse është jashtë orarit)
        AgentLoginLog.objects.create(
            agent=user,
            ip_address=ip,
            user_agent=ua,
            outside_allowed_hours=is_outside,  # kjo fushë duhet të ekzistojë në model
        )

        # 🔹 Nëse është jashtë orarit → dërgo email vetëm tek admini
        if is_outside:
            admin_email = getattr(settings, "DEFAULT_FROM_EMAIL", "support@albos-crm.com")

            send_mail(
                subject="⏰ Loguar jashtë orarit",
                message=(
                    f"Agjenti {user.get_full_name()} ({user.email}) "
                    f"u logua në {login_time}.\n\n"
                    f"IP: {ip}\n"
                    f"Browser: {ua}"
                ),
                from_email=admin_email,
                recipient_list=[admin_email],
                fail_silently=True,
            )



import random

@login_required
def shuffle_leads(request):
    if request.method == "POST" and request.user.is_organisor:
        agent_ids = request.POST.getlist("agent_ids")
        status_ids = request.POST.getlist("statuses")

        if not agent_ids or not status_ids:
            messages.error(request, "❌ Zgjidh të paktën një agjent dhe një status.")
            return redirect("leads:lead-list")

        if len(agent_ids) < 2:
            messages.error(request, "⚠️ Shuffle kërkon minimumi 2 agjentë.")
            return redirect("leads:lead-list")

        agents = list(Agent.objects.filter(id__in=agent_ids))

        qs = Lead.objects.filter(agent__in=agents)

        if "unassigned" in status_ids:
            qs = qs.filter(
                Q(category__isnull=True) |
                Q(category_id__in=[sid for sid in status_ids if sid != "unassigned"])
            )
        else:
            qs = qs.filter(category_id__in=status_ids)

        leads = list(qs)

        if not leads:
            messages.warning(request, "ℹ️ S’u gjetën leads për shuffle.")
            return redirect("leads:lead-list")

        # 🔥 RANDOM SHUFFLE
        random.shuffle(leads)

        reassigned = 0

        for lead in leads:
            new_agent = random.choice(agents)
            if lead.agent != new_agent:
                lead.agent = new_agent
                lead.save()
                reassigned += 1

        # Notifikime
        for agent in agents:
            count = Lead.objects.filter(agent=agent, id__in=[l.id for l in leads]).count()
            if count > 0:
                Notification.objects.create(
                    user=agent.user,
                    message=f"🔄 Ju janë rishpërndarë {count} leads rastësisht.",
                    url=reverse("leads:lead-list"),
                )

        messages.success(request, f"✅ U përzien {len(leads)} leads në mënyrë rastësore.")
        return redirect("leads:lead-list")

    return redirect("leads:lead-list")
