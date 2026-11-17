"""Clean up caption text before reading it.

Instagram captions often use "fancy" letters (𝐏𝐎𝐏 𝐔𝐏, 𝑷𝒓𝒐𝒔𝒔𝒊𝒎𝒊, ᴄᴄ ɴɪɢʜᴛ) that look like text but
are other Unicode characters, so a pattern looking for "novembre" or "23:30" misses them.
"""
from __future__ import annotations

import re
import unicodedata

# small capitals (used as capitals: ᴄᴄ ɴɪɢʜᴛ = CC NIGHT) that NFKC leaves alone
_SMALL_CAPS = dict(zip(
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘꞯʀꜱᴛᴜᴠᴡʏᴢǫ",
    "ABCDEFGHIJKLMNOPQRSTUVWYZQ"))
_INVISIBLE = re.compile("[​-‏⁠-⁤︀-️­]")


def normalize(text: str | None) -> str:
    """Fancy Unicode letters and digits -> plain ones; invisible characters removed.
    Accented letters (è, ì) and emoji are kept."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(_SMALL_CAPS.get(c, c) for c in t)
    t = _INVISIBLE.sub("", t)
    return t.replace("’", "'").replace("‘", "'")
