"""Synology Surveillance Station integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import (
    SynologyApiError,
    SynologyAuthError,
    SynologyConnectionError,
    SynologyPermissionError,
    SynologySurveillanceApi,
    SynologyUnsupportedError,
)
from .const import (
    CONF_CAMERA_IDS,
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    METADATA_REFRESH_INTERVAL,
    MIN_POLL_INTERVAL,
    PLATFORMS,
)
from .persistence import (
    LEGACY_STORAGE_VERSION,
    STORAGE_VERSION,
    deserialize_last_events,
    serialize_last_events,
)
from .runtime import SurveillanceRuntime
from .views import RecordingProxyView

_LOGGER = logging.getLogger(__name__)
_STORAGE_INDEX_VERSION = 1

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SurveillanceConfigEntry = ConfigEntry[SurveillanceRuntime]


def _camera_id_from_device(device: dr.DeviceEntry, identity: str) -> int | None:
    """Extract an integration camera ID from a device identifier."""
    prefix = f"{identity}_camera_"
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        suffix = identifier.removeprefix(prefix)
        return int(suffix) if suffix.isdigit() else None
    return None


def _storage_index_ids(value: Any) -> set[int]:
    """Parse a private storage index defensively."""
    if not isinstance(value, dict) or not isinstance(value.get("camera_ids"), list):
        return set()
    return {
        int(camera_id)
        for camera_id in value["camera_ids"]
        if str(camera_id).isdigit()
    }


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Initialize shared state and the authenticated recording proxy."""
    hass.data.setdefault(DOMAIN, {})
    hass.http.register_view(RecordingProxyView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SurveillanceConfigEntry) -> bool:
    """Set up a Surveillance Station config entry."""
    api = SynologySurveillanceApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        use_ssl=entry.data[CONF_USE_SSL],
        verify_ssl=entry.data[CONF_VERIFY_SSL],
    )
    try:
        info, available_cameras = await api.async_initialize()
    except SynologyAuthError as err:
        raise ConfigEntryAuthFailed from err
    except SynologyPermissionError as err:
        raise ConfigEntryError(
            "The account cannot access Surveillance Station"
        ) from err
    except SynologyUnsupportedError as err:
        raise ConfigEntryError(str(err)) from err
    except SynologyConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    configured_camera_ids = entry.options.get(CONF_CAMERA_IDS)
    selected_ids: set[int] | None = None
    if configured_camera_ids is not None:
        selected_ids = {
            int(camera_id)
            for camera_id in configured_camera_ids
            if str(camera_id).isdigit()
        }
        cameras = tuple(
            camera
            for camera in available_cameras
            if camera.camera_id in selected_ids
        )
        if not cameras:
            await api.async_logout()
            raise ConfigEntryError("None of the selected cameras is available")
    else:
        cameras = available_cameras

    runtime = SurveillanceRuntime(api, info, cameras, available_cameras)
    entry.runtime_data = runtime
    hass.data[DOMAIN][entry.entry_id] = runtime

    identity = info.serial or f"{api.host}:{api.port}"
    device_registry = dr.async_get(hass)
    existing_camera_devices: dict[int, dr.DeviceEntry] = {}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if (camera_id := _camera_id_from_device(device, identity)) is not None:
            existing_camera_devices[camera_id] = device

    event_store_index = Store[dict[str, Any]](
        hass,
        _STORAGE_INDEX_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_event_index",
    )
    indexed_camera_ids = _storage_index_ids(await event_store_index.async_load())
    known_camera_ids = indexed_camera_ids | set(existing_camera_devices)
    retained_camera_ids = (
        selected_ids
        if selected_ids is not None
        else {camera.camera_id for camera in cameras}
    )
    stale_camera_ids = known_camera_ids - retained_camera_ids
    for camera_id in stale_camera_ids:
        await Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.last_event.{camera_id}",
        ).async_remove()
        if device := existing_camera_devices.get(camera_id):
            device_registry.async_remove_device(device.id)
    indexed_camera_ids = (
        (known_camera_ids - stale_camera_ids)
        | {camera.camera_id for camera in cameras}
    )
    await event_store_index.async_save(
        {"camera_ids": sorted(indexed_camera_ids)}
    )

    legacy_event_store = Store[dict[str, Any]](
        hass,
        LEGACY_STORAGE_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_events",
    )
    event_stores = {
        camera.camera_id: Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.last_event.{camera.camera_id}",
        )
        for camera in cameras
    }
    restored_events = {}
    missing_camera_ids: set[int] = set()
    for camera_id, store in event_stores.items():
        restored = deserialize_last_events(await store.async_load(), {camera_id})
        if restored:
            restored_events.update(restored)
        else:
            missing_camera_ids.add(camera_id)
    if missing_camera_ids:
        legacy_events = deserialize_last_events(
            await legacy_event_store.async_load(), missing_camera_ids
        )
        if legacy_events:
            restored_events.update(legacy_events)
            runtime.persistence_diagnostics["migrated_legacy_events"] = len(
                legacy_events
            )
            for camera_id, event in legacy_events.items():
                await event_stores[camera_id].async_save(
                    serialize_last_events({camera_id: event})
                )
            await legacy_event_store.async_remove()
    runtime.restore_events(restored_events)

    def event_store_listener(camera_id: int):
        """Create a listener that persists only one camera's latest event."""

        @callback
        def schedule_event_store_save(_event: Any) -> None:
            runtime.persistence_diagnostics.update(
                {
                    "last_save_at": dt_util.utcnow().isoformat(),
                    "last_save_error": None,
                }
            )
            event_stores[camera_id].async_delay_save(
                lambda: serialize_last_events(
                    {camera_id: runtime.last_events[camera_id]}
                ),
                1,
            )

        return schedule_event_store_save

    for camera in cameras:
        entry.async_on_unload(
            runtime.add_state_event_listener(
                camera.camera_id, event_store_listener(camera.camera_id)
            )
        )

    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identity)},
        manufacturer="Synology",
        model="Surveillance Station",
        name=entry.title,
        sw_version=info.version,
        configuration_url=api.base_url,
    )
    runtime.hub_device_id = hub_device.id

    polling_enabled = entry.options.get(CONF_POLLING_ENABLED, True)
    poll_interval = max(
        MIN_POLL_INTERVAL,
        min(
            MAX_POLL_INTERVAL,
            int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        ),
    )
    poll_lock = asyncio.Lock()
    reauth_started = False

    def start_reauth() -> None:
        """Start one repair flow and make stale network state visibly unavailable."""
        nonlocal reauth_started
        runtime.mark_all_polling_unavailable()
        if reauth_started:
            return
        reauth_started = True
        entry.async_start_reauth(hass)

    def local_day_start(received_at: datetime) -> datetime:
        """Return local midnight as UTC for stable daily API counters."""
        local_now = dt_util.as_local(received_at)
        return dt_util.as_utc(
            local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        )

    async def refresh_metadata(received_at: datetime) -> None:
        """Refresh lightweight daily counts and optional camera connectivity."""
        runtime.begin_metadata_refresh(received_at)
        recording_counts: dict[int, int] = {}
        camera_statuses: dict[int, int] = {}
        camera_status_refresh_succeeded = False
        error_types: set[str] = set()
        if runtime.recording_count_refresh_due(received_at):
            runtime.begin_recording_count_refresh(received_at)
            day_start = local_day_start(received_at)
            for camera in runtime.cameras:
                try:
                    recording_counts[camera.camera_id] = (
                        await api.async_count_recordings(
                            camera.camera_id, day_start, received_at
                        )
                    )
                except SynologyAuthError as err:
                    error_types.add(type(err).__name__)
                    start_reauth()
                    return
                except SynologyApiError as err:
                    error_types.add(type(err).__name__)
                    runtime.mark_recording_count_unavailable(camera.camera_id)
                except Exception as err:  # Keep one camera from blocking the others.
                    error_types.add(type(err).__name__)
                    runtime.mark_recording_count_unavailable(camera.camera_id)
                    _LOGGER.exception(
                        "Unexpected recording-count error for camera %s",
                        camera.camera_id,
                    )
        if runtime.camera_status_supported:
            try:
                camera_statuses = await api.async_get_camera_statuses()
                camera_status_refresh_succeeded = True
            except SynologyAuthError as err:
                error_types.add(type(err).__name__)
                start_reauth()
                return
            except SynologyApiError as err:
                error_types.add(type(err).__name__)
                runtime.mark_camera_statuses_unavailable()
            except Exception as err:
                error_types.add(type(err).__name__)
                runtime.mark_camera_statuses_unavailable()
                _LOGGER.exception("Unexpected camera-status refresh error")
        runtime.complete_metadata_refresh(
            received_at,
            recording_counts,
            camera_statuses,
            error_types,
            camera_status_refresh_succeeded=camera_status_refresh_succeeded,
        )

    async def bootstrap_latest_events(received_at: datetime) -> None:
        """Seed an empty persistent store from verified detection recordings."""
        day_start = local_day_start(received_at)
        restored_camera_ids: set[int] = set()
        detection_counts = runtime.polling_diagnostics["last_detection_counts"]
        entity_registry = er.async_get(hass)
        for camera in runtime.cameras:
            camera_id = camera.camera_id
            if camera_id in runtime.last_events:
                continue
            total_count = runtime.recordings_today.get(camera_id)
            detection_count = detection_counts.get(camera_id)
            if (
                not total_count
                or detection_count is None
                or total_count != detection_count
            ):
                continue
            try:
                recordings = await api.async_list_recordings(
                    camera_id,
                    day_start,
                    received_at,
                    limit=min(total_count, 200),
                )
            except SynologyApiError:
                _LOGGER.debug(
                    "Could not bootstrap the latest detection for camera %s",
                    camera_id,
                    exc_info=True,
                )
                continue
            if not recordings:
                continue
            recording = max(
                recordings,
                key=lambda item: (
                    item.start_time or day_start,
                    item.recording_id,
                ),
            )
            if recording.start_time is None:
                identity = runtime.info.serial or f"{api.host}:{api.port}"
                event_entity_id = entity_registry.async_get_entity_id(
                    "event",
                    DOMAIN,
                    f"{identity}_{camera_id}_detection",
                )
                restored_state = (
                    hass.states.get(event_entity_id) if event_entity_id else None
                )
                restored_time = (
                    dt_util.parse_datetime(restored_state.state)
                    if restored_state is not None
                    else None
                )
                if restored_time is None:
                    continue
                recording = replace(
                    recording,
                    start_time=dt_util.as_utc(restored_time),
                )
            snapshot = None
            snapshot_content_type = None
            if info.allow_snapshot is not False:
                try:
                    snapshot, snapshot_content_type = await api.async_get_snapshot(
                        camera_id
                    )
                except SynologyApiError:
                    _LOGGER.debug(
                        "Could not bootstrap the latest snapshot for camera %s",
                        camera_id,
                        exc_info=True,
                    )
            if runtime.restore_recording(
                recording,
                received_at,
                snapshot=snapshot,
                snapshot_content_type=snapshot_content_type,
            ) is not None:
                restored_camera_ids.add(camera_id)
        for camera_id in restored_camera_ids:
            try:
                await event_stores[camera_id].async_save(
                    serialize_last_events(
                        {camera_id: runtime.last_events[camera_id]}
                    )
                )
            except Exception as err:
                runtime.persistence_diagnostics["last_save_error"] = type(err).__name__
                _LOGGER.debug(
                    "Could not persist bootstrapped event for camera %s",
                    camera_id,
                    exc_info=True,
                )
            else:
                runtime.persistence_diagnostics.update(
                    {
                        "last_save_at": dt_util.utcnow().isoformat(),
                        "last_save_error": None,
                    }
                )

    async def poll_runtime(_now: Any = None) -> None:
        """Refresh event recordings and low-frequency camera metadata."""
        if poll_lock.locked():
            return
        async with poll_lock:
            received_at = dt_util.utcnow()
            if polling_enabled:
                detection_counts: dict[int, int] = {}
                detection_count_errors: dict[int, str] = {}
                day_start = local_day_start(received_at)
                authentication_failed = False
                for camera in runtime.cameras:
                    camera_id = camera.camera_id
                    try:
                        current_count = await api.async_count_detection_recordings(
                            camera_id, day_start, received_at
                        )
                    except SynologyAuthError as err:
                        detection_count_errors[camera_id] = (
                            f"{type(err).__name__}:{err.code}"
                        )
                        runtime.mark_detection_count_unavailable(camera_id)
                        start_reauth()
                        authentication_failed = True
                        break
                    except SynologyApiError as err:
                        detection_count_errors[camera_id] = (
                            f"{type(err).__name__}:{err.code}"
                        )
                        runtime.mark_detection_count_unavailable(camera_id)
                        _LOGGER.debug(
                            "Could not count event recordings for camera %s",
                            camera_id,
                            exc_info=True,
                        )
                        continue
                    except Exception as err:  # Keep one camera from blocking the others.
                        detection_count_errors[camera_id] = type(err).__name__
                        runtime.mark_detection_count_unavailable(camera_id)
                        _LOGGER.exception(
                            "Unexpected event-count error for camera %s", camera_id
                        )
                        continue

                    detection_counts[camera_id] = current_count
                    event_delta, previous_success = runtime.accept_detection_count(
                        camera_id, current_count, received_at
                    )
                    if event_delta <= 0:
                        runtime.polling_diagnostics["recording_lists_skipped"] += 1
                        continue

                    runtime.polling_diagnostics["recording_lists_requested"] += 1
                    query_start = max(
                        day_start,
                        (previous_success or received_at) - timedelta(seconds=30),
                    )
                    recordings = ()
                    try:
                        recordings = await api.async_list_recordings(
                            camera_id,
                            query_start,
                            received_at,
                            limit=max(100, min(event_delta * 2, 500)),
                        )
                    except SynologyAuthError as err:
                        detection_count_errors[camera_id] = (
                            f"{type(err).__name__}:{err.code}"
                        )
                        start_reauth()
                        authentication_failed = True
                    except SynologyApiError:
                        _LOGGER.debug(
                            "Could not enrich events with recordings for camera %s",
                            camera_id,
                            exc_info=True,
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Unexpected recording-list error for camera %s", camera_id
                        )

                    matched_recordings = runtime.select_safe_event_recordings(
                        camera_id,
                        recordings,
                        event_delta,
                        received_at,
                        after=previous_success,
                    )
                    event_sources: list[Any] = [*matched_recordings]
                    event_sources.extend(
                        None for _unused in range(event_delta - len(event_sources))
                    )

                    snapshot = None
                    snapshot_content_type = None
                    if info.allow_snapshot is not False:
                        try:
                            snapshot, snapshot_content_type = (
                                await api.async_get_snapshot(camera_id)
                            )
                        except SynologyAuthError:
                            start_reauth()
                            authentication_failed = True
                        except SynologyApiError:
                            _LOGGER.debug(
                                "Could not enrich camera %s event with a snapshot",
                                camera_id,
                                exc_info=True,
                            )
                        except Exception:
                            _LOGGER.exception(
                                "Unexpected snapshot error for camera %s", camera_id
                            )

                    first_counter_value = current_count - event_delta + 1
                    for index, recording in enumerate(event_sources):
                        is_latest = index == len(event_sources) - 1
                        runtime.accept_detection(
                            camera_id,
                            received_at,
                            first_counter_value + index,
                            recording=recording,
                            snapshot=snapshot if is_latest else None,
                            snapshot_content_type=(
                                snapshot_content_type if is_latest else None
                            ),
                        )
                    if authentication_failed:
                        break

                runtime.finish_poll(
                    received_at, detection_counts, detection_count_errors
                )
            if runtime.metadata_refresh_due(
                received_at, METADATA_REFRESH_INTERVAL
            ) and not reauth_started:
                await refresh_metadata(received_at)

    if polling_enabled:
        runtime.enable_polling(poll_interval)
    await poll_runtime()

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await api.async_logout()
        raise

    await bootstrap_latest_events(dt_util.utcnow())

    update_interval = poll_interval if polling_enabled else METADATA_REFRESH_INTERVAL
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            poll_runtime,
            timedelta(seconds=update_interval),
            cancel_on_shutdown=True,
        )
    )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SurveillanceConfigEntry) -> bool:
    """Unload entities and the NAS session."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    await entry.runtime_data.api.async_logout()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: SurveillanceConfigEntry) -> None:
    """Remove all private snapshots and migration storage owned by the entry."""
    index_store = Store[dict[str, Any]](
        hass,
        _STORAGE_INDEX_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_event_index",
    )
    camera_ids = _storage_index_ids(await index_store.async_load())
    identity = entry.unique_id
    if identity:
        registry = dr.async_get(hass)
        camera_ids.update(
            camera_id
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
            if (camera_id := _camera_id_from_device(device, identity)) is not None
        )
    for camera_id in camera_ids:
        await Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.last_event.{camera_id}",
        ).async_remove()
    await Store[dict[str, Any]](
        hass,
        LEGACY_STORAGE_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_events",
    ).async_remove()
    await index_store.async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: SurveillanceConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow confirmed removal only for a camera no longer returned by Synology."""
    identity = entry.unique_id
    if not identity or (
        camera_id := _camera_id_from_device(device_entry, identity)
    ) is None:
        return False
    if camera_id in {
        camera.camera_id for camera in entry.runtime_data.available_cameras
    }:
        return False
    await Store[dict[str, Any]](
        hass,
        STORAGE_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_event.{camera_id}",
    ).async_remove()
    index_store = Store[dict[str, Any]](
        hass,
        _STORAGE_INDEX_VERSION,
        f"{DOMAIN}.{entry.entry_id}.last_event_index",
    )
    indexed_camera_ids = _storage_index_ids(await index_store.async_load())
    indexed_camera_ids.discard(camera_id)
    await index_store.async_save({"camera_ids": sorted(indexed_camera_ids)})
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: SurveillanceConfigEntry) -> None:
    """Reload after connection or polling settings change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(
    hass: HomeAssistant, entry: SurveillanceConfigEntry
) -> bool:
    """Remove the obsolete webhook secret from pre-0.3 entries."""
    if entry.version < 2:
        data = dict(entry.data)
        data.pop("webhook_id", None)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True
