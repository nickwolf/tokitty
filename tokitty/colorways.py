"""User-made colorways: the colorways.json store and its install into sprites.

Lives beside customization.json in the per-user state dir:

  {"version": 1, "colorways": {"custom-1": {"name": "Slate", "coat": "#7d8aa6",
   "shade": "#626e88", "mark": "#4a5468", "light": "#e0e5ef", "ear": "#f2a7b8"}}}

The key is a stable id, "custom-<N>" (the lowest unused positive N when the
colorway is created); customization.json's `colorway` field holds that id, so
renaming or re-toning a colorway never touches the accounts that use it.
Deleting one rewrites those accounts to orange first.

The file is optional and read defensively: absent or unparseable => {}; an
entry with a bad id or any tone that is not "#rrggbb" is dropped. A bad,
empty or colliding name never drops an entry (a built-in added later could
otherwise orphan a user's cat): it gets a deterministic replacement, see
_resolve_name. Randomize and "Surprise me" never draw from here
(sprites.COLORWAYS holds the built-ins only).
"""
from __future__ import annotations

import colorsys
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from tokitty import sprites
from tokitty.customize import _HEX_RE, load_customization, save_customization

COLORWAYS_FILENAME = "colorways.json"
VERSION = 1
MAX_NAME_LEN = 24
TONE_KEYS = sprites.TONE_KEYS  # coat, shade, mark, light, ear

# id -> {"name": str, "coat": "#rrggbb", ...}
Store = Dict[str, Dict[str, str]]


def _id_number(cid: str) -> int:
    return int(cid.split("-", 1)[1])


def validate_name(name: object, store: Store, exclude_id: Optional[str] = None) -> Optional[str]:
    """None when `name` is acceptable, else a short message for the user.
    Non-empty after strip, at most MAX_NAME_LEN characters, not a built-in
    name and not another custom's name (both case-insensitive). `exclude_id`
    is the colorway being renamed, so keeping its own name is fine."""
    if not isinstance(name, str) or not name.strip():
        return "Give the colorway a name."
    clean = name.strip()
    if len(clean) > MAX_NAME_LEN:
        return f"Use {MAX_NAME_LEN} characters or fewer."
    folded = clean.casefold()
    if folded in {b.casefold() for b in sprites.COLORWAYS}:
        return "That name belongs to a built-in colorway."
    for cid, entry in store.items():
        if cid != exclude_id and entry["name"].casefold() == folded:
            return "Another colorway already has that name."
    return None


def _clean_tones(tones: object) -> Optional[Dict[str, str]]:
    if not isinstance(tones, dict):
        return None
    out: Dict[str, str] = {}
    for key in TONE_KEYS:
        value = tones.get(key)
        if not isinstance(value, str) or not _HEX_RE.fullmatch(value):
            return None
        out[key] = value.lower()
    return out


def _custom_number(name: str) -> int:
    """N for a name of the form "Custom <N>", else 0."""
    head, _, tail = name.partition(" ")
    return int(tail) if head == "Custom" and tail.isdigit() else 0


def _resolve_name(name: object, store: Store, counter: int) -> str:
    """A name validate_name accepts for an entry that must be kept. A usable
    name stays as is. A colliding or over-long one becomes "<name> (custom)",
    then "<name> (custom 2)", ... (the base is trimmed to fit MAX_NAME_LEN);
    an empty or non-string one becomes "Custom <N>" with N above `counter`."""
    if isinstance(name, str) and name.strip():
        clean = name.strip()
        if validate_name(clean, store) is None:
            return clean
        n = 1
        while True:
            suffix = " (custom)" if n == 1 else f" (custom {n})"
            candidate = clean[:MAX_NAME_LEN - len(suffix)].rstrip() + suffix
            if validate_name(candidate, store) is None:
                return candidate
            n += 1
    n = counter + 1
    while validate_name(f"Custom {n}", store) is not None:
        n += 1
    return f"Custom {n}"


def load_colorways(state_dir: Path) -> Store:
    path = Path(state_dir) / COLORWAYS_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = data.get("colorways") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    store: Store = {}
    counter = 0
    for cid in sorted((k for k in raw if sprites.is_custom_id(k)), key=_id_number):
        entry = raw[cid]
        if not isinstance(entry, dict):
            continue
        tones = _clean_tones(entry)
        if tones is None:
            continue
        # Applied in id order, so a hand-made duplicate gives way to the lower id.
        name = _resolve_name(entry.get("name"), store, counter)
        counter = max(counter, _custom_number(name))
        store[cid] = {"name": name, **tones}
    return store


def save_colorways(state_dir: Path, store: Store) -> None:
    path = Path(state_dir) / COLORWAYS_FILENAME
    payload = {"version": VERSION, "colorways": store}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def next_id(store: Store, taken: Iterable[str] = ()) -> str:
    """The lowest positive N unused by the store and by `taken` (the ids
    customization entries point at, which may dangle), as "custom-<N>"."""
    taken = {_id_number(cid) for cid in [*store, *taken] if sprites.is_custom_id(cid)}
    n = 1
    while n in taken:
        n += 1
    return f"custom-{n}"


def find_by_tones(store: Store, tones: Dict[str, str]) -> Optional[str]:
    """The id of a colorway whose five tones equal `tones`, else None."""
    want = {k: tones[k].lower() for k in TONE_KEYS}
    for cid in sorted(store, key=_id_number):
        if all(store[cid][k] == want[k] for k in TONE_KEYS):
            return cid
    return None


def add_colorway(state_dir: Path, name: str, tones: Dict[str, str]) -> str:
    """Create a colorway and return its id. Raises ValueError for a bad name
    or tone. Does not touch sprites; call apply() or Controls.on_colorways_changed."""
    store = load_colorways(state_dir)
    problem = validate_name(name, store)
    if problem:
        raise ValueError(problem)
    clean = _clean_tones(tones)
    if clean is None:
        raise ValueError("Every tone must be a #rrggbb colour.")
    cid = next_id(store, taken=[c.colorway for c in load_customization(state_dir).values()])
    store[cid] = {"name": name.strip(), **clean}
    save_colorways(state_dir, store)
    return cid


def update_colorway(state_dir: Path, cid: str, name: Optional[str] = None,
                    tones: Optional[Dict[str, str]] = None) -> None:
    """Rename and/or re-tone an existing colorway (None leaves a part as is).
    Raises KeyError for an unknown id and ValueError for a bad name or tone."""
    store = load_colorways(state_dir)
    if cid not in store:
        raise KeyError(cid)
    entry = dict(store[cid])
    if name is not None:
        problem = validate_name(name, store, exclude_id=cid)
        if problem:
            raise ValueError(problem)
        entry["name"] = name.strip()
    if tones is not None:
        clean = _clean_tones(tones)
        if clean is None:
            raise ValueError("Every tone must be a #rrggbb colour.")
        entry.update(clean)
    store[cid] = entry
    save_colorways(state_dir, store)


def delete_colorway(state_dir: Path, cid: str) -> int:
    """Remove a colorway after moving every customization entry that uses it
    to orange (customization first, so a crash between the writes leaves a
    dangling id, which already renders as orange, not a lost look). Returns
    how many entries were rewritten."""
    state_dir = Path(state_dir)
    customs = load_customization(state_dir)
    rewritten = 0
    for key, custom in list(customs.items()):
        if custom.colorway == cid:
            customs[key] = replace(custom, colorway="orange")
            rewritten += 1
    if rewritten:
        save_customization(state_dir, customs)
    store = load_colorways(state_dir)
    if cid in store:
        del store[cid]
        save_colorways(state_dir, store)
    return rewritten


def palette_from_tones(tones: Dict[str, str], pattern: str = "tabby",
                       tail: str = "plain") -> Dict[str, str]:
    """The full palette sprites.resolve_palette would give a colorway with
    these tones, without registering it (the editor's live preview). Mirrors
    sprites._resolved_palette; tests/qt/test_colorway_editor.py pins the two
    together over every built-in x pattern x tail."""
    tokens = {
        "coat": tones["coat"], "shade": tones["shade"], "mark": tones["mark"],
        "light": tones["light"], "white": sprites.BASE_PALETTE["w"],
    }
    merged = dict(sprites.BASE_PALETTE)
    merged["p"] = tones["ear"]
    for char, src in sprites.PATTERNS[pattern].items():
        merged[char] = tokens.get(src, src)
    rings = sprites.TAIL_STYLES[tail]
    if rings is not None:
        merged["y"], merged["Y"] = tokens[rings[0]], tokens[rings[1]]
    return sprites.derive_tones(merged)


def tones_of(entry: Dict[str, str]) -> Dict[str, str]:
    """Just the five tones of a store entry."""
    return {k: entry[k] for k in TONE_KEYS}


def apply(store: Store) -> None:
    """Push a store into sprites (replaces the custom table, drops caches)."""
    sprites.set_custom_colorways({cid: tones_of(entry) for cid, entry in store.items()})


def install(state_dir: Path) -> Store:
    """Read colorways.json and apply it. Returns the store."""
    store = load_colorways(state_dir)
    apply(store)
    return store


def names(store: Store) -> List[str]:
    """Display names in id order."""
    return [store[cid]["name"] for cid in sorted(store, key=_id_number)]


# --- deriving the other tones from a coat ---------------------------------
# Fitted by eye against the six built-ins. In HLS space, hue is kept and
#   shade = lightness x 0.80, saturation x 0.85   (built-ins: ~0.75-0.84)
#   mark  = lightness x 0.62, saturation x 0.90   (built-ins: ~0.56-0.68 on
#           the tabby-striped coats; black/white are special-cased art)
#   light = coat mixed 65% of the way to white in RGB (built-ins: ~0.57-0.75)
# The ear tone is not derived: it is pink on every built-in but black's.

_SHADE_L, _SHADE_S = 0.80, 0.85
_MARK_L, _MARK_S = 0.62, 0.90
_LIGHT_MIX = 0.65


def _to_rgb(hex_color: str):
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _scaled(rgb, l_scale: float, s_scale: float):
    h, light, sat = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb(h, min(1.0, light * l_scale), min(1.0, sat * s_scale))


def derive_from_coat(coat_hex: str) -> Dict[str, str]:
    """A starting shade, mark and light for a coat colour (deterministic;
    always valid lowercase #rrggbb). Raises ValueError on a bad coat."""
    if not isinstance(coat_hex, str) or not _HEX_RE.fullmatch(coat_hex):
        raise ValueError("coat must be #rrggbb")
    rgb = _to_rgb(coat_hex)
    return {
        "shade": _to_hex(_scaled(rgb, _SHADE_L, _SHADE_S)),
        "mark": _to_hex(_scaled(rgb, _MARK_L, _MARK_S)),
        "light": _to_hex(tuple(c + (1 - c) * _LIGHT_MIX for c in rgb)),
    }
