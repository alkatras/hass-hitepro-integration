from __future__ import annotations

import json
import logging
from datetime import timedelta

import aiohttp
import ssl
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import CONF_API_KEY, CONF_LIGHT_DEVICES, CONF_URL, DEFAULT_SCAN_INTERVAL, DOMAIN, SERVICE_REFRESH
from .discovery import (
    HiteEntity,
    async_publish_discovery,
    async_remove_discovery,
    async_trigger_reload,
    build_entities,
    build_gateway_entity,
    build_legacy_cleanup_entities,
    parse_hitepro_js,
)

from .discovery import CLEANUP_VERSION
from .state_filter import LED3SStateFilter

_LOGGER = logging.getLogger(__name__)

# A failed config fetch at start leaves the integration without entities
# until the next periodic refresh (scan_interval, often an hour). Retry
# sooner until the first fetch succeeds.
START_RETRY_SECONDS = 90

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "entities": [],
        "unsub": None,
        "cleanup_version": 0,
        "periodic_reload_done": False,
        "state_filter": LED3SStateFilter(hass),
        "retry_unsub": None,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_refresh(call: ServiceCall) -> None:
        for entry_item in hass.config_entries.async_entries(DOMAIN):
            await _async_refresh_entry(hass, entry_item, force_reload=True)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)

    # The filter does not depend on the gateway config: start it first, so a
    # failed fetch below does not leave Relay-LED3S lights without state.
    await hass.data[DOMAIN][entry.entry_id]["state_filter"].async_start()
    if not await _async_refresh_entry(hass, entry):
        _schedule_start_retry(hass, entry)
    _start_refresh_timer(hass, entry)

    entry.async_on_unload(
        hass.data[DOMAIN][entry.entry_id].get("unsub", lambda: None)
    )

    entry.async_on_unload(
        entry.add_update_listener(_async_options_updated)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].get(entry.entry_id, {})
    unsub = data.get("unsub")
    if unsub:
        unsub()
    retry_unsub = data.get("retry_unsub")
    if retry_unsub:
        retry_unsub()

    state_filter: LED3SStateFilter | None = data.get("state_filter")
    if state_filter:
        state_filter.async_stop()

    entities: list[HiteEntity] = data.get("entities", [])
    await async_remove_discovery(hass, entities)

    hass.data[DOMAIN].pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _start_refresh_timer(hass, entry)


def _start_refresh_timer(hass: HomeAssistant, entry: ConfigEntry) -> None:
    data = hass.data[DOMAIN].get(entry.entry_id, {})
    unsub = data.get("unsub")
    if unsub:
        unsub()

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    _LOGGER.info("Starting refresh timer for %s, interval=%ds", entry.entry_id, scan_interval)

    async def _timer_callback(_now):
        _LOGGER.info("Timer fired for %s", entry.entry_id)
        await _async_refresh_entry(hass, entry, periodic=True)

    unsub = async_track_time_interval(
        hass,
        _timer_callback,
        timedelta(seconds=scan_interval),
    )
    hass.data[DOMAIN][entry.entry_id]["unsub"] = unsub


def _schedule_start_retry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data is None:
        return
    _LOGGER.warning("HiTE PRO config not loaded, retrying in %ds", START_RETRY_SECONDS)

    async def _retry(_now) -> None:
        data["retry_unsub"] = None
        if data.get("entities"):
            return  # a periodic refresh or the service already loaded it
        if not await _async_refresh_entry(hass, entry):
            _schedule_start_retry(hass, entry)

    data["retry_unsub"] = async_call_later(hass, START_RETRY_SECONDS, _retry)


def _entities_signature(entities: list[HiteEntity]) -> dict[tuple[str, str], str]:
    return {(e.domain, e.object_id): json.dumps(e.config, sort_keys=True, ensure_ascii=False) for e in entities}


async def _async_refresh_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    periodic: bool = False,
    force_reload: bool = False,
) -> bool:
    """Fetch the gateway config and publish discovery. False if the fetch failed."""
    _LOGGER.info("Refreshing entry %s", entry.entry_id)
    url: str = entry.data.get(CONF_URL, "")
    api_key: str = entry.data.get(CONF_API_KEY, "")
    full_url = f"{url}?key={api_key}" if api_key else url

    try:
        config_text = await _async_fetch_config(full_url)
    except Exception as err:
        _LOGGER.error("Failed to fetch HiTE PRO config: %s", err)
        return False

    try:
        data = parse_hitepro_js(config_text)
    except Exception as err:
        _LOGGER.error("Failed to parse HiTE PRO config: %s", err)
        return False

    cells = data.get("cells", {})
    url: str = entry.data.get(CONF_URL, "")
    light_devices: list[str] = entry.options.get(CONF_LIGHT_DEVICES, [])
    new_entities = build_entities(cells, light_devices=light_devices)
    gateway_entity = build_gateway_entity(url)
    new_entities.append(gateway_entity)

    data_store = hass.data[DOMAIN].get(entry.entry_id, {})
    old_entities: list[HiteEntity] = data_store.get("entities", [])
    old_ids = {(e.domain, e.object_id) for e in old_entities}
    new_ids = {(e.domain, e.object_id) for e in new_entities}

    removed = [e for e in old_entities if (e.domain, e.object_id) not in new_ids]
    cleanup_entities: list[HiteEntity] = []
    current_version = data_store.get("cleanup_version", 0)
    if current_version < CLEANUP_VERSION:
        cleanup_entities = build_legacy_cleanup_entities(cells, light_devices=light_devices)
        cleanup_entities = [e for e in cleanup_entities if (e.domain, e.object_id) not in new_ids]

    if removed:
        await async_remove_discovery(hass, removed)

    if cleanup_entities:
        await async_remove_discovery(hass, cleanup_entities)

    await async_publish_discovery(hass, new_entities)

    # Reload makes the gateway dump its cache, and that cache lies about
    # Relay-LED3S levels. Send it only when the device set or a discovery
    # config changed, on the first periodic refresh after start (initial
    # state sync, as before), or when the refresh service asks for it.
    changed = _entities_signature(old_entities) != _entities_signature(new_entities)
    first_periodic = periodic and not data_store.get("periodic_reload_done", False)
    if old_entities and (force_reload or changed or first_periodic):
        await async_trigger_reload(hass)
        if periodic:
            data_store["periodic_reload_done"] = True
    elif old_entities:
        _LOGGER.debug("Nothing changed, gateway Reload skipped")

    hass.data[DOMAIN][entry.entry_id]["entities"] = new_entities
    if cleanup_entities:
        hass.data[DOMAIN][entry.entry_id]["cleanup_version"] = CLEANUP_VERSION
    _LOGGER.info(
        "HiTE PRO refreshed: %d entities (%d added, %d removed, %d legacy cleaned)",
        len(new_entities),
        len(new_ids - old_ids),
        len(removed),
        len(cleanup_entities),
    )
    return True


async def _async_fetch_config(url: str) -> str:
    if url.startswith("https://"):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    else:
        connector = aiohttp.TCPConnector()
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()