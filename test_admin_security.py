import unittest
from pathlib import Path


class AdminSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent
        cls.html = (root / "public" / "admin.html").read_text()
        cls.javascript = (root / "public" / "admin.js").read_text()
        cls.rules = (root / "firestore.rules").read_text()

    def test_admin_page_contains_no_embedded_password_or_inline_handlers(self):
        self.assertNotIn("ADMIN_PASSWORD", self.html)
        self.assertNotIn("onclick=", self.html)
        self.assertIn('autocomplete="current-password"', self.html)

    def test_admin_javascript_requires_custom_claim_and_uses_safe_text_rendering(self):
        self.assertIn("getIdTokenResult", self.javascript)
        self.assertIn("token.claims.admin !== true", self.javascript)
        self.assertIn("nickname.textContent", self.javascript)
        self.assertNotIn("innerHTML", self.javascript)

    def test_firestore_admin_access_requires_auth_claim(self):
        self.assertIn("request.auth.token.admin == true", self.rules)
        self.assertIn("allow list: if isAdmin()", self.rules)
        self.assertIn("allow delete: if isAdmin()", self.rules)


if __name__ == "__main__":
    unittest.main()
