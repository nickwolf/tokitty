import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "tokitty" / "streamdock" / "plugin" / "com.tokitty.deck.sdPlugin"


def test_manifest_declares_one_keypad_action():
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["UUID"] == "com.tokitty.deck"
    assert manifest["CodePath"] == "index.html"
    (action,) = manifest["Actions"]
    assert action["UUID"] == "com.tokitty.deck.key"
    assert action["Controllers"] == ["Keypad"]
    assert action["PropertyInspectorPath"] == "pi.html"
    for name in (manifest["Icon"], action["Icon"], manifest["CategoryIcon"]):
        assert (PLUGIN / name).is_file()


def test_pages_load_config_and_use_no_external_resources():
    for page in ("index.html", "pi.html"):
        text = (PLUGIN / page).read_text(encoding="utf-8")
        assert '<script src="config.js"></script>' in text
        assert "—" not in text
        # The only URLs are the loopback server and the VSD websocket.
        for url in re.findall(r"""["'](https?|wss?)://([^"'/:]+)""", text):
            assert url[1] == "127.0.0.1", url


def test_property_inspector_offers_blank_role_and_prompt_choices():
    text = (PLUGIN / "pi.html").read_text(encoding="utf-8")
    assert '["blank", "Blank"]' in text
    assert "During a permission prompt" in text
    for value, label in (("", "Default"), ("allow", "Allow"), ("deny", "Deny"), ("always", "Always")):
        assert f'["{value}", "{label}"]' in text
    # The dedicated roles are gone from the role list.
    assert "(permission prompts)" not in text


def test_property_inspector_saves_role_and_prompt_together():
    text = (PLUGIN / "pi.html").read_text(encoding="utf-8")
    assert "payload: { role: current, prompt: currentPrompt }" in text
    assert text.count("select.onchange = save;") == 2


def test_plugin_replays_keys_when_a_new_tokitty_answers():
    text = (PLUGIN / "index.html").read_text(encoding="utf-8")
    assert "if (!connected || plan.boot !== boot) {" in text
    assert "boot = plan.boot;" in text
