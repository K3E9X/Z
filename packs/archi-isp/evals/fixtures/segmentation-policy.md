# Network segmentation policy (fixture)

Zones: INTERNET, DMZ, USER (staff workstations), APP (application servers), DATA (databases and storage),
ADMIN (bastions and admin workstations), OSS (management of the access and core network: OLT, routers, ACS).

- S1. No any/any: source, destination and port are always explicit. A source of 0.0.0.0/0 or a whole /8, or a
  port range wider than 10 ports, counts as "any".
- S2. Every flow that crosses zones has a business justification.
- S3. INTERNET may only reach the DMZ. No flow from INTERNET to USER, APP, DATA, ADMIN or OSS.
- S4. Administration protocols are encrypted only (SSH, HTTPS, SNMPv3, SFTP) and come only from the ADMIN zone.
  Telnet, FTP, HTTP, SNMP v1/v2c, rlogin and RDP from any other zone are refused.
- S5. Every flow has a named owner (team and person).
