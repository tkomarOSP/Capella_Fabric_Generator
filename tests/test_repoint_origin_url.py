"""Fabric_MCP_Issues: push_model_changes built a malformed remote URL that got
worse on every retry.

The regression: repoint_origin's replacement template was a literal 0x01 byte
instead of a group reference, so the scheme was replaced by a control character.
Because the function runs on a URL it previously wrote, each push prepended
another byte -- git showed https://?github.com/..., then https://??github.com/...

This tests the URL string directly rather than through git, because the string
IS the bug. The original suite missed it: its remote was a file:// URL, which
the credential regex never matches, so the broken template was never exercised.
That is the lesson worth keeping -- a fixture chosen for convenience silently
excluded the only code path that mattered.
"""
import sys

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
from git_service import _inject_pat, _strip_credential

CLEAN = "https://github.com/tkSDISW/After_Treatment_System"

cases = [
    ("bare URL",                CLEAN),
    ("credential embedded",     "https://oauth2:ghp_secret@github.com/tkSDISW/After_Treatment_System"),
    ("corrupted once",          "https://oauth2:ghp_secret@\x01github.com/tkSDISW/After_Treatment_System"),
    ("corrupted twice",         "https://oauth2:ghp_secret@\x01\x01github.com/tkSDISW/After_Treatment_System"),
    ("corrupted, no credential", "https://\x01\x01github.com/tkSDISW/After_Treatment_System"),
]
for label, url in cases:
    got = _strip_credential(url)
    assert got == CLEAN, f"{label}: got {got!r}"
    print(f"{label:26} -> clean")
print("a session already corrupted on disk recovers on its next push, no re-clone needed")

# The property that actually failed: repeated inject/strip cycles must converge.
url = CLEAN
for i in range(1, 11):
    url = _inject_pat(_strip_credential(url), "ghp_secret")
    assert _strip_credential(url) == CLEAN, f"drifted on cycle {i}: {url!r}"
    assert "\x01" not in url and url.count("@") == 1, f"cycle {i}: {url!r}"
print("stable across 10 push cycles -- no accumulation")

assert _inject_pat(_strip_credential(CLEAN), "tok") == \
    "https://oauth2:tok@github.com/tkSDISW/After_Treatment_System"
print("credential injected in the one correct place")

# A rotated token replaces the old one rather than stacking beside it.
first = _inject_pat(_strip_credential(CLEAN), "tok-old")
second = _inject_pat(_strip_credential(first), "tok-new")
assert "tok-old" not in second and "tok-new" in second, second
print("token rotation replaces, never stacks")

print("\nALL REMOTE-URL CHECKS PASSED")
