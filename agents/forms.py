from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()

class AgentModelForm(forms.ModelForm):
    password1 = forms.CharField(
        widget=forms.PasswordInput,
        required=True,
        label="Password"
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput,
        required=True,
        label="Confirm Password"
    )

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("password1")
        p2 = cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user
