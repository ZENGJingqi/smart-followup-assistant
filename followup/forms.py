from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import FollowUp, Patient, Treatment, UserProfile


class DateInput(forms.DateInput):
    input_type = "text"

    def __init__(self, attrs=None, format="%Y-%m-%d"):
        attrs = attrs or {}
        existing_class = attrs.pop("class", "")
        merged_attrs = {
            "lang": "zh-CN",
            "placeholder": "YYYY-MM-DD",
            "inputmode": "numeric",
            "autocomplete": "off",
            "maxlength": "10",
            "pattern": r"\d{4}-\d{2}-\d{2}",
            "title": "请输入 YYYY-MM-DD 格式",
            "data-date-input": "true",
            "class": " ".join(filter(None, [existing_class, "js-date-input"])),
        }
        merged_attrs.update(attrs)
        super().__init__(attrs=merged_attrs, format=format)


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ["name", "gender", "birth_date", "ethnicity", "phone", "address"]
        widgets = {
            "birth_date": DateInput(),
        }


class TreatmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.initial.get("start_date"):
            self.initial["start_date"] = timezone.localdate()
            self.fields["start_date"].initial = self.initial["start_date"]

    class Meta:
        model = Treatment
        fields = [
            "group_name",
            "treatment_name",
            "start_date",
            "total_weeks",
            "followup_interval_days",
            "chief_complaint",
            "present_illness",
            "past_history",
            "personal_history",
            "marital_history",
            "allergy_history",
            "family_history",
            "tongue_diagnosis",
            "pulse_diagnosis",
            "tcm_disease",
            "western_disease",
            "treatment_principle",
            "prescription",
            "notes",
        ]
        widgets = {
            "start_date": DateInput(),
        }


class FollowUpForm(forms.ModelForm):
    next_followup_in_days = forms.IntegerField(
        label="距下次随访天数",
        required=False,
        min_value=0,
        widget=forms.NumberInput(
            attrs={
                "min": 0,
                "step": 1,
                "placeholder": "例如 30",
            }
        ),
        help_text="填写天数后，会按本次随访日期自动推算下次随访日期。",
    )

    def __init__(self, *args, treatment=None, **kwargs):
        self.treatment = treatment or getattr(kwargs.get("instance"), "treatment", None)
        super().__init__(*args, **kwargs)

        if not self.treatment:
            return

        interval_days = self.treatment.followup_interval_days

        if not self.instance.pk and not self.initial.get("followup_date"):
            self.initial["followup_date"] = timezone.localdate()
            self.fields["followup_date"].initial = self.initial["followup_date"]

        followup_date = self.initial.get("followup_date") or self.instance.followup_date
        planned_next = (
            self.initial.get("planned_next_followup_date")
            or self.instance.planned_next_followup_date
        )

        if followup_date and not planned_next:
            planned_next = followup_date + timedelta(days=interval_days)
            self.initial["planned_next_followup_date"] = planned_next
            self.fields["planned_next_followup_date"].initial = planned_next

        if self.instance.pk and self.instance.followup_date and self.instance.planned_next_followup_date:
            delta_days = (self.instance.planned_next_followup_date - self.instance.followup_date).days
            self.initial.setdefault("next_followup_in_days", max(delta_days, 0))
        else:
            self.initial.setdefault("next_followup_in_days", interval_days)

        self.fields["next_followup_in_days"].initial = self.initial["next_followup_in_days"]

    def clean(self):
        cleaned_data = super().clean()
        visit_number = cleaned_data.get("visit_number")
        treatment = self.treatment

        if visit_number and treatment:
            queryset = treatment.followups.filter(visit_number=visit_number)
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                self.add_error("visit_number", "该次随访编号已存在，请勿重复录入。")

        followup_date = cleaned_data.get("followup_date")
        next_followup_in_days = cleaned_data.get("next_followup_in_days")
        planned_next_followup_date = cleaned_data.get("planned_next_followup_date")

        if treatment and followup_date:
            if next_followup_in_days is not None:
                planned_next_followup_date = followup_date + timedelta(days=next_followup_in_days)
                cleaned_data["planned_next_followup_date"] = planned_next_followup_date
            elif not planned_next_followup_date:
                planned_next_followup_date = followup_date + timedelta(
                    days=treatment.followup_interval_days
                )
                cleaned_data["planned_next_followup_date"] = planned_next_followup_date

        if followup_date and planned_next_followup_date:
            if planned_next_followup_date < followup_date:
                self.add_error("planned_next_followup_date", "下次随访日期不能早于本次随访日期。")
            cleaned_data["next_followup_in_days"] = (
                planned_next_followup_date - followup_date
            ).days

        return cleaned_data

    class Meta:
        model = FollowUp
        fields = [
            "visit_number",
            "followup_date",
            "planned_next_followup_date",
            "symptoms",
            "medication_adherence",
            "adverse_events",
            "notes",
        ]
        widgets = {
            "followup_date": DateInput(),
            "planned_next_followup_date": DateInput(),
        }


class PatientFilterForm(forms.Form):
    VIEW_CHOICES = [
        ("card", "卡片视图"),
        ("table", "列表视图"),
    ]
    STATUS_CHOICES = [
        ("", "全部状态"),
        ("today", "今日回访"),
        ("active", "随访中"),
        ("done", "已完成"),
        ("overdue", "已逾期"),
    ]

    q = forms.CharField(label="关键词", required=False)
    group_name = forms.CharField(label="分组", required=False)
    status = forms.ChoiceField(label="状态", required=False, choices=STATUS_CHOICES)
    start_date_from = forms.DateField(label="开始时间从", required=False, widget=DateInput())
    start_date_to = forms.DateField(label="开始时间到", required=False, widget=DateInput())
    view = forms.ChoiceField(label="视图", required=False, choices=VIEW_CHOICES)


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="用户名")
    password = forms.CharField(label="密码", widget=forms.PasswordInput)


class AccountCreateForm(UserCreationForm):
    ROLE_CHOICES = [
        (UserProfile.ROLE_ADMIN, "管理员"),
        (UserProfile.ROLE_NORMAL, "普通"),
    ]

    role = forms.ChoiceField(label="账号类型", choices=ROLE_CHOICES)
    first_name = forms.CharField(label="姓名", required=False)
    is_active = forms.BooleanField(label="启用账号", required=False, initial=True)
    modify_window_days = forms.IntegerField(
        label="可修改/删除历史数据天数",
        min_value=1,
        help_text="管理员默认 365 天，普通账号默认 3 天。",
    )

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "role",
            "modify_window_days",
            "is_active",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        default_role = self.initial.get("role") or UserProfile.ROLE_ADMIN
        self.fields["modify_window_days"].initial = UserProfile.default_modify_window_days(
            default_role
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data.get("first_name", "")
        user.is_active = self.cleaned_data.get("is_active", True)
        if commit:
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "role": self.cleaned_data["role"],
                    "modify_window_days": self.cleaned_data["modify_window_days"],
                },
            )
        return user


class AccountUpdateForm(forms.ModelForm):
    ROLE_CHOICES = [
        (UserProfile.ROLE_ADMIN, "管理员"),
        (UserProfile.ROLE_NORMAL, "普通"),
    ]

    role = forms.ChoiceField(label="账号类型", choices=ROLE_CHOICES)
    modify_window_days = forms.IntegerField(
        label="可修改/删除历史数据天数",
        min_value=1,
        help_text="管理员默认 365 天，普通账号默认 3 天。",
    )
    new_password1 = forms.CharField(
        label="新密码",
        required=False,
        widget=forms.PasswordInput,
        help_text="不修改密码可留空。",
    )
    new_password2 = forms.CharField(
        label="确认新密码",
        required=False,
        widget=forms.PasswordInput,
        help_text="如填写新密码，需要再次输入确认。",
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "is_active")
        labels = {
            "username": "用户名",
            "first_name": "姓名",
            "is_active": "启用账号",
        }

    def __init__(self, *args, **kwargs):
        self.profile = kwargs.pop("profile")
        super().__init__(*args, **kwargs)
        self.fields["role"].initial = self.profile.role
        self.fields["modify_window_days"].initial = self.profile.effective_modify_window_days

    def clean(self):
        cleaned_data = super().clean()
        password_1 = cleaned_data.get("new_password1")
        password_2 = cleaned_data.get("new_password2")
        if password_1 or password_2:
            if password_1 != password_2:
                raise ValidationError("两次输入的新密码不一致。")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password1")
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
            self.profile.role = self.cleaned_data["role"]
            self.profile.modify_window_days = self.cleaned_data["modify_window_days"]
            self.profile.save()
        return user
