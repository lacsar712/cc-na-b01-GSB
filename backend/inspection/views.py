from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from inspection.models import PREVIEW_TOKEN_TTL_SECONDS, Inspection, PreviewToken
from inspection.rules import judge


def _can_write(user) -> bool:
    return user.groups.filter(name="inspector").exists()


def _parse_payload(post):
    """从表单里读出编号和三项数值，缺项或非法即抛错。"""
    try:
        measured = float(post["measured_cd"])
        required = float(post["required_cd"])
        bearing = float(post["bearing_error_deg"])
        code = post["aid_code"].strip()
    except (KeyError, ValueError):
        raise ValueError("bad payload")
    if not code:
        raise ValueError("bad payload")
    return code, measured, required, bearing


def health(_request):
    from django.http import JsonResponse

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
@require_http_methods(["GET", "POST"])
def create_view(request):
    if not _can_write(request.user):
        return HttpResponseForbidden("仅巡检员可登记灯光巡检")
    if request.method == "GET":
        return render(request, "form.html", {"error": ""})

    # 落库必须先过预览：凭证、勾选、内容一致，缺一不可
    try:
        code, measured, required, bearing = _parse_payload(request.POST)
    except ValueError:
        return render(request, "form.html", {"error": "请填编号和三项数值"})

    token_code = request.POST.get("preview_token", "").strip()
    confirmed = request.POST.get("confirm") == "on"
    token = PreviewToken.objects.filter(code=token_code).first()
    now = timezone.now()

    if not confirmed:
        error = "请先勾选确认预览结论"
    elif not token_code or token is None:
        error = "缺少有效预览凭证，请先生成预览"
    elif token.canceled or token.consumed_at is not None:
        error = "预览凭证已失效，请重新生成预览"
    elif now >= token.expires_at:
        error = "预览凭证已过期，请重新生成预览"
    elif not token.matches(code, measured, required, bearing):
        error = "填写内容与预览不一致，请重新生成预览"
    else:
        with transaction.atomic():
            consumed = PreviewToken.objects.filter(
                pk=token.pk,
                consumed_at__isnull=True,
                canceled=False,
                expires_at__gt=now,
            ).update(consumed_at=now)
            if consumed != 1:
                return render(
                    request,
                    "form.html",
                    {"error": "预览凭证已失效，请重新生成预览"},
                )
            row = Inspection.objects.create(
                aid_code=token.aid_code,
                measured_cd=token.measured_cd,
                required_cd=token.required_cd,
                bearing_error_deg=token.bearing_error_deg,
                verdict=token.verdict,
                note=token.note,
                preview_token=token.code,
                created_by=request.user.username,
            )
        return redirect("detail", pk=row.pk)
    return render(request, "form.html", {"error": error})


@login_required
@require_http_methods(["POST"])
def preview_view(request):
    if not _can_write(request.user):
        return HttpResponseForbidden("仅巡检员可登记灯光巡检")
    try:
        code, measured, required, bearing = _parse_payload(request.POST)
    except ValueError:
        return render(request, "form.html", {"error": "请填编号和三项数值"})
    verdict, note = judge(measured, required, bearing)
    token = PreviewToken.objects.create(
        aid_code=code,
        measured_cd=measured,
        required_cd=required,
        bearing_error_deg=bearing,
        verdict=verdict,
        note=note,
        created_by=request.user.username,
        expires_at=timezone.now() + timedelta(seconds=PREVIEW_TOKEN_TTL_SECONDS),
    )
    return render(
        request,
        "preview.html",
        {"token": token, "ttl": PREVIEW_TOKEN_TTL_SECONDS},
    )


@login_required
@require_http_methods(["POST"])
def cancel_preview_view(request):
    if not _can_write(request.user):
        return HttpResponseForbidden("仅巡检员可登记灯光巡检")
    token_code = request.POST.get("preview_token", "").strip()
    PreviewToken.objects.filter(code=token_code, consumed_at__isnull=True).update(
        canceled=True
    )
    return redirect("create")
