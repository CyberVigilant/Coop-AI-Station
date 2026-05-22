from django import forms
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models

from .models import Student, ReportType, OppCategory

User = get_user_model()


class SignupForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput(render_value=True))
    password2 = forms.CharField(widget=forms.PasswordInput(render_value=True))

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise ValidationError("This username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("This email is already registered.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        if p1:
            validate_password(p1)
        return cleaned

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
        )
        Student.objects.create(
            user=user,
            full_name=self.cleaned_data["full_name"],
        )
        return user


class LoginForm(forms.Form):
    identifier = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean() 
        identifier = cleaned.get("identifier", "").strip()
        password = cleaned.get("password")

        # allow login by username OR email
        user_obj = None
        if "@" in identifier:
            user_obj = User.objects.filter(email__iexact=identifier).first()
            username = user_obj.username if user_obj else None
        else:
            username = identifier

        user = authenticate(username=username, password=password)
        if user is None:
            raise ValidationError("Invalid username/email or password.")
        cleaned["user"] = user
        return cleaned


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["full_name", "major", "phone_num", "linkedin_url"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.required = False
            field.widget.attrs.update({"class": "form-control input-icon"})

        self.fields["full_name"].widget.attrs.update({"placeholder": "Enter Full Name"})
        self.fields["major"].widget.attrs.update({"placeholder": "Enter Your Major"})
        self.fields["phone_num"].widget.attrs.update({"placeholder": "+966 5X XXX XXXX"})
        self.fields["linkedin_url"].widget.attrs.update({"placeholder": "https://linkedin.com/in/..."})


class AccountUpdateForm(forms.ModelForm):
    password1 = forms.CharField(widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.required = False
            field.widget.attrs.update({"class": "form-control input-icon"})

        self.fields["username"].widget.attrs.update({"placeholder": "Enter new username"})
        self.fields["email"].widget.attrs.update({"placeholder": "new.email@mail.com"})
        self.fields["password1"].widget.attrs.update({"placeholder": "••••••••"})
        self.fields["password2"].widget.attrs.update({"placeholder": "••••••••"})

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")

        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            if p1:
                validate_password(p1)

        return cleaned


class ReportForm(forms.Form):
    report_type = forms.ChoiceField(
        choices=ReportType.choices,
        widget=forms.Select(attrs={"class": "form-select rounded-xl"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control rounded-xl",
            "rows": 3,
            "placeholder": "Tell us more about the issue (optional)",
        }),
    )


STAR_CHOICES = [(str(i), str(i)) for i in range(1, 6)]


class RatingForm(forms.Form):
    learning_value = forms.ChoiceField(choices=STAR_CHOICES)
    work_env       = forms.ChoiceField(choices=STAR_CHOICES)
    mentorship     = forms.ChoiceField(choices=STAR_CHOICES)
    outcome        = forms.ChoiceField(choices=STAR_CHOICES)


from datetime import date as _date


class SubmitOpportunityForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        label="Opportunity Title",
        widget=forms.TextInput(attrs={
            "class": "form-control rounded-xl",
            "placeholder": "e.g. Software Engineering Intern",
        }),
    )
    company = forms.CharField(
        max_length=255,
        label="Company Name",
        widget=forms.TextInput(attrs={
            "class": "form-control rounded-xl",
            "placeholder": "e.g. Saudi Aramco",
        }),
    )
    category = forms.ModelChoiceField(
        queryset=OppCategory.objects.all().order_by(
            models.Case(models.When(name__iexact="other", then=1), default=0),
            "name",
        ),
        label="Field / Major",
        empty_label="Select a field",
        widget=forms.Select(attrs={"class": "form-select rounded-xl"}),
    )
    deadline = forms.DateField(
        required=False,
        label="Application Deadline (optional)",
        widget=forms.DateInput(attrs={"class": "form-control rounded-xl", "type": "date"}),
    )
    source_link = forms.URLField(
        max_length=800,
        label="Source Link",
        widget=forms.URLInput(attrs={
            "class": "form-control rounded-xl",
            "placeholder": "https://careers.company.com/job/123",
        }),
        help_text="Direct link to the official job posting",
    )
    description = forms.CharField(
        required=False,
        max_length=5000,
        label="Description (optional)",
        widget=forms.Textarea(attrs={
            "class": "form-control rounded-xl",
            "rows": 4,
            "placeholder": "Brief description of the opportunity...",
        }),
    )

    def clean_deadline(self):
        d = self.cleaned_data.get("deadline")
        if d and d < _date.today():
            raise forms.ValidationError("The deadline cannot be in the past.")
        return d
