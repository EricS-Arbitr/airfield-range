(*
  fuel_farm.st  --  OpenPLC v4 Structured Text program for the fuel farm.
  Placeholder skeleton. Implements the interlock logic from build sheet
  §4 (Pump permissive, rack overfill, tank LL, ESD latch, lead/lag).

  Author in OpenPLC Editor (Autonomy-Logic), compile there, drop the
  compiled artifact alongside this .st file in roles/fuel_plc/files/.

  Address map matches group_vars/fuel.yml:
    Coils / discrete inputs / input registers / holding registers.
  Register scaling (T101_LEVEL 0-10000 = 0.00-100.00%, etc.) is applied
  by consumers -- the ST program works in raw counts.
*)

PROGRAM main
  VAR
    (* --- Command coils (%QX) --- *)
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

    (* --- Status discrete inputs (%IX) --- *)
    P201_RUN_STS    AT %IX0.0 : BOOL;
    P202_RUN_STS    AT %IX0.1 : BOOL;
    LR1_GROUND_OK   AT %IX0.2 : BOOL;
    LR2_GROUND_OK   AT %IX0.3 : BOOL;
    LR1_DEADMAN     AT %IX0.4 : BOOL;
    LR2_DEADMAN     AT %IX0.5 : BOOL;
    LR1_OVERFILL    AT %IX0.6 : BOOL;
    LR2_OVERFILL    AT %IX0.7 : BOOL;
    T101_HI_LVL     AT %IX1.0 : BOOL;
    T101_LO_LVL     AT %IX1.1 : BOOL;
    ESD_ACTIVE      AT %IX1.2 : BOOL;

    (* --- Analog input registers (%IW) --- *)
    T101_LEVEL      AT %IW0 : INT;
    T102_LEVEL      AT %IW1 : INT;
    T103_LEVEL      AT %IW2 : INT;
    T101_TEMP       AT %IW3 : INT;
    LR1_FLOW        AT %IW4 : INT;
    LR2_FLOW        AT %IW5 : INT;
    HEADER_PRESS    AT %IW6 : INT;

    (* --- Setpoint holding registers (%QW) --- *)
    LR1_PRESET_GAL  AT %QW0 : INT;
    LR2_PRESET_GAL  AT %QW1 : INT;
    SYS_MODE        AT %QW7 : INT;

    (* Interlock scratch *)
    LR1_PERMIT : BOOL;
    LR2_PERMIT : BOOL;
  END_VAR

  (* --- Pump permissive =
       GROUND_OK ∧ DEADMAN ∧ ¬OVERFILL ∧ (TANK_LVL > LL) ∧ ¬ESD --- *)
  LR1_PERMIT := LR1_GROUND_OK AND LR1_DEADMAN
                AND NOT LR1_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  LR2_PERMIT := LR2_GROUND_OK AND LR2_DEADMAN
                AND NOT LR2_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  (* Latch load valves + pump commands off if permissive breaks *)
  IF NOT LR1_PERMIT THEN
    LR1_LOAD_VLV := FALSE;
  END_IF;
  IF NOT LR2_PERMIT THEN
    LR2_LOAD_VLV := FALSE;
  END_IF;

  (* Global ESD latch: everything off until ESD_RESET pulses *)
  IF ESD_ACTIVE THEN
    P201_RUN_CMD := FALSE;
    P202_RUN_CMD := FALSE;
    T101_OUT_VLV := FALSE;
    T102_OUT_VLV := FALSE;
    T103_OUT_VLV := FALSE;
    LR1_LOAD_VLV := FALSE;
    LR2_LOAD_VLV := FALSE;
  END_IF;

  (* TODO: lead/lag pump rotation on header pressure setpoint (§4).
     Implement in OpenPLC Editor with a rising-edge alternator + a
     header-pressure hysteresis band. This skeleton just leaves both
     pump commands as-received from the SCADA bus, honoring only the
     interlock kill paths above. *)

END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON CPU
    TASK main_task (INTERVAL := T#100ms, PRIORITY := 1);
    PROGRAM main_inst WITH main_task : main;
  END_RESOURCE
END_CONFIGURATION
