import io
import zipfile
import json
from unittest.mock import patch
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .ai import AIServiceError, build_patient_context
from .models import FollowUp, Patient, Treatment, UserProfile


class FollowupWorkflowTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username="admin", password="Admin@123456")
        UserProfile.objects.create(user=self.admin_user, role=UserProfile.ROLE_ADMIN)
        self.client.force_login(self.admin_user)

        self.patient = Patient.objects.create(
            patient_id="P001",
            name="Patient A",
            gender="male",
            birth_date=date(1980, 4, 1),
            ethnicity="Han",
            phone="13800000000",
            address="Nanjing Road 1",
        )
        self.treatment = Treatment.objects.create(
            patient=self.patient,
            group_name="Treatment Group",
            treatment_name="Standard Plan",
            start_date=date(2026, 4, 1),
            total_weeks=12,
            followup_interval_days=14,
            western_disease="Hypertension",
        )

    def test_progress_calculation(self):
        self.assertEqual(self.treatment.planned_followup_count, 6)
        self.assertEqual(self.treatment.completed_followup_count, 0)
        self.assertEqual(str(self.treatment.next_followup_date), "2026-04-15")

    def test_admin_default_modify_window_days_is_365(self):
        self.admin_user.profile.refresh_from_db()
        self.assertEqual(self.admin_user.profile.modify_window_days, 365)

    def test_patient_list_default_view_renders(self):
        response = self.client.get(reverse("patient_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 6)
        self.assertIn("stats", response.context)

    def test_patient_list_defaults_to_next_followup_date_ascending(self):
        earlier_patient = Patient.objects.create(
            patient_id="P002",
            name="Patient B",
            gender="female",
            birth_date=date(1991, 5, 1),
        )
        Treatment.objects.create(
            patient=earlier_patient,
            group_name="Group B",
            treatment_name="Sooner Followup",
            start_date=date(2026, 3, 20),
            total_weeks=12,
            followup_interval_days=14,
        )

        response = self.client.get(reverse("patient_list"))
        rows = list(response.context["page_obj"].object_list)

        self.assertEqual(rows[0]["patient"].patient_id, "P002")
        self.assertEqual(rows[1]["patient"].patient_id, "P001")

    def test_followup_create_view(self):
        response = self.client.post(
            reverse("followup_create", args=[self.treatment.id]),
            {
                "visit_number": 1,
                "followup_date": "2026-04-15",
                "next_followup_in_days": 14,
                "planned_next_followup_date": "2026-04-29",
                "symptoms": "Improved",
                "medication_adherence": "Good",
                "adverse_events": "",
                "notes": "No special issues",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FollowUp.objects.count(), 1)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.completed_followup_count, 1)
        self.assertEqual(str(self.treatment.next_followup_date), "2026-04-29")

    def test_build_patient_context_avoids_direct_identifiers(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
            planned_next_followup_date="2026-04-29",
            symptoms="症状改善",
        )

        context = build_patient_context(self.patient)
        serialized = json.dumps(context, ensure_ascii=False)

        self.assertEqual(context["patient_id"], "P001")
        self.assertNotIn("Patient A", serialized)
        self.assertNotIn("13800000000", serialized)
        self.assertIn("latest_treatment", context)
        self.assertIn("recent_followups", context)

    def test_create_forms_default_dates_to_today(self):
        patient_create_response = self.client.get(reverse("patient_create"))
        followup_create_response = self.client.get(reverse("followup_create", args=[self.treatment.id]))

        today_value = timezone.localdate().isoformat()
        self.assertContains(patient_create_response, f'value="{today_value}"')
        self.assertContains(followup_create_response, f'value="{today_value}"')
        self.assertContains(followup_create_response, 'name="next_followup_in_days"')

    def test_patient_detail_page_renders(self):
        response = self.client.get(reverse("patient_detail", args=[self.patient.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Patient A")
        self.assertContains(response, "Standard Plan")
        self.assertContains(response, "智随")
        self.assertContains(response, "qwen-plus")

    @patch("followup.views.chat_with_patient")
    def test_patient_ai_chat_endpoint_returns_reply(self, mock_chat):
        mock_chat.return_value = ("这是 AI 回复。", {"patient_id": "P001"})

        response = self.client.post(
            reverse("patient_ai_chat", args=[self.patient.id]),
            data=json.dumps(
                {
                    "message": "请总结当前情况",
                    "history": [],
                    "include_basic": True,
                    "include_latest_treatment": True,
                    "include_recent_followups": True,
                    "include_full_history": False,
                    "model": "qwen-plus",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reply"], "这是 AI 回复。")
        self.assertEqual(payload["model"], "qwen-plus")
        args, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["model_name"], "qwen-plus")

    def test_patient_ai_chat_rejects_empty_message(self):
        response = self.client.post(
            reverse("patient_ai_chat", args=[self.patient.id]),
            data=json.dumps({"message": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    @patch("followup.views.chat_with_patient")
    def test_patient_ai_chat_surfaces_service_error(self, mock_chat):
        mock_chat.side_effect = AIServiceError("智随当前限流，请稍后再试。", status_code=429)

        response = self.client.post(
            reverse("patient_ai_chat", args=[self.patient.id]),
            data=json.dumps({"message": "请继续分析"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "智随当前限流，请稍后再试。")

    def test_patient_ai_chat_rejects_invalid_model(self):
        response = self.client.post(
            reverse("patient_ai_chat", args=[self.patient.id]),
            data=json.dumps({"message": "请继续分析", "model": "qwen-vl-max"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "所选模型不可用，请重新选择。")

    def test_patient_list_table_view_renders(self):
        response = self.client.get(reverse("patient_list"), {"view": "table", "q": "Patient"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 10)
        self.assertContains(response, "Treatment Group")

    def test_export_selected_patients(self):
        response = self.client.post(
            reverse("patient_export"),
            {"scope": "selected", "selected_ids": [self.patient.id], "current_query": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])

    def test_export_detail_zip_contains_three_aggregate_csvs(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
            symptoms="Improved",
        )
        response = self.client.post(
            reverse("patient_export_detail"),
            {"scope": "selected", "selected_ids": [self.patient.id], "current_query": "view=table"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")

        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertEqual(set(names), {"基本信息.csv", "诊疗记录.csv", "随访记录.csv"})

    def test_next_followup_number_uses_max_visit_number(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
        )
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=3,
            followup_date="2026-05-01",
        )
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.next_followup_number, 4)

    def test_status_label_shows_today_due(self):
        self.treatment.start_date = timezone.localdate() - date.resolution * 14
        self.treatment.save()
        self.assertEqual(self.treatment.status_label, "今日回访")

    def test_next_followup_date_follows_last_followup_plan(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
            planned_next_followup_date="2026-04-30",
        )
        self.treatment.refresh_from_db()
        self.assertEqual(str(self.treatment.next_followup_date), "2026-04-30")

    def test_export_detail_zip_writes_treatment_and_followup_sequence_in_csv(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=3,
            followup_date="2026-05-01",
            symptoms="Backfill",
        )
        response = self.client.post(
            reverse("patient_export_detail"),
            {"scope": "selected", "selected_ids": [self.patient.id], "current_query": "view=table"},
        )
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        followup_csv = archive.read("随访记录.csv").decode("utf-8-sig")
        self.assertIn("第01次诊疗", followup_csv)
        self.assertIn("第03次随访", followup_csv)

    def test_followup_duplicate_visit_number_is_rejected(self):
        FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
        )
        response = self.client.post(
            reverse("followup_create", args=[self.treatment.id]),
            {
                "visit_number": 1,
                "followup_date": "2026-04-20",
                "next_followup_in_days": 14,
                "symptoms": "Duplicate",
                "medication_adherence": "Good",
                "adverse_events": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "随访编号")

    def test_update_forms_render_existing_date_values(self):
        followup = FollowUp.objects.create(
            treatment=self.treatment,
            visit_number=1,
            followup_date="2026-04-15",
            planned_next_followup_date="2026-04-29",
        )

        patient_response = self.client.get(reverse("patient_update", args=[self.patient.id]))
        treatment_response = self.client.get(reverse("treatment_update", args=[self.treatment.id]))
        followup_response = self.client.get(reverse("followup_update", args=[followup.id]))

        self.assertContains(patient_response, 'value="1980-04-01"')
        self.assertContains(treatment_response, 'value="2026-04-01"')
        self.assertContains(followup_response, 'value="2026-04-15"')
        self.assertContains(followup_response, 'value="2026-04-29"')

    def test_manual_close_followup_hides_next_date(self):
        response = self.client.post(
            reverse("treatment_toggle_followup", args=[self.treatment.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.treatment.refresh_from_db()
        self.assertTrue(self.treatment.followup_closed)
        self.assertIsNone(self.treatment.next_followup_date)
        self.assertEqual(self.treatment.status_label, "已完成")

    def test_new_followup_reopens_closed_treatment(self):
        self.treatment.close_followup()

        response = self.client.post(
            reverse("followup_create", args=[self.treatment.id]),
            {
                "visit_number": 1,
                "followup_date": "2026-04-07",
                "next_followup_in_days": 30,
                "planned_next_followup_date": "2026-05-07",
                "symptoms": "Restarted followup",
                "medication_adherence": "Good",
                "adverse_events": "",
                "notes": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.treatment.refresh_from_db()
        self.assertFalse(self.treatment.followup_closed)
        self.assertEqual(str(self.treatment.next_followup_date), "2026-05-07")

    def test_auto_patient_id_generation(self):
        patient = Patient.objects.create(
            name="Generated Patient",
            gender="female",
            birth_date=date(1990, 1, 1),
        )
        self.assertTrue(patient.patient_id.startswith(timezone.localdate().strftime("%Y%m%d")))

    def test_can_add_second_treatment_for_same_patient(self):
        response = self.client.post(
            reverse("treatment_create", args=[self.patient.id]),
            {
                "group_name": "Observation Group",
                "treatment_name": "Second Treatment",
                "start_date": "2026-05-01",
                "total_weeks": 8,
                "followup_interval_days": 14,
                "chief_complaint": "Return visit",
                "present_illness": "Improving",
                "past_history": "",
                "personal_history": "",
                "marital_history": "",
                "allergy_history": "",
                "family_history": "",
                "tongue_diagnosis": "",
                "pulse_diagnosis": "",
                "tcm_disease": "Insomnia",
                "western_disease": "Hypertension",
                "treatment_principle": "Balance",
                "prescription": "Custom prescription",
                "notes": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.patient.treatments.count(), 2)

    def test_login_required_redirects_anonymous_user(self):
        self.client.logout()
        response = self.client.get(reverse("patient_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_page_renders_clean_text(self):
        self.client.logout()
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "临床科研随访管理系统")
        self.assertContains(response, "智能随访助手")
        self.assertNotContains(response, "北京中医药大学")
        self.assertNotContains(response, "zjingqi@bucm.edu.cn")

    def test_normal_user_cannot_export(self):
        self.client.logout()
        normal_user = User.objects.create_user(username="normal", password="Normal@123456")
        UserProfile.objects.create(user=normal_user, role=UserProfile.ROLE_NORMAL)
        self.client.force_login(normal_user)

        response = self.client.post(
            reverse("patient_export"),
            {"scope": "selected", "selected_ids": [self.patient.id], "current_query": ""},
            follow=True,
        )
        self.assertContains(response, "不能导出数据")

    def test_normal_user_can_view_table_but_not_export(self):
        self.client.logout()
        normal_user = User.objects.create_user(username="normal_view", password="Normal@123456")
        UserProfile.objects.create(user=normal_user, role=UserProfile.ROLE_NORMAL)
        self.client.force_login(normal_user)

        response = self.client.get(reverse("patient_list"), {"view": "table"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Patient A")
        self.assertNotContains(response, "导出勾选汇总 CSV")
        self.assertContains(response, "普通账号可查看数据，但不能导出数据。")

    def test_normal_user_cannot_modify_old_records(self):
        self.patient.created_at = timezone.now() - timedelta(days=8)
        self.patient.save(update_fields=["created_at"])

        self.client.logout()
        normal_user = User.objects.create_user(username="normal2", password="Normal@123456")
        UserProfile.objects.create(user=normal_user, role=UserProfile.ROLE_NORMAL)
        self.client.force_login(normal_user)

        response = self.client.get(reverse("patient_update", args=[self.patient.id]), follow=True)
        self.assertContains(response, "近 3 天内创建的数据")

    def test_admin_cannot_modify_records_older_than_configured_window(self):
        self.patient.created_at = timezone.now() - timedelta(days=366)
        self.patient.save(update_fields=["created_at"])

        response = self.client.get(reverse("patient_update", args=[self.patient.id]), follow=True)
        self.assertContains(response, "近 365 天内创建的数据")

    def test_root_can_open_account_management(self):
        self.client.logout()
        root_user = User.objects.create_user(username="root", password="RootPass@123456")
        UserProfile.objects.create(user=root_user, role=UserProfile.ROLE_ROOT)
        self.client.force_login(root_user)

        response = self.client.get(reverse("account_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "账号管理")
        self.assertContains(response, "可修改/删除天数")

    def test_admin_cannot_open_account_management(self):
        response = self.client.get(reverse("account_list"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "只有 Root 可以管理账号")

    def test_root_can_update_account(self):
        self.client.logout()
        root_user = User.objects.create_user(username="root", password="RootPass@123456")
        UserProfile.objects.create(user=root_user, role=UserProfile.ROLE_ROOT)
        target_user = User.objects.create_user(username="staff1", password="Staff@123456")
        UserProfile.objects.create(
            user=target_user,
            role=UserProfile.ROLE_NORMAL,
            modify_window_days=3,
        )
        self.client.force_login(root_user)

        response = self.client.post(
            reverse("account_update", args=[target_user.pk]),
            {
                "username": "staff1",
                "first_name": "新姓名",
                "role": UserProfile.ROLE_ADMIN,
                "modify_window_days": 120,
                "is_active": "on",
                "new_password1": "Changed@123456",
                "new_password2": "Changed@123456",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target_user.refresh_from_db()
        target_user.profile.refresh_from_db()
        self.assertEqual(target_user.first_name, "新姓名")
        self.assertEqual(target_user.profile.role, UserProfile.ROLE_ADMIN)
        self.assertEqual(target_user.profile.modify_window_days, 120)
        self.assertTrue(target_user.check_password("Changed@123456"))

    def test_root_can_delete_account(self):
        self.client.logout()
        root_user = User.objects.create_user(username="root", password="RootPass@123456")
        UserProfile.objects.create(user=root_user, role=UserProfile.ROLE_ROOT)
        target_user = User.objects.create_user(username="staff2", password="Staff@123456")
        UserProfile.objects.create(user=target_user, role=UserProfile.ROLE_NORMAL)
        self.client.force_login(root_user)

        response = self.client.post(reverse("account_delete", args=[target_user.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="staff2").exists())

    def test_root_can_open_account_update_for_user_without_profile(self):
        self.client.logout()
        root_user = User.objects.create_user(username="root", password="RootPass@123456")
        UserProfile.objects.create(user=root_user, role=UserProfile.ROLE_ROOT)
        target_user = User.objects.create_user(username="staff3", password="Staff@123456")
        self.client.force_login(root_user)

        response = self.client.get(reverse("account_update", args=[target_user.pk]))

        self.assertEqual(response.status_code, 200)
        target_user.refresh_from_db()
        self.assertTrue(hasattr(target_user, "profile"))
        self.assertEqual(target_user.profile.role, UserProfile.ROLE_NORMAL)

    def test_root_cannot_delete_self_from_ui(self):
        self.client.logout()
        root_user = User.objects.create_user(username="root", password="RootPass@123456")
        UserProfile.objects.create(user=root_user, role=UserProfile.ROLE_ROOT)
        self.client.force_login(root_user)

        response = self.client.post(reverse("account_delete", args=[root_user.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前账号不能在页面中删除")
        self.assertTrue(User.objects.filter(username="root").exists())
