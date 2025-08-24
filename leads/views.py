import logging
import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
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
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.views import generic, View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from agents.mixins import OrganisorAndLoginRequiredMixin
from .forms import (
    LeadForm,
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


class SignupView(generic.CreateView):
    template_name = "registration/signup.html"
    form_class = CustomUserCreationForm

    def get_success_url(self):
        return reverse("login")

    def form_valid(self, form):
        user = form.save(commit=False)
        user.is_organisor = False  # çdo user i ri nuk është organizator
        user.is_agent = False      # as agent
        user.save()
        return super().form_valid(form)



class LandingPageView(generic.TemplateView):
    template_name = "landing.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


class DashboardView(OrganisorAndLoginRequiredMixin, generic.TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = super(DashboardView, self).get_context_data(**kwargs)

        user = self.request.user

        # How many leads we have in total
        total_lead_count = Lead.objects.filter(organisation=user.userprofile).count()

        # How many new leads in the last 30 days
        thirty_days_ago = datetime.date.today() - datetime.timedelta(days=30)

        total_in_past30 = Lead.objects.filter(
            organisation=user.userprofile,
            date_added__gte=thirty_days_ago
        ).count()

        # How many converted leads in the last 30 days
        converted_category = Category.objects.get(name="Converted")
        converted_in_past30 = Lead.objects.filter(
            organisation=user.userprofile,
            category=converted_category,
            converted_date__gte=thirty_days_ago
        ).count()

        context.update({
            "total_lead_count": total_lead_count,
            "total_in_past30": total_in_past30,
            "converted_in_past30": converted_in_past30
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
        elif hasattr(user, "agent"):  # kontrollojmë nëse ka agent
            queryset = Lead.objects.filter(
                organisation=user.agent.organisation,
                agent__isnull=False
            ).filter(agent__user=user)
        else:
            return Lead.objects.none()

        # --- Filtrimet ---
        q = self.request.GET.get("q")
        agent = self.request.GET.get("agent")
        category = self.request.GET.get("category")

        if q:
            queryset = queryset.filter(
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(id__iexact=q) |
                Q(phone_number__icontains=q) |
                Q(email__icontains=q)
            )
        if agent:
            queryset = queryset.filter(agent__id=agent)
        if category:
            queryset = queryset.filter(category__id=category)

        sort = self.request.GET.get("sort")
        if sort == "date_asc":
            queryset = queryset.order_by("date_added")
        elif sort == "date_desc":
            queryset = queryset.order_by("-date_added")
        elif sort == "first_asc":
            queryset = queryset.order_by("first_name")
        elif sort == "first_desc":
            queryset = queryset.order_by("-first_name")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        if user.is_organisor:
            context["unassigned_leads"] = Lead.objects.filter(
                organisation=user.userprofile,
                agent__isnull=True
            )
            context["agents"] = Agent.objects.filter(
                organisation=user.userprofile
            )
            context["categories"] = Category.objects.filter(
                organisation=user.userprofile
            )
        elif hasattr(user, "agent"):
            context["agents"] = Agent.objects.filter(
                organisation=user.agent.organisation
            )
            context["categories"] = Category.objects.filter(
                organisation=user.agent.organisation
            )
        else:
            context["agents"] = Agent.objects.none()
            context["categories"] = Category.objects.none()

        context["unread_notifications"] = user.notifications.filter(read=False)
        context["unread_count"] = context["unread_notifications"].count()

        # Ruaj id-të sipas filtrave, sort-it dhe faqes aktuale
        queryset = self.get_queryset()
        lead_ids = list(queryset.values_list("id", flat=True))
        self.request.session["visible_leads"] = lead_ids

        # Ruaj querystring që të rikthehesh me back button
        self.request.session["last_leads_query"] = self.request.GET.urlencode()

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
        send_mail(
            subject="A lead has been created",
            message="Go to the site to see the new lead",
            from_email="test@test.com",
            recipient_list=["test2@test.com"]
        )
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
        lead.save()
        return super(AssignAgentView, self).form_valid(form)


class CategoryListView(LoginRequiredMixin, generic.ListView):
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


class CategoryDetailView(LoginRequiredMixin, generic.DetailView):
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
        converted_category = Category.objects.get(name="Converted")
        if form.cleaned_data["category"] == converted_category:
            if lead_before_update.category != converted_category:
                instance.converted_date = datetime.datetime.now()
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
    

class AssignMultipleAgentsView(LoginRequiredMixin, generic.ListView):
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
        updated_count = leads.update(agent=agent)

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

class PublicLeadCreateView(generic.ListView):
    template_name = "leads/public_lead_form.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    def post(self, request, *args, **kwargs):
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        email = request.POST.get("email")
        phone_number = request.POST.get("phone_number")
        age = request.POST.get("age")
        service = request.POST.get("service")
        source = request.POST.get("source")  # mund ta shtosh në form-in tënd

        if not (first_name and last_name and email):
            messages.error(request, "Plotëso të gjitha fushat e kërkuara.")
            return render(request, self.template_name)

        # Marrim user admin
        admin_user = User.objects.get(username="admin")
        organisation = admin_user.userprofile

        # Marrim agentin që lidhet me admin_user
        try:
            agent = Agent.objects.get(user=admin_user)
        except Agent.DoesNotExist:
            agent = Agent.objects.create(user=admin_user, organisation=organisation)

        try:
            new_category = Category.objects.get(name="NEW", organisation=organisation)
        except Category.DoesNotExist:
            new_category = Category.objects.create(name="New", organisation=organisation)

        # Krijojmë lead
        Lead.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=phone_number,
            age=age,
            organisation=organisation,
            agent=agent,
            category=new_category
        )

        # Krijojmë notifikim për admin  <-- E KOMENTUAR SE E BËN SIGNAL-I
        # Notification.objects.create(
        #     user=admin_user,
        #     message=f"Lead i ri: {first_name} {last_name} ({email})",
        #     url="/admin/leads/lead/"
        # )

        # Nëse është nga reklama  <-- E KOMENTUAR SE E BËN SIGNAL-I
        # if source == "advertisement":
        #     Notification.objects.create(
        #         user=admin_user,
        #         message=f"Lead nga reklama: {first_name} {last_name}",
        #         url="/admin/leads/lead/"
        #     )

        messages.success(request, "New lead just came !")
        return redirect("leads:thank-you")


class ThankYouView(generic.TemplateView):
    template_name = "leads/thank_you.html"
    


@login_required
def lead_next(request, pk):
    lead_ids = request.session.get("visible_leads", [])
    pk = int(pk)
    if pk not in lead_ids:
        return redirect("leads:lead-list")

    current_index = lead_ids.index(pk)
    if current_index + 1 < len(lead_ids):
        next_id = lead_ids[current_index + 1]
    else:
        next_id = lead_ids[0]  # rikthehet tek i pari nëse s’ka më
    return redirect("leads:lead-detail", pk=next_id)


@login_required
def lead_prev(request, pk):
    lead_ids = request.session.get("visible_leads", [])
    pk = int(pk)
    if pk not in lead_ids:
        return redirect("leads:lead-list")

    current_index = lead_ids.index(pk)
    if current_index - 1 >= 0:
        prev_id = lead_ids[current_index - 1]
    else:
        prev_id = lead_ids[-1]  # shkon tek i fundit nëse është tek i pari
    return redirect("leads:lead-detail", pk=prev_id)




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
            qs = qs.filter(created_at__gt=dt)
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
    } for n in qs[:10]]

    return JsonResponse({
        "count": len(items),
        "items": items,
        "server_time": now().isoformat(),
        "csrf_token": get_token(request),
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

    return JsonResponse({"ok": True})


@receiver(post_save, sender=Lead)
def notify_new_lead(sender, instance, created, **kwargs):
    if not created:
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