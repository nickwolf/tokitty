# Pixel reference images

These PNGs are what the Qt widget's real paint output must match, pixel for pixel, on Linux, Windows and macOS. `tests/qt/test_pixel_refs.py` compares them; `tests/qt/pixel_check.py` builds, compares and regenerates them. The design is in `docs/superpowers/specs/2026-10-14-qt-pixel-tests-design.md` (issue #128).

## What is here

`cat/<state>@1x.png` and `@2x.png` are one sheet per sprite state: every coat (6 colourways x 9 patterns) as a row and every frame of the state as a column, drawn by the widget and cropped from a grab. They are exact-palette indexed PNGs.

`card/<layout>@1x.png` and `@2x.png` are whole-widget grabs of six layouts: default, halo (halo on, opaque), low_opacity (20 percent, halo auto), cat_right (`inside_right`), cat_top (`on_top`) and models. Card text is drawn in the bundled Aileron font with full hinting and no subpixel antialiasing, so Linux and Windows agree. The product's own fonts are covered by a fit check, not by pixels.

`card/masks.json` holds the text regions each card reference was made with, in device pixels.

`keys/<name>.png` are Stream Dock keys, 64x64 whatever the dpr. They are compared on Linux and Windows and skipped on macOS.

## What is masked

macOS rasterizes text differently (CoreText) and nothing changes that, so on macOS every card layout is compared with its text regions masked out. The mask is the union of the regions in `card/masks.json` and the regions the live render recorded. On every system the low_opacity layout is masked the same way, because text over a translucent card differs by up to one level per channel between Intel and AMD CPUs. Everything outside the mask must match exactly, including the cat.

## Updating a reference

Run `python tests/qt/pixel_check.py update [name ...]` on Linux or Windows. It refuses on macOS, because macOS output is never the reference. It renders the current output, prints one line per file (`unchanged`, `new`, or the pixel count and bounding box), writes old, new and difference images to `build/pixel-diff/`, and then writes only the files that changed. Names are `cat/<state>`, `card/<layout>` or `keys/<key>`, optionally with `@1x` or `@2x`, or just `cat`, `card` or `keys`. Look at the images before committing. The test suite never writes references.

## Reading a failure

A failing test names the file, the number of differing pixels, their bounding box and the first few coordinates with both colours. The `pixel-diff-<os>` artifact of the CI run has `<name>.compare.png` (reference, actual and difference side by side, with the masked regions outlined in cyan), plus the actual and reference images. On a pull request the `pixel-report` artifact and the job summary list every reference file the pull request changed against its base, with old, new and difference images, which is what to open when reviewing an art change.

## Drift triage

The references were measured on two CPUs and one runner image per OS. A CI mismatch that no code change explains may be environment drift (a runner image, a Qt build or a CPU). Rerun the job, compare the CPU in the job log, look at the difference image, and only then update. A new translucent paint case (a tag chip, an antialiased edge over a translucent card) should be measured on both an Intel and an AMD machine before its reference is trusted unmasked. A PySide6 bump that changes rasterization fails these tests; run `update`, inspect the images and commit them with the bump.

## Provenance

PySide6 6.12.0 (pinned exactly). Last generated in WSL Ubuntu on an Intel i7-9700KF, and checked on native Windows 11 on the same machine.
