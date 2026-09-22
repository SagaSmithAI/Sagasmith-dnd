"""Compatibility entry point; implementation lives in Runtime.cli."""


# Compatibility only: new application code imports Runtime directly.
_RUNTIME_EXPORTS = (
    'CliError',
    '_json_value',
    '_dict',
    '_datetime',
    '_parser',
    '_require',
    '_profile_for',
    '_character_view',
    '_party_sheet',
    '_party_state_with_sheet',
    '_sheet_for_campaign',
    '_persist_character',
    '_dispatch',
    '_error',
    'main',
)

def __getattr__(name: str):
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from sagasmith_dnd._runtime_compat import runtime_attribute

    return runtime_attribute('cli', name)


if __name__ == "__main__":
    raise SystemExit(__getattr__("main")())
