"""Contratto sul canale: valore interno "1"/"2", lettere A/B come alias.

Le due uscite della valvola sono serigrafate A e B sul dispositivo, ma tutto
il codice (API dei servizi, unique_id dei sensori, chiavi del run log,
prefissi con cui la card risolve le entita') usa "1"/"2". Le lettere sono
quindi uno strato di sola presentazione PIU' un alias accettato in ingresso:
chi scrive `channel: "A"` nello YAML deve ottenere lo stesso effetto di
`channel: "1"`, senza che nulla a valle veda mai una lettera.

Come test_history_logic.py, si carica il solo modulo puro: il pacchetto
dell'integrazione non e' importabile qui (il suo __init__ richiede
homeassistant) e aggiungerne la directory al pythonpath esporrebbe tutti i
suoi moduli come nomi top-level.
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
CHANNEL_LABELS = _module.CHANNEL_LABELS
normalize_channel = _module.normalize_channel
line_option_label = _module.line_option_label


# ── normalize_channel ──
@pytest.mark.parametrize("value", ["1", "2"])
def test_i_valori_canonici_restano_invariati(value):
    assert normalize_channel(value) == value


def test_le_lettere_del_pannello_sono_alias():
    assert normalize_channel("A") == "1"
    assert normalize_channel("B") == "2"


def test_le_lettere_minuscole_sono_accettate():
    assert normalize_channel("a") == "1"
    assert normalize_channel("b") == "2"


def test_gli_interi_sono_accettati_come_prima():
    # Retrocompatibilita': lo schema precedente usava vol.Coerce(str), quindi
    # un'automazione che passa il numero 1 invece della stringa "1" funziona.
    assert normalize_channel(1) == "1"
    assert normalize_channel(2) == "2"


def test_gli_spazi_intorno_sono_ignorati():
    assert normalize_channel(" b ") == "2"


@pytest.mark.parametrize("value", ["3", "0", "C", "AB", "", "   ", None, [], 1.5])
def test_i_valori_non_validi_danno_none(value):
    assert normalize_channel(value) is None


# ── mappa etichette ──
def test_ogni_canale_ha_esattamente_una_etichetta():
    assert set(CHANNEL_LABELS) == set(CHANNELS) == {"1", "2"}
    assert sorted(CHANNEL_LABELS.values()) == ["A", "B"]


def test_ogni_etichetta_e_alias_del_proprio_canale():
    # L'invariante che tiene allineati lo strato di presentazione (card,
    # traduzioni) e lo strato di input (alias YAML): se un giorno le etichette
    # diventassero "1"/"2" o "sinistra"/"destra", questo test cade.
    for channel, label in CHANNEL_LABELS.items():
        assert normalize_channel(label) == channel


# ── Etichetta di una linea nel selettore della device action ──
#
# Qui l'etichetta la costruiamo noi in Python, e HA non la traduce: quindi
# deve restare NEUTRA rispetto alla lingua. Solo la lettera serigrafata, che
# e' universale, piu' il nome che l'utente ha scelto. La parola "Linea" vive
# nel nome del campo, che invece e' tradotto.
def test_senza_nome_l_etichetta_e_la_sola_lettera():
    assert line_option_label("A", "") == "A"
    assert line_option_label("B", None) == "B"


def test_col_nome_lettera_e_nome_sono_uniti_da_un_trattino():
    assert line_option_label("A", "Prato davanti") == "A — Prato davanti"


def test_gli_spazi_intorno_al_nome_sono_ignorati():
    assert line_option_label("B", "  Giardino  ") == "B — Giardino"
    assert line_option_label("B", "   ") == "B"


def test_l_etichetta_non_contiene_parole_traducibili():
    # Se un giorno qualcuno ci mettesse "Linea", la device action mostrerebbe
    # italiano a un utente inglese: il selettore non passa dalle traduzioni.
    label = line_option_label("A", "Prato davanti")
    for word in ("Linea", "Line", "线路", "Channel"):
        assert word not in label
