"""EPG-based logo index.

Parses the cached EPG XML (epg_cache/_epg.xml.gz) to extract
channel display-name → icon URL mappings.  epg.one/img/ serves
real PNG logos for ~7000 channels, including provider-specific
Russian channels that aren't in the iptv-org database.

Lookup is fast (in-memory dict) and uses normalised keys so that
"Кинохит HD", "КИНОХИТ", "kinohit hd" all collapse to the same entry.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Optional


# Tokens stripped before key comparison
_NOISE = frozenset({
    "hd", "fhd", "uhd", "4k", "8k", "sd", "hq",
    "+", "!", ".", ",",
})
_QUALITY_RE = re.compile(r"\s*(hd|fhd|uhd|4k|\+\d+)\s*$", re.IGNORECASE)


def _norm(name: str) -> str:
    """Lowercase, strip quality suffixes and punctuation for key matching."""
    s = _QUALITY_RE.sub("", name.strip()).lower()
    s = re.sub(r"[!?.,;:()\[\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class EpgIconIndex:
    """In-memory index of EPG channel names → icon URLs."""

    def __init__(self) -> None:
        # normalized_name → icon_url
        self._index: dict[str, str] = {}

    @property
    def size(self) -> int:
        return len(self._index)

    def load_from_xml_gz(self, path: Path) -> None:
        """Parse channels from an EPG XML gzip file and build the index."""
        if not path.exists() or path.stat().st_size == 0:
            return

        index: dict[str, str] = {}
        current_names: list[str] = []
        current_icon: Optional[str] = None
        in_channel = False

        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "<channel " in line:
                        in_channel = True
                        current_names = []
                        current_icon = None
                    elif in_channel:
                        m = re.search(r"<display-name[^>]*>([^<]+)</display-name>", line)
                        if m:
                            current_names.append(m.group(1).strip())
                        m = re.search(r'<icon src="([^"]+)"', line)
                        if m:
                            current_icon = m.group(1).strip()
                        if "</channel>" in line:
                            in_channel = False
                            if current_names and current_icon:
                                for raw in current_names:
                                    key = _norm(raw)
                                    if key and len(key) >= 2:
                                        index.setdefault(key, current_icon)
        except (OSError, EOFError):
            return

        self._index = index

    def lookup(self, channel_name: str) -> Optional[str]:
        """Return an icon URL for the channel name, or None."""
        key = _norm(channel_name)
        if not key or len(key) < 2:
            return None
        # Exact normalised match
        url = self._index.get(key)
        if url:
            return url
        # Substring: try without trailing numbers/qualifiers
        # e.g. "setanta sports 1" → try "setanta sports"
        parts = key.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            url = self._index.get(parts[0])
            if url:
                return url
        return None
