import random

from django.core.mail import send_mail
from django.views import generic
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import reverse
from leads.models import Agent
from .forms import AgentModelForm
from .mixins import OrganisorAndLoginRequiredMixin
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from .forms import AgentModelForm
from leads.models import Agent




@login_required
def agent_update(request, pk):
    agent = get_object_or_404(Agent, pk=pk, organisation=request.user.userprofile)
    user = agent.user

    if request.method == "POST":
        form = AgentModelForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save()
            # nëse admin po ndryshon fjalëkalimin e vet, mos e nxirr nga sesioni
            if user.pk == request.user.pk and form.cleaned_data.get("password1"):
                update_session_auth_hash(request, user)
            messages.success(request, "Agjenti u përditësua me sukses.")
            return redirect("agents:agent-detail", pk=agent.pk)
    else:
        form = AgentModelForm(instance=user)

    return render(request, "agents/agent_update.html", {"form": form, "agent": agent})



class AgentListView(OrganisorAndLoginRequiredMixin, generic.ListView):
    template_name = "agents/agent_list.html"
    
    def get_queryset(self):
        organisation = self.request.user.userprofile
        return Agent.objects.filter(organisation=organisation)


class AgentCreateView(OrganisorAndLoginRequiredMixin, generic.View):
    template_name = "agents/agent_create.html"
    form_class = AgentModelForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {"form": form})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            # krijo user-in
            user = form.save(commit=False)
            user.is_agent = True
            user.is_organisor = False
            user.save()

            # lidhe me organizatën e adminit
            Agent.objects.create(
                user=user,
                organisation=request.user.userprofile
            )

            messages.success(request, "Agjenti u krijua me sukses ✅")
            return redirect("agents:agent-list")
    
        # nëse forma nuk është valide → kthe gabimet
        return render(request, self.template_name, {"form": form})


class AgentDetailView(OrganisorAndLoginRequiredMixin, generic.DetailView):
    template_name = "agents/agent_detail.html"
    context_object_name = "agent"

    def get_queryset(self):
        organisation = self.request.user.userprofile
        return Agent.objects.filter(organisation=organisation)


class AgentUpdateView(OrganisorAndLoginRequiredMixin, generic.UpdateView):
    template_name = "agents/agent_update.html"
    form_class = AgentModelForm
    model = Agent  # ← e bëjmë eksplicit

    def get_queryset(self):
        organisation = self.request.user.userprofile
        return Agent.objects.filter(organisation=organisation)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Forma është për User, ndaj i japim instance=agent.user
        kwargs["instance"] = self.get_object().user
        return kwargs

    def form_valid(self, form):
        user = form.save()  # form.save() përdor set_password() nëse password1 s'është bosh
        # Nëse po ndryshon fjalëkalimin e vet, mos e nxirr nga sesioni:
        if user.pk == self.request.user.pk and form.cleaned_data.get("password1"):
            update_session_auth_hash(self.request, user)
        messages.success(self.request, "Agjenti u përditësua me sukses.")
        return redirect("agents:agent-detail", pk=self.get_object().pk)

    def get_success_url(self):
        # nuk thirret sepse ne po bëjmë redirect në form_valid, por s’prish punë ta lëmë.
        return reverse("agents:agent-list")


class AgentDeleteView(OrganisorAndLoginRequiredMixin, generic.DeleteView):
    template_name = "agents/agent_delete.html"
    context_object_name = "agent"

    def get_success_url(self):
        return reverse("agents:agent-list")

    def get_queryset(self):
        organisation = self.request.user.userprofile
        return Agent.objects.filter(organisation=organisation)
