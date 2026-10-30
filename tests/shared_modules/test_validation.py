import unittest

from backend.shared_modules.validate_input import (
    MAX_SCOPE_PREFIX,
    MAX_TIMEOUT_SECONDS,
    ValidationResult,
    validate,
    validate_confidence,
    validate_ip,
    validate_mac,
    validate_scope,
    validate_timeout,
)


#Helpers
class Checks(unittest.TestCase):
    def assertAccepted(self, result, expected):
        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.valid, result.reason)
        self.assertEqual(result.value, expected)
        self.assertIsNone(result.reason)

    def assertRejected(self, result, contains=None):
        self.assertIsInstance(result, ValidationResult)
        self.assertFalse(result.valid, result.value)
        self.assertIsNone(result.value)
        self.assertTrue(result.reason)
        if contains:
            self.assertIn(contains, result.reason)


#Result Record
class TestValidationResult(Checks):
    def test_ok(self):
        result = ValidationResult.ok("x")
        self.assertAccepted(result, "x")
        self.assertTrue(result)

    def test_reject(self):
        result = ValidationResult.reject("bad")
        self.assertRejected(result, "bad")
        self.assertFalse(result)


#IP Address
class TestValidateIp(Checks):
    def test_accepts_good_addresses(self):
        for text in ["192.168.1.10", "10.0.0.1", "255.255.255.255", "8.8.8.8"]:
            with self.subTest(text=text):
                self.assertAccepted(validate_ip(text), text)

    def test_accepts_zero_address(self):
        self.assertAccepted(validate_ip("0.0.0.0"), "0.0.0.0")

    def test_strips_spaces(self):
        self.assertAccepted(validate_ip("  192.168.1.10 "), "192.168.1.10")

    def test_message(self):
        result = validate_ip("192.168.1.300")
        self.assertRejected(result)
        self.assertEqual(result.reason, "Invalid IP address: 192.168.1.300")

    def test_rejects_bad_addresses(self):
        bad = ["192.168.1", "192.168.1.1.1", "a.b.c.d", "192.168.01.1",
               "-1.0.0.0", "192.168.1.1/24", "::1", "192,168,1,1"]
        for text in bad:
            with self.subTest(text=text):
                self.assertRejected(validate_ip(text), "Invalid IP address")

    def test_rejects_blank_and_wrong_type(self):
        for value in ["", "   ", None, 3232235777, ["10.0.0.1"]]:
            with self.subTest(value=value):
                self.assertRejected(validate_ip(value))


#MAC Address
class TestValidateMac(Checks):
    def test_accepts_all_three_forms(self):
        for text in ["aa-bb-cc-dd-ee-ff", "AA:BB:CC:DD:EE:FF", "aabb.ccdd.eeff"]:
            with self.subTest(text=text):
                self.assertAccepted(validate_mac(text), "AA:BB:CC:DD:EE:FF")

    def test_fixes_case(self):
        self.assertAccepted(validate_mac("aa:Bb:cC:dd:EE:ff"), "AA:BB:CC:DD:EE:FF")
        self.assertAccepted(validate_mac("AABB.CCDD.EEFF"), "AA:BB:CC:DD:EE:FF")

    def test_strips_spaces(self):
        self.assertAccepted(validate_mac(" aa-bb-cc-dd-ee-ff "), "AA:BB:CC:DD:EE:FF")

    def test_rejects_mixed_separators(self):
        for text in ["aa:bb-cc:dd-ee:ff", "aa-bb-cc-dd-ee:ff", "aabb.ccdd-eeff"]:
            with self.subTest(text=text):
                self.assertRejected(validate_mac(text), "not a valid MAC address")

    def test_rejects_bad_macs(self):
        bad = ["aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:ff:00", "gg:bb:cc:dd:ee:ff",
               "aabbccddeeff", "a:b:c:d:e:f", "aabb.ccdd", "aa bb cc dd ee ff"]
        for text in bad:
            with self.subTest(text=text):
                self.assertRejected(validate_mac(text), "not a valid MAC address")

    def test_rejects_blank_and_wrong_type(self):
        for value in ["", None, 123456]:
            with self.subTest(value=value):
                self.assertRejected(validate_mac(value))


#Scope
class TestValidateScope(Checks):
    def test_accepts_private_ranges(self):
        for text in ["192.168.1.0/24", "10.0.0.0/24", "172.16.4.0/22",
                     "172.31.255.0/24", "192.168.0.0/22"]:
            with self.subTest(text=text):
                self.assertAccepted(validate_scope(text), text)

    def test_accepts_slash_32(self):
        self.assertAccepted(validate_scope("192.168.1.50/32"), "192.168.1.50/32")

    def test_accepts_size_limit(self):
        text = f"10.0.0.0/{MAX_SCOPE_PREFIX}"
        self.assertAccepted(validate_scope(text), text)

    def test_strips_spaces(self):
        self.assertAccepted(validate_scope(" 192.168.1.0/24 "), "192.168.1.0/24")

    def test_rejects_too_big(self):
        for text in ["10.0.0.0/8", "192.168.0.0/16", f"10.0.0.0/{MAX_SCOPE_PREFIX - 1}"]:
            with self.subTest(text=text):
                self.assertRejected(validate_scope(text), "too big")

    def test_rejects_public_ranges(self):
        for text in ["8.8.8.0/24", "172.32.0.0/24", "11.0.0.0/24"]:
            with self.subTest(text=text):
                self.assertRejected(validate_scope(text), "Can't scan")

    def test_rejects_special_ranges(self):
        for text in ["127.0.0.0/24", "169.254.1.0/24", "0.0.0.0/32",
                     "0.0.0.0/0", "224.0.0.0/24"]:
            with self.subTest(text=text):
                self.assertRejected(validate_scope(text))

    def test_rejects_wrong_start(self):
        result = validate_scope("192.168.1.5/24")
        self.assertRejected(result, "Range should start at 192.168.1.0/24")

    def test_rejects_missing_slash(self):
        self.assertRejected(validate_scope("192.168.1.0"), "CIDR")

    def test_rejects_bad_address_or_number(self):
        for text in ["192.168.1.300/24", "192.168.1.0/33", "192.168.1.0/",
                     "192.168.1.0/abc", "/24", "192.168.1.0/-1", "192.168.1.0/24/1"]:
            with self.subTest(text=text):
                self.assertRejected(validate_scope(text), "not a valid scan range")

    def test_rejects_blank_and_wrong_type(self):
        for value in ["", None, 24]:
            with self.subTest(value=value):
                self.assertRejected(validate_scope(value))


#Confidence Score
class TestValidateConfidence(Checks):
    def test_accepts_range(self):
        for value in [0, 1, 50, 99, 100]:
            with self.subTest(value=value):
                self.assertAccepted(validate_confidence(value), value)

    def test_accepts_text_and_whole_floats(self):
        self.assertAccepted(validate_confidence("85"), 85)
        self.assertAccepted(validate_confidence(" 7 "), 7)
        self.assertAccepted(validate_confidence(75.0), 75)

    def test_rejects_out_of_range(self):
        for value in [101, -1, 1000, "101", "-5"]:
            with self.subTest(value=value):
                self.assertRejected(validate_confidence(value), "out of range")

    def test_rejects_decimals(self):
        for value in [50.5, "50.5", float("nan"), float("inf")]:
            with self.subTest(value=value):
                self.assertRejected(validate_confidence(value))

    def test_rejects_wrong_type(self):
        for value in [True, False, None, "", "high", [50]]:
            with self.subTest(value=value):
                self.assertRejected(validate_confidence(value))

    def test_message(self):
        self.assertEqual(validate_confidence(101).reason,
                         "101 is out of range. Confidence scores go from 0 to 100.")


#Timeout
class TestValidateTimeout(Checks):
    def test_accepts_good_timeouts(self):
        for value, expected in [(1, 1.0), (0.5, 0.5), (2.25, 2.25),
                                (MAX_TIMEOUT_SECONDS, float(MAX_TIMEOUT_SECONDS)),
                                ("3", 3.0), (" 1.5 ", 1.5)]:
            with self.subTest(value=value):
                self.assertAccepted(validate_timeout(value), expected)

    def test_rejects_zero_and_negative(self):
        for value in [0, 0.0, -1, "-2"]:
            with self.subTest(value=value):
                self.assertRejected(validate_timeout(value), "Timeout must be")

    def test_rejects_over_limit(self):
        for value in [MAX_TIMEOUT_SECONDS + 0.01, 3600, "999"]:
            with self.subTest(value=value):
                self.assertRejected(validate_timeout(value), "Timeout must be")

    def test_rejects_wrong_type(self):
        for value in [None, "", "soon", True, float("nan"), float("inf"), "inf", [1]]:
            with self.subTest(value=value):
                self.assertRejected(validate_timeout(value))


#Validate by kind
class TestValidate(Checks):
    def test_picks_the_right_check(self):
        self.assertAccepted(validate("10.0.0.1", "ip"), "10.0.0.1")
        self.assertAccepted(validate("aa-bb-cc-dd-ee-ff", "mac"), "AA:BB:CC:DD:EE:FF")
        self.assertAccepted(validate("10.0.0.0/24", "scope"), "10.0.0.0/24")
        self.assertAccepted(validate(42, "confidence"), 42)
        self.assertAccepted(validate(2, "timeout"), 2.0)

    def test_unknown_kind(self):
        self.assertRejected(validate("x", "shoe_size"), "Unknown kind")


#No crashes on bad input
class TestNeverCrashes(unittest.TestCase):
    def test_no_check_crashes(self):
        weird = [None, "", " ", 0, -1, 1.5, float("nan"), True, [], {}, object(),
                 b"10.0.0.1", "\x00", "1" * 10000]
        for check in [validate_ip, validate_mac, validate_scope,
                      validate_confidence, validate_timeout]:
            for value in weird:
                with self.subTest(check=check.__name__, value=repr(value)[:30]):
                    self.assertIsInstance(check(value), ValidationResult)


if __name__ == "__main__":
    unittest.main()