"""Compatibility entry point; implementation lives in Runtime.portrait_extraction."""


# Compatibility only: new application code imports Runtime directly.
_RUNTIME_EXPORTS = (
    'ExtractedPortrait',
    'PortraitInspection',
    '_pymupdf',
    '_intersection_area',
    '_compact_identity',
    '_identity_heading_matches',
    '_statblock_heading',
    '_candidate_crops',
    '_trim_leading_text',
    '_visual_score',
    'PortraitExtractor',
    'extract_actor_portrait',
)

def __getattr__(name: str):
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from sagasmith_dnd._runtime_compat import runtime_attribute

    return runtime_attribute('portrait_extraction', name)
