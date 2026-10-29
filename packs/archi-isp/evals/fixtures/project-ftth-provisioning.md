# Project: FTTH ONT auto-provisioning (fixture)

Purpose: replace manual ONT activation with an auto-configuration server (ACS). When a customer plugs in a new
ONT or set-top box, it contacts the ACS, which pushes the line profile and firmware.

- Users: about 40 provisioning operators (internal, web console of the ACS), 2.1 million customer devices.
- Data: subscriber line identifiers, device serial numbers, line profiles, customer ids. The project team
  classifies it C2.
- Hosting: two on-premises VMs in the OSS zone (acs-01.example.internal, acs-02.example.internal).
- Flows:
  - customer devices (access network) -> ACS: TR-069 (CWMP) over HTTP, port 7547; TLS "planned for a later phase";
  - ACS -> ordering system (APP zone): REST API over HTTPS to fetch the line profile of an order;
  - operators (USER zone) -> ACS web console: HTTPS, local accounts of the ACS product;
  - the ACS vendor connects for support through a site-to-site VPN that ends on the ACS VMs.
- Firmware images are downloaded by the devices from the ACS over HTTP.
- Open points: who administers the ACS accounts; retention of device logs.
