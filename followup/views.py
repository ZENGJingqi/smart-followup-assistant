import csv
import io
import json
import zipfile
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .ai import AIServiceError, chat_with_patient
from .forms import (
    AccountCreateForm,
    AccountUpdateForm,
    FollowUpForm,
    LoginForm,
    PatientFilterForm,
    PatientForm,
    TreatmentForm,
)
from .models import FollowUp, Patient, Treatment, UserProfile
from .permissions import (
    can_export_data,
    can_manage_accounts,
    can_modify_record,
    get_modify_window_days,
    get_user_profile,
    get_user_role,
    get_user_role_label,
)


STATUS_NOT_STARTED = "未开始"
STATUS_ACTIVE = "随访中"
STATUS_DONE = "已完成"
STATUS_OVERDUE = "已逾期"
STATUS_TODAY = "今日回访"


def _redirect_with_error(request, message, target, *args):
    messages.error(request, message)
    return redirect(target, *args)


def _json_error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _ensure_export_permission(request):
    if can_export_data(request.user):
        return None
    return _redirect_with_error(request, "普通账号不能导出数据。", "patient_list")


def _ensure_modify_permission(request, obj, target, *args):
    if can_modify_record(request.user, obj):
        return None
    modify_window_days = get_modify_window_days(request.user)
    limit_text = f"近 {modify_window_days} 天" if modify_window_days else "当前权限范围"
    return _redirect_with_error(
        request,
        f"当前账号只能修改或删除{limit_text}内创建的数据。",
        target,
        *args,
    )


def _account_rows(current_user):
    rows = []
    for user in User.objects.select_related("profile").order_by("date_joined", "username"):
        profile = get_user_profile(user)
        role = get_user_role(user)
        rows.append(
            {
                "user": user,
                "role": role,
                "role_label": get_user_role_label(user) or "普通",
                "modify_window_days": (
                    "不限制"
                    if role == UserProfile.ROLE_ROOT
                    else f"{(profile.effective_modify_window_days if profile else 3)} 天"
                ),
                "can_manage": role != UserProfile.ROLE_ROOT,
                "can_delete": role != UserProfile.ROLE_ROOT and user.pk != current_user.pk,
            }
        )
    return rows


def login_view(request):
    if request.user.is_authenticated:
        return redirect("patient_list")

    form = LoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("patient_list")

    return render(
        request,
        "followup/login.html",
        {"form": form, "next_url": request.GET.get("next", "")},
    )


@login_required
def logout_view(request):
    if request.method == "POST":
        logout(request)
    return redirect("login")


@login_required
def account_list(request):
    if not can_manage_accounts(request.user):
        return _redirect_with_error(request, "只有 Root 可以管理账号。", "patient_list")
    return render(
        request,
        "followup/account_list.html",
        {"page_title": "账号管理", "account_rows": _account_rows(request.user)},
    )


@login_required
def account_create(request):
    if not can_manage_accounts(request.user):
        return _redirect_with_error(request, "只有 Root 可以创建账号。", "patient_list")

    form = AccountCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"账号 {user.username} 已创建。")
        return redirect("account_list")

    return render(
        request,
        "followup/account_form.html",
        {
            "page_title": "新建账号",
            "form": form,
            "submit_label": "创建账号",
            "cancel_url": reverse("account_list"),
        },
    )


@login_required
def account_update(request, pk):
    if not can_manage_accounts(request.user):
        return _redirect_with_error(request, "只有 Root 可以编辑账号。", "patient_list")

    target_user = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    profile = get_user_profile(target_user)
    if profile.role == UserProfile.ROLE_ROOT:
        return _redirect_with_error(request, "Root 账号不能在页面中编辑。", "account_list")

    form = AccountUpdateForm(request.POST or None, instance=target_user, profile=profile)
    if request.method == "POST" and form.is_valid():
        updated_user = form.save()
        messages.success(request, f"账号 {updated_user.username} 已更新。")
        return redirect("account_list")

    return render(
        request,
        "followup/account_form.html",
        {
            "page_title": "编辑账号",
            "form": form,
            "submit_label": "保存修改",
            "cancel_url": reverse("account_list"),
        },
    )


@login_required
def account_delete(request, pk):
    if not can_manage_accounts(request.user):
        return _redirect_with_error(request, "只有 Root 可以删除账号。", "patient_list")

    target_user = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    profile = get_user_profile(target_user)
    if profile.role == UserProfile.ROLE_ROOT or target_user.pk == request.user.pk:
        return _redirect_with_error(request, "当前账号不能在页面中删除。", "account_list")

    if request.method == "POST":
        username = target_user.username
        target_user.delete()
        messages.success(request, f"账号 {username} 已删除。")
        return redirect("account_list")

    return render(
        request,
        "followup/confirm_delete.html",
        {
            "app_name": settings.APP_NAME,
            "title": "删除账号",
            "description": f"将删除账号“{target_user.username}”，此操作不可恢复。",
            "cancel_url": reverse("account_list"),
        },
    )


def _build_patient_rows():
    patients = (
        Patient.objects.prefetch_related("treatments__followups")
        .annotate(
            treatment_count_value=Count("treatments", distinct=True),
            followup_count_value=Count("treatments__followups", distinct=True),
        )
        .all()
    )
    rows = []
    for patient in patients:
        treatment = patient.latest_treatment
        rows.append(
            {
                "patient": patient,
                "treatment": treatment,
                "group_name": treatment.display_group_name if treatment else patient.group_name,
                "western_disease": (
                    treatment.display_western_disease if treatment else patient.diagnosis
                ),
                "status": treatment.status_label if treatment else STATUS_NOT_STARTED,
                "start_date": treatment.start_date if treatment else None,
                "next_followup_date": treatment.next_followup_date if treatment else None,
                "progress_percent": treatment.progress_percent if treatment else 0,
                "completed_count": treatment.completed_followup_count if treatment else 0,
                "planned_count": treatment.planned_followup_count if treatment else 0,
                "treatment_count": getattr(patient, "treatment_count_value", 0),
                "followup_count": getattr(patient, "followup_count_value", 0),
            }
        )
    return rows


def _build_dashboard_stats(rows):
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    today_due = sum(1 for row in rows if row["next_followup_date"] == today)
    week_due = sum(
        1
        for row in rows
        if row["next_followup_date"] and today < row["next_followup_date"] <= week_end
    )
    return {
        "patient_count": len(rows),
        "treatment_count": sum(row["treatment_count"] for row in rows),
        "followup_count": sum(row["followup_count"] for row in rows),
        "today_due": today_due,
        "week_due": week_due,
    }


def _matches_filters(row, cleaned):
    patient = row["patient"]
    treatment = row["treatment"]

    keyword = (cleaned.get("q") or "").strip().lower()
    if keyword:
        haystacks = [
            patient.patient_id,
            patient.name,
            patient.phone,
            patient.ethnicity,
            patient.address,
            row["group_name"],
            row["western_disease"],
            treatment.tcm_disease if treatment else "",
            treatment.treatment_name if treatment else "",
            treatment.chief_complaint if treatment else "",
        ]
        if not any(keyword in (item or "").lower() for item in haystacks):
            return False

    group_name = (cleaned.get("group_name") or "").strip().lower()
    if group_name and group_name not in (row["group_name"] or "").lower():
        return False

    status = cleaned.get("status")
    if status == "today" and row["status"] != STATUS_TODAY:
        return False
    if status == "active" and row["status"] != STATUS_ACTIVE:
        return False
    if status == "done" and row["status"] != STATUS_DONE:
        return False
    if status == "overdue" and row["status"] != STATUS_OVERDUE:
        return False

    start_date_from = cleaned.get("start_date_from")
    if start_date_from and (not row["start_date"] or row["start_date"] < start_date_from):
        return False

    start_date_to = cleaned.get("start_date_to")
    if start_date_to and (not row["start_date"] or row["start_date"] > start_date_to):
        return False

    return True


def _sort_rows(rows, ordering):
    status_order = {
        STATUS_OVERDUE: 0,
        STATUS_TODAY: 1,
        STATUS_ACTIVE: 2,
        STATUS_DONE: 3,
        STATUS_NOT_STARTED: 4,
    }
    sort_map = {
        "patient_id": lambda item: item["patient"].patient_id or "",
        "-patient_id": lambda item: item["patient"].patient_id or "",
        "name": lambda item: item["patient"].name or "",
        "-name": lambda item: item["patient"].name or "",
        "age": lambda item: item["patient"].current_age or -1,
        "-age": lambda item: item["patient"].current_age or -1,
        "ethnicity": lambda item: item["patient"].ethnicity or "",
        "-ethnicity": lambda item: item["patient"].ethnicity or "",
        "group_name": lambda item: item["group_name"] or "",
        "-group_name": lambda item: item["group_name"] or "",
        "western_disease": lambda item: item["western_disease"] or "",
        "-western_disease": lambda item: item["western_disease"] or "",
        "treatment_count": lambda item: item["treatment_count"],
        "-treatment_count": lambda item: item["treatment_count"],
        "followup_count": lambda item: item["followup_count"],
        "-followup_count": lambda item: item["followup_count"],
        "start_date": lambda item: item["start_date"] or date.max,
        "-start_date": lambda item: item["start_date"] or date.min,
        "next_followup_date": lambda item: item["next_followup_date"] or date.max,
        "-next_followup_date": lambda item: item["next_followup_date"] or date.min,
        "status": lambda item: status_order.get(item["status"], 99),
        "-status": lambda item: status_order.get(item["status"], 99),
    }
    key = sort_map.get(ordering, sort_map["next_followup_date"])
    reverse = ordering.startswith("-")
    return sorted(rows, key=key, reverse=reverse)


def _get_filtered_rows(data):
    form = PatientFilterForm(data or None)
    form.is_valid()
    cleaned = getattr(form, "cleaned_data", {})
    all_rows = _build_patient_rows()
    rows = [row for row in all_rows if _matches_filters(row, cleaned)]
    ordering = (data or {}).get("ordering") or "next_followup_date"
    allowed_orderings = {
        "patient_id",
        "-patient_id",
        "name",
        "-name",
        "age",
        "-age",
        "ethnicity",
        "-ethnicity",
        "group_name",
        "-group_name",
        "western_disease",
        "-western_disease",
        "treatment_count",
        "-treatment_count",
        "followup_count",
        "-followup_count",
        "start_date",
        "-start_date",
        "next_followup_date",
        "-next_followup_date",
        "status",
        "-status",
    }
    if ordering not in allowed_orderings:
        ordering = "next_followup_date"
    rows = _sort_rows(rows, ordering)
    return form, rows, cleaned, all_rows


def _encode_query(request, **updates):
    query = request.GET.copy()
    for key, value in updates.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


def _get_export_rows(request):
    raw_query = request.POST.get("current_query", "")
    query_data = QueryDict(raw_query, mutable=False)
    _, rows, _, _ = _get_filtered_rows(query_data)
    selected_ids = {int(item) for item in request.POST.getlist("selected_ids") if item.isdigit()}
    scope = request.POST.get("scope", "selected")
    if scope == "selected":
        if not selected_ids:
            return []
        rows = [row for row in rows if row["patient"].id in selected_ids]
    return rows


def _csv_bytes(headers, values):
    text_stream = io.StringIO()
    writer = csv.writer(text_stream)
    writer.writerow(headers)
    for row in values:
        writer.writerow(row)
    return text_stream.getvalue().encode("utf-8-sig")


def _ordered_treatments(patient):
    return sorted(
        patient.treatments.all(),
        key=lambda item: (item.start_date or date.min, item.created_at),
    )


def _build_detail_export_tables(rows):
    basic_rows = []
    treatment_rows = []
    followup_rows = []

    patients = (
        Patient.objects.filter(pk__in=[row["patient"].pk for row in rows])
        .prefetch_related("treatments__followups")
        .order_by("patient_id", "created_at")
    )

    for patient in patients:
        basic_rows.append(
            [
                patient.patient_id,
                patient.name,
                patient.get_gender_display(),
                patient.birth_date or "",
                patient.current_age or "",
                patient.ethnicity,
                patient.phone,
                patient.address,
            ]
        )

        treatments = _ordered_treatments(patient)
        total_treatments = len(treatments)
        for treatment_index, treatment in enumerate(treatments, start=1):
            treatment_label = f"第{treatment_index:02d}次诊疗"
            treatment_rows.append(
                [
                    patient.patient_id,
                    patient.name,
                    treatment_label,
                    total_treatments,
                    treatment.treatment_name,
                    treatment.display_group_name,
                    treatment.start_date or "",
                    treatment.total_weeks,
                    treatment.followup_interval_days,
                    treatment.status_label,
                    treatment.followup_closed_at or "",
                    treatment.completed_followup_count,
                    treatment.planned_followup_count,
                    treatment.next_followup_date or "",
                    treatment.chief_complaint,
                    treatment.present_illness,
                    treatment.past_history,
                    treatment.personal_history,
                    treatment.marital_history,
                    treatment.allergy_history,
                    treatment.family_history,
                    treatment.tongue_diagnosis,
                    treatment.pulse_diagnosis,
                    treatment.tcm_disease,
                    treatment.display_western_disease,
                    treatment.treatment_principle,
                    treatment.prescription,
                    treatment.notes,
                ]
            )

            followups = list(treatment.followups.all())
            total_followups = len(followups)
            for followup in followups:
                followup_rows.append(
                    [
                        patient.patient_id,
                        patient.name,
                        treatment_label,
                        total_treatments,
                        f"第{followup.visit_number:02d}次随访",
                        total_followups,
                        treatment.treatment_name,
                        followup.followup_date or "",
                        followup.planned_next_followup_date or "",
                        followup.symptoms,
                        followup.medication_adherence,
                        followup.adverse_events,
                        followup.notes,
                    ]
                )

    return {
        "基本信息.csv": _csv_bytes(
            ["患者编号", "姓名", "性别", "出生日期", "当前年龄", "民族", "电话", "住址"],
            basic_rows,
        ),
        "诊疗记录.csv": _csv_bytes(
            [
                "患者编号",
                "姓名",
                "诊疗序号",
                "诊疗总次数",
                "治疗方案",
                "分组",
                "治疗开始日期",
                "总随访周数",
                "随访间隔（天）",
                "当前状态",
                "结束回访日期",
                "已完成随访",
                "计划随访",
                "下次随访日期",
                "主诉",
                "现病史",
                "既往史",
                "个人史",
                "婚育史",
                "过敏史",
                "家族史",
                "舌诊",
                "脉诊",
                "中医疾病",
                "西医疾病",
                "治则治法",
                "处方",
                "备注",
            ],
            treatment_rows,
        ),
        "随访记录.csv": _csv_bytes(
            [
                "患者编号",
                "姓名",
                "诊疗序号",
                "诊疗总次数",
                "随访序号",
                "该诊疗下随访总次数",
                "治疗方案",
                "随访日期",
                "下次建议随访日期",
                "症状变化",
                "用药依从性",
                "不良反应",
                "备注",
            ],
            followup_rows,
        ),
    }


@login_required
def patient_list(request):
    filter_form, rows, cleaned, all_rows = _get_filtered_rows(request.GET)
    for row in rows:
        row["can_edit"] = can_modify_record(request.user, row["patient"])
    stats = _build_dashboard_stats(all_rows)
    view_mode = cleaned.get("view") or "card"
    paginator = Paginator(rows, 6 if view_mode == "card" else 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    ordering = request.GET.get("ordering") or "next_followup_date"

    def next_order(field_name):
        return f"-{field_name}" if ordering == field_name else field_name

    return render(
        request,
        "followup/patient_list.html",
        {
            "app_name": settings.APP_NAME,
            "filter_form": filter_form,
            "page_obj": page_obj,
            "view_mode": view_mode,
            "stats": stats,
            "current_query": request.GET.urlencode(),
            "toggle_card_query": _encode_query(request, view="card", page=None),
            "toggle_table_query": _encode_query(request, view="table", page=None),
            "prev_page_query": _encode_query(
                request,
                page=page_obj.previous_page_number() if page_obj.has_previous() else None,
            ),
            "next_page_query": _encode_query(
                request,
                page=page_obj.next_page_number() if page_obj.has_next() else None,
            ),
            "sort_queries": {
                "patient_id": _encode_query(request, ordering=next_order("patient_id"), page=None),
                "name": _encode_query(request, ordering=next_order("name"), page=None),
                "age": _encode_query(request, ordering=next_order("age"), page=None),
                "ethnicity": _encode_query(request, ordering=next_order("ethnicity"), page=None),
                "group_name": _encode_query(request, ordering=next_order("group_name"), page=None),
                "western_disease": _encode_query(
                    request, ordering=next_order("western_disease"), page=None
                ),
                "treatment_count": _encode_query(
                    request, ordering=next_order("treatment_count"), page=None
                ),
                "followup_count": _encode_query(
                    request, ordering=next_order("followup_count"), page=None
                ),
                "start_date": _encode_query(request, ordering=next_order("start_date"), page=None),
                "next_followup_date": _encode_query(
                    request, ordering=next_order("next_followup_date"), page=None
                ),
                "status": _encode_query(request, ordering=next_order("status"), page=None),
            },
        },
    )


@login_required
def patient_create(request):
    if request.method == "POST":
        patient_form = PatientForm(request.POST)
        treatment_form = TreatmentForm(request.POST)
        if patient_form.is_valid() and treatment_form.is_valid():
            patient = patient_form.save()
            treatment = treatment_form.save(commit=False)
            treatment.patient = patient
            treatment.save()
            messages.success(request, f"患者已创建，编号为 {patient.patient_id}。")
            return redirect("patient_detail", pk=patient.pk)
    else:
        patient_form = PatientForm()
        treatment_form = TreatmentForm()
    return render(
        request,
        "followup/patient_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "新建患者",
            "submit_label": "保存",
            "patient_form": patient_form,
            "treatment_form": treatment_form,
            "preview_patient_id": Patient.generate_patient_id(),
        },
    )


@login_required
def patient_update(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    denied = _ensure_modify_permission(request, patient, "patient_detail", patient.pk)
    if denied:
        return denied
    if request.method == "POST":
        patient_form = PatientForm(request.POST, instance=patient)
        if patient_form.is_valid():
            patient_form.save()
            messages.success(request, "患者主档案已更新。")
            return redirect("patient_detail", pk=patient.pk)
    else:
        patient_form = PatientForm(instance=patient)
    return render(
        request,
        "followup/patient_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "编辑患者",
            "submit_label": "更新",
            "patient_form": patient_form,
            "patient": patient,
        },
    )


@login_required
def patient_delete(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    denied = _ensure_modify_permission(request, patient, "patient_detail", patient.pk)
    if denied:
        return denied
    if request.method == "POST":
        patient.delete()
        messages.success(request, "患者及其相关诊疗、随访记录已删除。")
        return redirect("patient_list")
    return render(
        request,
        "followup/confirm_delete.html",
        {
            "app_name": settings.APP_NAME,
            "title": "删除患者",
            "description": f"将删除患者“{patient.name}”及其全部诊疗和随访记录，此操作不可恢复。",
            "cancel_url": reverse("patient_detail", args=[patient.pk]),
        },
    )


@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(
        Patient.objects.prefetch_related("treatments__followups"),
        pk=pk,
    )
    ordered_treatments = _ordered_treatments(patient)
    latest_pk = ordered_treatments[-1].pk if ordered_treatments else None
    treatment_rows = []
    for sequence, treatment in enumerate(ordered_treatments, start=1):
        treatment_rows.append(
            {
                "treatment": treatment,
                "followups": [
                    {
                        "object": item,
                        "can_modify": can_modify_record(request.user, item),
                    }
                    for item in treatment.followups.all()
                ],
                "next_visit_number": treatment.next_followup_number,
                "sequence": sequence,
                "is_latest": treatment.pk == latest_pk,
                "can_modify": can_modify_record(request.user, treatment),
            }
        )
    treatment_rows.reverse()
    return render(
        request,
        "followup/patient_detail.html",
        {
            "patient": patient,
            "treatment_rows": treatment_rows,
            "can_modify_patient": can_modify_record(request.user, patient),
            "ai_options": {
                "include_basic": True,
                "include_latest_treatment": True,
                "include_recent_followups": True,
                "include_full_history": False,
            },
            "ai_model_choices": settings.AI_TEXT_MODEL_CHOICES,
            "ai_default_model": settings.AI_MODEL,
        },
    )


@login_required
def patient_ai_chat(request, pk):
    if request.method != "POST":
        return _json_error("仅支持 POST 请求。", status=405)

    patient = get_object_or_404(Patient, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return _json_error("请求内容不是有效的 JSON。")

    message = (payload.get("message") or "").strip()
    if not message:
        return _json_error("请输入问题后再发送。")

    options = {
        "include_basic": bool(payload.get("include_basic", True)),
        "include_latest_treatment": bool(payload.get("include_latest_treatment", True)),
        "include_recent_followups": bool(payload.get("include_recent_followups", True)),
        "include_full_history": bool(payload.get("include_full_history", False)),
    }
    allowed_models = set(settings.AI_TEXT_MODEL_CHOICES)
    model_name = (payload.get("model") or settings.AI_MODEL).strip()
    if model_name not in allowed_models:
        return _json_error("所选模型不可用，请重新选择。", status=400)

    try:
        reply, context = chat_with_patient(
            patient,
            message,
            history=payload.get("history") or [],
            options=options,
            model_name=model_name,
        )
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    except AIServiceError as exc:
        return _json_error(str(exc), status=exc.status_code)
    except Exception:
        return _json_error("智随暂时不可用，请稍后重试。", status=502)

    return JsonResponse({"ok": True, "reply": reply, "context": context, "model": model_name})


@login_required
def treatment_create(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    if request.method == "POST":
        form = TreatmentForm(request.POST)
        if form.is_valid():
            treatment = form.save(commit=False)
            treatment.patient = patient
            treatment.save()
            messages.success(request, "新的诊疗记录已创建。")
            return redirect("patient_detail", pk=patient.pk)
    else:
        form = TreatmentForm()
    return render(
        request,
        "followup/treatment_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "新增诊疗",
            "submit_label": "保存诊疗",
            "patient": patient,
            "form": form,
        },
    )


@login_required
def treatment_update(request, pk):
    treatment = get_object_or_404(Treatment.objects.select_related("patient"), pk=pk)
    denied = _ensure_modify_permission(request, treatment, "patient_detail", treatment.patient.pk)
    if denied:
        return denied
    if request.method == "POST":
        form = TreatmentForm(request.POST, instance=treatment)
        if form.is_valid():
            form.save()
            messages.success(request, "诊疗记录已更新。")
            return redirect("patient_detail", pk=treatment.patient.pk)
    else:
        form = TreatmentForm(instance=treatment)
    return render(
        request,
        "followup/treatment_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "编辑诊疗",
            "submit_label": "更新诊疗",
            "patient": treatment.patient,
            "form": form,
        },
    )


@login_required
def treatment_delete(request, pk):
    treatment = get_object_or_404(Treatment.objects.select_related("patient"), pk=pk)
    denied = _ensure_modify_permission(request, treatment, "patient_detail", treatment.patient.pk)
    if denied:
        return denied
    if request.method == "POST":
        patient_pk = treatment.patient.pk
        treatment.delete()
        messages.success(request, "诊疗记录及其随访已删除。")
        return redirect("patient_detail", pk=patient_pk)
    return render(
        request,
        "followup/confirm_delete.html",
        {
            "app_name": settings.APP_NAME,
            "title": "删除诊疗",
            "description": f"将删除“{treatment.treatment_name}”及其全部随访记录，此操作不可恢复。",
            "cancel_url": reverse("patient_detail", args=[treatment.patient.pk]),
        },
    )


@login_required
def treatment_toggle_followup(request, pk):
    treatment = get_object_or_404(Treatment.objects.select_related("patient"), pk=pk)
    denied = _ensure_modify_permission(request, treatment, "patient_detail", treatment.patient.pk)
    if denied:
        return denied
    if request.method != "POST":
        return redirect("patient_detail", pk=treatment.patient.pk)

    if treatment.followup_closed:
        treatment.reopen_followup()
        messages.success(request, "该诊疗已重新启动回访。")
    else:
        treatment.close_followup()
        messages.success(request, "该诊疗已结束回访，后续计划随访将不再提醒。")
    return redirect("patient_detail", pk=treatment.patient.pk)


@login_required
def followup_create(request, treatment_id):
    treatment = get_object_or_404(Treatment, pk=treatment_id)
    initial = {"visit_number": treatment.next_followup_number}
    scheduled_next_followup_date = treatment.next_followup_date
    if request.method == "POST":
        form = FollowUpForm(request.POST, initial=initial, treatment=treatment)
        if form.is_valid():
            followup = form.save(commit=False)
            followup.treatment = treatment
            followup.save()
            if treatment.followup_closed:
                treatment.reopen_followup()
            messages.success(request, "随访记录已保存。")
            return redirect("patient_detail", pk=treatment.patient.pk)
    else:
        form = FollowUpForm(initial=initial, treatment=treatment)
    return render(
        request,
        "followup/followup_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "录入随访",
            "submit_label": "保存随访",
            "form": form,
            "treatment": treatment,
            "followup_interval_days": treatment.followup_interval_days,
            "scheduled_next_followup_date": scheduled_next_followup_date,
        },
    )


@login_required
def followup_update(request, pk):
    followup = get_object_or_404(FollowUp.objects.select_related("treatment__patient"), pk=pk)
    denied = _ensure_modify_permission(
        request,
        followup,
        "patient_detail",
        followup.treatment.patient.pk,
    )
    if denied:
        return denied
    if request.method == "POST":
        form = FollowUpForm(request.POST, instance=followup, treatment=followup.treatment)
        if form.is_valid():
            form.save()
            messages.success(request, "随访记录已更新。")
            return redirect("patient_detail", pk=followup.treatment.patient.pk)
    else:
        form = FollowUpForm(instance=followup, treatment=followup.treatment)
    return render(
        request,
        "followup/followup_form.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "编辑随访",
            "submit_label": "更新随访",
            "form": form,
            "treatment": followup.treatment,
            "followup_interval_days": followup.treatment.followup_interval_days,
            "scheduled_next_followup_date": followup.treatment.next_followup_date,
        },
    )


@login_required
def followup_delete(request, pk):
    followup = get_object_or_404(FollowUp.objects.select_related("treatment__patient"), pk=pk)
    denied = _ensure_modify_permission(
        request,
        followup,
        "patient_detail",
        followup.treatment.patient.pk,
    )
    if denied:
        return denied
    if request.method == "POST":
        patient_pk = followup.treatment.patient.pk
        followup.delete()
        messages.success(request, "随访记录已删除。")
        return redirect("patient_detail", pk=patient_pk)
    return render(
        request,
        "followup/confirm_delete.html",
        {
            "app_name": settings.APP_NAME,
            "title": "删除随访",
            "description": f"将删除第 {followup.visit_number} 次随访记录，此操作不可恢复。",
            "cancel_url": reverse("patient_detail", args=[followup.treatment.patient.pk]),
        },
    )


@login_required
def patient_export(request):
    denied = _ensure_export_permission(request)
    if denied:
        return denied
    rows = _get_export_rows(request)

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="patients_export.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "患者编号",
            "姓名",
            "性别",
            "出生日期",
            "当前年龄",
            "民族",
            "电话",
            "住址",
            "最新分组",
            "西医疾病",
            "治疗方案",
            "治疗开始日期",
            "当前状态",
            "已完成随访",
            "计划随访",
            "下次随访日期",
        ]
    )
    for row in rows:
        patient = row["patient"]
        treatment = row["treatment"]
        writer.writerow(
            [
                patient.patient_id,
                patient.name,
                patient.get_gender_display(),
                patient.birth_date or "",
                patient.current_age or "",
                patient.ethnicity,
                patient.phone,
                patient.address,
                row["group_name"],
                row["western_disease"],
                treatment.treatment_name if treatment else "",
                row["start_date"] or "",
                row["status"],
                row["completed_count"],
                row["planned_count"],
                row["next_followup_date"] or "",
            ]
        )
    return response


@login_required
def patient_export_detail(request):
    denied = _ensure_export_permission(request)
    if denied:
        return denied
    rows = _get_export_rows(request)
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for file_name, content in _build_detail_export_tables(rows).items():
            zip_file.writestr(file_name, content)

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="patients_detail_export.zip"'
    return response
