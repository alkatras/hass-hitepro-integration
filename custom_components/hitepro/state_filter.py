"""State filter for Relay-LED3S channels.

The gateway answers a Reload with a dump of its own cache. For Relay-LED3S
modules that cache is unreliable: dim levels come back scaled down or as 0
(observed mapping 32->2, 40->15, 60->43, 85->78, 100->100), and after an
unanswered command the cache is reset to 0. Home Assistant would then show a
lit lamp as off.

A genuine module answer always carries ``<channel>_temperatureMK`` next to the
channel values: ``ch1, ch1_temperatureMK, ch2, ch2_temperatureMK, ...``, each
pair tens of milliseconds apart. A Reload dump never carries it, and a dump can
arrive a second after a real answer. So a channel value is republished only
when a ``_temperatureMK`` of the same module arrives *after* it within
``PAIR_WINDOW`` seconds. Lights of filtered channels read their state from the
proxy topic instead of the gateway topic.

The filter recognises channels by their control id alone, so it works even
when the gateway config could not be fetched at start (the lights then still
exist from retained discovery configs).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from .const import LED3S_PREFIX, PROXY_STATE_PREFIX, WB_CTRL_TOPIC

_LOGGER = logging.getLogger(__name__)

PAIR_WINDOW = 1.0
TEMP_SUFFIX = "_temperatureMK"
_CHANNEL_RE = re.compile(rf"^{re.escape(LED3S_PREFIX)}[^_]+_\d+$")


def module_of(control_id: str) -> str:
    """Return the module key, e.g. Relay-LED3S_AA42FCF1 for Relay-LED3S_AA42FCF1_2."""
    return "_".join(control_id.split("_")[:2])


def proxy_topic(control_id: str) -> str:
    return f"{PROXY_STATE_PREFIX}/{control_id}"


def is_filtered_control(control_id: str) -> bool:
    """A Relay-LED3S channel value, e.g. Relay-LED3S_AA42FCF1_2."""
    return bool(_CHANNEL_RE.match(control_id))


class StatePairing:
    """Pure pairing logic, no Home Assistant imports (testable on a journal)."""

    def __init__(self) -> None:
        self.known: dict[str, str] = {}
        self._pending: dict[str, list[tuple[float, str, str]]] = {}

    def set_known(self, control_id: str, value: str) -> None:
        self.known[control_id] = value

    def _emit(self, control_id: str, value: str, out: list[tuple[str, str]]) -> None:
        if self.known.get(control_id) != value:
            self.known[control_id] = value
            out.append((control_id, value))

    def expire(self, now: float) -> None:
        for module, items in list(self._pending.items()):
            kept = [item for item in items if now - item[0] <= PAIR_WINDOW]
            if kept:
                self._pending[module] = kept
            else:
                del self._pending[module]

    def on_message(self, control_id: str, value: str, now: float) -> list[tuple[str, str]]:
        """Feed one gateway message, return (control_id, value) pairs to publish."""
        out: list[tuple[str, str]] = []
        self.expire(now)

        if control_id.endswith(TEMP_SUFFIX):
            base = control_id[: -len(TEMP_SUFFIX)]
            if not is_filtered_control(base):
                return out
            module = module_of(base)
            for _t, pending_id, pending_value in self._pending.pop(module, []):
                self._emit(pending_id, pending_value, out)
            return out

        if not is_filtered_control(control_id):
            return out

        module = module_of(control_id)
        if control_id not in self.known:
            # Nothing known yet (first start, new device): seed once so the
            # entity is not stuck unknown. Real answers correct it later.
            self._emit(control_id, value, out)
        else:
            self._pending.setdefault(module, []).append((now, control_id, value))
        return out


class LED3SStateFilter:
    """Home Assistant wrapper: subscribes to gateway topics, publishes proxy states."""

    def __init__(self, hass: Any) -> None:
        self._hass = hass
        self._pairing = StatePairing()
        self._unsubs: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        from homeassistant.components import mqtt

        await mqtt.async_wait_for_mqtt_client(self._hass)

        async def _proxy_received(msg: Any) -> None:
            control_id = msg.topic.rsplit("/", 1)[-1]
            payload = msg.payload if isinstance(msg.payload, str) else msg.payload.decode()
            if payload != "":
                self._pairing.set_known(control_id, payload)

        async def _gateway_received(msg: Any) -> None:
            control_id = msg.topic.rsplit("/", 1)[-1]
            if not control_id.startswith(LED3S_PREFIX):
                return
            payload = msg.payload if isinstance(msg.payload, str) else msg.payload.decode()
            for out_id, value in self._pairing.on_message(control_id, payload, time.monotonic()):
                _LOGGER.debug("LED3S state confirmed: %s=%s", out_id, value)
                await mqtt.async_publish(self._hass, proxy_topic(out_id), value, qos=1, retain=True)

        # Proxy first: its retained values tell which channels are already known.
        self._unsubs.append(await mqtt.async_subscribe(self._hass, f"{PROXY_STATE_PREFIX}/+", _proxy_received, qos=1))
        self._unsubs.append(await mqtt.async_subscribe(self._hass, f"{WB_CTRL_TOPIC}/+", _gateway_received, qos=0))
        _LOGGER.info("LED3S state filter started")

    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
