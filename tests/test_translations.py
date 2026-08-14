"""Contratto i18n sui nomi delle azioni mostrati nel selettore di HA.

Il selettore azioni di Home Assistant rende ogni voce come una riga sola,
`«Nome integrazione»: «Nome azione»`, troncata con ellissi: su mobile stanno
~28 caratteri. Con il nome integrazione a consumarne una parte, due azioni che
condividono il prefisso diventano indistinguibili (era il caso di
"Irrigazione a litri" / "Irrigazione a minuti", entrambe troncate a
"Irrigazi…").

La regola che questi test proteggono: in OGNI lingua, due azioni della stessa
integrazione devono differire entro i primi caratteri di `name` e di
`description` (la description e' la seconda riga, il fallback quando la prima
tronca). Vale anche per le traduzioni future, che e' il motivo per cui questo
e' un test e non una nota nel README.
"""

import itertools
import json
import pathlib
import re

import pytest

_COMPONENT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "zha_sonoff_quirks"
)

#: Quanti caratteri iniziali devono bastare a distinguere due azioni. Stimato
#: sul budget del selettore mobile meno il prefisso dell'integrazione.
DISCRIMINATOR_PREFIX = 8


def _catalogs() -> dict[str, dict]:
    """Ritorna {etichetta: json} per strings.json e ogni translations/*.json."""
    files = {"strings.json": _COMPONENT / "strings.json"}
    files.update(
        {f"translations/{p.name}": p
         for p in sorted((_COMPONENT / "translations").glob("*.json"))}
    )
    return {label: json.loads(p.read_text(encoding="utf-8"))
            for label, p in files.items()}


CATALOGS = _catalogs()


@pytest.mark.parametrize("label", CATALOGS)
@pytest.mark.parametrize("field", ["name", "description"])
def test_azioni_distinguibili_dal_prefisso(label: str, field: str) -> None:
    """Due azioni non devono condividere i primi caratteri di name/description."""
    services = CATALOGS[label]["services"]
    for a, b in itertools.combinations(sorted(services), 2):
        pa = services[a][field][:DISCRIMINATOR_PREFIX].casefold()
        pb = services[b][field][:DISCRIMINATOR_PREFIX].casefold()
        assert pa != pb, (
            f"{label}: '{a}' e '{b}' condividono i primi "
            f"{DISCRIMINATOR_PREFIX} caratteri di '{field}' ({pa!r}); "
            "nel selettore azioni troncato risultano identici"
        )


@pytest.mark.parametrize("label", CATALOGS)
def test_traduzioni_allineate_a_strings(label: str) -> None:
    """Ogni traduzione espone le stesse chiavi di strings.json."""
    def keys(node, prefix=""):
        if not isinstance(node, dict):
            return {prefix}
        return set().union(*(keys(v, f"{prefix}.{k}") for k, v in node.items())) \
            if node else {prefix}

    assert keys(CATALOGS[label]) == keys(CATALOGS["strings.json"]), (
        f"{label} e strings.json hanno strutture diverse"
    )


def test_ogni_servizio_ha_le_stringhe() -> None:
    """I servizi dichiarati in services.yaml esistono in tutti i cataloghi."""
    # Parsing a regex sui soli nomi di servizio (colonna 0): pyyaml non e' tra
    # le dipendenze di test e non vale aggiungerlo per tre righe.
    declared = set(
        re.findall(
            r"^([a-z_]+):$",
            (_COMPONENT / "services.yaml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert declared, "nessun servizio trovato in services.yaml"
    for label, catalog in CATALOGS.items():
        assert set(catalog["services"]) == declared, (
            f"{label}: servizi {set(catalog['services'])} != "
            f"services.yaml {declared}"
        )
