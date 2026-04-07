from django.contrib import admin

from .models import FollowUp, Patient, Treatment, UserProfile


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "name", "gender", "birth_date", "current_age_display", "ethnicity")
    search_fields = ("patient_id", "name", "phone", "ethnicity")

    @admin.display(description="褰撳墠骞撮緞")
    def current_age_display(self, obj):
        return obj.current_age or "-"


@admin.register(Treatment)
class TreatmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "treatment_name", "group_name", "start_date", "total_weeks")
    list_filter = ("start_date", "group_name")


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ("treatment", "visit_number", "followup_date")
    list_filter = ("followup_date",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "modify_window_days", "created_at")
    list_filter = ("role",)
