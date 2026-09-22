from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inspection.models import Inspection, PreviewToken

LOW_LIGHT = {
    "aid_code": "LH-23",
    "measured_cd": "900",
    "required_cd": "1200",
    "bearing_error_deg": "0.5",
}


class BaseCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        group = Group.objects.create(name="inspector")
        cls.keeper = User.objects.create_user(username="keeper", password="light123456")
        cls.keeper.groups.add(group)
        cls.watch = User.objects.create_user(username="watch", password="watch123456")
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

    def make_preview(self, payload=None):
        self.client.post(reverse("preview"), payload or LOW_LIGHT)
        return PreviewToken.objects.latest("id")

    def commit(self, token, payload=None, confirm=True):
        data = dict(payload or LOW_LIGHT)
        data["preview_token"] = token.code
        if confirm:
            data["confirm"] = "on"
        return self.client.post(reverse("create"), data)


class PreviewFlowTests(BaseCase):
    def test_low_light_preview_reads_insufficient(self):
        self.login_keeper()
        resp = self.client.post(reverse("preview"), LOW_LIGHT)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "光强不足")
        self.assertContains(resp, "不合格")
        token = PreviewToken.objects.get()
        self.assertContains(resp, token.code)
        # 预览本身不落库
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_without_token_keeps_two_rows(self):
        self.login_keeper()
        resp = self.client.post(reverse("create"), {**LOW_LIGHT, "confirm": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_without_confirm_keeps_two_rows(self):
        self.login_keeper()
        token = self.make_preview()
        self.commit(token, confirm=False)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_after_cancel_keeps_two_rows(self):
        self.login_keeper()
        token = self.make_preview()
        self.client.post(reverse("cancel_preview"), {"preview_token": token.code})
        self.commit(token)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_with_expired_token_keeps_two_rows(self):
        self.login_keeper()
        token = self.make_preview()
        PreviewToken.objects.filter(pk=token.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.commit(token)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_with_mismatched_payload_keeps_two_rows(self):
        self.login_keeper()
        token = self.make_preview()
        changed = dict(LOW_LIGHT, measured_cd="1500")
        self.commit(token, payload=changed)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_commit_with_unknown_token_keeps_two_rows(self):
        self.login_keeper()
        resp = self.client.post(
            reverse("create"), {**LOW_LIGHT, "preview_token": "PV-DEADBEEF", "confirm": "on"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_successful_commit_shows_token_code(self):
        self.login_keeper()
        token = self.make_preview()
        resp = self.commit(token)
        self.assertEqual(Inspection.objects.count(), 3)
        row = Inspection.objects.latest("id")
        self.assertEqual(row.preview_token, token.code)
        self.assertEqual(row.verdict, "不合格")
        self.assertEqual(row.note, "光强不足")
        self.assertRedirects(resp, reverse("detail", args=[row.pk]))
        detail = self.client.get(reverse("detail", args=[row.pk]))
        self.assertContains(detail, token.code)
        listing = self.client.get(reverse("list"))
        self.assertContains(listing, token.code)
        # 凭证已消耗，不能重复使用
        token.refresh_from_db()
        self.assertIsNotNone(token.consumed_at)
        self.commit(token)
        self.assertEqual(Inspection.objects.count(), 3)


class ReadOnlyTests(BaseCase):
    def setUp(self):
        self.client.force_login(self.watch)

    def test_no_create_entry_for_readonly(self):
        resp = self.client.get(reverse("list"))
        self.assertNotContains(resp, "登记")

    def test_create_form_forbidden(self):
        self.assertEqual(self.client.get(reverse("create")).status_code, 403)

    def test_preview_forbidden(self):
        self.assertEqual(self.client.post(reverse("preview"), LOW_LIGHT).status_code, 403)
        self.assertEqual(PreviewToken.objects.count(), 0)

    def test_commit_forbidden(self):
        resp = self.client.post(
            reverse("create"), {**LOW_LIGHT, "preview_token": "PV-X", "confirm": "on"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Inspection.objects.count(), 2)

    def test_seed_rows_visible(self):
        resp = self.client.get(reverse("list"))
        self.assertContains(resp, "LH-01")
        self.assertContains(resp, "LH-09")
