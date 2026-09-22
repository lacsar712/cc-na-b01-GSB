from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inspection.models import Inspection, PreviewToken

# 实测光强低于要求，预览应判“光强不足”
PAYLOAD = {
    "aid_code": "LH-21",
    "measured_cd": "800",
    "required_cd": "1200",
    "bearing_error_deg": "0.2",
}


class BaseCase(TestCase):
    def setUp(self):
        group = Group.objects.create(name="inspector")
        self.keeper = User.objects.create_user(username="keeper", password="light123456")
        self.keeper.groups.add(group)
        self.watch = User.objects.create_user(username="watch", password="watch123456")
        Inspection.objects.create(
            aid_code="LH-01", measured_cd=1400, required_cd=1200,
            bearing_error_deg=0.4, verdict="合格", note="光强与方位均在限内",
            created_by="keeper",
        )
        Inspection.objects.create(
            aid_code="LH-09", measured_cd=800, required_cd=1200,
            bearing_error_deg=0.2, verdict="不合格", note="光强不足",
            created_by="keeper",
        )

    def login_keeper(self):
        self.client.force_login(self.keeper)

    def preview(self, **overrides):
        return self.client.post(reverse("preview"), dict(PAYLOAD, **overrides))

    def submit(self, **fields):
        return self.client.post(reverse("create"), dict(PAYLOAD, **fields))


class PreviewTests(BaseCase):
    def test_low_light_preview_reads_insufficient(self):
        self.login_keeper()
        resp = self.preview()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["verdict"], "不合格")
        self.assertEqual(body["note"], "光强不足")
        self.assertTrue(body["token"])
        self.assertGreater(body["expires_in"], 0)
        # 预览本身不落库
        self.assertEqual(Inspection.objects.count(), 2)

    def test_preview_requires_login(self):
        resp = self.preview()
        self.assertEqual(resp.status_code, 302)

    def test_preview_forbidden_for_read_only(self):
        self.client.force_login(self.watch)
        resp = self.preview()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PreviewToken.objects.count(), 0)


class CreateTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.login_keeper()

    def token_for(self, **overrides):
        return self.preview(**overrides).json()["token"]

    def test_submit_without_token_keeps_two_rows(self):
        resp = self.submit(confirm="on")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_submit_without_confirm_keeps_two_rows(self):
        resp = self.submit(preview_token=self.token_for())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_mismatched_payload_keeps_two_rows(self):
        resp = self.submit(
            preview_token=self.token_for(), confirm="on", measured_cd="900"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_expired_token_keeps_two_rows(self):
        token = self.token_for()
        PreviewToken.objects.filter(code=token).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        resp = self.submit(preview_token=token, confirm="on")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_valid_token_writes_row_with_token_code(self):
        token = self.token_for()
        resp = self.submit(preview_token=token, confirm="on")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Inspection.objects.count(), 3)
        row = Inspection.objects.get(aid_code="LH-21")
        self.assertEqual(row.preview_token, token)
        self.assertEqual(row.verdict, "不合格")
        self.assertEqual(row.note, "光强不足")
        self.assertIsNotNone(PreviewToken.objects.get(code=token).consumed_at)
        # 新行和详情页都能查到消耗掉的凭证编号
        self.assertContains(self.client.get(reverse("list")), token)
        self.assertContains(self.client.get(reverse("detail", args=[row.pk])), token)

    def test_token_cannot_be_reused(self):
        token = self.token_for()
        self.submit(preview_token=token, confirm="on")
        resp = self.submit(preview_token=token, confirm="on")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 3)

    def test_token_bound_to_requesting_user(self):
        token = self.token_for()
        other = User.objects.create_user(username="keeper2", password="x")
        other.groups.add(Group.objects.get(name="inspector"))
        self.client.force_login(other)
        resp = self.submit(preview_token=token, confirm="on")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)


class ReadOnlyTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.watch)

    def test_watch_cannot_see_form_or_entry(self):
        self.assertEqual(self.client.get(reverse("create")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("list")), "登记")

    def test_watch_cannot_submit(self):
        resp = self.submit(preview_token="whatever", confirm="on")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Inspection.objects.count(), 2)
