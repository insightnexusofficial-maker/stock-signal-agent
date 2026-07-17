import unittest

import admin_fcm


class AdminFcmTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_does_not_expose_token(self):
        token = "sensitive-device-token"
        value = admin_fcm.fingerprint(token)

        self.assertEqual(value, "4ba7c606a712")
        self.assertNotIn(token, value)
        self.assertEqual(len(value), 12)

    def test_clean_text_removes_line_breaks_and_limits_length(self):
        value = admin_fcm.clean_text("nickname\nwith\rbreaks" + "x" * 50)

        self.assertNotIn("\n", value)
        self.assertNotIn("\r", value)
        self.assertLessEqual(len(value), 40)


if __name__ == "__main__":
    unittest.main()
