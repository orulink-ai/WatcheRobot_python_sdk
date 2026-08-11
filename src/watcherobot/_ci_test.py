# Test: deliberate type issue for CI validation
def _ci_test_bad_return() -> str:
    return 42  # mypy should flag this
