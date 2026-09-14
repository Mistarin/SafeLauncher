import unittest

from core.cache_policy import CachePolicy, cache_policies, cache_policy


class CachePolicyTests(unittest.TestCase):
    def test_policies_preserve_existing_resource_freshness(self):
        self.assertEqual(cache_policy("steam-build").max_age_seconds, 15 * 60)
        self.assertEqual(cache_policy("steam-tags").max_age_seconds, 7 * 24 * 60 * 60)
        self.assertEqual(cache_policy("profile-avatar").max_age_seconds, 30 * 24 * 60 * 60)
        self.assertEqual(cache_policy("profile-avatar-catalog").max_age_seconds, 24 * 60 * 60)

    def test_image_policies_carry_safe_content_types(self):
        self.assertEqual(cache_policy("profile-artwork").content_type, "image/jpeg")
        self.assertEqual(cache_policy("profile-background").content_type, "image/jpeg")
        self.assertEqual(cache_policy("profile-avatar").content_type, "image/png")

    def test_unknown_policy_is_not_silently_defaulted(self):
        with self.assertRaises(KeyError):
            cache_policy("unclassified-resource")

    def test_policy_snapshot_is_typed_and_nonempty(self):
        policies = cache_policies()
        self.assertTrue(policies)
        self.assertTrue(all(isinstance(policy, CachePolicy) for policy in policies))
        self.assertEqual(len({policy.name for policy in policies}), len(policies))


if __name__ == "__main__":
    unittest.main()
