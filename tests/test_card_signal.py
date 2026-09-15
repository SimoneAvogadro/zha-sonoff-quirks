"""Icona qualita' del segnale Zigbee nella card (LQI / RSSI / linkquality).

La logica pura (soglie, risoluzione per device via hass.entities, lettura)
vive in tests/signal-quality.test.js e gira con node in un contesto vm; qui
la si lancia da pytest (saltata se node manca) e si verifica il contratto
strutturale fra template e riferimenti DOM, come test_card_labels.py.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).parents[1]
_CARD_PATH = _ROOT / "custom_components/zha_sonoff_quirks/www/sonoff-valve-card.js"
CARD = _CARD_PATH.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
def test_logica_icona_segnale_in_node():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests/signal-quality.test.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_wrapper_icona_nel_template_e_nella_mappa_dom():
    """L'id sq-wrap compare nel template e _cacheEls lo referenzia."""
    assert 'id="sq-wrap"' in CARD
    cache = re.search(r"_cacheEls\(\) \{(.*?)\n  \}", CARD, re.S)
    assert cache and "sqWrap" in cache.group(1)


def test_update_applica_icona():
    """_update chiama sqApply con il wrapper: senza, l'icona resta nascosta."""
    assert re.search(r"sqApply\(el\.sqWrap", CARD)
