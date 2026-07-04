"""Deliberately failing test — exists only to prove the CI gate blocks red PRs. Never merged."""


def test_this_pr_must_be_blocked() -> None:
    assert False, "this failure should make ci-ok red and lock the merge button"
