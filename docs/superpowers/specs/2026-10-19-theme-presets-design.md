# Theme presets, sharing, and colour-blind-safe bars

Issues: #132 Theme presets, plus import and export, and #131 Colour-blind-safe usage bars. Status: agreed with Nick 2026-10-19 from a mock sheet rendered by the real widget.

## Where things stood

`theme.json` (#110) holds the whole look, and #139 added two designs, Refined (the default) and Classic, behind the Card style switch on the Theme tab. `apply_design` swaps only `LOOK_FIELDS` and leaves opacity, bar layout and thresholds, what text shows, the halo and the cat alone. The issue text for #132 was written before #139, so its "Classic (today's look)" is the existing Classic design, not a new preset.

## Decisions

- Presets are overlays, not designs. `theme.apply_preset(theme, name)` changes only the fields that preset names and keeps everything else, so presets stack (Glass then Colour-blind safe gives both). They are buttons on the Theme tab, not a selector, because an overlay cannot be read back as "the current preset". No preset touches the cat section. `DESIGNS` and `design_preset` are unchanged. Bar colours and shape are look fields, so a Card style switch after Colour-blind safe or Minimal puts the design's back; notches are not, so they stay. Accepted rather than special-cased: the Card style hint already says it sets the bar colours, and the preset is one click to reapply.
- Glass: `card.opacity` 40 and `text.halo` "on".
- Minimal: `section_labels` removed from `text.show` and `bars.shape` "rounded". Nick picked this over also hiding the status line and account name, since the account name matters with several accounts.
- Colour-blind safe: `bars.low` #56b4e9, `bars.mid` #e69f00, `bars.high` #d55e00 (Okabe-Ito sky blue, orange and vermillion, which differ in lightness as well as hue) and `bars.notches` on.
- Notches (#131): a new `bars.notches` bool, default off, validated like any other bool. It is the user's own setting, not a look field, so switching between Refined and Classic keeps it. `theme.notch_xs` places one notch at `mid_at` and one at `high_at`, none for a threshold at 0 or 100. The widget cuts a 1 px line through the session and weekly bars with `CompositionMode_Source` in the card's colour and alpha, so the notch reads as a gap at any opacity and over any fill. Per-model bars show a share of the total, not a limit, so they get none.
- Import replaces the card, bars and text with the file's and keeps the user's cat settings. A field the file gets wrong takes the default, not the user's old value, and the tab lists it. A file with no card, bars or text section, or one that is not JSON, is refused and nothing changes. A file from before #139 (no `card.design`) is migrated the way `load_theme` migrates one.
- Export writes the card, bars and text only, in the same shape as `theme.json`.

## Not in scope

The Stream Dock usage key (`streamdock/render.py`) still colours its bars with `display.bar_color` and draws no notches, so the colour-blind preset does not reach the deck. That is a follow-up.

## Tests

`tests/test_theme.py` covers each preset's fields, stacking, a save and load of every preset, notch positions and validation, and import and export (round trip, bad fields, the pre-#139 migration, refused files). `tests/qt/test_widget.py` samples the notch pixels at 100% and 40% opacity and checks the models view has none. `tests/qt/test_qt_settings.py` drives the preset buttons, the notches toggle, and import and export with `QFileDialog` patched. A new `card/notches` pixel reference pins the colour-blind preset's look.
