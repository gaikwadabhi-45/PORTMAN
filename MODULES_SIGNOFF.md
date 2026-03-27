# PORTMAN - Module Sign-Off Document

**Document Date:** 24-March-2026
**Modules Covered:** VC01, VCN01, LDUD01, MBC01, LUEU01, RP01
**System:** PORTMAN - Port Management System

---

## Table of Contents

1. [VC01 - Vessel Creation](#1-vc01---vessel-creation)
2. [VCN01 - Vessel Call Number](#2-vcn01---vessel-call-number)
3. [LDUD01 - Loading / Discharge / Unloading / Delivery](#3-ldud01---loading--discharge--unloading--delivery)
4. [MBC01 - Motor Barge / Craft Operations](#4-mbc01---motor-barge--craft-operations)
5. [LUEU01 - Load / Unload Equipment Utilisation](#5-lueu01---load--unload-equipment-utilisation)
6. [RP01 - Reports](#6-rp01---reports)
7. [Masters Reference](#7-masters-reference)

---

## 1. VC01 - Vessel Creation

**Purpose:** Register and maintain vessel master data used across all operational modules.
**Auto-numbering:** VM1, VM2, VM3...
**Applies to:** Both Import & Export (master data)

### Header Fields

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Doc No | Auto | — | Read-only, system generated |
| 2 | Vessel Name | Free text | — | Primary identifier |
| 3 | IMO No | Free text | — | International Maritime Organisation number |
| 4 | Call Sign | Free text | — | Radio call sign |
| 5 | MMSI No | Free text | — | Maritime Mobile Service Identity |
| 6 | Vessel Type | Dropdown | Vessel Type Master (VTM01) | e.g. Bulk Carrier, Tanker |
| 7 | Vessel Category | Dropdown | Vessel Category Master (VCM01) | e.g. Geared, Gearless |
| 8 | Flag / Nationality | Dropdown | Vessel Flag Master (VFM01) | Country flag |
| 9 | GT (Gross Tonnage) | Number | — | |
| 10 | DWT (Deadweight) | Number | — | |
| 11 | LOA (Length Overall) | Number | — | In metres |
| 12 | Beam | Number | — | In metres |
| 13 | No. of Holds | Number | — | Used for stowage plan validation in VCN01 |
| 14 | No. of Cranes | Number | — | Vessel-mounted cranes |
| 15 | Year of Built | Number | — | |
| 16 | Doc Status | Dropdown | Pending / Approved / Rejected | |
| 17 | Created By | Auto | — | Logged-in user |
| 18 | Created Date | Auto | — | Today's date |

---

## 2. VCN01 - Vessel Call Number

**Purpose:** Register a vessel call (arrival notice) for import or export operations. Links vessel, agent, stevedore, cargo declarations and stowage plan.
**Auto-numbering:** VCN-2526-001, VCN-2526-002... (financial year based)
**Workflow:** Draft → Approved

### 2.1 Header Fields

| # | Field | Type | Dropdown Source | Import | Export | Remarks |
|---|-------|------|----------------|--------|--------|---------|
| 1 | VCN Doc No | Auto | — | ✓ | ✓ | Read-only |
| 2 | Operation Type | Dropdown | Import / Export | ✓ | ✓ | Determines which cargo declaration tab is shown |
| 3 | Vessel | Dropdown | Vessel Creation (VC01) | ✓ | ✓ | |
| 4 | Agent | Dropdown | Vessel Agent Master (VAM01) | ✓ | ✓ | Shipping agent |
| 5 | Stevedore | Dropdown | Importer/Exporter Master (VIEM01) | ✓ | ✓ | |
| 6 | Customer | Dropdown | Customer Master (CRM01) | ✓ | ✓ | |
| 7 | Cargo Type | Dropdown | Cargo Master (VCG01) | ✓ | ✓ | e.g. Dry Bulk, Liquid |
| 8 | Type of Discharge | Dropdown | Discharge Type Master (VDM01) | ✓ | ✓ | e.g. Grab, Conveyor |
| 9 | Doc Series | Dropdown | VCN Doc Series Master (VCDS01) | ✓ | ✓ | |
| 10 | Load Port | Dropdown | Port Master (VPM01) | ✓ | ✓ | Origin port |
| 11 | Discharge Port | Dropdown | Port Master (VPM01) | ✓ | ✓ | Destination port |
| 12 | Doc Date | Date picker | — | ✓ | ✓ | |
| 13 | Doc Status | System | Draft / Approved | ✓ | ✓ | Controlled by approval workflow |

### 2.2 Nominations

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | ETA | Datetime | — | Estimated Time of Arrival |
| 2 | ETD | Datetime | — | Estimated Time of Departure |
| 3 | Vessel Run Type | Dropdown | Run Type Master (VSDM01) | e.g. Direct, Transhipment |
| 4 | Arrival Fore Draft | Number | — | In metres |
| 5 | Arrival After Draft | Number | — | In metres |

### 2.3 Anchorage

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Anchorage Name | Dropdown | Anchorage Master (VANM01) | e.g. Inner Anchorage, Outer Anchorage |
| 2 | Anchorage Arrival | Datetime | — | |
| 3 | Anchorage Departure | Datetime | — | |

### 2.4 Delays

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Delay Name | Dropdown | Delay Master (PDM01) | e.g. Weather, Tidal, Equipment |
| 2 | Delay Start | Datetime | — | |
| 3 | Delay End | Datetime | — | |

### 2.5 Cargo Declaration — Import

**Shown when:** Operation Type = Import

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Cargo Name | Dropdown | Cargo Master (VCG01) | |
| 2 | Customer | Dropdown | Customer Master (CRM01) | Consignee per cargo line |
| 3 | B/L No | Free text | — | Bill of Lading number |
| 4 | B/L Date | Date picker | — | |
| 5 | B/L Quantity | Number | — | |
| 6 | UOM | Dropdown | UOM Master (VCUM01) | e.g. MT, KG |
| 7 | IGM Number | Free text | — | Import General Manifest |
| 8 | IGM Manual No | Free text | — | Manual reference |
| 9 | IGM Date | Date picker | — | |

### 2.6 Cargo Declaration — Export

**Shown when:** Operation Type = Export

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | EGM / Shipping Bill No | Free text | — | Export General Manifest |
| 2 | EGM / Shipping Bill Date | Date picker | — | |
| 3 | Cargo Name | Dropdown | Cargo Master (VCG01) | |
| 4 | Customer | Dropdown | Customer Master (CRM01) | |
| 5 | B/L No | Free text | — | |
| 6 | B/L Date | Date picker | — | |
| 7 | B/L Quantity | Number | — | |
| 8 | UOM | Dropdown | UOM Master (VCUM01) | |

### 2.7 Stowage Plan

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Cargo Name | Dropdown | From this VCN's cargo declarations | Auto-populated |
| 2 | Hold Name | Dropdown | Hold Master (VHM01) | e.g. Hold 1, Hold 2 |
| 3 | Hatchwise Quantity | Number | — | Cannot exceed total B/L qty |
| 4 | Hatch Completion Time | Datetime | — | |

**Approval Requirements:**
- Operation Type, Vessel, Agent, Stevedore, Cargo Type, Discharge Port must be filled
- Minimum 1 complete Cargo Declaration entry (cargo name, B/L no, date, quantity, UOM)
- All vessel holds must be covered in Stowage Plan with cargo and quantity

---

## 3. LDUD01 - Loading / Discharge / Unloading / Delivery

**Purpose:** Track the full operational lifecycle of a vessel call — from anchorage arrival through discharge/loading to final hold completion. Links to an approved VCN.
**Auto-numbering:** LDUD-2526-001, LDUD-2526-002... (financial year based)
**Workflow:** Draft → Closed / Partial Close

### 3.1 Header Fields

| # | Field | Type | Dropdown Source | Import | Export | Remarks |
|---|-------|------|----------------|--------|--------|---------|
| 1 | Doc No | Auto | — | ✓ | ✓ | Read-only |
| 2 | VCN | Dropdown | Approved VCN entries | ✓ | ✓ | Auto-fills vessel name, anchored datetime, operation type |
| 3 | Operation Type | Auto | — | ✓ | ✓ | Inherited from VCN |
| 4 | Vessel Name | Auto | — | ✓ | ✓ | Auto-filled from VCN |
| 5 | Anchored DateTime | Auto | — | ✓ | ✓ | From VCN anchorage |
| 6 | Arrival Inner Anchorage | Datetime | — | ✓ | ✓ | |
| 7 | Arrival Outer Anchorage | Datetime | — | ✓ | ✓ | |
| 8 | Arrived MBPT | Datetime | — | ✓ | ✓ | |
| 9 | Arrived MFL | Datetime | — | ✓ | ✓ | |
| 10 | Free Pratique Granted | Datetime | — | ✓ | ✓ | Health clearance |
| 11 | NOR Tendered | Datetime | — | ✓ | ✓ | Notice of Readiness — required for closure |
| 12 | NOR Accepted | Datetime | — | ✓ | ✓ | |
| 13 | Discharge Commenced | Datetime | — | ✓ | — | Import only |
| 14 | Discharge Completed | Datetime | — | ✓ | — | Import only |
| 15 | Custom Clearance | Datetime | — | ✓ | ✓ | |
| 16 | Agent/Stevedore Onboard | Datetime | — | ✓ | ✓ | |
| 17 | Material PO Number | Free text | — | ✓ | ✓ | SAP PO reference |
| 18 | Initial Draft Survey From | Free text | — | ✓ | ✓ | Draft reading |
| 19 | Initial Draft Survey To | Free text | — | ✓ | ✓ | |
| 20 | Initial Draft Survey Qty | Number | — | ✓ | ✓ | Calculated tonnage |
| 21 | Final Draft Survey From | Free text | — | ✓ | ✓ | |
| 22 | Final Draft Survey To | Free text | — | ✓ | ✓ | |

**Computed fields shown in list view (not editable):**
- Cargo Names, B/L Quantities (from VCN cargo declarations)
- Balance (B/L Qty minus MV Anchorage Ops Qty)
- Agent Name, Stevedore Name (from VCN)
- Ops Started / Ops Completed (from anchorage recording)

### 3.2 Anchorage Recording

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Anchorage Name | Dropdown | Anchorage Master (VANM01) | |
| 2 | Cargo Name | Dropdown | From VCN cargo declarations | |
| 3 | Anchored | Datetime | — | |
| 4 | Discharge/Load Started | Datetime | — | Required for closure |
| 5 | Discharge/Load Commenced | Datetime | — | |
| 6 | Anchor Aweigh | Datetime | — | |
| 7 | Cargo Quantity | Number | — | |

### 3.3 MV Anchorage Discharge / Loading

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Hold Name | Dropdown | Hold Master (VHM01) | |
| 2 | Cargo Name | Dropdown | From VCN cargo declarations | |
| 3 | Start Time | Datetime | — | |
| 4 | End Time | Datetime | — | |
| 5 | Quantity | Number | — | Per-hold discharge/load quantity |

### 3.4 Barge Lines — Import

**Shown when:** Operation Type = Import

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Trip No | Auto | — | Auto-incremented per barge |
| 2 | Hold Name | Dropdown | Hold Master (VHM01) | |
| 3 | Barge Name | Dropdown | Barge Master (PBM01) | |
| 4 | Contractor | Dropdown | Contractor Master | |
| 5 | Cargo Name | Dropdown | From VCN cargo declarations | |
| 6 | Crane Loaded From | Free text | — | |
| 7 | BPT / BFL | Datetime | — | Barge Pilot Time |
| 8 | Alongside Vessel | Datetime | — | |
| 9 | Commenced Loading | Datetime | — | |
| 10 | Completed Loading | Datetime | — | |
| 11 | Cast Off MV | Datetime | — | |
| 12 | Anchored Gull Island | Datetime | — | |
| 13 | Aweigh Gull Island | Datetime | — | |
| 14 | Alongside Berth | Datetime | — | |
| 15 | Commence Discharge Berth | Datetime | — | |
| 16 | Completed Discharge Berth | Datetime | — | |
| 17 | Cast Off Berth | Datetime | — | |
| 18 | Cast Off Berth (NT) | Datetime | — | Night time cast off |
| 19 | Discharge Quantity | Number | — | Quantity discharged |

### 3.5 Barge Lines — Export

**Shown when:** Operation Type = Export

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Trip No | Auto | — | |
| 2 | Hold Name | Dropdown | Hold Master (VHM01) | |
| 3 | Barge Name | Dropdown | Barge Master (PBM01) | |
| 4 | Contractor | Dropdown | Contractor Master | |
| 5 | Cargo Name | Dropdown | From VCN cargo declarations | |
| 6 | Trip Start | Datetime | — | |
| 7 | AMF at Port | Datetime | — | All Made Fast at port |
| 8 | Cast Off Port | Datetime | — | |
| 9 | Port Crane | Free text | — | Crane used at port |
| 10 | Anchored Gull Island (Empty) | Datetime | — | |
| 11 | Aweigh Gull Island (Empty) | Datetime | — | |
| 12 | Alongside Vessel | Datetime | — | |
| 13 | Commenced Loading | Datetime | — | Loading onto vessel |
| 14 | Completed Loading | Datetime | — | |
| 15 | Cast Off MV | Datetime | — | |
| 16 | Cast Off Loading Berth | Datetime | — | |
| 17 | Discharge Quantity | Number | — | |

### 3.6 Delays

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Delay Name | Dropdown | Delay Master (PDM01) | |
| 2 | Start DateTime | Datetime | — | |
| 3 | End DateTime | Datetime | — | |
| 4 | Total Time (Minutes) | Auto-calculated | — | Computed from start/end |
| 5 | Total Time (Hours) | Auto-calculated | — | Computed from start/end |
| 6 | Minus Delay Hours | Free text | — | Deductible delay |
| 7 | Crane Number | Free text | — | |
| 8 | Delay Account Type | Dropdown | Delay Account Type Master | |
| 9 | Equipment Name | Dropdown | Equipment Master (VEM01) | |
| 10 | Delays to SOF | Free text | — | Statement of Facts reference |
| 11 | Invoiceable | Dropdown | Yes / No | |

### 3.7 Barge Cleaning

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Barge Name | Dropdown | Barge Master (PBM01) | |
| 2 | Payloader Name | Dropdown | Payloader Master (PPL01) | |
| 3 | HMR Start | Number | — | Hour Meter Reading |
| 4 | HMR End | Number | — | |
| 5 | Diesel Start | Free text | — | Diesel level |
| 6 | Diesel End | Free text | — | |
| 7 | Start Time | Datetime | — | |
| 8 | End Time | Datetime | — | |

### 3.8 Hold Discharge / Loading Completion

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Hold Name | Dropdown | Hold Master (VHM01) | |
| 2 | Commenced | Datetime | — | Required for closure |
| 3 | Completed | Datetime | — | Required for closure |

### 3.9 Hold-Cargo Configuration

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Hold Name | Dropdown | Hold Master (VHM01) | One cargo per hold |
| 2 | Cargo Name | Dropdown | From VCN cargo declarations | Assigns cargo to hold |

**Closure Requirements:**
- Vessel Name must be populated
- NOR Tendered must be filled
- At least 1 Anchorage Recording entry with Discharge/Load Started
- At least 1 MV Anchorage Discharge/Loading entry
- At least 1 Barge Line entry
- All Hold Completion entries must have both Commenced and Completed
- **Full Close** only when LUEU total quantity = B/L total quantity; otherwise **Partial Close**

---

## 4. MBC01 - Motor Barge / Craft Operations

**Purpose:** Track MBC (Motor Barge/Craft) movements for coastal cargo transport. Covers load port → discharge port lifecycle.
**Auto-numbering:** Based on doc series (e.g. MBC0001, MBC0002...)
**Workflow:** Draft → Approved

### 4.1 Header Fields

| # | Field | Type | Dropdown Source | Import | Export | Remarks |
|---|-------|------|----------------|--------|--------|---------|
| 1 | Doc No | Auto | — | ✓ | ✓ | Based on selected Doc Series |
| 2 | Doc Series | Dropdown | MBC Doc Series Master (MBCDS01) | ✓ | ✓ | |
| 3 | Operation Type | Dropdown | Import / Export | ✓ | ✓ | Determines which sub-sections are shown |
| 4 | MBC Name | Dropdown | MBC Master (MBCM01) | ✓ | ✓ | Barge/Craft name |
| 5 | Cargo Type | Dropdown | Cargo Master (VCG01) | ✓ | ✓ | |
| 6 | Cargo Name | Dropdown | Cargo Master (VCG01) | ✓ | ✓ | |
| 7 | B/L Quantity | Number | — | ✓ | ✓ | |
| 8 | UOM | Dropdown | UOM Master (VCUM01) | ✓ | ✓ | |
| 9 | Doc Date | Date picker | — | ✓ | ✓ | Defaults to today |
| 10 | Doc Status | System | Pending / Approved | ✓ | ✓ | |

### 4.2 Load Port Lines — Import

**Shown when:** Operation Type = Import

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | ETA | Datetime | — | Estimated arrival at load port |
| 2 | Arrived Load Port | Datetime | — | |
| 3 | Alongside Berth | Datetime | — | |
| 4 | Loading Commenced | Datetime | — | |
| 5 | Loading Completed | Datetime | — | |
| 6 | Cast Off Load Port | Datetime | — | |

### 4.3 Discharge Port Lines — Import

**Shown when:** Operation Type = Import

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Arrival Gull Island | Datetime | — | |
| 2 | Departure Gull Island | Datetime | — | |
| 3 | Arrived Yellow Crane | Datetime | — | |
| 4 | Vessel Arrival Port | Datetime | — | |
| 5 | Vessel All Made Fast | Datetime | — | |
| 6 | Unloading Commenced | Datetime | — | |
| 7 | Unloading Completed | Datetime | — | |
| 8 | Cleaning Commenced | Datetime | — | |
| 9 | Cleaning Completed | Datetime | — | |
| 10 | Vessel Cast Off | Datetime | — | |
| 11 | Sailed Out to Load Port | Datetime | — | |
| 12 | Unloaded By | Dropdown | Equipment / Crane list | |
| 13 | Unloading Berth | Dropdown | Berth Master (PBM01) | |
| 14 | Discharge Stop Shifting | Datetime | — | |
| 15 | Discharge Start Shifting | Datetime | — | |

### 4.4 Export Load Port Lines

**Shown when:** Operation Type = Export

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Arrived at Port | Datetime | — | |
| 2 | Alongside at Berth | Datetime | — | |
| 3 | Loading Commenced | Datetime | — | |
| 4 | Loading Completed | Datetime | — | |
| 5 | Cast Off from Berth | Datetime | — | |
| 6 | Sailed Out from Port | Datetime | — | |
| 7 | ETA at Gull Island | Datetime | — | |
| 8 | Unloaded By | Dropdown | Equipment / Crane list | |
| 9 | Berth Master | Free text | — | |

### 4.5 Cleaning Details

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Payloader Name | Dropdown | Payloader Master (PPL01) | |
| 2 | HMR Start | Number | — | Hour Meter Reading |
| 3 | HMR End | Number | — | |
| 4 | Diesel Start | Free text | — | |
| 5 | Diesel End | Free text | — | |
| 6 | Start Time | Datetime | — | |
| 7 | End Time | Datetime | — | |

### 4.6 Customer Details

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Customer Name | Dropdown | Customer Master (CRM01) | Required for approval |
| 2 | Cargo Name | Dropdown | Cargo Master (VCG01) | |
| 3 | Bill of Coastal Goods No | Free text | — | |
| 4 | Quantity | Number | — | |
| 5 | Material PO | Free text | — | SAP PO reference |

**Approval Requirements:**
- Operation Type, MBC Name, Cargo Name, B/L Quantity must be filled
- At least 1 Customer Detail entry

---

## 5. LUEU01 - Load / Unload Equipment Utilisation

**Purpose:** Daily equipment utilisation log — tracks what each piece of equipment (crane, grab, conveyor, etc.) did per shift. Lines are linked to VCN or MBC source documents. Used as the basis for billing.
**No document number** — operates at line level, filtered by equipment.

### 5.1 Line Fields

| # | Field | Type | Dropdown Source | Remarks |
|---|-------|------|----------------|---------|
| 1 | Source Type | Dropdown | VCN / MBC | Links to a Vessel Call or Motor Barge |
| 2 | Source Document | Dropdown | VCN list or MBC list | e.g. "VCN-2526-001 / MV Stellar" |
| 3 | Entry Date | Date picker | — | Shift date |
| 4 | Shift | Dropdown | Day / Night | |
| 5 | From Time | Time picker | — | Shift start time |
| 6 | To Time | Time picker | — | Shift end time |
| 7 | Equipment Name | Dropdown | Equipment Master (VEM01) | e.g. Crane 1, Grab 2 |
| 8 | Operator Name | Dropdown | Shift Operator Master (PSM01) | |
| 9 | Barge Name | Dropdown | Barges from LDUD (for VCN) / MBC Master (for MBC) | Format: "Barge / Trip" for VCN source |
| 10 | Cargo Name | Dropdown | From VCN or MBC cargo declarations | |
| 11 | Operation Type | Free text | — | e.g. Discharge, Loading |
| 12 | Quantity | Number | — | Quantity handled |
| 13 | UOM | Dropdown | UOM Master (VCUM01) | e.g. MT |
| 14 | Route Name | Dropdown | Conveyor Route Master | Conveyor route if applicable |
| 15 | System Name | Dropdown | Port Systems Master | Conveyor system |
| 16 | Berth Name | Dropdown | Berth Master (PBM01) | |
| 17 | Shift Incharge | Dropdown | Shift Incharge Master | |
| 18 | Delay Name | Dropdown | Delay Master (PDM01) | Delay reason if no quantity |
| 19 | Start Time | Datetime | — | Operation start |
| 20 | End Time | Datetime | — | Operation end |
| 21 | Remarks | Free text | — | |

### 5.2 Billing Fields (system-managed)

| # | Field | Remarks |
|---|-------|---------|
| 22 | Is Billed | Whether this line has been billed |
| 23 | Billed Quantity | Quantity billed (supports partial billing) |

### 5.3 Split Feature

An LUEU line can be **split** into two lines (e.g., for multi-customer allocation). The original line retains the remaining quantity, and a new child line is created with the split portion.

| # | Field | Remarks |
|---|-------|---------|
| 24 | Split Quantity | Quantity for this split portion |
| 25 | Split Remark | Reason for the split |

---

## 6. RP01 - Reports

**Purpose:** Reporting module for operational and management reports. PORTMAN has a minimal set of pre-built reports since most operational data originates from LUEU01 (Equipment Utilisation) and can be analysed using the built-in **Custom Report Designer** — a drag-and-drop pivot table where users can build their own reports from any data source without developer involvement.

### 6.1 Pre-Built Reports

| # | Report | Data Source | Description | Output |
|---|--------|-----------|-------------|--------|
| 1 | **Vessel Statement of Facts** | LDUD01, VCN01 | Official SOF document for any vessel call — arrival times, draft surveys, anchorage movements, discharge operations, idle delays, and hatch-wise cargo completion | Printable PDF |
| 2 | **Barge Statement of Facts** | LDUD01 | Barge-wise SOF — barge trip timings, loading/discharge at vessel and berth | Printable PDF |
| 3 | **MBC Statement of Facts** | MBC01 | SOF for MBC operations — load port and discharge port timings (Import), or load port details (Export) | Printable PDF |
| 4 | **Mother Vessel Discharged Report** | LDUD01, VCN01 | Date-range filtered list of discharged vessels. Day-wise discharge, delay log, delay classification by account type, and performance block | Excel download |
| 5 | **MBC TAT Report** | MBC01 | Trip-wise master data and aggregated Turn Around Time (TAT) summary for Import MBC operations. Two sheets: Master Data and TAT averages (date, MTD, YTD) | Excel download |
| 6 | **Barge Lines Report** | LDUD01 | Barge line details for Import or Export vessel calls. Select one or more vessels to see the full barge line data with vessel header repeated per entry | On-screen table |
| 7 | **Daily Operations Report** | LUEU01, LDUD01 | Daily summary of vessel operations — arrivals, departures, cargo operations, and idle times. Day-wise details and performance metrics | Excel download |
| 8 | **Shift Report** | LUEU01 | Per-shift cargo quantities pivoted by equipment and route, with detailed delay breakdowns by type. Select date and shift to preview or download | On-screen + Excel |

### 6.2 Custom Report Designer

Users can build their own pivot reports without any technical knowledge:

- **Drag-and-drop** fields into rows, columns, and value areas
- Choose from any data source (LUEU lines, Vessel calls, MBC operations, etc.)
- Select aggregation type (Sum, Count, Average, Min, Max)
- Switch between table view and chart types (Bar, Line, Pie, etc.)
- **Save configurations** for reuse — named reports that can be loaded anytime

### 6.3 Dashboard Widgets

Interactive dashboard with configurable widgets — pivot tables and live charts that can be arranged and customised per user preference.

---

## 7. Masters Reference

All dropdown masters used across the five modules:

| Master | Module Code | Used In |
|--------|-----------|---------|
| Vessel Type Master | VTM01 | VC01 (Vessel Type) |
| Vessel Category Master | VCM01 | VC01 (Vessel Category) |
| Vessel Flag Master | VFM01 | VC01 (Nationality) |
| Vessel Agent Master | VAM01 | VCN01 (Agent) |
| Importer/Exporter Master | VIEM01 | VCN01 (Stevedore) |
| Customer Master | CRM01 | VCN01, MBC01, LUEU01 (Customer) |
| Cargo Master | VCG01 | VCN01, MBC01, LDUD01, LUEU01 (Cargo Type & Cargo Name) |
| UOM Master | VCUM01 | VCN01, MBC01, LUEU01 (Quantity UOM) |
| Discharge Type Master | VDM01 | VCN01 (Type of Discharge) |
| Hold Master | VHM01 | VCN01 (Stowage Plan), LDUD01 (Barge/Ops/Hold Completion) |
| Anchorage Master | VANM01 | VCN01, LDUD01 (Anchorage) |
| Port Master | VPM01 | VCN01 (Load Port, Discharge Port) |
| VCN Doc Series Master | VCDS01 | VCN01 (Doc Series) |
| Run Type Master | VSDM01 | VCN01 (Nominations) |
| Delay Master | PDM01 | VCN01, LDUD01, LUEU01 (Delays) |
| Berth Master | PBM01 | MBC01, LUEU01 (Berth) |
| MBC Master | MBCM01 | MBC01 (MBC Name) |
| MBC Doc Series Master | MBCDS01 | MBC01 (Doc Series) |
| Payloader Master | PPL01 | LDUD01, MBC01 (Cleaning) |
| Equipment Master | VEM01 | LDUD01 (Delays), LUEU01 (Equipment) |
| Shift Operator Master | PSM01 | LUEU01 (Operator) |
| Shift Incharge Master | — | LUEU01 (Shift Incharge) |
| Port Systems Master | — | LUEU01 (System Name) |
| Conveyor Route Master | — | LUEU01 (Route Name) |
| Barge Master | — | LDUD01 (Barge Lines) |
| Contractor Master | — | LDUD01 (Barge Lines) |
| Delay Account Type Master | — | LDUD01 (Delays) |

---

## Module Flow

```
VC01 (Vessel Master)
  └──▶ VCN01 (Vessel Call Number) — links vessel
         ├── Nominations
         ├── Anchorage
         ├── Delays
         ├── Cargo Declaration (Import / Export)
         └── Stowage Plan
              │
              ▼
         LDUD01 (Discharge/Loading Operations) — links to approved VCN
         ├── Anchorage Recording
         ├── MV Anchorage Discharge/Loading
         ├── Barge Lines (Import / Export)
         ├── Delays
         ├── Barge Cleaning
         ├── Hold Completion
         └── Hold-Cargo Config
              │
              ▼
         LUEU01 (Equipment Utilisation) — source: VCN or MBC
              │
              ├──▶ FIN01 (Billing) → FINV01 (Invoicing)
              │
              └──▶ RP01 (Reports)
                   ├── Pre-built: Vessel SOF, MBC SOF, Discharged, TAT, etc.
                   ├── Custom Report Designer (user-built pivot reports)
                   └── Dashboard Widgets

MBC01 (Motor Barge/Craft) — independent of VCN
  ├── Load Port Lines (Import)
  ├── Discharge Port Lines (Import)
  ├── Export Load Port Lines (Export)
  ├── Cleaning Details
  └── Customer Details
       │
       ▼
  LUEU01 (Equipment Utilisation) — source: MBC
```

---

**End of Document**
