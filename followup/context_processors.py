from django.conf import settings

from .permissions import can_export_data, can_manage_accounts, get_user_role_label


def app_context(request):
    return {
        "app_name": settings.APP_NAME,
        "app_subtitle": settings.APP_SUBTITLE,
        "app_copyright": settings.APP_COPYRIGHT,
        "app_notice": settings.APP_NOTICE,
        "support_email": settings.APP_SUPPORT_EMAIL,
        "current_user_role": get_user_role_label(request.user),
        "can_export_data": can_export_data(request.user),
        "can_manage_accounts": can_manage_accounts(request.user),
    }
