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
CHANNELS = _module.CHANNELS
line_name_uid_prefix = _module.line_name_uid_prefix

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
    """Lettera, icona e wrapper di ogni pulsante devono essere raggiunti dal render."""
    # 4 pulsanti (litri/tempo x linea A/B), ciascuno con: il wrapper che porta
    # il tooltip, lo span della lettera dentro il pulsante e lo span dell'icona.
    ids = set(re.findall(r'id="((?:chc|gbl|gbi)-[a-z0-9]+)"', CARD))
    assert len(ids) == 12, f"attesi 12 id, trovati {sorted(ids)}"
    for element_id in sorted(ids):
        assert f'$("{element_id}")' in CARD, (
            f"l'id {element_id} esiste nel template ma non e' in this._el: "
            "il render non lo aggiornerebbe mai"
        )


def test_l_icona_ha_un_contenitore_suo_dentro_il_pulsante():
    """La lettera non deve essere spazzata via dallo swap play/stop.

    Il render sostituisce l'icona a ogni cambio di stato: se lo facesse sul
    pulsante intero (b.innerHTML) cancellerebbe anche la lettera accanto.
    """
    assert not re.search(r"\bb\.innerHTML\s*=", CARD), (
        "l'icona viene ancora scritta sull'intero pulsante: cancellerebbe la lettera"
    )


def test_la_lettera_sopra_il_pulsante_e_stata_rimossa():
    """La vecchia resa (lettera sopra il cerchio) non deve lasciare residui."""
    for residue in ('class="chid"', "chid-l1", ".chid{", "chidL1"):
        assert residue not in CARD, f"residuo della resa precedente: {residue}"


# ── Entita' "Nome linea": prefisso unique_id condiviso con text.py ──
#
# La card cerca queste entita' per prefisso in UID_PREFIX_RULES; l'integrazione
# lo costruisce con line_name_uid_prefix(). Se i due divergono la card non le
# trova e ricade silenziosamente su "Linea A": nessun errore, solo il nome che
# non compare. Da qui il test.
def _uid_prefix_rules() -> dict:
    """Le righe di UID_PREFIX_RULES della card, come {chiave: (dominio, prefisso)}."""
    block = re.search(r"const UID_PREFIX_RULES = \[(.*?)\];", CARD, re.S)
    assert block, "UID_PREFIX_RULES non trovata nella card"
    rows = re.findall(r'\["([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\]', block.group(1))
    return {key: (domain, prefix) for key, domain, prefix in rows}


@pytest.mark.parametrize("channel", ["1", "2"])
def test_la_card_cerca_i_nomi_linea_col_prefisso_dell_integrazione(channel: str):
    rules = _uid_prefix_rules()
    key = f"line_name_{channel}"
    assert key in rules, f"{key} manca da UID_PREFIX_RULES: la card non lo risolverebbe"
    domain, prefix = rules[key]
    assert domain == "text", f"{key} risolto nel dominio {domain!r}, atteso 'text'"
    assert prefix == line_name_uid_prefix(channel)


@pytest.mark.parametrize("channel", ["1", "2"])
def test_i_nomi_linea_sono_chiavi_opzionali(channel: str):
    """Un'integrazione piu' vecchia non deve far comparire il banner di config."""
    key = f"line_name_{channel}"
    for name in ("ENTITY_KEYS", "OPTIONAL_KEYS"):
        block = re.search(rf"const {name} = \[(.*?)\];", CARD, re.S)
        assert block, f"{name} non trovata nella card"
        assert f'"{key}"' in block.group(1), f"{key} manca da {name}"
    runtime = re.search(r"const RUNTIME_KEYS = \[(.*?)\];", CARD, re.S)
    assert f'"{key}"' not in runtime.group(1), (
        f"{key} e' in RUNTIME_KEYS: la sua assenza farebbe comparire il banner "
        "di configurazione su ogni card collegata a un'integrazione precedente"
    )
