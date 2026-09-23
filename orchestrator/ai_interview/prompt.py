from __future__ import annotations

import re

REF_SECTIONS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)
BASE_SECTIONS = (
    "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music",
)


def validate_prompt(mode: str, prompt: str, dialogue: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    sections = REF_SECTIONS if mode == "ref2va" else BASE_SECTIONS
    positions: list[int] = []
    for name in sections:
        matches = list(re.finditer(rf"(?m)^{re.escape(name)}:\s*", prompt))
        if len(matches) != 1:
            errors.append(f"prompt must contain exactly one {name}: section")
        else:
            positions.append(matches[0].start())
    if len(positions) == len(sections) and positions != sorted(positions):
        errors.append("prompt sections are out of official H3 order")
    if mode == "ref2va" and "<Subject 1>" not in prompt:
        errors.append("Ref2VA prompt must define at least <Subject 1>")
    integrated = prompt.casefold()
    if "no narration" not in integrated:
        errors.append("prompt must explicitly say no narration")
    if dialogue:
        for line in dialogue:
            if line and line not in prompt:
                errors.append(f"prompt does not preserve dialogue verbatim: {line}")
        if "<d>[" not in prompt:
            errors.append("dialogue prompt must use <d>[Language] ...</d>")
    elif "no dialogue" not in integrated:
        errors.append("silent prompt must explicitly say no dialogue")
    return errors
