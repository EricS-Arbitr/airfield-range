# Blackstone Auxiliary Field — Scenario, Cyber Key Terrain & Vulnerability Reference

Mission framing, cyber key-terrain tiering and the catalogue of deliberate
weaknesses for the `blackstone.mil` military-airfield range.

Audience: event coordinators, scenario designers and red/blue leads. This
document complements [`RANGE_GUIDE.md`](RANGE_GUIDE.md) (topology, telemetry,
deploy) and [`RANGE_OVERVIEW.md`](RANGE_OVERVIEW.md) with the training-scenario
material: who the range *is*, what matters most on it, and where it is
deliberately weak.

> **Two reading conventions.** The **Scenario** is fiction — a backstory to hang
> an exercise on. The **Cyber Key Terrain** and **Intentional Vulnerability
> Catalog** are grounded in the real build: every host named exists in
> [`hosts`](../hosts) and the `RANGE_GUIDE` inventory, and every weakness listed
> is actually present in the Ansible roles or the platform images. Where a
> specific CVE or patch level matters to an exercise, confirm it against the
> deployed SimSpace image build — the catalogue below lists the deliberate,
> *structural* weaknesses, not an asserted CVE inventory.
>
> This is the Security-Onion-only build; a sibling full-stack build adds a
> distributed Splunk cluster and a Malcolm OT appliance and has its own copy of
> this reference.

---

## 1. Scenario

### The organization

**Blackstone Auxiliary Field** is a military auxiliary airfield supporting
flight operations, fuel handling and air-traffic control. Its network is built
as one Active Directory forest with two domains:

- **`blackstone.mil`** — the base enterprise: Headquarters, IT and Supply user
  segments, core Services, and a DMZ with public web, FTP and DNS.
- **`fops.blackstone.mil`** — a **child domain** for flight operations:
  flight-ops services and apps, the Flight-Control-Center (air-traffic control
  and recorded weather), and flight-ops user workstations.

Behind an L3.5 boundary sits an **OT fuel farm** — a Purdue-style chain of a
control-room HMI, an OpenPLC controller, a field simulator, a historian and an
audit database — and on the Flight-Control-Center a bespoke **air-traffic-control
subsystem** replays radar/ADS-B and weather into a controller working position.

### The mission

Three things must not fail: **flight operations must continue**, the **fuel
process must stay safe and trustworthy**, and the **flight-ops identity domain
must stay intact**. A Security Operations Center on the SOC segment — Security
Onion, six analysts and SIFT/FLARE/OpenVAS tooling — exists to detect and
contain intrusion across the enterprise, the flight-ops domain and the OT
enclave.

### A representative exercise storyline

One of many an operator can run on this terrain — written here to show how the
pieces connect, not as the only path:

1. **Entry.** An actor gains a foothold on the base enterprise — through the DMZ
   (web/FTP) or a phished enterprise user.
2. **Cross the trust.** Because `fops.blackstone.mil` is a child domain in the
   same forest, the parent/child trust is automatic, bidirectional and
   transitive. The attacker abuses it to move from `blackstone.mil` into the
   flight-ops domain — the moment of interest is identity traffic appearing
   where it should not.
3. **Two divergent objectives.**
   - *Flight-ops track:* reach the Flight-Control-Center and the ATC subsystem;
     optionally exploit the (opt-in) ATC flight-data-processor weaknesses.
   - *Process track:* pivot through `bs-ops-fw` and `bs-modbus-gateway` into the
     fuel farm and issue unauthenticated Modbus writes to the OpenPLC
     controller. The OT process hosts carry no endpoint agent, so the wire —
     `soc-sensor-ot` — is the compensating control.
4. **Defence.** Because sensor coverage is positional, the blue team correlates
   across `soc-sensor-corp`, `soc-sensor-fops`, `soc-sensor-ot` and
   `soc-sensor-edge`.

The same terrain supports enterprise-only scenarios, flight-ops identity
scenarios, OT-first scenarios and ATC-focused scenarios; the key-terrain and
vulnerability tables below are what an author draws on to build them.

---

## 2. Cyber Key Terrain (CKT)

Cyber Key Terrain is the infrastructure, systems and data whose compromise most
hurts the mission. It is tiered by the confidentiality/integrity/availability
(CIA) guarantee each asset must hold:

| Tier | Guarantee required | Compromise impact |
|---|---|---|
| **Tier 1** | 100% confidentiality **and** integrity **and** availability | Grave; halts the core mission (flight operations, the fuel process, identity, or the detection mission itself). |
| **Tier 2** | 100% confidentiality **and** integrity | Serious; interrupts or degrades operations. |
| **Tier 3** | 100% integrity | Degraded performance; recoverable. |

### Infrastructure CKT — routers and firewalls

| Device | Tier | CIA | Effect of compromise |
|---|---|---|---|
| `bs-ops-fw` | 1 | A, I | The default-deny L3.5 OT boundary. Loss or misconfiguration opens the path from flight-ops/enterprise into the fuel process. |
| `bs-modbus-gateway` | 1 | A | The single routed path into the fuel-farm Purdue segments **and** the OT mirror feed to `soc-sensor-ot`. Loss severs OT reachability and blinds OT monitoring at once. |
| `bs-edge-fw` | 2 | A, I | Edge NAT/BGP boundary and the Internet-facing control point. |
| `bs-core-rtr`, `bs-ops-rtr`, `bs-sec-rtr`, `bs-edge-rtr` | 2 | A | Enterprise/flight-ops reachability and the corporate/flight-ops/edge mirror feeds. Compromise degrades both connectivity and the detection picture. |

### Servers and services CKT

| Device | Tier | CIA | Effect of compromise |
|---|---|---|---|
| `bs-dc01`, `bs-dc02`, `fops-dc01`, `fops-dc02` | 1 | C, I, A | Active Directory for both domains. The automatic parent/child trust means a root-domain compromise reaches flight-ops and vice-versa. |
| `control-room-hmi`, `ff-plc-1`, `fuel-farm-sim` | 1 | A, I | The fuel process and its control — HMI, OpenPLC controller and field simulator. No endpoint agent; loss of integrity misrepresents or destabilises fuel handling. |
| `atc-radar`, `atc-station` | 1 | A, I | The air picture and the controller working position. Falsified or lost ATC data is a flight-safety-level integrity/availability failure. |
| `soc-so-manager`, `soc-so-search`, `soc-sensor-corp/-fops/-ot/-edge` | 1 | A, I | The detection mission. Loss blinds the defenders; falsified telemetry is the most damaging integrity failure the SOC can suffer. |
| `fuel-hist`, `fuel-db` | 2 | C, I, A | Fuel historian (InfluxDB/Telegraf/Grafana) and audit DB (PostgreSQL/TimescaleDB) — the process record. |
| `fops-app01..03` | 2 | A, I | Flight-ops application servers; degrade flight-ops business function. |
| `atc-weather` | 2 | A | Recorded-weather feed into the ATC display. |
| `bs-file01/02`, `fops-file01/02` | 2 | C, A | File servers; internally sensitive documents. |
| `soc-analyst01..06`, `soc-sift`, `soc-flare`, `soc-openvas` | 2 | C, I | Analyst positions and the forensic/scanning bench; an attacker here can steer the investigation. |
| `bs-ftp` | 2 | C, A | DMZ FTP — cleartext service; exposed credentials/data. |
| `bs-proxy`, `soc-syslog`, `bs-wec`, `fops-wec01` | 2 | A | Egress control, the central log collector and the Windows event collectors — attacker targets for log tampering. |
| `bs-www`, `bs-ns1` | 3 | A | Public web and DMZ name resolution; recoverable. |
| Enterprise / flight-ops workstations | 3 | I | Footholds for entry and lateral movement; individually recoverable. |

---

## 3. Intentional Vulnerability Catalog

These weaknesses are present **by design** for training. They are not defects to
fix, and each pairs a weakness with the telemetry that makes it detectable,
because the point of the range is to practise seeing them.

> **Accuracy notes.** Unlike the sibling power-utility range, this build does
> **not** disable Windows Defender — endpoint EDR and Sysmon are retained across
> the Windows fleet — and `bs-www` is a static page with no seeded web flaw
> today. The ATC flight-data-processor weaknesses (#4) are **opt-in** and
> disabled by default.
>
> This repository is public and these weaknesses are intentional range content.
> Still: **rotate all default credentials before any deployment that is not a
> lab.** CWE and ATT&CK identifiers are the weakness *class*; confirm any
> version-specific CVE against the deployed image.

| # | Component / host | Weakness | Class | Lives in | Detection opportunity |
|---|---|---|---|---|---|
| 1 | `ff-plc-1` (OpenPLC `:502`), `fuel-farm-sim` (pymodbus), `control-room-hmi` (FUXA client) | **Unauthenticated Modbus TCP** — no auth or encryption; any reachable host can read/write coils and registers | CWE-306; ATT&CK T0836 / T0855 | roles `fuel_plc`, `fuel_hmi`, `fuel_farm_sim` (protocol-inherent) | `soc-sensor-ot` — unexpected Modbus write function codes against the PLC |
| 2 | `fuel-hist` (InfluxDB 2.7 / Grafana), `fuel-db` (PostgreSQL/TimescaleDB), `control-room-hmi` (FUXA UI) | **Exposed OT management consoles** — process dashboards and databases reachable in-enclave with weak/role auth | CWE-306 / CWE-1188; ATT&CK T0822 | roles `fuel_historian`, `fuel_db`, `fuel_hmi` | `soc-sensor-ot` — new web/DB sessions to Level-3 hosts |
| 3 | `atc-radar` (readsb/tar1090), `atc-station` | **Unauthenticated ADS-B / radar replay** — ADS-B has no authenticity by design; the replayed air picture can be injected or spoofed | CWE-345; ATT&CK T0856 (analogue) | role `atc_core` | `soc-sensor-fops` — anomalous feed sources into the scope |
| 4 | `atc-station` flight-data processor | **Opt-in web weaknesses** — SQLi in the strip filter and an unauthenticated `/api/update_squawk`, **gated behind `atc_vulns_enabled` (default `false`)** | CWE-89 / CWE-306; ATT&CK T1190 | role `atc_core` (`fdp/app.py`, each marked `# RANGE-VULN:`) | enable `atc_vulns_enabled` for web-exploitation scenarios; instructor key documents each |
| 5 | `bs-ops-fw` (L3.5 boundary) | **Permissive boundary** — default-deny by design but carries lab "allow any" rules today | CWE-1188; ATT&CK T0812 | pfSense L3.5 boundary config | a cross-boundary reachability test that succeeds is expected for now; tightening the ACLs is the exercise control |
| 6 | `control-room-hmi`, `ff-plc-1`, `fuel-farm-sim` | **No endpoint instrumentation** on the OT process hosts (deliberate) — they run the process and carry no agent | visibility gap (not an injected flaw) | `[unmanaged]` OT membership | reconstruct from the wire (`soc-sensor-ot`) only — the designed compensating control |
| 7 | `bs-ftp` (DMZ) | **Cleartext FTP** — credentials and data traverse the network unencrypted | CWE-319; ATT&CK T1071 | DMZ FTP service | `soc-sensor-edge` / `soc-sensor-corp` — plaintext credentials on the wire |
| 8 | `blackstone.mil` ↔ `fops.blackstone.mil` | **Automatic transitive forest trust** — bidirectional parent/child trust is a cross-domain escalation path | ATT&CK T1482 / T1134.005 | single-forest AD design | cross-domain authentication appearing where it should not (the `RANGE_GUIDE` flags this explicitly) |
| 9 | Windows & Linux fleet, OT services | **Shared / default credentials** — `simspace` (Windows & Linux), pfSense `admin`, VyOS `vyos`, OT role accounts | CWE-798; ATT&CK T1078 / T1550 | `group_vars` / vault | SO authentication datasets; one credential used across many hosts |
| 10 | `is-inet` | **Email ingress** — inbound mail to both domains is the designed phishing path | ATT&CK T1566 | scenario mail host | `soc-sensor-corp` / `soc-sensor-fops`; mail flow |

### Using the catalogue

- An **OT / process-integrity** exercise draws on #1, #2, #5, #6.
- An **ATC** exercise draws on #3 and (opt-in) #4.
- An **identity / cross-domain** exercise draws on #7, #8, #9, #10.

Each row names the role or inventory group that produces the weakness, so an
author can enable, disable or harden it deliberately rather than by accident.
