"""Availability for the single team shared by all service groups.

Existing bookings are read as-is; no migration, relabeling or cancellation is
needed to make old bookings occupy the team's calendar.
"""
from datetime import datetime, timedelta

from .models import Booking, BookingTimeSlot, ServiceGroup


def lock_shared_team():
    # This seeded, protected service-group row is the shared team's mutex.
    # Call inside transaction.atomic() before checking/creating any reservation.
    # It serializes even empty slots across customers and service groups on PG.
    return ServiceGroup.objects.select_for_update().get(key='car_wash')


def _moment(day, slot):
    return datetime.combine(day + timedelta(days=slot.day_offset), slot.start_time)


def shared_booked_labels(day, group, *, exclude_booking_id=None):
    group_id = getattr(group, 'pk', group)
    # Include disabled slots: disabling sales must not hide an existing booking.
    slots = list(BookingTimeSlot.objects.all())
    by_key = {(slot.group_id, slot.label): slot for slot in slots}
    reservations = Booking.objects.filter(
        date__range=(day - timedelta(days=1), day + timedelta(days=1)),
    ).exclude(status='canceled')
    if exclude_booking_id is not None:
        reservations = reservations.exclude(pk=exclude_booking_id)
    occupied = set()
    legacy_labels = set()
    for booking_day, label, booking_group in reservations.values_list(
        'date', 'time_slot', 'service_group_id',
    ):
        slot = by_key.get((booking_group, label))
        if slot is not None:
            occupied.add(_moment(booking_day, slot))
        elif booking_day == day:
            # Preserve protection for older labels without a schedule definition.
            legacy_labels.add(label)
    result = set(legacy_labels)
    for slot in slots:
        if slot.group_id == group_id and (
            _moment(day, slot) in occupied or slot.label in legacy_labels
        ):
            result.add(slot.label)
    return sorted(result)


def shared_slot_is_booked(day, label, group, *, exclude_booking_id=None):
    return label in shared_booked_labels(day, group, exclude_booking_id=exclude_booking_id)
