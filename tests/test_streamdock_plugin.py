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


def test_property_inspector_offers_decision_roles():
    text = (PLUGIN / "pi.html").read_text(encoding="utf-8")
    for role, label in (("allow", "Allow"), ("deny", "Deny"), ("always", "Always")):
        assert f'["{role}", "{label} (permission prompts)"]' in text
