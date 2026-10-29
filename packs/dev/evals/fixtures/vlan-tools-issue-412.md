# netops/vlan-tools#412: VLAN ranges lose their last VLAN

Reporter: Camille Roux (NetOps provisioning)

When a port profile uses a range, the last VLAN is never provisioned. Example from the Lyon-3 POP rollout:

    parse_vlan_range("100-105")  ->  [100, 101, 102, 103, 104]

Expected `[100, 101, 102, 103, 104, 105]`: ranges in port profiles are inclusive (same as the switch CLI).
Single VLANs and comma lists work. Please fix in `vlan.py` and add a test.
