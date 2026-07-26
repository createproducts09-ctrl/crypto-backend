"""Normalize Groq / gpt-oss model output into clean user-facing prose."""

from __future__ import annotations

import re


_SPECIAL_TOKEN = re.compile(r"<\|[^|>]+?\|>")
_CHANNEL_BLOCK = re.compile(
    r"(?:^|\n)\s*(?:analysis|commentary|reasoning)\s*\n.*?(?=(?:^|\n)\s*(?:final|response|message)\b|\Z)",
    re.I | re.S,
)
_FINAL_MARK = re.compile(
    r"(?:^|\n)\s*(?:final|response|assistant(?:\s*final)?|message)\s*[:\n]\s*",
    re.I,
)
_CODE_FENCE = re.compile(r"^```(?:markdown|md|text)?\s*|\s*```$", re.I | re.M)
_MULTI_NL = re.compile(r"\n{3,}")
# Don't treat the leading # of ### as "glue" (would turn ### into # + ##).
_HEADING_GLUE = re.compile(r"([^#\n])[ \t]+(#{1,3}\s+\d+[).]\s+)")
_SECTION_GLUE = re.compile(r"([^\n])\s*(---)\s*([^\n])")


def normalize_model_output(text: str | None) -> str:
    """Strip reasoning wrappers / special tokens; normalize desk markdown."""
    if not text:
        return ""

    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = _SPECIAL_TOKEN.sub("", out)

    # Prefer content after an explicit final/response marker (gpt-oss style).
    finals = list(_FINAL_MARK.finditer(out))
    if finals:
        out = out[finals[-1].end() :]
    else:
        # Drop leading analysis/reasoning blocks when present.
        out = _CHANNEL_BLOCK.sub("\n", out)

    out = _CODE_FENCE.sub("", out)
    out = out.replace("\u00a0", " ")

    # Ensure section markers aren't glued to previous lines
    out = _HEADING_GLUE.sub(r"\1\n\n\2", out)
    out = _SECTION_GLUE.sub(r"\1\n\n\2\n\n\3", out)

    # Normalize heading styles Groq often emits
    lines: list[str] = []
    for raw in out.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        # **1) Snapshot**  →  ### 1) Snapshot
        bold_h = re.match(r"^\*\*(\d{1,2}[).]\s+.+?)\*\*$", stripped)
        if bold_h:
            lines.append(f"### {bold_h.group(1).strip()}")
            continue
        # 1) Snapshot  (bare known desk header)
        bare = re.match(
            r"^(\d{1,2})[).]\s+(Snapshot|Market Tape|Trend.*|Fundamentals?|"
            r"Narratives?.*|Risks?.*|What to Monitor.*|Watch Next.*)$",
            stripped,
            re.I,
        )
        if bare:
            lines.append(f"### {bare.group(1)}) {bare.group(2).strip()}")
            continue
        # Collapse spaces inside the line
        lines.append(re.sub(r"[ \t]{2,}", " ", stripped))

    out = "\n".join(lines)
    out = _MULTI_NL.sub("\n\n", out).strip()

    # Drop trailing incomplete fragments (cut mid-sentence by token limit)
    if out and out[-1] not in ".!?…\"'”’)" and "\n" in out:
        parts = out.rsplit("\n", 1)
        last = parts[-1].strip()
        if len(last) < 40 and not last.startswith(("#", "*", "-", "•")):
            out = parts[0].rstrip()

    return out.strip()
