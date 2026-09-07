"""Explicit policy: no adapter generation limits is not a very large budget."""

BUDGET_FIELDS = ('timeout_seconds', 'max_calls', 'max_output_tokens', 'max_input_chars')


def is_unlimited(config):
    return config.get('budget_mode', 'bounded') == 'unlimited'


def validate_budget_policy(config):
    """Return errors; bounded numeric checks remain at existing native gates."""
    mode = config.get('budget_mode', 'bounded')
    if mode not in ('bounded', 'unlimited'):
        return ['invalid_budget_mode']
    if mode == 'unlimited':
        return ['unlimited_requires_explicit_null_limit:' + key for key in BUDGET_FIELDS
                if key not in config or config[key] is not None]
    return []


def describe_budget_policy(config):
    if is_unlimited(config):
        return ('No adapter generation deadline, HTTP call cap, input length cap or output token cap; '
                'native/provider limits preserved; HTTP attempts and available usage still recorded')
    return ('Root HTTP call cap; per-call output cap; compact complete JSON Unicode codepoint cap; '
            'no monetary equality claim')
