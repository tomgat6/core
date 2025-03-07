"""Tests for home_connect sensor entities."""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

from aiohomeconnect.model import (
    ArrayOfEvents,
    ArrayOfStatus,
    Event,
    EventKey,
    EventMessage,
    EventType,
    Status,
    StatusKey,
)
from aiohomeconnect.model.error import HomeConnectApiError
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.home_connect.const import (
    BSH_DOOR_STATE_CLOSED,
    BSH_DOOR_STATE_LOCKED,
    BSH_DOOR_STATE_OPEN,
    BSH_EVENT_PRESENT_STATE_CONFIRMED,
    BSH_EVENT_PRESENT_STATE_OFF,
    BSH_EVENT_PRESENT_STATE_PRESENT,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from tests.common import MockConfigEntry

TEST_HC_APP = "Dishwasher"


EVENT_PROG_DELAYED_START = {
    EventType.STATUS: {
        EventKey.BSH_COMMON_STATUS_OPERATION_STATE: "BSH.Common.EnumType.OperationState.DelayedStart",
    },
}


EVENT_PROG_RUN = {
    EventType.STATUS: {
        EventKey.BSH_COMMON_STATUS_OPERATION_STATE: "BSH.Common.EnumType.OperationState.Run",
    },
    EventType.EVENT: {
        EventKey.BSH_COMMON_OPTION_REMAINING_PROGRAM_TIME: 0,
        EventKey.BSH_COMMON_OPTION_PROGRAM_PROGRESS: 60,
    },
}

EVENT_PROG_UPDATE_1 = {
    EventType.EVENT: {
        EventKey.BSH_COMMON_OPTION_REMAINING_PROGRAM_TIME: 0,
        EventKey.BSH_COMMON_OPTION_PROGRAM_PROGRESS: 80,
    },
    EventType.STATUS: {
        EventKey.BSH_COMMON_STATUS_OPERATION_STATE: "BSH.Common.EnumType.OperationState.Run",
    },
}

EVENT_PROG_UPDATE_2 = {
    EventType.EVENT: {
        EventKey.BSH_COMMON_OPTION_REMAINING_PROGRAM_TIME: 20,
        EventKey.BSH_COMMON_OPTION_PROGRAM_PROGRESS: 99,
    },
    EventType.STATUS: {
        EventKey.BSH_COMMON_STATUS_OPERATION_STATE: "BSH.Common.EnumType.OperationState.Run",
    },
}

EVENT_PROG_END = {
    EventType.STATUS: {
        EventKey.BSH_COMMON_STATUS_OPERATION_STATE: "BSH.Common.EnumType.OperationState.Ready",
    },
}


@pytest.fixture
def platforms() -> list[str]:
    """Fixture to specify platforms to test."""
    return [Platform.SENSOR]


async def test_sensors(
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
) -> None:
    """Test sensor entities."""
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED


async def test_paired_depaired_devices_flow(
    appliance_ha_id: str,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that removed devices are correctly removed from and added to hass on API events."""
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    device = device_registry.async_get_device(identifiers={(DOMAIN, appliance_ha_id)})
    assert device
    entity_entries = entity_registry.entities.get_entries_for_device_id(device.id)
    assert entity_entries

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                EventType.DEPAIRED,
                data=ArrayOfEvents([]),
            )
        ]
    )
    await hass.async_block_till_done()

    device = device_registry.async_get_device(identifiers={(DOMAIN, appliance_ha_id)})
    assert not device
    for entity_entry in entity_entries:
        assert not entity_registry.async_get(entity_entry.entity_id)

    # Now that all everything related to the device is removed, pair it again
    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                EventType.PAIRED,
                data=ArrayOfEvents([]),
            )
        ]
    )
    await hass.async_block_till_done()

    assert device_registry.async_get_device(identifiers={(DOMAIN, appliance_ha_id)})
    for entity_entry in entity_entries:
        assert entity_registry.async_get(entity_entry.entity_id)


async def test_connected_devices(
    appliance_ha_id: str,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that devices reconnected.

    Specifically those devices whose settings, status, etc. could
    not be obtained while disconnected and once connected, the entities are added.
    """
    get_status_original_mock = client.get_status

    def get_status_side_effect(ha_id: str):
        if ha_id == appliance_ha_id:
            raise HomeConnectApiError(
                "SDK.Error.HomeAppliance.Connection.Initialization.Failed"
            )
        return get_status_original_mock.return_value

    client.get_status = AsyncMock(side_effect=get_status_side_effect)
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED
    client.get_status = get_status_original_mock

    device = device_registry.async_get_device(identifiers={(DOMAIN, appliance_ha_id)})
    assert device
    entity_entries = entity_registry.entities.get_entries_for_device_id(device.id)

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                EventType.CONNECTED,
                data=ArrayOfEvents([]),
            )
        ]
    )
    await hass.async_block_till_done()

    device = device_registry.async_get_device(identifiers={(DOMAIN, appliance_ha_id)})
    assert device
    new_entity_entries = entity_registry.entities.get_entries_for_device_id(device.id)
    assert len(new_entity_entries) > len(entity_entries)


@pytest.mark.parametrize("appliance_ha_id", [TEST_HC_APP], indirect=True)
async def test_sensor_entity_availabilty(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
    appliance_ha_id: str,
) -> None:
    """Test if sensor entities availability are based on the appliance connection state."""
    entity_ids = [
        "sensor.dishwasher_operation_state",
        "sensor.dishwasher_salt_nearly_empty",
    ]
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        assert state
        assert state.state != STATE_UNAVAILABLE

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                EventType.DISCONNECTED,
                ArrayOfEvents([]),
            )
        ]
    )
    await hass.async_block_till_done()

    for entity_id in entity_ids:
        assert hass.states.is_state(entity_id, STATE_UNAVAILABLE)

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                EventType.CONNECTED,
                ArrayOfEvents([]),
            )
        ]
    )
    await hass.async_block_till_done()

    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        assert state
        assert state.state != STATE_UNAVAILABLE


# Appliance_ha_id program sequence with a delayed start.
PROGRAM_SEQUENCE_EVENTS = (
    EVENT_PROG_DELAYED_START,
    EVENT_PROG_RUN,
    EVENT_PROG_UPDATE_1,
    EVENT_PROG_UPDATE_2,
    EVENT_PROG_END,
)

# Entity mapping to expected state at each program sequence.
ENTITY_ID_STATES = {
    "sensor.dishwasher_operation_state": (
        "delayedstart",
        "run",
        "run",
        "run",
        "ready",
    ),
    "sensor.dishwasher_program_finish_time": (
        "unavailable",
        "2021-01-09T12:00:00+00:00",
        "2021-01-09T12:00:00+00:00",
        "2021-01-09T12:00:20+00:00",
        "unavailable",
    ),
    "sensor.dishwasher_program_progress": (
        "unavailable",
        "60",
        "80",
        "99",
        "unavailable",
    ),
}


@pytest.mark.parametrize("appliance_ha_id", [TEST_HC_APP], indirect=True)
@pytest.mark.parametrize(
    ("states", "event_run"),
    list(
        zip(
            list(zip(*ENTITY_ID_STATES.values(), strict=False)),
            PROGRAM_SEQUENCE_EVENTS,
            strict=False,
        )
    ),
)
async def test_program_sensors(
    client: MagicMock,
    appliance_ha_id: str,
    states: tuple,
    event_run: dict[EventType, dict[EventKey, str | int]],
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
) -> None:
    """Test sequence for sensors that expose information about a program."""
    entity_ids = ENTITY_ID_STATES.keys()

    time_to_freeze = "2021-01-09 12:00:00+00:00"
    freezer.move_to(time_to_freeze)

    assert config_entry.state == ConfigEntryState.NOT_LOADED
    client.get_status.return_value.status.extend(
        Status(
            key=StatusKey(event_key.value),
            raw_key=event_key.value,
            value=value,
        )
        for event_key, value in EVENT_PROG_DELAYED_START[EventType.STATUS].items()
    )
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                event_type,
                ArrayOfEvents(
                    [
                        Event(
                            key=event_key,
                            raw_key=event_key.value,
                            timestamp=0,
                            level="",
                            handling="",
                            value=value,
                        )
                    ],
                ),
            )
            for event_type, events in event_run.items()
            for event_key, value in events.items()
        ]
    )
    await hass.async_block_till_done()
    for entity_id, state in zip(entity_ids, states, strict=False):
        assert hass.states.is_state(entity_id, state)


@pytest.mark.parametrize("appliance_ha_id", [TEST_HC_APP], indirect=True)
@pytest.mark.parametrize(
    ("initial_operation_state", "initial_state", "event_order", "entity_states"),
    [
        (
            "BSH.Common.EnumType.OperationState.Ready",
            STATE_UNAVAILABLE,
            (EventType.STATUS, EventType.EVENT),
            (STATE_UNKNOWN, "60"),
        ),
        (
            "BSH.Common.EnumType.OperationState.Run",
            STATE_UNKNOWN,
            (EventType.EVENT, EventType.STATUS),
            ("60", "60"),
        ),
    ],
)
async def test_program_sensor_edge_case(
    initial_operation_state: str,
    initial_state: str,
    event_order: tuple[EventType, EventType],
    entity_states: tuple[str, str],
    appliance_ha_id: str,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
) -> None:
    """Test edge case for the program related entities."""
    entity_id = "sensor.dishwasher_program_progress"
    client.get_status = AsyncMock(
        return_value=ArrayOfStatus(
            [
                Status(
                    StatusKey.BSH_COMMON_OPERATION_STATE,
                    StatusKey.BSH_COMMON_OPERATION_STATE.value,
                    initial_operation_state,
                )
            ]
        )
    )

    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    assert hass.states.is_state(entity_id, initial_state)

    for event_type, state in zip(event_order, entity_states, strict=True):
        await client.add_events(
            [
                EventMessage(
                    appliance_ha_id,
                    event_type,
                    ArrayOfEvents(
                        [
                            Event(
                                key=event_key,
                                raw_key=event_key.value,
                                timestamp=0,
                                level="",
                                handling="",
                                value=value,
                            )
                        ],
                    ),
                )
                for event_key, value in EVENT_PROG_RUN[event_type].items()
            ]
        )
        await hass.async_block_till_done()
        assert hass.states.is_state(entity_id, state)


# Program sequence for SensorDeviceClass.TIMESTAMP edge cases.
PROGRAM_SEQUENCE_EDGE_CASE = [
    EVENT_PROG_DELAYED_START,
    EVENT_PROG_RUN,
    EVENT_PROG_END,
    EVENT_PROG_END,
]

# Expected state at each sequence.
ENTITY_ID_EDGE_CASE_STATES = [
    "unavailable",
    "2021-01-09T12:00:01+00:00",
    "unavailable",
    "unavailable",
]


@pytest.mark.parametrize("appliance_ha_id", [TEST_HC_APP], indirect=True)
async def test_remaining_prog_time_edge_cases(
    appliance_ha_id: str,
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
) -> None:
    """Run program sequence to test edge cases for the remaining_prog_time entity."""
    entity_id = "sensor.dishwasher_program_finish_time"
    time_to_freeze = "2021-01-09 12:00:00+00:00"
    freezer.move_to(time_to_freeze)

    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    for (
        event,
        expected_state,
    ) in zip(PROGRAM_SEQUENCE_EDGE_CASE, ENTITY_ID_EDGE_CASE_STATES, strict=False):
        await client.add_events(
            [
                EventMessage(
                    appliance_ha_id,
                    event_type,
                    ArrayOfEvents(
                        [
                            Event(
                                key=event_key,
                                raw_key=event_key.value,
                                timestamp=0,
                                level="",
                                handling="",
                                value=value,
                            )
                        ],
                    ),
                )
                for event_type, events in event.items()
                for event_key, value in events.items()
            ]
        )
        await hass.async_block_till_done()
        freezer.tick()
        assert hass.states.is_state(entity_id, expected_state)


@pytest.mark.parametrize(
    (
        "entity_id",
        "event_key",
        "event_type",
        "event_value_update",
        "expected",
        "appliance_ha_id",
    ),
    [
        (
            "sensor.dishwasher_door",
            EventKey.BSH_COMMON_STATUS_DOOR_STATE,
            EventType.STATUS,
            BSH_DOOR_STATE_LOCKED,
            "locked",
            "Dishwasher",
        ),
        (
            "sensor.dishwasher_door",
            EventKey.BSH_COMMON_STATUS_DOOR_STATE,
            EventType.STATUS,
            BSH_DOOR_STATE_CLOSED,
            "closed",
            "Dishwasher",
        ),
        (
            "sensor.dishwasher_door",
            EventKey.BSH_COMMON_STATUS_DOOR_STATE,
            EventType.STATUS,
            BSH_DOOR_STATE_OPEN,
            "open",
            "Dishwasher",
        ),
        (
            "sensor.fridgefreezer_freezer_door_alarm",
            "EVENT_NOT_IN_STATUS_YET_SO_SET_TO_OFF",
            EventType.EVENT,
            "",
            "off",
            "FridgeFreezer",
        ),
        (
            "sensor.fridgefreezer_freezer_door_alarm",
            EventKey.REFRIGERATION_FRIDGE_FREEZER_EVENT_DOOR_ALARM_FREEZER,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_OFF,
            "off",
            "FridgeFreezer",
        ),
        (
            "sensor.fridgefreezer_freezer_door_alarm",
            EventKey.REFRIGERATION_FRIDGE_FREEZER_EVENT_DOOR_ALARM_FREEZER,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_PRESENT,
            "present",
            "FridgeFreezer",
        ),
        (
            "sensor.fridgefreezer_freezer_door_alarm",
            EventKey.REFRIGERATION_FRIDGE_FREEZER_EVENT_DOOR_ALARM_FREEZER,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_CONFIRMED,
            "confirmed",
            "FridgeFreezer",
        ),
        (
            "sensor.coffeemaker_bean_container_empty",
            EventType.EVENT,
            "EVENT_NOT_IN_STATUS_YET_SO_SET_TO_OFF",
            "",
            "off",
            "CoffeeMaker",
        ),
        (
            "sensor.coffeemaker_bean_container_empty",
            EventKey.CONSUMER_PRODUCTS_COFFEE_MAKER_EVENT_BEAN_CONTAINER_EMPTY,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_OFF,
            "off",
            "CoffeeMaker",
        ),
        (
            "sensor.coffeemaker_bean_container_empty",
            EventKey.CONSUMER_PRODUCTS_COFFEE_MAKER_EVENT_BEAN_CONTAINER_EMPTY,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_PRESENT,
            "present",
            "CoffeeMaker",
        ),
        (
            "sensor.coffeemaker_bean_container_empty",
            EventKey.CONSUMER_PRODUCTS_COFFEE_MAKER_EVENT_BEAN_CONTAINER_EMPTY,
            EventType.EVENT,
            BSH_EVENT_PRESENT_STATE_CONFIRMED,
            "confirmed",
            "CoffeeMaker",
        ),
    ],
    indirect=["appliance_ha_id"],
)
async def test_sensors_states(
    entity_id: str,
    event_key: EventKey,
    event_type: EventType,
    event_value_update: str,
    appliance_ha_id: str,
    expected: str,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
) -> None:
    """Tests for Appliance_ha_id alarm sensors."""
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    await client.add_events(
        [
            EventMessage(
                appliance_ha_id,
                event_type,
                ArrayOfEvents(
                    [
                        Event(
                            key=event_key,
                            raw_key=str(event_key),
                            timestamp=0,
                            level="",
                            handling="",
                            value=event_value_update,
                        )
                    ],
                ),
            ),
        ]
    )
    await hass.async_block_till_done()
    assert hass.states.is_state(entity_id, expected)


@pytest.mark.parametrize(
    (
        "appliance_ha_id",
        "entity_id",
        "status_key",
        "unit_get_status",
        "unit_get_status_value",
        "get_status_value_call_count",
    ),
    [
        (
            "Oven",
            "sensor.oven_current_oven_cavity_temperature",
            StatusKey.COOKING_OVEN_CURRENT_CAVITY_TEMPERATURE,
            "°C",
            None,
            0,
        ),
        (
            "Oven",
            "sensor.oven_current_oven_cavity_temperature",
            StatusKey.COOKING_OVEN_CURRENT_CAVITY_TEMPERATURE,
            None,
            "°C",
            1,
        ),
    ],
    indirect=["appliance_ha_id"],
)
async def test_sensor_unit_fetching(
    appliance_ha_id: str,
    entity_id: str,
    status_key: StatusKey,
    unit_get_status: str | None,
    unit_get_status_value: str | None,
    get_status_value_call_count: int,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    integration_setup: Callable[[MagicMock], Awaitable[bool]],
    setup_credentials: None,
    client: MagicMock,
) -> None:
    """Test that the sensor entities are capable of fetching units."""

    async def get_status_mock(ha_id: str) -> ArrayOfStatus:
        if ha_id != appliance_ha_id:
            return ArrayOfStatus([])
        return ArrayOfStatus(
            [
                Status(
                    key=status_key,
                    raw_key=status_key.value,
                    value=0,
                    unit=unit_get_status,
                )
            ]
        )

    client.get_status = AsyncMock(side_effect=get_status_mock)
    client.get_status_value = AsyncMock(
        return_value=Status(
            key=status_key,
            raw_key=status_key.value,
            value=0,
            unit=unit_get_status_value,
        )
    )

    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert await integration_setup(client)
    assert config_entry.state == ConfigEntryState.LOADED

    entity_state = hass.states.get(entity_id)
    assert entity_state
    assert (
        entity_state.attributes["unit_of_measurement"] == unit_get_status
        or unit_get_status_value
    )

    assert client.get_status_value.call_count == get_status_value_call_count
