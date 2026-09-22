import secrets

from django.db import models
from django.utils import timezone

# 预览凭证有效期（秒）：短时有效，过期不能落库
PREVIEW_TOKEN_TTL_SECONDS = 120


def new_preview_code() -> str:
    return "PV-" + secrets.token_hex(4).upper()


class Inspection(models.Model):
    aid_code = models.CharField("航标编号", max_length=40)
    measured_cd = models.FloatField("实测光强")
    required_cd = models.FloatField("要求光强")
    bearing_error_deg = models.FloatField("方位偏差")
    verdict = models.CharField("结论", max_length=20)
    note = models.CharField("说明", max_length=200)
    preview_token = models.CharField("预览凭证", max_length=40, blank=True, default="")
    created_by = models.CharField("登记人", max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class PreviewToken(models.Model):
    """一次判定预览下发的短时凭证，落库时必须带回且内容一致。"""

    code = models.CharField("凭证编号", max_length=40, unique=True, default=new_preview_code)
    aid_code = models.CharField("航标编号", max_length=40)
    measured_cd = models.FloatField("实测光强")
    required_cd = models.FloatField("要求光强")
    bearing_error_deg = models.FloatField("方位偏差")
    verdict = models.CharField("结论", max_length=20)
    note = models.CharField("说明", max_length=200)
    created_by = models.CharField("登记人", max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("过期时间")
    consumed_at = models.DateTimeField("消耗时间", null=True, blank=True)
    canceled = models.BooleanField("已取消", default=False)

    class Meta:
        ordering = ["-id"]

    def matches(self, aid_code, measured_cd, required_cd, bearing_error_deg) -> bool:
        return (
            self.aid_code == aid_code
            and self.measured_cd == measured_cd
            and self.required_cd == required_cd
            and self.bearing_error_deg == bearing_error_deg
        )

    def is_usable(self, now=None) -> bool:
        now = now or timezone.now()
        return not self.canceled and self.consumed_at is None and now < self.expires_at
