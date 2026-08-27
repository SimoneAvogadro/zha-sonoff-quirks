"""Costruttori degli unique_id delle entita' create dall'integrazione.

La card risolve le entita' proprietarie dell'integrazione per PREFISSO del loro
unique_id, perche' la coda e' l'entity_id dello switch e quello l'utente puo'
rinominarlo. Il prefisso e' quindi un contratto fra due repository di codice
(la platform Python e il JavaScript della card): questi test lo fissano da un
lato, test_card_labels.py lo verifica dall'altro.

Come test_history_logic.py, si carica il solo modulo puro.
"""

import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "swv_const",
    pathlib.Path(__file__).parents[1] / "custom_components/zha_sonoff_quirks/const.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
CHANNELS = _module.CHANNELS
DOMAIN = _module.DOMAIN
line_name_uid_prefix = _module.line_name_uid_prefix
line_name_unique_id = _module.line_name_unique_id


def test_il_prefisso_contiene_dominio_e_canale():
    assert line_name_uid_prefix("1") == f"{DOMAIN}_line_name_ch1"
    assert line_name_uid_prefix("2") == f"{DOMAIN}_line_name_ch2"


def test_l_unique_id_appende_l_entity_id_dello_switch():
    assert (
        line_name_unique_id("2", "switch.swv_zf2_switch_2")
        == f"{DOMAIN}_line_name_ch2_switch.swv_zf2_switch_2"
    )


@pytest.mark.parametrize("channel", ["1", "2"])
def test_l_unique_id_inizia_sempre_col_prefisso(channel: str):
    # L'invariante che rende possibile il match per prefisso della card: se
    # l'entity_id dello switch finisse davanti al canale, la risoluzione
    # fallirebbe per ogni valvola rinominata.
    uid = line_name_unique_id(channel, "switch.qualunque_nome")
    assert uid.startswith(line_name_uid_prefix(channel))


def test_i_prefissi_dei_due_canali_sono_distinti():
    prefixes = {line_name_uid_prefix(c) for c in CHANNELS}
    assert len(prefixes) == len(CHANNELS)
    # ...e nessuno e' prefisso dell'altro: sarebbero indistinguibili al match.
    for a in prefixes:
        for b in prefixes:
            assert a == b or not a.startswith(b)
