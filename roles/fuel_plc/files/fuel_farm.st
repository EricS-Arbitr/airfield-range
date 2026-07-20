(*
  fuel_farm.st -- OpenPLC v3 Structured Text program for the fuel farm.
  Interlock logic from build sheet section 4 (Pump permissive, rack
  overfill, tank LL, ESD latch). Tag-to-Modbus binding is authoritative
  here: it MUST match group_vars/fuel.yml (fuel_modbus_coils /
  discrete_inputs / input_registers / holding_registers) exactly.

  Address map (post-2026-07-20 reshuffle so inputs read real
  slave-polled data instead of unbound zeros):
    QX0.0-QX1.1        --> Modbus coils    0-9    (ST OUTPUTS -- pump/valve commands)
    IX100.0-IX101.2    --> Modbus DIs      800-810 (fuelsim mirror -- status feedback)
    IW100-IW109        --> Modbus IRs      100-109 (fuelsim mirror -- analog measurements)
    QW0-QW7            --> Modbus HRs      0-7    (ST OUTPUTS -- setpoints, unused)
  Interlock LOGIC references variable names not addresses, so only
  the AT bindings change; no logic edits needed.

  NOTE on IW10: the meter totalizer high-word for rack 2 (LR2_METER_HI)
  would sit at input register 10, but matiec on the fdamador image
  refuses multi-digit "0-9" addresses in some cases (parses %IW10 as
  %IW1 followed by 0 -> recovery -> cascading downstream syntax errors).
  Until we upgrade to a matiec build that reliably handles multi-digit
  addresses, we expose IR 0-9 only and leave LR2_METER_HI's Modbus
  address unbound at the PLC. Reading through fuelsim directly still
  works because pymodbus doesn't share matiec's grammar.

  NOTE on % in comments: also stripped from this file's comments as a
  defensive move -- some matiec forks treat the percent character as
  reserved even inside comment blocks. Comments now say IW instead of
  percent-IW throughout.
*)

PROGRAM main
  VAR
    P201_RUN_CMD    AT %QX0.0 : BOOL;
    P202_RUN_CMD    AT %QX0.1 : BOOL;
    T101_OUT_VLV    AT %QX0.2 : BOOL;
    T102_OUT_VLV    AT %QX0.3 : BOOL;
    T103_OUT_VLV    AT %QX0.4 : BOOL;
    LR1_LOAD_VLV    AT %QX0.5 : BOOL;
    LR2_LOAD_VLV    AT %QX0.6 : BOOL;
    FSII_INJ_CMD    AT %QX0.7 : BOOL;
    ESD_RESET       AT %QX1.0 : BOOL;
    ALARM_ACK       AT %QX1.1 : BOOL;

    (* Input DIs (status feedback from fuelsim). Bound at %IX100.0+
       so the ST reads OpenPLC's slave-device-polling mirror of
       fuelsim's DI 0-10 (verified via probe c31d31d that matiec
       accepts triple-digit %IX addresses). Previous %IX0.x bindings
       always saw zero because OpenPLC's own DI 0-10 are unbound to
       any local IO on our software-only PLC. *)
    P201_RUN_STS    AT %IX100.0 : BOOL;
    P202_RUN_STS    AT %IX100.1 : BOOL;
    LR1_GROUND_OK   AT %IX100.2 : BOOL;
    LR2_GROUND_OK   AT %IX100.3 : BOOL;
    LR1_DEADMAN     AT %IX100.4 : BOOL;
    LR2_DEADMAN     AT %IX100.5 : BOOL;
    LR1_OVERFILL    AT %IX100.6 : BOOL;
    LR2_OVERFILL    AT %IX100.7 : BOOL;
    T101_HI_LVL     AT %IX101.0 : BOOL;
    T101_LO_LVL     AT %IX101.1 : BOOL;
    ESD_ACTIVE      AT %IX101.2 : BOOL;

    (* Input IRs (analog measurements from fuelsim). Bound at %IW100+
       for the same reason as the DIs above -- OpenPLC's own %IW0-9
       are unbound. *)
    T101_LEVEL      AT %IW100 : INT;
    T102_LEVEL      AT %IW101 : INT;
    T103_LEVEL      AT %IW102 : INT;
    T101_TEMP       AT %IW103 : INT;
    LR1_FLOW        AT %IW104 : INT;
    LR2_FLOW        AT %IW105 : INT;
    HEADER_PRESS    AT %IW106 : INT;
    LR1_METER_LO    AT %IW107 : INT;
    LR1_METER_HI    AT %IW108 : INT;
    LR2_METER_LO    AT %IW109 : INT;

    LR1_PRESET_GAL     AT %QW0 : INT;
    LR2_PRESET_GAL     AT %QW1 : INT;
    LR1_ACTIVE_TRUCK   AT %QW2 : INT;
    LR2_ACTIVE_TRUCK   AT %QW3 : INT;
    LR1_SRC_TANK       AT %QW4 : INT;
    LR2_SRC_TANK       AT %QW5 : INT;
    PUMP_LEADLAG_SEL   AT %QW6 : INT;
    SYS_MODE           AT %QW7 : INT;
  END_VAR

  (* Non-located scratch variables must live in a separate VAR block --
     matiec on the fdamador image can't parse a non-located declaration
     that follows a located one in the same block (grammar quirk: the
     parser enters "expecting AT %..." state after the first located var
     and errors with "invalid located variable declaration" on any plain
     name that follows). *)
  VAR
    LR1_PERMIT : BOOL;
    LR2_PERMIT : BOOL;
  END_VAR

  LR1_PERMIT := LR1_GROUND_OK AND LR1_DEADMAN
                AND NOT LR1_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  LR2_PERMIT := LR2_GROUND_OK AND LR2_DEADMAN
                AND NOT LR2_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  IF NOT LR1_PERMIT THEN
    LR1_LOAD_VLV := FALSE;
  END_IF;
  IF NOT LR2_PERMIT THEN
    LR2_LOAD_VLV := FALSE;
  END_IF;

  IF ESD_ACTIVE THEN
    P201_RUN_CMD := FALSE;
    P202_RUN_CMD := FALSE;
    T101_OUT_VLV := FALSE;
    T102_OUT_VLV := FALSE;
    T103_OUT_VLV := FALSE;
    LR1_LOAD_VLV := FALSE;
    LR2_LOAD_VLV := FALSE;
  END_IF;

END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 1);
    PROGRAM main_inst WITH main_task : main;
  END_RESOURCE
END_CONFIGURATION
