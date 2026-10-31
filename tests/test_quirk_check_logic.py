"""Decisione del reload one-shot di ZHA al cold boot (quirk_check_logic.py).

Su un avvio a freddo ZHA risolve le quirk dei dispositivi PRIMA che questa
integrazione registri le sue: la SWV-ZF2 resta senza quirk e lo ZBMINIR2 resta
sulla quirk built-in (senza inching). L'integrazione ricarica ZHA una volta per
sessione; qui si verifica la decisione pura, senza Home Assistant.

Come test_history_logic.py, il modulo puro si carica da file: il pacchetto
dell'integrazione non e' importabile qui (il suo __init__ richiede
homeassistant).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
from unittest import mock

from conftest import build_zigpy_device
import pytest
import sonoff_zbminir2
from zha.quirks import QUIRK_REGISTRY_ENTRY_ATTR
from zhaquirks import ZHA_DEVICE_REGISTRY
import zhaquirks.sonoff.zbminir2 as builtin_zbminir2

_PACKAGE_DIR = pathlib.Path(__file__).parents[1] / "custom_components/zha_sonoff_quirks"


def _load(name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PACKAGE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    # @dataclass risolve le annotazioni tramite sys.modules[cls.__module__].
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


logic = _load("_zsq_quirk_check_logic", "quirk_check_logic.py")
const = _load("swv_const_quirk_check", "const.py")
decide_zha_reload = logic.decide_zha_reload

#: Segnaposti per le entry del registry: la decisione confronta per identita'.
OURS = object()
BUILTIN = object()
#: Una terza quirk ZBMINIR2, es. da custom_quirks_path.
CUSTOM = object()
ATTR = "_quirk_registry_entry"

#: Signature dell'endpoint 1 dei due ZBMINIR2 reali (come in
#: test_sonoff_zbminir2.py; il device_type e' un segnaposto).
ZBMINIR2_SIGNATURE = {
    1: {
        "profile_id": 260,
        "device_type": 0x0100,
        "in_clusters": [
            0x0000, 0x0003, 0x0004, 0x0005, 0x0006, 0x0B05, 0xFC57, 0xFC11,
        ],
        "out_clusters": [0x0003, 0x0006, 0x0019],
    },
}


def _zigpy(manufacturer="SONOFF", model="ZBMINIR2", entry=None, attr=ATTR):
    """Dispositivo zigpy finto; senza `entry` l'attributo non esiste proprio."""
    device = types.SimpleNamespace(manufacturer=manufacturer, model=model)
    if entry is not None:
        setattr(device, attr, entry)
    return device


def _swv(name="Valvola", model="SWV-ZF2", quirk_applied=False):
    """Dispositivo ZHA della valvola (il controllo SWV usa model/quirk_applied)."""
    return types.SimpleNamespace(
        name=name,
        model=model,
        quirk_applied=quirk_applied,
        device=_zigpy(model=model),
    )


def _relay(name="Relay", entry=BUILTIN, manufacturer="SONOFF", model="ZBMINIR2"):
    """Dispositivo ZHA di uno ZBMINIR2 risolto su `entry`."""
    return types.SimpleNamespace(
        name=name,
        model=model,
        quirk_applied=entry is not None,
        device=_zigpy(manufacturer, model, entry),
    )


def _decide(devices, **overrides):
    kwargs = {
        "already_reloaded": False,
        "swv_models": const.SWV_MODELS,
        "zbminir2_model": const.ZBMINIR2_MODEL_INFO,
        "zbminir2_builtin_entry": BUILTIN,
        "zbminir2_effective": True,
        "entry_attr": ATTR,
    }
    kwargs.update(overrides)
    return decide_zha_reload(devices, **kwargs)


# --------------------------------------------------------------------------- #
# SWV-ZF2: comportamento invariato
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", ["SWV-ZF2", "SWV-ZF2U", "SWV-ZF2E"])
def test_unquirked_swv_triggers_reload(model):
    valve = _swv(model=model)

    decision = _decide([valve])

    assert decision.reload is True
    assert decision.swv_unquirked == (valve,)
    assert decision.zbminir2_on_builtin_quirk == ()


def test_quirked_swv_does_not_trigger_reload():
    assert _decide([_swv(quirk_applied=True)]).reload is False


def test_swv_without_quirk_applied_attribute_is_skipped():
    # Fail-closed sulla deriva dell'API di ZHA: solo un quirk_applied
    # ESPLICITAMENTE False conta come "senza quirk".
    valve = types.SimpleNamespace(name="Valvola", model="SWV-ZF2")

    assert _decide([valve]).reload is False


def test_swv_check_does_not_depend_on_the_zbminir2_override():
    valve = _swv()

    for overrides in (
        {"zbminir2_builtin_entry": None, "zbminir2_effective": False},
        {"entry_attr": None},
        {"zbminir2_effective": False},
    ):
        decision = _decide([valve], **overrides)
        assert decision.reload is True
        assert decision.swv_unquirked == (valve,)


def test_other_models_are_ignored():
    other = types.SimpleNamespace(
        name="Lampada", model="TS0601", quirk_applied=False, device=_zigpy(
            manufacturer="_TZE200_abc", model="TS0601"
        )
    )

    assert _decide([other]).reload is False


# --------------------------------------------------------------------------- #
# ZBMINIR2: override dell'inching
# --------------------------------------------------------------------------- #


def test_zbminir2_on_builtin_entry_triggers_reload():
    relay = _relay(entry=BUILTIN)

    decision = _decide([relay])

    assert decision.reload is True
    assert decision.zbminir2_on_builtin_quirk == (relay,)
    assert decision.swv_unquirked == ()


def test_zbminir2_on_our_entry_does_not_trigger_reload():
    assert _decide([_relay(entry=OURS)]).reload is False


def test_zbminir2_on_a_third_quirk_does_not_trigger_reload():
    # Es. una quirk ZBMINIR2 in custom_quirks_path: ZHA la ri-registra a ogni
    # avvio del gateway e vince di nuovo, quindi un reload non cambierebbe
    # nulla (sarebbe un riavvio della rete Zigbee per niente a ogni boot).
    assert _decide([_relay(entry=CUSTOM)]).reload is False


def test_zbminir2_entries_are_compared_by_identity():
    """Una entry "uguale" alla built-in ma non la stessa non conta (fail-closed).

    Se ZHA un giorno salvasse sul dispositivo una copia della entry, il
    confronto per identita' non scatta: nessun reload spurio a ogni avvio.
    """

    class LooksLikeBuiltin:
        def __eq__(self, other):
            return True

        __hash__ = object.__hash__

    assert _decide([_relay(entry=LooksLikeBuiltin())]).reload is False


def test_override_not_registered_never_triggers_reload():
    decision = _decide(
        [_relay(entry=BUILTIN)],
        zbminir2_builtin_entry=None,
        zbminir2_effective=False,
    )

    assert decision.reload is False


def test_missing_builtin_entry_wins_over_an_inconsistent_effective_flag():
    # Senza la entry built-in non c'e' nulla con cui confrontare: anche un
    # flag "effective" incoerente non deve far partire il reload.
    decision = _decide(
        [_relay(entry=BUILTIN)], zbminir2_builtin_entry=None, zbminir2_effective=True
    )

    assert decision.reload is False


def test_override_not_effective_does_not_trigger_reload():
    # Un'altra quirk ZBMINIR2 registrata dopo la nostra vince: un reload
    # risolverebbe di nuovo su quella, quindi sarebbe un riavvio per niente.
    assert _decide([_relay(entry=BUILTIN)], zbminir2_effective=False).reload is False


@pytest.mark.parametrize("entry_attr", [None, "", 42])
def test_missing_registry_entry_api_skips_the_zbminir2_check(entry_attr):
    assert _decide([_relay(entry=BUILTIN)], entry_attr=entry_attr).reload is False


def test_zha_device_without_zigpy_device_is_skipped():
    relay = types.SimpleNamespace(name="Relay", model="ZBMINIR2", quirk_applied=True)

    assert _decide([relay]).reload is False


def test_zbminir2_without_registry_entry_attribute_is_skipped():
    # Attributo assente: dispositivo senza quirk o attributo rinominato da
    # ZHA. Indistinguibili, quindi niente reload (fail-closed).
    assert _decide([_relay(entry=None)]).reload is False


def test_zbminir2_entry_stored_under_another_name_is_skipped():
    relay = types.SimpleNamespace(
        name="Relay",
        model="ZBMINIR2",
        quirk_applied=True,
        device=_zigpy(entry=BUILTIN, attr="_renamed_entry"),
    )

    assert _decide([relay]).reload is False


@pytest.mark.parametrize(
    ("manufacturer", "model"),
    [("SONOFF", "MINI-ZBD"), ("eWeLink", "ZBMINIR2"), ("SONOFF", "ZBMINI")],
)
def test_only_sonoff_zbminir2_is_checked(manufacturer, model):
    # L'override si applica solo a SONOFF/ZBMINIR2: per gli altri un reload
    # non cambierebbe nulla.
    relay = _relay(entry=BUILTIN, manufacturer=manufacturer, model=model)

    assert _decide([relay]).reload is False


def test_zbminir2_match_uses_the_zigpy_device_not_the_zha_model():
    # Il registry abbina manufacturer/model del dispositivo zigpy; il model
    # mostrato da ZHA puo' essere riscritto da una quirk dichiarativa.
    relay = _relay(entry=BUILTIN)
    relay.model = "Mini R2"

    assert _decide([relay]).zbminir2_on_builtin_quirk == (relay,)


# --------------------------------------------------------------------------- #
# Un solo reload per sessione
# --------------------------------------------------------------------------- #


def test_already_reloaded_session_never_reloads_again():
    decision = _decide([_swv(), _relay(entry=BUILTIN)], already_reloaded=True)

    assert decision.reload is False
    assert decision.swv_unquirked == ()
    assert decision.zbminir2_on_builtin_quirk == ()


def test_swv_and_zbminir2_share_one_reload():
    valve, relay_1, relay_2 = _swv(), _relay("Scale"), _relay("Garage")

    decision = _decide([valve, _relay("Mio", entry=OURS), relay_1, relay_2])

    assert decision.reload is True
    assert decision.swv_unquirked == (valve,)
    assert decision.zbminir2_on_builtin_quirk == (relay_1, relay_2)


def test_no_devices_no_reload():
    assert _decide([]).reload is False


def test_devices_iterable_is_consumed_once():
    # Il chiamante passa gateway.devices.values(): una sola passata.
    devices = iter([_swv(), _relay(entry=BUILTIN)])

    decision = _decide(devices)

    assert len(decision.swv_unquirked) == 1
    assert len(decision.zbminir2_on_builtin_quirk) == 1


# --------------------------------------------------------------------------- #
# Messaggio di log
# --------------------------------------------------------------------------- #


def test_describe_lists_both_kinds_with_names():
    decision = _decide([_swv("Orto"), _relay("Scale"), _relay("Garage")])

    text = decision.describe()

    assert "SWV" in text and "Orto" in text
    assert "ZBMINIR2" in text and "Scale, Garage" in text


def test_describe_mentions_only_what_triggered():
    assert "ZBMINIR2" not in _decide([_swv("Orto")]).describe()
    assert "SWV" not in _decide([_relay("Scale")]).describe()


def test_describe_falls_back_to_the_device_when_nameless():
    relay = types.SimpleNamespace(
        model="ZBMINIR2", quirk_applied=True, device=_zigpy(entry=BUILTIN)
    )

    assert str(relay) in _decide([relay]).describe()


# --------------------------------------------------------------------------- #
# Stato dell'override e API di ZHA (letti dall'integrazione)
# --------------------------------------------------------------------------- #


def test_registry_entry_attr_is_the_zha_constant():
    assert logic.registry_entry_attr() == QUIRK_REGISTRY_ENTRY_ATTR


def test_registry_entry_attr_is_none_when_zha_api_is_missing():
    with mock.patch.dict(sys.modules, {"zha.quirks": None}):
        assert logic.registry_entry_attr() is None


def test_registry_entry_attr_is_none_when_constant_is_gone():
    fake = types.ModuleType("zha.quirks")
    with mock.patch.dict(sys.modules, {"zha.quirks": fake}):
        assert logic.registry_entry_attr() is None


def test_override_state_of_the_real_module():
    builtin_entry, effective = logic.zbminir2_override_state(sonoff_zbminir2)

    # La entry restituita e' quella built-in che l'override sostituisce: e'
    # l'unica su cui un reload sposta davvero il relè sull'override.
    assert builtin_entry is sonoff_zbminir2.BUILTIN_ENTRY
    assert builtin_entry.source.module == builtin_zbminir2.__name__
    assert effective is True


def test_override_state_when_module_failed_to_import():
    # quirks/__init__.py lascia sonoff_zbminir2 = None se l'import fallisce.
    assert logic.zbminir2_override_state(None) == (None, False)


def _module(registry_entry=OURS, builtin_entry=BUILTIN, effective=lambda: True):
    return types.SimpleNamespace(
        REGISTRY_ENTRY=registry_entry,
        BUILTIN_ENTRY=builtin_entry,
        quirk_is_effective=effective,
    )


def test_override_state_when_not_registered():
    assert logic.zbminir2_override_state(_module(registry_entry=None)) == (
        None,
        False,
    )


@pytest.mark.parametrize("builtin_entry", [None, OURS])
def test_override_state_without_a_distinct_builtin_entry(builtin_entry):
    # Modulo di una versione che non espone la entry built-in, o incoerente.
    module = _module(builtin_entry=builtin_entry)

    assert logic.zbminir2_override_state(module) == (None, False)


def test_override_state_of_a_module_without_builtin_entry_attribute():
    module = types.SimpleNamespace(REGISTRY_ENTRY=OURS, quirk_is_effective=lambda: True)

    assert logic.zbminir2_override_state(module) == (None, False)


def test_override_state_when_self_check_raises():
    def boom():
        raise RuntimeError("registry changed")

    assert logic.zbminir2_override_state(_module(effective=boom)) == (BUILTIN, False)


def test_override_state_requires_a_literal_true():
    module = _module(effective=lambda: mock.MagicMock())

    assert logic.zbminir2_override_state(module) == (BUILTIN, False)


def test_override_state_detects_a_later_registration():
    from zha.quirks import DeviceMatch, ModelInfo, QuirkRegistryEntry

    later = QuirkRegistryEntry(
        device_match=DeviceMatch(applies_to=(ModelInfo("SONOFF", "ZBMINIR2"),)),
    )
    with ZHA_DEVICE_REGISTRY.preserve_state():
        ZHA_DEVICE_REGISTRY.register(later)
        assert logic.zbminir2_override_state(sonoff_zbminir2) == (
            sonoff_zbminir2.BUILTIN_ENTRY,
            False,
        )


# --------------------------------------------------------------------------- #
# Scenario reale: cold boot con il registry vero
# --------------------------------------------------------------------------- #


def _zha_device(zigpy_device, name):
    """Device ZHA minimale: espone il dispositivo zigpy come `.device`."""
    return types.SimpleNamespace(
        name=name,
        model=zigpy_device.model,
        quirk_applied=hasattr(zigpy_device, QUIRK_REGISTRY_ENTRY_ATTR),
        device=zigpy_device,
    )


def _decide_live(devices):
    builtin_entry, effective = logic.zbminir2_override_state(sonoff_zbminir2)
    return decide_zha_reload(
        devices,
        already_reloaded=False,
        swv_models=const.SWV_MODELS,
        zbminir2_model=const.ZBMINIR2_MODEL_INFO,
        zbminir2_builtin_entry=builtin_entry,
        zbminir2_effective=effective,
        entry_attr=logic.registry_entry_attr(),
    )


def test_cold_boot_builtin_resolution_reloads_then_settles(app):
    """Prima del reload: entry built-in -> reload. Dopo: entry nostra -> stop."""
    with ZHA_DEVICE_REGISTRY.preserve_state():
        # Cold boot: ZHA risolve i dispositivi quando c'e' solo la built-in.
        ZHA_DEVICE_REGISTRY.remove(sonoff_zbminir2.REGISTRY_ENTRY)
        before = ZHA_DEVICE_REGISTRY.resolve(
            build_zigpy_device(app, model="ZBMINIR2", signature=ZBMINIR2_SIGNATURE)
        )
    assert getattr(before, QUIRK_REGISTRY_ENTRY_ATTR).source.module == (
        builtin_zbminir2.__name__
    )

    # Poi l'integrazione ha registrato l'override: va ricaricato ZHA.
    relay = _zha_device(before, "Scale")
    decision = _decide_live([relay])
    assert decision.reload is True
    assert decision.zbminir2_on_builtin_quirk == (relay,)

    # Il reload ricrea i dispositivi zigpy, che ora risolvono sull'override.
    after = ZHA_DEVICE_REGISTRY.resolve(
        build_zigpy_device(app, model="ZBMINIR2", signature=ZBMINIR2_SIGNATURE)
    )
    assert _decide_live([_zha_device(after, "Scale")]).reload is False


def test_cold_boot_with_a_custom_zbminir2_quirk_does_not_reload(app):
    """Quirk ZBMINIR2 in custom_quirks_path: nessun reload inutile a ogni boot.

    Al cold boot zhaquirks.setup() registra la quirk custom dopo la built-in,
    quindi i relè risolvono su quella; poi questa integrazione registra
    l'override, che in quel momento vince. Ma ogni avvio del gateway ZHA
    rimuove e ri-esegue le quirk custom (che tornano in testa): un reload
    risolverebbe di nuovo sulla custom. Quindi niente reload.
    """
    from zha.quirks import DeviceMatch, ModelInfo, QuirkRegistryEntry

    custom = QuirkRegistryEntry(
        device_match=DeviceMatch(applies_to=(ModelInfo("SONOFF", "ZBMINIR2"),)),
    )
    with ZHA_DEVICE_REGISTRY.preserve_state():
        ZHA_DEVICE_REGISTRY.remove(sonoff_zbminir2.REGISTRY_ENTRY)
        ZHA_DEVICE_REGISTRY.register(custom)
        resolved = ZHA_DEVICE_REGISTRY.resolve(
            build_zigpy_device(app, model="ZBMINIR2", signature=ZBMINIR2_SIGNATURE)
        )
        assert getattr(resolved, QUIRK_REGISTRY_ENTRY_ATTR) is custom

        # L'integrazione registra l'override dopo: ora vince lui...
        ZHA_DEVICE_REGISTRY.register(sonoff_zbminir2.REGISTRY_ENTRY)
        assert sonoff_zbminir2.quirk_is_effective() is True

        # ...ma il relè e' sulla custom, non sulla built-in: nessun reload.
        assert _decide_live([_zha_device(resolved, "Garage")]).reload is False


def test_real_swv_device_is_not_mistaken_for_zbminir2(quirked_device):
    valve = _zha_device(quirked_device, "Valvola")

    assert _decide_live([valve]).reload is False


# --------------------------------------------------------------------------- #
# Costanti dell'integrazione
# --------------------------------------------------------------------------- #


def test_zbminir2_model_info_matches_the_quirk():
    assert const.ZBMINIR2_MODEL_INFO == (
        sonoff_zbminir2.MANUFACTURER,
        sonoff_zbminir2.MODEL,
    )


def test_manifest_and_const_versions_match():
    manifest = json.loads((_PACKAGE_DIR / "manifest.json").read_text())

    assert manifest["version"] == const.VERSION
