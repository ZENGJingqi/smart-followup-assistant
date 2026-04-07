from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/new/", views.account_create, name="account_create"),
    path("accounts/<int:pk>/edit/", views.account_update, name="account_update"),
    path("accounts/<int:pk>/delete/", views.account_delete, name="account_delete"),
    path("", views.patient_list, name="patient_list"),
    path("patients/export/", views.patient_export, name="patient_export"),
    path("patients/export/detail/", views.patient_export_detail, name="patient_export_detail"),
    path("patients/new/", views.patient_create, name="patient_create"),
    path("patients/<int:pk>/edit/", views.patient_update, name="patient_update"),
    path("patients/<int:pk>/delete/", views.patient_delete, name="patient_delete"),
    path("patients/<int:pk>/ai-chat/", views.patient_ai_chat, name="patient_ai_chat"),
    path("patients/<int:pk>/", views.patient_detail, name="patient_detail"),
    path(
        "patients/<int:patient_pk>/treatments/new/",
        views.treatment_create,
        name="treatment_create",
    ),
    path("treatments/<int:pk>/edit/", views.treatment_update, name="treatment_update"),
    path("treatments/<int:pk>/delete/", views.treatment_delete, name="treatment_delete"),
    path(
        "treatments/<int:pk>/toggle-followup/",
        views.treatment_toggle_followup,
        name="treatment_toggle_followup",
    ),
    path(
        "treatments/<int:treatment_id>/followups/new/",
        views.followup_create,
        name="followup_create",
    ),
    path("followups/<int:pk>/edit/", views.followup_update, name="followup_update"),
    path("followups/<int:pk>/delete/", views.followup_delete, name="followup_delete"),
]
