import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from inspection.models import Inspection, PreviewToken
from inspection.rules import judge

FORBIDDEN_MSG = "仅巡检员可登记灯光巡检"


def _can_write(user) -> bool:
    return user.groups.filter(name="inspector").exists()


def _parse_payload(post):
    """从表单里取出四项登记内容，缺项或非数值返回 None。"""
    try:
        measured = float(post["measured_cd"])
        required = float(post["required_cd"])
        bearing = float(post["bearing_error_deg"])
        code = post["aid_code"].strip()
        if not code:
            raise ValueError("empty")
    except (KeyError, ValueError):
        return None
    return {
        "aid_code": code,
        "measured_cd": measured,
        "required_cd": required,
        "bearing_error_deg": bearing,
    }


def health(_request):
    return JsonResponse({"status": "ok", "service": "nav-aid-inspection"})


@require_http_methods(["GET", "POST"])
def login_view(request):
    from django.contrib.auth import authenticate, login

    error = ""
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username", "").strip(),
            password=request.POST.get("password", ""),
        )
        if user is None:
            error = "用户名或密码错误"
        else:
            login(request, user)
            return redirect("list")
    return render(request, "login.html", {"error": error})


def logout_view(request):
    from django.contrib.auth import logout

    logout(request)
    return redirect("login")


@login_required
def list_view(request):
    rows = Inspection.objects.all()
    return render(request, "list.html", {"rows": rows, "can_write": _can_write(request.user)})


@login_required
def detail_view(request, pk):
    row = get_object_or_404(Inspection, pk=pk)
    return render(request, "detail.html", {"row": row})


@login_required
@require_POST
def preview_view(request):
    """判定预览：给出即将写入的结论和说明，并下发一张短时预览凭证。"""
    if not _can_write(request.user):
        return HttpResponseForbidden(FORBIDDEN_MSG)
    payload = _parse_payload(request.POST)
    if payload is None:
        return JsonResponse({"error": "请填编号和三项数值"}, status=400)
    verdict, note = judge(
        payload["measured_cd"], payload["required_cd"], payload["bearing_error_deg"]
    )
    ttl = settings.PREVIEW_TOKEN_TTL_SECONDS
    token = PreviewToken.objects.create(
        code=secrets.token_hex(8),
        verdict=verdict,
        note=note,
        created_by=request.user.username,
        expires_at=timezone.now() + timedelta(seconds=ttl),
        **payload,
    )
    return JsonResponse(
        {
            "token": token.code,
            "verdict": verdict,
            "note": note,
            "expires_in": ttl,
            "expires_at": token.expires_at.isoformat(),
            **payload,
        }
    )


def _write_with_token(request, payload):
    """校验凭证与确认勾选后落库，返回 (row, error)，二者必居其一。"""
    code = request.POST.get("preview_token", "").strip()
    if not code:
        return None, "请先判定预览，拿到预览凭证后再写入"
    if request.POST.get("confirm") != "on":
        return None, "请勾选确认后再写入"
    now = timezone.now()
    with transaction.atomic():
        token = PreviewToken.objects.select_for_update().filter(code=code).first()
        if (
            token is None
            or token.created_by != request.user.username
            or token.consumed_at is not None
            or token.expires_at <= now
        ):
            return None, "预览凭证无效或已过期，请重新判定预览"
        if (
            token.aid_code != payload["aid_code"]
            or token.measured_cd != payload["measured_cd"]
            or token.required_cd != payload["required_cd"]
            or token.bearing_error_deg != payload["bearing_error_deg"]
        ):
            return None, "填写内容与预览不一致，请重新判定预览"
        row = Inspection.objects.create(
            **payload,
            verdict=token.verdict,
            note=token.note,
            preview_token=token.code,
            created_by=request.user.username,
        )
        token.consumed_at = now
        token.save(update_fields=["consumed_at"])
    return row, None


@login_required
@require_http_methods(["GET", "POST"])
def create_view(request):
    if not _can_write(request.user):
        return HttpResponseForbidden(FORBIDDEN_MSG)
    error = ""
    values = {"aid_code": "", "measured_cd": "", "required_cd": "1200", "bearing_error_deg": "0"}
    if request.method == "POST":
        values = request.POST
        payload = _parse_payload(request.POST)
        if payload is None:
            error = "请填编号和三项数值"
        else:
            row, error = _write_with_token(request, payload)
            if row is not None:
                return redirect("detail", pk=row.pk)
    return render(request, "form.html", {"error": error, "values": values})
