# change/calendar#91: night windows never match

Reporter: Sophie Garnier (change management)

The standard window for core network changes is 22:00 to 02:00. `in_window(23, 22, 2)` returns False, so the
calendar marks every night change as "outside window" and the CAB rejects them. Windows that cross midnight must
work; daytime windows must keep working.
