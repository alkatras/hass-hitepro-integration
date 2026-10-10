# Changelog

## 1.3.1 (2026-10-10)

### Fixed

- **Relay-LED3S lights frozen after a failed start**: the state filter took its channel list from the gateway config, so when the config fetch failed at Home Assistant start (gateway busy or unreachable) the filter ran with no channels and dropped every Relay-LED3S answer. The lights still existed from retained discovery configs but kept their old state until the next periodic refresh, up to `scan_interval` later. The filter now recognises channels by control id (`Relay-LED3S_<serial>_<n>`) and starts before the config fetch. Replayed on 14 days of gateway traffic (358 Reloads): 0 wrong states, same as 1.3.0 with a loaded config; 1.3.0 with an empty channel list got all 3837 checks wrong.
- **No retry after a failed start**: a failed config fetch at start is now retried every 90 s until it succeeds, instead of waiting for the periodic refresh.

## 1.3.0 (2026-10-07)

### Fixed

- **Relay-LED3S lights shown off or dimmer after a gateway Reload**: the gateway answers Reload with a dump of its own cache, and for Relay-LED3S modules that cache is wrong — dim levels come back scaled down or as `0` (observed 32→2, 40→15, 60→43, 85→78, 100→100), and after an unanswered command the cache is reset to `0`. Home Assistant then showed lit lamps as off, scenes skipped them as "already off", and automations took the later correction for a manual switch-on. Relay-LED3S lights now read their state from a proxy topic `hitepro/state/<control_id>` that carries only genuine module answers: a channel value is republished only when a `<channel>_temperatureMK` of the same module follows it within 1 s (real answers always carry it, dumps never do). Replayed on 11 days of gateway traffic (352 Reloads): states wrong 30 s after a Reload went from 290 to 0. Commands still go to the gateway topics; the proxy prefix is outside `/devices/hite-pro/#`, so the gateway never sees it.

### Changed

- **Periodic refresh no longer sends Reload every time**: Reload is sent only when the device set or a discovery config changed, on the first periodic refresh after start (initial state sync, as before), or when `hitepro.refresh_devices` is called.

## 1.2.1 (2026-08-23)

### Fixed

- **Pushbutton events never firing**: The `event`-domain `value_template` for pushbutton controls rendered a bare `press`/`release` word, but Home Assistant's MQTT event platform requires the templated payload to be JSON with an `event_type` key. Every message was silently discarded as invalid JSON, so `event.*` pushbutton entities stayed stuck on `unknown` forever regardless of real button presses. The template now renders `{"event_type": "press"}` / `{"event_type": "release"}`.

## 1.2.0 (2026-08-17)

### Added

- **Smart RGBW light discovery**: Paired `*_rgb` and `*_brightness` controls are now combined into a single MQTT light entity with both color and brightness control. RGB format is translated between HiTE PRO (`R;G;B`) and Home Assistant (`R,G,B`) automatically.
- **Pushbutton → event entities**: Pushbutton controls are now mapped to MQTT event entities (device class: `button`) with `press`/`release` event types, replacing the previous binary sensor mapping.
- **Relay-Drive → button entities**: `Relay-Drive_*_open` and `Relay-Drive_*_close` controls are now mapped to button entities (Открыть/Закрыть) with a synthetic Stop button that sends `0` to halt motion.
- **Window/окно binary sensor device class**: Windows are now recognized alongside doors.
- **Smart Motion, Smart Water, Checker, power device classes**: Binary sensors now get correct device classes (`motion`, `moisture`, `opening`, `problem`) based on control ID prefix.
- **Illumination percentage sensors**: Text controls with values like `0%` matching illumination keywords are now mapped as numeric measurement sensors with `%` unit.
- **`state_class: measurement`** added to temperature and humidity sensors for long-term statistics.
- **Versioned legacy cleanup**: On upgrade, stale retained MQTT discovery configs for old split RGB lights, old pushbutton binary sensors, and old Relay-Drive switches are automatically removed once. Uses integer `CLEANUP_VERSION` for future-proof migration.

### Changed

- **Entity comparison** now uses `(domain, object_id)` tuples, correctly detecting domain migrations (e.g. pushbutton: binary_sensor → event).
- **Binary sensor default device class** changed from always-`safety` to `None` — only set `device_class` when a keyword match is found.
- **`_slugify`** now falls back to `"entity"` instead of returning empty strings for non-Latin titles.
- **Illumination detection** uses strict `^\d+%$` regex to avoid creating spurious sensors from non-numeric text values.

### Fixed

- **Dimmable light state**: `state_value_template` returned `ON`/`OFF`
  while `payload_on`/`payload_off` held the numeric device values, so the
  rendered state never matched and dimmers stayed `unknown`. The template
  now emits the exact payload strings. Affects `range` controls and
  combined RGB+brightness lights.
- **Post-startup state**: Skip gateway Reload on first load (states arrive before MQTT is ready). Periodic refreshes trigger Reload correctly, so states populate within one refresh cycle after HA restart.

## 1.1.0 (2026-05-27)

- Add light_devices option to override switch→light

## 1.0.1 (2026-05-17)

- Only trigger gateway reload when entities change

## 1.0.0 (2026-05-17)

- Initial public release