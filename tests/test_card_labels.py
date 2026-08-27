"""Contratto fra la card Lovelace e const.py sulle etichette delle linee.

La lettera serigrafata su ciascuna uscita e' scritta in DUE posti: la mappa
CHANNEL_LABELS in const.py (usata dai servizi per accettare "A"/"B" come alias)
e la costante CH_LETTER nella card (usata per disegnarla sopra il pulsante). Se
divergono, la card mostra una lettera che il servizio non riconosce.

Non c'e' un test runner JS in questo repo, quindi la card si controlla dal suo
sorgente: e' poco, ma copre esattamente le regressioni realistiche (qualcuno
tocca una mappa e non l'altra, o aggiunge un id nel template senza il
corrispondente riferimento DOM).
"""

import importlib.util
import json
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).parents[1] / "custom_components/zha_sonoff_quirks"

_spec = importlib.util.spec_from_file_location("swv_const", _ROOT / "const.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
CHANNEL_LABELS = _module.CHANNEL_LABELS

CARD = (_ROOT / "www/sonoff-valve-card.js").read_text(encoding="utf-8")


def test_le_lettere_della_card_rispecchiano_const_py():
    """CH_LETTER nella card == CHANNEL_LABELS in const.py."""
    match = re.search(r"^const CH_LETTER = (\{.*?\});$", CARD, re.M)
    assert match, "CH_LETTER non trovata nella card"
    # La forma { "1": "A", "2": "B" } e' JSON valido una volta isolata.
    assert json.loads(match.group(1)) == CHANNEL_LABELS


@pytest.mark.parametrize("key,letter", [("line1", "A"), ("line2", "B")])
def test_le_etichette_di_default_finiscono_con_la_lettera(key: str, letter: str):
    """In ogni lingua della card, line1/line2 terminano con A/B."""
    values = re.findall(rf'{key}: "([^"]*)"', CARD)
    assert values, f"nessuna etichetta {key} trovata nella card"
    for value in values:
        assert value.endswith(letter), (
            f"etichetta {key} = {value!r}: deve terminare con {letter!r}, "
            "la lettera serigrafata sulla valvola"
        )


def test_ogni_id_del_template_ha_un_riferimento_dom():
    """Gli span di lettera e i loro wrapper devono essere raggiunti dal render."""
    ids = set(re.findall(r'id="((?:chid|chc)-[a-z0-9]+)"', CARD))
    assert len(ids) == 8, f"attesi 8 id lettera/wrapper, trovati {sorted(ids)}"
    for element_id in sorted(ids):
        assert f'$("{element_id}")' in CARD, (
            f"l'id {element_id} esiste nel template ma non e' in this._el: "
            "il render non lo aggiornerebbe mai"
        )
