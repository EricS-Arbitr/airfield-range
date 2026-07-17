(*
  fuel_farm.st  --  OpenPLC v3 Structured Text program for the fuel farm.
  Interlock logic from build sheet §4 (Pump permissive, rack overfill,
  tank LL, ESD latch). Tag-to-Modbus binding is authoritative here: it
  MUST match group_vars/fuel.yml (fuel_modbus_coils / _discrete_inputs /
  _input_registers / _holding_registers) exactly.

  Address map:
    %QX0.0-%QX1.1  --> Modbus coils     0-9   (fuel_modbus_coils)
    %IX0.0-%IX1.2  --> Modbus DIs       0-10  (fuel_modbus_discrete_inputs)
    %IW0-%IW10     --> Modbus IRs       0-10  (fuel_modbus_input_registers)
    %QW0-%QW7      --> Modbus HRs       0-7   (fuel_modbus_holding_registers)

  Register scaling (T101_LEVEL 0-10000 = 0.00-100.00%, TEMP 0.1 degF/count,
  HEADER_PRESS 0.1 psi/count) is applied by consumers -- the ST program
  works in raw counts. Totalizers are 32-bit split hi-first
  (fuel_totalizer_word_order = "hi_first").
*)

PROGRAM main
  VAR
    (* --- Command coils (%QX) --- fuel_modbus_coils addr 0-9 --- *)
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

    (* --- Status discrete inputs (%IX) --- fuel_modbus_discrete_inputs 0-10 --- *)
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

    (* --- Analog input registers (%IW) --- fuel_modbus_input_registers 0-10 --- *)
    T101_LEVEL      AT %IW0  : INT;
    T102_LEVEL      AT %IW1  : INT;
    T103_LEVEL      AT %IW2  : INT;
    T101_TEMP       AT %IW3  : INT;
    LR1_FLOW        AT %IW4  : INT;
    LR2_FLOW        AT %IW5  : INT;
    HEADER_PRESS    AT %IW6  : INT;
    LR1_METER_LO    AT %IW7  : INT;
    LR1_METER_HI    AT %IW8  : INT;
    LR2_METER_LO    AT %IW9  : INT;
    LR2_METER_HI    AT %IW10 : INT;

    (* --- Setpoint holding registers (%QW) --- fuel_modbus_holding_registers 0-7 --- *)
    LR1_PRESET_GAL     AT %QW0 : INT;
    LR2_PRESET_GAL     AT %QW1 : INT;
    LR1_ACTIVE_TRUCK   AT %QW2 : INT;
    LR2_ACTIVE_TRUCK   AT %QW3 : INT;
    LR1_SRC_TANK       AT %QW4 : INT;
    LR2_SRC_TANK       AT %QW5 : INT;
    PUMP_LEADLAG_SEL   AT %QW6 : INT;
    SYS_MODE           AT %QW7 : INT;

    (* Interlock scratch *)
    LR1_PERMIT : BOOL;
    LR2_PERMIT : BOOL;
  END_VAR

  (* --- Pump permissive =
       GROUND_OK AND DEADMAN AND NOT OVERFILL AND (TANK_LVL > LL) AND NOT ESD --- *)
  LR1_PERMIT := LR1_GROUND_OK AND LR1_DEADMAN
                AND NOT LR1_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  LR2_PERMIT := LR2_GROUND_OK AND LR2_DEADMAN
                AND NOT LR2_OVERFILL
                AND NOT T101_LO_LVL
                AND NOT ESD_ACTIVE;

  (* Latch load valves off if permissive breaks *)
  IF NOT LR1_PERMIT THEN
    LR1_LOAD_VLV := FALSE;
  END_IF;
  IF NOT LR2_PERMIT THEN
    LR2_LOAD_VLV := FALSE;
  END_IF;

  (* Global ESD latch: everything off until ESD_RESET pulses. Ladder-side
     lead/lag rotation and header-pressure hysteresis are TODO -- MVP only
     enforces the kill paths. *)
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
  RESOURCE Res0 ON CPU
    TASK main_task (INTERVAL := T#100ms, PRIORITY := 1);
    PROGRAM main_inst WITH main_task : main;
  END_RESOURCE
END_CONFIGURATION
