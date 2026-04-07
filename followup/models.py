from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, models
from django.utils import timezone


class Patient(models.Model):
    GENDER_CHOICES = [
        ("male", "男"),
        ("female", "女"),
        ("other", "其他"),
    ]

    patient_id = models.CharField("患者编号", max_length=30, unique=True, blank=True)
    name = models.CharField("姓名", max_length=50)
    gender = models.CharField("性别", max_length=10, choices=GENDER_CHOICES)
    birth_date = models.DateField("出生日期", null=True, blank=True)
    ethnicity = models.CharField("民族", max_length=30, blank=True)
    age = models.PositiveIntegerField("年龄", default=0, blank=True)
    phone = models.CharField("电话", max_length=20, blank=True)
    address = models.CharField("住址", max_length=255, blank=True)
    group_name = models.CharField("分组", max_length=50, blank=True)
    diagnosis = models.CharField("诊断", max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "患者"
        verbose_name_plural = "患者"

    def __str__(self):
        return f"{self.name} ({self.patient_id})"

    @classmethod
    def generate_patient_id(cls, target_date=None):
        target_date = target_date or timezone.localdate()
        prefix = target_date.strftime("%Y%m%d")
        latest_id = (
            cls.objects.filter(patient_id__startswith=prefix)
            .order_by("-patient_id")
            .values_list("patient_id", flat=True)
            .first()
        )
        sequence = 1
        if latest_id and latest_id[-4:].isdigit():
            sequence = int(latest_id[-4:]) + 1

        candidate = f"{prefix}{sequence:04d}"
        while cls.objects.filter(patient_id=candidate).exists():
            sequence += 1
            candidate = f"{prefix}{sequence:04d}"
        return candidate

    @property
    def current_age(self):
        if self.birth_date:
            today = timezone.localdate()
            age_value = today.year - self.birth_date.year
            if (today.month, today.day) < (self.birth_date.month, self.birth_date.day):
                age_value -= 1
            return max(age_value, 0)
        return self.age or None

    def save(self, *args, **kwargs):
        if self.birth_date:
            self.age = self.current_age or 0
        if self.patient_id:
            super().save(*args, **kwargs)
            return

        last_error = None
        for _ in range(10):
            self.patient_id = self.generate_patient_id()
            try:
                super().save(*args, **kwargs)
                return
            except IntegrityError as exc:
                last_error = exc
                self.patient_id = ""
        if last_error:
            raise last_error

    @property
    def latest_treatment(self):
        cached_treatment = getattr(self, "_latest_treatment_cache", None)
        if cached_treatment is not None:
            return cached_treatment
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("treatments")
        if prefetched is not None:
            if not prefetched:
                return None
            latest_treatment = sorted(
                prefetched,
                key=lambda item: (item.start_date or date.min, item.created_at),
                reverse=True,
            )[0]
            self._latest_treatment_cache = latest_treatment
            return latest_treatment
        latest_treatment = self.treatments.order_by("-start_date", "-created_at").first()
        self._latest_treatment_cache = latest_treatment
        return latest_treatment


class Treatment(models.Model):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="treatments",
        verbose_name="患者",
    )
    group_name = models.CharField("分组", max_length=50, blank=True)
    treatment_name = models.CharField("治疗方案", max_length=100)
    start_date = models.DateField("治疗开始日期")
    total_weeks = models.PositiveIntegerField("总随访周数", default=12)
    followup_interval_days = models.PositiveIntegerField("随访间隔（天）", default=14)
    followup_closed = models.BooleanField("已结束回访", default=False)
    followup_closed_at = models.DateField("结束回访日期", null=True, blank=True)
    chief_complaint = models.TextField("主诉", blank=True)
    present_illness = models.TextField("现病史", blank=True)
    past_history = models.TextField("既往史", blank=True)
    personal_history = models.TextField("个人史", blank=True)
    marital_history = models.TextField("婚育史", blank=True)
    allergy_history = models.TextField("过敏史", blank=True)
    family_history = models.TextField("家族史", blank=True)
    tongue_diagnosis = models.TextField("舌诊", blank=True)
    pulse_diagnosis = models.TextField("脉诊", blank=True)
    tcm_disease = models.CharField("中医疾病", max_length=100, blank=True)
    western_disease = models.CharField("西医疾病", max_length=100, blank=True)
    treatment_principle = models.TextField("治则治法", blank=True)
    prescription = models.TextField("处方", blank=True)
    notes = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-created_at"]
        verbose_name = "诊疗"
        verbose_name_plural = "诊疗"

    def __str__(self):
        return f"{self.patient.name} - {self.treatment_name}"

    def _prefetched_followups(self):
        cached_followups = getattr(self, "_sorted_prefetched_followups", None)
        if cached_followups is not None:
            return cached_followups
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("followups")
        if prefetched is None:
            return None
        sorted_followups = sorted(prefetched, key=lambda item: (item.visit_number, item.followup_date))
        self._sorted_prefetched_followups = sorted_followups
        return sorted_followups

    def close_followup(self):
        self.followup_closed = True
        self.followup_closed_at = timezone.localdate()
        self.save(update_fields=["followup_closed", "followup_closed_at"])

    def reopen_followup(self):
        self.followup_closed = False
        self.followup_closed_at = None
        self.save(update_fields=["followup_closed", "followup_closed_at"])

    @property
    def display_group_name(self):
        return self.group_name or self.patient.group_name

    @property
    def display_western_disease(self):
        return self.western_disease or self.patient.diagnosis

    @property
    def planned_followup_count(self):
        if self.followup_interval_days <= 0 or self.total_weeks <= 0:
            return 0
        return max(1, (self.total_weeks * 7) // self.followup_interval_days)

    @property
    def completed_followup_count(self):
        prefetched = self._prefetched_followups()
        if prefetched is not None:
            return len(prefetched)
        return self.followups.count()

    @property
    def followup_count(self):
        return self.completed_followup_count

    @property
    def latest_visit_number(self):
        prefetched = self._prefetched_followups()
        if prefetched is not None:
            return prefetched[-1].visit_number if prefetched else 0
        latest = (
            self.followups.order_by("-visit_number")
            .values_list("visit_number", flat=True)
            .first()
        )
        return latest or 0

    @property
    def progress_percent(self):
        total = self.planned_followup_count
        if total == 0:
            return 0
        return min(100, int(self.completed_followup_count / total * 100))

    @property
    def next_followup_number(self):
        return self.latest_visit_number + 1

    @property
    def next_followup_date(self):
        if self.followup_closed:
            return None
        if self.completed_followup_count >= self.planned_followup_count:
            return None
        prefetched = self._prefetched_followups()
        latest_followup = prefetched[-1] if prefetched else None
        if latest_followup is None:
            latest_followup = self.followups.order_by("-visit_number", "-followup_date").first()
        if latest_followup:
            if latest_followup.planned_next_followup_date:
                return latest_followup.planned_next_followup_date
            return latest_followup.followup_date + timedelta(days=self.followup_interval_days)
        return self.start_date + timedelta(days=self.followup_interval_days)

    @property
    def is_due_today(self):
        next_date = self.next_followup_date
        return bool(next_date and next_date == timezone.localdate())

    @property
    def is_overdue(self):
        next_date = self.next_followup_date
        return bool(next_date and next_date < timezone.localdate())

    @property
    def status_label(self):
        if self.followup_closed:
            return "已完成"
        if self.completed_followup_count >= self.planned_followup_count:
            return "已完成"
        if self.is_due_today:
            return "今日回访"
        if self.is_overdue:
            return "已逾期"
        return "随访中"


class FollowUp(models.Model):
    treatment = models.ForeignKey(
        Treatment,
        on_delete=models.CASCADE,
        related_name="followups",
        verbose_name="诊疗",
    )
    visit_number = models.PositiveIntegerField("第几次随访")
    followup_date = models.DateField("随访日期", default=timezone.localdate)
    planned_next_followup_date = models.DateField(
        "下次建议随访日期",
        null=True,
        blank=True,
        help_text="默认按本次随访后 14 天生成，也可以手动调整。",
    )
    symptoms = models.TextField("症状变化", blank=True)
    medication_adherence = models.CharField("用药依从性", max_length=100, blank=True)
    adverse_events = models.TextField("不良反应", blank=True)
    notes = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["visit_number", "followup_date"]
        verbose_name = "随访记录"
        verbose_name_plural = "随访记录"
        constraints = [
            models.UniqueConstraint(
                fields=["treatment", "visit_number"], name="unique_followup_visit_number"
            )
        ]

    def __str__(self):
        return f"{self.treatment.patient.name} - 第{self.visit_number}次随访"


class UserProfile(models.Model):
    ROLE_ROOT = "root"
    ROLE_ADMIN = "admin"
    ROLE_NORMAL = "normal"
    ROLE_CHOICES = [
        (ROLE_ROOT, "Root"),
        (ROLE_ADMIN, "管理员"),
        (ROLE_NORMAL, "普通"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField("角色", max_length=20, choices=ROLE_CHOICES, default=ROLE_NORMAL)
    modify_window_days = models.PositiveIntegerField(
        "可修改/删除历史数据天数",
        null=True,
        blank=True,
        help_text="管理员默认 365 天，普通账号默认 3 天。",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "账号角色"
        verbose_name_plural = "账号角色"

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

    @classmethod
    def default_modify_window_days(cls, role):
        if role == cls.ROLE_ADMIN:
            return 365
        if role == cls.ROLE_NORMAL:
            return 3
        return None

    @property
    def effective_modify_window_days(self):
        if self.role == self.ROLE_ROOT:
            return None
        return self.modify_window_days or self.default_modify_window_days(self.role)

    def save(self, *args, **kwargs):
        if self.role != self.ROLE_ROOT and not self.modify_window_days:
            self.modify_window_days = self.default_modify_window_days(self.role)
        super().save(*args, **kwargs)
