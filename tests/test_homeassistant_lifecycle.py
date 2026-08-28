"""Tests that exercise the integration through real Home Assistant APIs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import (  # noqa: E402
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.synology_surveillance_station import (  # noqa: E402
    async_setup_entry,
    async_unload_entry,
)
from custom_components.synology_surveillance_station.api import (  # noqa: E402
    SynologyAuthError,
)
from custom_components.synology_surveillance_station.config_flow import (  # noqa: E402
    SynologySurveillanceConfigFlow,
)
from custom_components.synology_surveillance_station.const import (  # noqa: E402
    CONF_POLLING_ENABLED,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.synology_surveillance_station.models import (  # noqa: E402
    Camera,
    SurveillanceInfo,
)

USER_INPUT = {
    CONF_HOST: "nas.example.test",
    CONF_PORT: 5001,
    CONF_USERNAME: "homeassistant_surveillance",
    CONF_PASSWORD: "not-a-real-password",
    CONF_USE_SSL: True,
    CONF_VERIFY_SSL: False,
}
INFO = SurveillanceInfo(
    serial="TESTSERIAL",
    version="9.3.12139",
    path=None,
    camera_number=1,
    license_number=1,
    user_privilege=1,
    allow_snapshot=True,
)
CAMERA = Camera(
    camera_id=1,
    name="Test camera",
    vendor="Example",
    model="Model 1",
    status=1,
    ds_id=0,
    video_codec=0,
)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_config_flow_creates_unique_entry(hass) -> None:
    """A validated NAS should create one config entry with a stable unique ID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch.object(
        SynologySurveillanceConfigFlow,
        "_async_validate",
        AsyncMock(return_value=INFO),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Surveillance Station (nas.example.test)"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "TESTSERIAL"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_config_flow_maps_authentication_error(hass) -> None:
    """Authentication failures should be shown as a repairable form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with patch.object(
        SynologySurveillanceConfigFlow,
        "_async_validate",
        AsyncMock(side_effect=SynologyAuthError("invalid credentials", 400)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


def _fake_api(*, initialize_error: Exception | None = None) -> SimpleNamespace:
    initialize = AsyncMock(return_value=(INFO, (CAMERA,)))
    if initialize_error is not None:
        initialize.side_effect = initialize_error
    return SimpleNamespace(
        host="nas.example.test",
        port=5001,
        base_url="https://nas.example.test:5001",
        async_initialize=initialize,
        async_logout=AsyncMock(),
        async_count_detection_recordings=AsyncMock(return_value=0),
        async_count_recordings=AsyncMock(return_value=0),
        async_get_camera_statuses=AsyncMock(return_value={1: 1}),
        async_list_recordings=AsyncMock(return_value=()),
        async_get_snapshot=AsyncMock(return_value=(None, None)),
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_and_unload_entry_lifecycle(hass) -> None:
    """Set up runtime state, initial metadata, and a clean NAS logout."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Surveillance Station (nas.example.test)",
        unique_id="TESTSERIAL",
        data=USER_INPUT,
        options={CONF_POLLING_ENABLED: False},
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    api = _fake_api()

    with (
        patch(
            "custom_components.synology_surveillance_station.SynologySurveillanceApi",
            return_value=api,
        ),
        patch(
            "custom_components.synology_surveillance_station.async_track_time_interval",
            return_value=lambda: None,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(),
        ) as forward,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ) as unload,
    ):
        assert await async_setup_entry(hass, entry)
        await hass.async_block_till_done()

        runtime = hass.data[DOMAIN][entry.entry_id]
        assert runtime.cameras == (CAMERA,)
        assert runtime.recordings_today == {1: 0}
        assert runtime.camera_statuses == {1: 1}
        forward.assert_awaited_once()

        assert await async_unload_entry(hass, entry)
        unload.assert_awaited_once()

    api.async_logout.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_entry_requests_reauthentication(hass) -> None:
    """Invalid stored credentials should use Home Assistant's auth-failure path."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Surveillance Station (nas.example.test)",
        unique_id="TESTSERIAL",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    api = _fake_api(
        initialize_error=SynologyAuthError("expired credentials", 106)
    )

    with (
        patch(
            "custom_components.synology_surveillance_station.SynologySurveillanceApi",
            return_value=api,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)

    assert entry.entry_id not in hass.data[DOMAIN]
