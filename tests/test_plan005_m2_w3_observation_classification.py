"""Focused W3 contract probes for the existing Git observation policy."""

from aota_forge.runtime.task_main.review_adoption import _trusted_source_is_clean


def _observation(**overrides):
    observation = {
        "available": True,
        "complete": True,
        "is_dirty": False,
        "staged": {"paths": [], "truncated": False},
        "unstaged": {"paths": [], "truncated": False},
        "untracked": {"paths": [], "truncated": False},
        "ignored": {"paths": [], "truncated": False},
    }
    observation.update(overrides)
    return observation


def test_known_runtime_only_observation_is_clean():
    observation = _observation(
        ignored={"paths": [".aota/handoffs/" + "a" * 64 + ".json"], "truncated": False}
    )
    assert _trusted_source_is_clean(observation) is True


def test_unknown_or_truncated_observation_fails_closed():
    unknown = _observation(
        ignored={"paths": [".aota/handoffs/nested/unknown.json"], "truncated": False}
    )
    truncated = _observation(
        ignored={"paths": [], "truncated": True}
    )
    assert _trusted_source_is_clean(unknown) is False
    assert _trusted_source_is_clean(truncated) is False
