# Vision Core typedefs (decoded from LabVIEW type descriptors)

One section per `.ctl`. Labels and enum items are exact. Nested typedef names are not kept by LabVIEW in this format.

## alox_api_typedef_camera_status.ctl

- Camera Status: Cluster
  - Type: String
  - Status: String
  - ImageTotal: String
  - Timestamp: String
  - PixelSize: String
  - PixelUnits: String
  - ExposureTime: String

## alox_api_typedef_camz_hardware_warning.ctl

- CAMERA: Cluster
  - CAM SERVER STATUS: Cluster
    - SERVER: Boolean
    - CAM HIMAG STATUS: Boolean
    - CAM LOMAG STATUS: Boolean
    - Z STAGE STATUS: Boolean
    - HEARTBEAT: U32
  - CAM ERR: U32

## alox_api_typedef_error.ctl

- Error: Cluster
  - Source: String
  - Code: I32
  - Description: String

## alox_api_typedef_failed.ctl

- Failed: Cluster
  - Function: String
  - Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String

## alox_api_typedef_finished.ctl

- Finished: Cluster
  - Function: String
  - Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String

## alox_api_typedef_heartbeat.ctl

- Heartbeat: Cluster
  - Heartbeat: String

## alox_api_typedef_heartbeat_frequency.ctl

- Frequency: Cluster
  - Frequency: String

## alox_api_typedef_heartbeat_retries.ctl

- Retries: Cluster
  - Retries: String

## alox_api_typedef_image.ctl

- Image: Cluster
  - width: I32
  - height: I32
  - quality: I32
  - jpeg: String

## alox_api_typedef_measure_bowing.ctl

- Measure Bowing JSON Report: Cluster
  - Job: Cluster
    - Control_Job_ID: String
    - Process_Job_ID: String
    - Wafer_ID: String
    - Lot_ID: String
    - Zone_ID: String
  - Bowing: Cluster
    - zwafer1: String
    - zwafer2: String
    - zwafer3: String
    - zwafer4: String
    - Zwafer5: String
    - bowinga: String
    - bowinga: String
    - bowingunit: String
    - zwafer Unit: String
  - Parallelism: Cluster
    - zchuck1: String
    - zchuck3: String
    - zchuck4: String
    - zchuck5: String
    - parallelismb: String
    - parallelismb: String
    - parallelismunit: String
    - zchuck unit: String

## alox_api_typedef_measure_mesa.ctl

- Measure Mesa: Cluster
  - Job: Cluster
    - Control_Job_ID: String
    - Process_Job_ID: String
    - Wafer_ID: String
    - Lot_ID: String
    - Zone_ID: String
  - Timestamp: Cluster
    - Unix: String
  - External_Diameter: Cluster
    - Fitted: String
    - Unit: String
  - Oxyd_Diameter: Cluster
    - Fitted: String
    - Median: String
    - Average: String
    - Surface: String
    - Inscribed: String
    - Circumscribed: String
    - Circularity: String
    - Type_Measure_Rate: String
    - STDDEV FET/#: String
    - Unit: String
  - Oxyd_Rate: Cluster
    - Rate: String
    - Unit: String
    - Remaining_time: String
  - Mesa_Positions: Cluster
    - x: String
    - y: String
    - z: String
    - unit: String
  - Wavelength: Cluster
    - wl: String
    - unit: String

## alox_api_typedef_measure_tilt.ctl

- Measure Tilt: Cluster
  - Job: Cluster
    - Control_Job_ID: String
    - Process_Job_ID: String
    - Wafer_ID: String
    - Lot_ID: String
    - Zone_ID: String
  - Tilt: Cluster
    - Angle: String
    - Unit: String

## alox_api_typedef_started.ctl

- Started: Cluster
  - Function: String
  - Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String

## alox_api_typedef_status.ctl

- Status: Cluster
  - State: String

## alox_api_typedef_tilt.ctl

- Tilt Angle: Cluster
  - Tilt: String
  - Units: String

## alox_api_typedef_version.ctl

- Version: Cluster
  - Major: String
  - Minor: String
  - Build: String
  - API: String
  - Release Date: String

## alox_api_typedef_watchod_ui_visibility.ctl

- ui: Cluster
  - visible: Boolean

## alox_api_typedef_websocket_event.ctl

- Websocket Event: Cluster
  - Websocket Event Type: enum U16
      0 = heartbeat
      1 = status
      2 = measure tilt
      3 = measure bowing
      4 = measure mesa
      5 = finished
      6 = failed
      7 = error
      8 = started
      9 = camz_hardware_warning
      10 = xy_hardware_warning
      11 = monochro_hardware_warning
      12 = hardware_reset
      13 = mesa_status
      14 = tilt_status
      15 = bowing_status
      16 = image
  - Variant: Variant

## alox_api_typedef_websocket_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Is Running ?
    3 = Client Connected ?
    4 = Get Status

## alox_typedef_api_notifiers_manager_method.ctl

- Notifiers Method: enum U16
    0 = Init
    1 = Get Notifier
    2 = Get All Notifiers
    3 = Exit

## alox_typedef_autofocus_method.ctl

- Autofocus Method: enum U16
    0 = FFT
    1 = Sobel
    2 = Prewitt
    3 = Diff
    4 = Gradient
    5 = Sigma
    6 = FULL

## alox_typedef_autofocus_session.ctl

- AF Session: Cluster
  - Init Z Pos (mm): DBL
  - Init Expo: DBL
  - Init WL: U32
  - Focus Error: Boolean
  - Measure Error: Boolean
  - Zmin Timestamp: U32
  - Init Z OK: Boolean
  - Init Expo OK: Boolean
  - Init WL OK: Boolean

## alox_typedef_autofocus_speed.ctl

- Autofocus Speed: enum U16
    0 = Slow
    1 = Fast

## alox_typedef_boot_ctrls_refnums.ctl

- Controls Refnums: Cluster
  - Main VI: Refnum (kind 8)
  - Settings Sub Panel: Refnum (kind 8)
  - HL Sub Panel: Refnum (kind 8)
  - LL Sub Panel: Refnum (kind 8)
  - Alarm: Refnum (kind 8)
  - Display Refnum In: Refnum (kind 8: ImageControl:ImageControl)

## alox_typedef_bowing_measurement.ctl

- Bowing Measurement Report: Cluster
  - Zwafer2 (mm): DBL
  - Zwafer1 (mm): DBL
  - Zwafer3 (mm): DBL
  - Zwafer4 (mm): DBL
  - Zwafer5 (mm): DBL
  - Zchuck1 (mm): DBL
  - Zchuck3 (mm): DBL
  - Zchuck4 (mm): DBL
  - Zchuck5 (mm): DBL
  - Parallelism A (µm): DBL
  - Bowing A (µm): DBL
  - Parallelism B (µm): DBL
  - Bowing B (µm): DBL
  - Bowing Valid: Boolean
  - Parallelism Valid: Boolean
  - Parallelism Valid A: Boolean
  - Parallelism Valid B: Boolean
  - Bowing Valid A: Boolean
  - Bowing Valid B: Boolean
  - dZchuck1 (mm): DBL
  - dZchuck3 (mm): DBL
  - dZchuck4 (mm): DBL
  - dZchuck5 (mm): DBL

## alox_typedef_bowing_measurement_report.ctl

- Bowing Measurement Report: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Bowing: Cluster
    - Z1: DBL
    - Z2: DBL
    - Z3: DBL
    - Value: DBL

## alox_typedef_bowing_measurement_report_ui.ctl

- Bowing Measurement Report UI: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Bowing: Cluster
    - Zwafer2 (mm): DBL
    - Zwafer1 (mm): DBL
    - Zwafer3 (mm): DBL
    - Zwafer4 (mm): DBL
    - Zwafer5 (mm): DBL
    - Zchuck1 (mm): DBL
    - Zchuck3 (mm): DBL
    - Zchuck4 (mm): DBL
    - Zchuck5 (mm): DBL
    - Parallelism A (µm): DBL
    - Bowing A (µm): DBL
    - Parallelism B (µm): DBL
    - Bowing B (µm): DBL
    - Bowing Valid: Boolean
    - Parallelism Valid: Boolean
    - Parallelism Valid A: Boolean
    - Parallelism Valid B: Boolean
    - Bowing Valid A: Boolean
    - Bowing Valid B: Boolean
    - dZchuck1 (mm): DBL
    - dZchuck3 (mm): DBL
    - dZchuck4 (mm): DBL
    - dZchuck5 (mm): DBL

## alox_typedef_bowing_measurement_size.ctl

- Bowing Measurement Report UI: Cluster
  - Bowing: Cluster
    - Zwafer2 (mm): DBL
    - Zwafer1 (mm): DBL
    - Zwafer3 (mm): DBL
    - Zwafer4 (mm): DBL
    - Zwafer5 (mm): DBL
    - Zchuck1 (mm): DBL
    - Zchuck3 (mm): DBL
    - Zchuck4 (mm): DBL
    - Zchuck5 (mm): DBL
    - Parallelism A (µm): DBL
    - Bowing A (µm): DBL
    - Parallelism B (µm): DBL
    - Bowing B (µm): DBL
    - Bowing Valid: Boolean
    - Parallelism Valid: Boolean
    - Parallelism Valid A: Boolean
    - Parallelism Valid B: Boolean
    - Bowing Valid A: Boolean
    - Bowing Valid B: Boolean
    - dZchuck1 (mm): DBL
    - dZchuck3 (mm): DBL
    - dZchuck4 (mm): DBL
    - dZchuck5 (mm): DBL

## alox_typedef_bowing_status_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set
    3 = Get
    4 = Set Language
    5 = Get Description

## alox_typedef_calibration_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Calib
    3 = Get Calib
    4 = Reset Calib
    5 = Pixels To Real
    6 = Real To Pixels
    7 = Is Calibrated?
    8 = Get From Properties
    9 = Set To Properties

## alox_typedef_calibration_units.ctl

- Units: enum U16
    0 = pixels
    1 = µm
    2 = mm

## alox_typedef_camera_client_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Setup Camera
    3 = Cleanup Camera
    4 = Start Camera
    5 = Stop Camera
    6 = Show UI
    7 = Get Heartbeat
    8 = Get Status
    9 = Get Image
    10 = Get Camera Gain
    11 = Set Camera Gain
    12 = Get Camera Exposure
    13 = Set Camera Exposure
    14 = Is Camera Inited ?
    15 = Is Camera Started ?
    16 = Autofocus
    17 = Set FlatField
    18 = Refocus Fin
    19 = Refocus Oxyd
    20 = Set Flip
    21 = Set LED
    22 = Get LED
    23 = Get Client Queue
    24 = Setup Z Stage
    25 = Z Do Reference
    26 = Get Z Serial
    27 = Get Z Status
    28 = Get Z Error
    29 = Set Z Speed
    30 = Get Z Speed
    31 = Get Z Position
    32 = Z Move Absolute
    33 = Z Move Relative
    34 = Z Wait Move
    35 = Log
    36 = Z Stop
    37 = Z Enable Joystick
    38 = Z Disable Joystick
    39 = Z Joystick Enabled?
    40 = Set Z Min Limit
    41 = Set Z Max Limit
    42 = Cleanup Z Stage

## alox_typedef_camera_flatfield_parameters.ctl

- Camera FlatField: Cluster
  - Enable: Boolean
  - FFC Path: Path
  - DFC Path: Path
  - Correction Factor: DBL

## alox_typedef_camera_heartbeat_report.ctl

- CAM HEARTBEAT REPORT: Cluster
  - CAM SERVER STATUS: Cluster
    - SERVER: Boolean
    - CAM HIMAG STATUS: Boolean
    - CAM LOMAG STATUS: Boolean
    - Z STAGE STATUS: Boolean
    - HEARTBEAT: U32
  - CAM ERR: U32

## alox_typedef_camera_selection_method.ctl

- Method: enum U16
    0 = Profile Current
    1 = Set default camera

## alox_typedef_camera_simulation_parameters.ctl

- Get Image Simulation Params: Cluster
  - Simulation: Boolean
  - Simulation Path: Path
  - Simulation Timer (ms): U32

## alox_typedef_camera_type.ctl

- Camera Type: enum U32
    0 = High Mag.
    1 = Low Mag.

## alox_typedef_circle.ctl

- Circle: Cluster
  - Center: Cluster
    - X: SGL
    - Y: SGL
  - Radius: DBL

## alox_typedef_circle_measure_type.ctl

- Diameter Measure: enum U16
    0 = Mean
    1 = Median
    2 = Fit
    3 = Surface
    4 = Circumscribed
    5 = Inscribed

## alox_typedef_core_mode.ctl

- Alox Core Mode: enum U16
    0 = Local
    1 = Remote

## alox_typedef_core_mode_manager_method.ctl

- Core Mode Manager Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Mode
    3 = Get Mode

## alox_typedef_display_control.ctl

- Ctl Refnum: Refnum (kind 8: ImageControl:ImageControl)

## alox_typedef_display_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Disp. Refnum
    3 = Get Disp. Refnum
    4 = Display Image
    5 = Get Image
    6 = Get ROI

## alox_typedef_downsampling_method.ctl

- Downsampling: enum U16
    0 = None
    1 = 25%
    2 = 50%
    3 = 75%
    4 = 90%

## alox_typedef_dual_objects_detection_parameters.ctl

- Dual Objects Detection: Cluster
  - Obj1 Model Path: Path
  - Obj1 Mask Path: Path
  - Obj2 Model Path: Path
  - Obj2 Mask Path: Path
  - Edge Threshold: U32
  - Min Length: I32
  - Morph Open: I32
  - Morph Close: I32
  - Obj1 Min Score (%): DBL
  - Obj1 Max Occl (%): DBL
  - Obj2 Min Score (%): DBL
  - Obj2 Max Occl (%): DBL

## alox_typedef_exposures_wl_lut_method.ctl

- Method: enum U16
    0 = Create Default
    1 = Import CSV
    2 = Export CSV
    3 = Get Exposure(WL)
    4 = Get Fixed Expo
    5 = Set Fixed Expo
    6 = Get Auto Expo
    7 = Set Auto Expo
    8 = Reset Auto Expo
    9 = Save calib values
    10 = Has Auto Expo ?
    11 = Has Fixed Expo ?
    12 = Get Wavelengths
    13 = Set Wavelengths

## alox_typedef_ext_camera_properties.ctl

- Camera Properties: Cluster
  - Camera Name: String
  - Buffers: I32
  - Timeout: I32
  - Flat Field: Boolean
  - Flat Field Path: Path
  - Dark Current Path: Path
  - Flat Field Factor: DBL
  - Camera Exposure Attribute: String
  - Camera Gain Attribute: String
  - Exposure Type: String
  - Gain Type: String
  - Horizontal Flip: Boolean
  - Vertical Flip: Boolean
  - Camera Framerate Attribute: String
  - Framerate Type: String
  - Max Framerate: DBL
  - Wait LED (ms): U32
  - Invert LED: Boolean
  - Camera Plug Type: enum U16
      0 = GiGe
      1 = USB
  - Exposure Lower Limit: DBL

## alox_typedef_ext_camera_session.ctl

- Camera Session: Cluster
  - Session: Refnum (kind 21: IMAQdx)
  - SRC Image: Refnum (kind 29: IMAQImage)
  - FFC Image : Refnum (kind 29: IMAQImage)
  - DFC Image: Refnum (kind 29: IMAQImage)
  - Simulation: Boolean
  - Simulation Path: Path
  - Simulation Index: I32
  - Simulation Time: I32
  - Last Focus Data: 2D array
    - (no label): DBL
  - Acquisition State: enum U16
      0 = Stopped
      1 = Start
      2 = Acquire
      3 = Finished
      4 = Error
  - AF State: enum U16
      0 = Start
      1 = Goto Z Pos
      2 = Wait Started
      3 = Measure
      4 = Finished
      5 = Error
  - RF State: enum U16
      0 = Start
      1 = Get Positions
      2 = Move Position
      3 = Check Position
      4 = Mesure Position
      5 = Get Focus
      6 = Move Focus
      7 = Wait Focus
      8 = Finished
      9 = Error
  - Focus Progress (%): DBL

## alox_typedef_ext_xystage_properties.ctl

- X Stage Properties: Cluster
  - Com: I32
  - Baudrate: U16
  - Axis: I32
  - Speed (mm/s): DBL
  - Slow Speed (mm/s): DBL
  - Wafer Origin (mm): DBL
  - Chuck Origin (mm): DBL

## alox_typedef_ext_zstage_properties.ctl

- Z Stage Properties: Cluster
  - Com: I32
  - Baudrate: U16
  - Axis: I32
  - Speed (mm/s): DBL
  - Slow Speed (mm/s): DBL
  - Min Position (mm): DBL
  - Max Position (mm): DBL

## alox_typedef_ext_zstage_session.ctl

- Z Session: Cluster
  - System: I32
  - Axes: 1D array
    - Axe: String
  - Identity: String
  - Serial Number: String

## alox_typedef_flip_options.ctl

- Flip Options: Cluster
  - H.Flip: Boolean
  - V.Flip: Boolean

## alox_typedef_floating_log_ui_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Show
    5 = Hide
    6 = Get UI Refnum

## alox_typedef_focus_detection_method.ctl

- Focus Detect Method: enum U16
    0 = Curve Maximum
    1 = Fit Maximum
    2 = Local Maximum

## alox_typedef_font_options.ctl

- Font Options: Cluster
  - Font: Cluster
    - Desired Font (Application): enum U16
        0 = User-specified Font
        1 = Application Font
        2 = System Font
        3 = Dialog Font
    - [User-specified Font]: Cluster
      - Font Name: String
      - Size: I16
      - Strikeout?: Boolean
      - Italic?: Boolean
      - Underline?: Boolean
      - Outline?: Boolean
      - Shadow?: Boolean
      - Bold?: Boolean
      - Rotation Angle: DBL
    - Horizontal Alignment: I16
    - Vertical Alignment: I16
  - Font Color: U32
  - Font Line Space: I32

## alox_typedef_functions_condition_manager_funcname.ctl

- Function: enum U16
    0 = Mesa Measure
    1 = Tilt Measure
    2 = Bowing Measure
    3 = Get Image
    4 = Autofocus Large
    5 = Refocus
    6 = Refocus Oxyd
    7 = Detect Circle
    8 = Tracking
    9 = Measure Oxyd
    10 = Search Wavelength
    11 = Search Object
    12 = Auto Exposure

## alox_typedef_functions_condition_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Running
    3 = Get Running
    4 = Get Condition OK
    5 = Get Joystick OK
    6 = Get Any Running ?

## alox_typedef_functions_status_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Update
    3 = Get Ctrl Refnum
    4 = Reset Status
    5 = Start
    6 = Stop

## alox_typedef_functions_ui_event.ctl

- Function UI Event: Cluster
  - Event: enum U16
      0 = Profile Changed
      1 = AF Large Started
      2 = AF Large Measured
      3 = AF Large Finished
      4 = WL Started
      5 = WL Measured
      6 = WL Finished
      7 = DO Started
      8 = DO Autofocus
      9 = DO Refocus
      10 = DO Moving
      11 = DO Searching
      12 = DO Finished
      13 = MESA Started
      14 = MESA State Changed
      15 = MESA Measured
      16 = MESA Finished
      17 = BOW Started
      18 = BOW Autofocus
      19 = BOW Refocus
      20 = BOW Measured
      21 = BOW State Changed
      22 = BOW Finished
      23 = TILT Started
      24 = TILT Autofocus
      25 = TILT Refocus
      26 = TILT Moving
      27 = TILT Measured
      28 = TILT State Changed
      29 = TILT Finished
      30 = Auto Expo Started
      31 = Auto Expo State Changed
      32 = Auto Expo Measure Finished
      33 = Auto Expo Finished
      34 = Refocus Started
      35 = Refocus Measured
      36 = Refocus Finished
      37 = Refocus Oxyd Started
      38 = Refocus Oxyd Measured
      39 = Refocus Oxyd Finished
      40 = User Profile Changed
      41 = Close Settings Panels
      42 = Exit Alox Events Loop
      43 = SEMI Data Changed
  - Param: Variant

## alox_typedef_functions_ui_event_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Generate Event
    3 = Get Event

## alox_typedef_functions_ui_event_type.ctl

- Event: enum U16
    0 = Profile Changed
    1 = AF Large Started
    2 = AF Large Measured
    3 = AF Large Finished
    4 = WL Started
    5 = WL Measured
    6 = WL Finished
    7 = DO Started
    8 = DO Autofocus
    9 = DO Refocus
    10 = DO Moving
    11 = DO Searching
    12 = DO Finished
    13 = MESA Started
    14 = MESA State Changed
    15 = MESA Measured
    16 = MESA Finished
    17 = BOW Started
    18 = BOW Autofocus
    19 = BOW Refocus
    20 = BOW Measured
    21 = BOW State Changed
    22 = BOW Finished
    23 = TILT Started
    24 = TILT Autofocus
    25 = TILT Refocus
    26 = TILT Moving
    27 = TILT Measured
    28 = TILT State Changed
    29 = TILT Finished
    30 = Auto Expo Started
    31 = Auto Expo State Changed
    32 = Auto Expo Measure Finished
    33 = Auto Expo Finished
    34 = Refocus Started
    35 = Refocus Measured
    36 = Refocus Finished
    37 = Refocus Oxyd Started
    38 = Refocus Oxyd Measured
    39 = Refocus Oxyd Finished
    40 = User Profile Changed
    41 = Close Settings Panels
    42 = Exit Alox Events Loop
    43 = SEMI Data Changed

## alox_typedef_hardware_module.ctl

- Hardware Reset Module: enum U16
    0 = Cameras/Z
    1 = XY Stages
    2 = Monochromator

## alox_typedef_hardware_module_heartbeat_event.ctl

- Module Heartbeat Event: Cluster
  - Server Type: enum U16
      0 = Camera
      1 = Monochromator
      2 = XY Stages
      3 = Z Stage
  - Data: Variant

## alox_typedef_hardware_module_heartbeat_report.ctl

- Modules Heartbeat Report: Cluster
  - Camera: Cluster
    - CAM SERVER STATUS: Cluster
      - SERVER: Boolean
      - CAM HIMAG STATUS: Boolean
      - CAM LOMAG STATUS: Boolean
      - Z STAGE STATUS: Boolean
      - HEARTBEAT: U32
    - CAM ERR: U32
  - XY: Cluster
    - XY SERVER STATUS: Cluster
      - SERVER: Boolean
      - X STAGE STATUS: Boolean
      - Y STAGE STATUS: Boolean
      - HEARTBEAT: U32
    - ERROR COUNT: U32
  - WL: Cluster
    - WL SERVER STATUS: Cluster
      - SERVER: Boolean
      - WL STATUS: Boolean
      - HEARTBEAT: U32
    - ERROR COUNT: U32

## alox_typedef_heartbeat_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Is Running ?
    3 = Get Heartbeat

## alox_typedef_hl_filename_saving_image_type.ctl

- Save_filename_type: enum U16
    0 = bow
    1 = tilt
    2 = mesa

## alox_typedef_hl_functions_condition_manager_funcname.ctl

- Function: enum U16
    0 = Mesa Measure
    1 = Tilt Measure
    2 = Bowing Measure

## alox_typedef_hl_functions_condition_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Running
    3 = Get Running
    4 = Get Condition OK
    5 = Get Joystick OK

## alox_typedef_hl_results_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set References
    3 = Open Panel
    4 = Close Panel

## alox_typedef_hl_results_manager_type.ctl

- Panel Type: enum U16
    0 = Bow Measurement
    1 = Tilt Measurement
    2 = Mesa Measurement

## alox_typedef_host_manager_method.ctl

- Host Method: enum U16
    0 = Init
    1 = Exit
    2 = Update
    3 = Get

## alox_typedef_hotkey.ctl

- HotKey: U8

## alox_typedef_image_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Get Image
    3 = Get Merged Overlays
    4 = Merge Overlays

## alox_typedef_init_ctrls_refnums.ctl

- CTRLS INIT refnums: Cluster
  - MONO_WL_CTRL: Refnum (kind 8)
  - X_CTRL: Refnum (kind 8)
  - Y_CTRL: Refnum (kind 8)
  - Profiles List: Refnum (kind 8)
  - CAMERA_TYPE: Refnum (kind 8)

## alox_typedef_language_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Language
    3 = Get String

## alox_typedef_length_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Clear
    3 = Set Start Point
    4 = Set End Point
    5 = Get Results
    6 = Overlay Results

## alox_typedef_line.ctl

- Line: Cluster
  - Start X: I32
  - Start Y: I32
  - End X: I32
  - End Y: I32

## alox_typedef_linear_fit_infos.ctl

- Linear Fit: Cluster
  - Slope: DBL
  - Intercept: DBL
  - Residual: DBL
  - Fit Score(%): DBL

## alox_typedef_ll_results_panels_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set References
    3 = Open Panel
    4 = Close Panel

## alox_typedef_ll_results_panels_manager_type.ctl

- Panel Type: enum U16
    0 = Get Image
    1 = Autofocus
    2 = Refocus
    3 = Refocus Oxyd
    4 = Detect Circle
    5 = Tracking
    6 = Measure Oxyd
    7 = Wavelength
    8 = Auto Exposure
    9 = Search Object

## alox_typedef_log_manager_method.ctl

- Method: enum U16
    0 = Default
    1 = Init
    2 = Exit
    3 = Set Log Window
    4 = Set Alarm Window
    5 = Clear Log
    6 = Clear Alarm
    7 = Log Info
    8 = Log Warning
    9 = Log Error
    10 = Clear All
    11 = Save Log
    12 = Log Error In

## alox_typedef_measure_oxyd_edge_polarity.ctl

- Edge Polarity: enum U16
    0 = All
    1 = Rising Edges
    2 = Falling Edges

## alox_typedef_measure_oxyd_search_direction.ctl

- Search Direction: enum U16
    0 = Outside To Inside
    1 = Inside To Outside

## alox_typedef_mesa_measurement_report_ui.ctl

- Measure Mesa Report UI: Cluster
  - SEMI Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - External: Cluster
    - Fit Diam. (µm): DBL
  - Oxyd: Cluster
    - Fit Diam. (µm): DBL
    - Median Diam. (µm): DBL
    - Average Diam. (µm): DBL
    - Surface Diam. (µm): DBL
    - Inscribed Diam. (µm): DBL
    - Circumscribed Diam. (µm): DBL
  - Oxyd Rate: Cluster
    - Rate (µm/min): DBL
    - Remain Time (s): DBL
  - Graph Data: 1D array
    - (no label): Cluster
      - X: 1D array
        - Float Output: DBL
      - X: 1D array
        - Float Output: DBL
  - Linear Fit: Cluster
    - Slope: DBL
    - Intercept: DBL
    - Residual: DBL
    - Fit Score(%): DBL

## alox_typedef_mesa_measurement_report_values_ui.ctl

- mesa measure report values: Cluster
  - Oxyd Diam.: DBL
  - External Diameter: DBL
  - Oxyd Target (µm): DBL
  - Oxid Filtered: 1D array
    - Oxid Filtered: DBL
  - #Average: I32
  - Oxid Row: 1D array
    - Oxid Filtered: DBL
  - Oxyd Diam. 2: DBL

## alox_typedef_mesa_status_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set
    3 = Get
    4 = Set Language
    5 = Get Description

## alox_typedef_mesapositions.ctl

- mesa_positions: Cluster
  - x: DBL
  - y: DBL
  - z: DBL

## alox_typedef_modules_heartbeat_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Is Running ?
    3 = Get Report

## alox_typedef_monochromator_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Setup
    3 = Cleanup
    4 = Is Inited?
    5 = Is Connected?
    6 = Set Timeout
    7 = Get Serial
    8 = Set Units
    9 = Get Units
    10 = Get Position
    11 = Set Position
    12 = Get Groove
    13 = Get Blaze
    14 = Get Grating
    15 = Get Speed
    16 = Set Speed
    17 = Get Last Position
    18 = Show UI
    19 = Get Heartbeat
    20 = Get Client Queue

## alox_typedef_move_event_parameters.ctl

- Move Event Parameters: Cluster
  - Search Positions: 1D array
    - Point: Cluster
      - X: SGL
      - Y: SGL
  - Current XY Pos (mm): Cluster
    - X: SGL
    - Y: SGL
  - Current Pos Index: I32

## alox_typedef_notifiers_type.ctl

- Notifier Type: enum U16
    0 = heartbeat
    1 = status
    2 = measure tilt
    3 = measure bowing
    4 = measure mesa
    5 = finished
    6 = failed
    7 = error
    8 = started
    9 = camz_hardware_warning
    10 = xy_hardware_warning
    11 = monochro_hardware_warning
    12 = hardware_reset
    13 = mesa_status
    14 = tilt_status
    15 = bowing_status
    16 = image

## alox_typedef_panels_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set References
    3 = Open Panel
    4 = Close Panel
    5 = Last open

## alox_typedef_panels_manager_type.ctl

- Panel Type: enum U16
    0 = Default
    1 = Vision Core
    2 = Get Image
    3 = Autofocus
    4 = Refocus
    5 = Refocus Oxid
    6 = Detect Circle
    7 = Tracking
    8 = Measure Oxid
    9 = Wavelength
    10 = Auto Exposure
    11 = Search Object
    12 = Bow Measurement
    13 = Tilt Measurement
    14 = Mesa Measurement

## alox_typedef_path_images_save.ctl

- images save path: Cluster
  - images oxid: Path
  - images mapping: Path
  - images bowing: Path
  - images tilt: Path

## alox_typedef_process_queue_item.ctl

- Alox Queue Item: Cluster
  - Method: enum U16
      0 = Init
      1 = Exit
      2 = Measure Mesa
      3 = Measure Tilt
      4 = Measure Bowing
      5 = Stop
      6 = Load Profile
      7 = Profiles Folder Changed
      8 = Get Image
      9 = Module Heartbeat
      10 = Abort
      11 = Refresh Profiles
      12 = Refresh Core Infos
      13 = Log Window Hidden
      14 = System Reset
      15 = Axis Referencing
      16 = System Shutdown
      17 = Show UI
      18 = Module Resetted
      19 = Mesa Job Finished
      20 = Bowing Job Finished
      21 = Tilt Job Finished
  - Variant: Variant

## alox_typedef_process_queue_item_bowing_params.ctl

- Measure Bowing queue param: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Bowing  Params: Cluster
    - Camera Type: enum U32
        0 = High Mag.
        1 = Low Mag.
    - Measure Distance (mm): DBL
    - Max Bowing (µm): DBL
    - Show Window: Boolean
    - Overlay Bowing: Boolean
    - Overlay Parallelism: Boolean
    - Fast method: Boolean

## alox_typedef_process_queue_item_mesa_params.ctl

- Measure MESA queue param: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Measure Mesa Params: Cluster
    - Start Position (mm): Cluster
      - X: SGL
      - Y: SGL
    - Tilt Correction: Boolean
    - Refocus Time (s): DBL
    - Refocus Retries: I32
    - Oxyd Target (µm): DBL
    - Line Reg. Time (s): DBL
    - Singleshot: Boolean
    - # Averages: U32
    - Show Window: Boolean
    - Overlay Time: Boolean
    - Overlay Oxyd Rate: Boolean
    - Overlay Oxyd Diameter: Boolean
    - Overlay Oxyd Status: Boolean
    - positionsctrl: Boolean
    - analysis: Boolean
    - Tracking: Boolean
    - WL Mode: enum U16
        0 = MANUAL
        1 = SCAN
    - EXPOSURE MODE: U16

## alox_typedef_process_queue_item_tilt_params.ctl

- Measure TILT queue param: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Tilt  Params: Cluster
    - Camera Type: enum U32
        0 = High Mag.
        1 = Low Mag.
    - Start Search Pos (mm): Cluster
      - X: SGL
      - Y: SGL
    - Tilt Method: enum U16
        0 = Straight Edge
        1 = Dual Objects
    - Max Moves: I32
    - Step (mm): DBL
    - Straight Edge Detection: Cluster
      - Search Direction: U32
      - Edge Polarity: U32
      - Minimum Edge Strength: U32
      - Step (Pixels): U32
      - Width: U32
      - Kernel Size: U32
      - Min. Points (%): U32
      - Angle Expected (°): DBL
      - Angle Range (°): DBL
      - Edge Method: enum U32
          0 = First Edge Rake
          1 = Best Edge Rake
          2 = Hough Edge Rake
          3 = First Edge Projection
          4 = Best Edge Projection
    - Dual Objects Detection: Cluster
      - Obj1 Model Path: Path
      - Obj1 Mask Path: Path
      - Obj2 Model Path: Path
      - Obj2 Mask Path: Path
      - Edge Threshold: U32
      - Min Length: I32
      - Morph Open: I32
      - Morph Close: I32
      - Obj1 Min Score (%): DBL
      - Obj1 Max Occl (%): DBL
      - Obj2 Min Score (%): DBL
      - Obj2 Max Occl (%): DBL
    - Autofocus: Boolean
    - Refocus: Boolean
    - Show Window: Boolean
    - Overlay Match: Boolean
    - Overlay Straight Edge: Boolean
    - Overlay Edges: Boolean
    - Overlay Tilt: Boolean

## alox_typedef_process_queue_item_type.ctl

- Type: enum U16
    0 = Init
    1 = Exit
    2 = Measure Mesa
    3 = Measure Tilt
    4 = Measure Bowing
    5 = Stop
    6 = Load Profile
    7 = Profiles Folder Changed
    8 = Get Image
    9 = Module Heartbeat
    10 = Abort
    11 = Refresh Profiles
    12 = Refresh Core Infos
    13 = Log Window Hidden
    14 = System Reset
    15 = Axis Referencing
    16 = System Shutdown
    17 = Show UI
    18 = Module Resetted
    19 = Mesa Job Finished
    20 = Bowing Job Finished
    21 = Tilt Job Finished

## alox_typedef_process_queue_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Get Queue

## alox_typedef_profile_manager_method.ctl

- Method: enum U16
    0 = Get Profiles List
    1 = Get Current Profile
    2 = New
    3 = Load
    4 = Save
    5 = Get Properties List
    6 = Get Property
    7 = Get Property (String)
    8 = Set Property
    9 = Set Property Default
    10 = Add Property
    11 = Del Property
    12 = Get All Properties
    13 = Set All Properties
    14 = Clear
    15 = New To Variant Out
    16 = Load to Variant Out
    17 = Save From Variant In
    18 = Check Variant In
    19 = Check

## alox_typedef_properties_manager_method.ctl

- Method: enum U16
    0 = New
    1 = Load
    2 = Save
    3 = Get Property
    4 = Set Property
    5 = Add Property
    6 = Del Property
    7 = Get Properties Names
    8 = Clear
    9 = Set Default Property
    10 = Property Exists?
    11 = Check

## alox_typedef_refocus_method.ctl

- Refocus Method: enum U16
    0 = Fin
    1 = Oxyd

## alox_typedef_semi_job_finished_event_json_report.ctl

- SEMI Job JSON: Cluster
  - Event: String
  - Function: String
  - Job: Cluster
    - Control_Job_ID: String
    - Process_Job_ID: String
    - Wafer_ID: String
    - Lot_ID: String

## alox_typedef_semi_job_finished_event_report.ctl

- SEMI Job Finished Report: Cluster
  - Function: String
  - SEMI Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String

## alox_typedef_semi_job_infos.ctl

- SEMI Job Infos: Cluster
  - Control Job ID: String
  - Process Job ID: String
  - Lot ID: String
  - Wafer ID: String
  - Zone ID: String

## alox_typedef_semi_manager_method.ctl

- SEMI Method: enum U16
    0 = Init
    1 = Exit
    2 = Set
    3 = Get

## alox_typedef_server_module_manager_method.ctl

- Method: enum U16
    0 = Load Server
    1 = Unload Server
    2 = Kill Server
    3 = Kill 'em All
    4 = Any Server alive ? 
    5 = Server Running ?

## alox_typedef_server_module_manager_type.ctl

- Server Type: enum U16
    0 = Camera
    1 = Monochromator
    2 = XY Stages
    3 = Z Stage

## alox_typedef_simulator_mesa_options.ctl

- Mesa Simulation Options: Cluster
  - X Start: DBL
  - Duration (s): DBL
  - Y Start: DBL
  - Y End: DBL
  - Add Noise (%): DBL
  - Noise  Amplitude (%): DBL
  - #  Samples: I32

## alox_typedef_splash_ui_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Notify

## alox_typedef_splash_ui_notification.ctl

- Splash Param: Cluster
  - Notification: enum U16
      0 = Version
      1 = Message
      2 = Exit
  - Variant: Variant

## alox_typedef_splash_ui_notification_type.ctl

- Notification: enum U16
    0 = Version
    1 = Message
    2 = Exit

## alox_typedef_stages_coordinate_system_control.ctl

- COORD. SYSTEM: U16

## alox_typedef_straight_edge_parameters.ctl

- Straight Edge Detection: Cluster
  - Search Direction: U32
  - Edge Polarity: U32
  - Minimum Edge Strength: U32
  - Step (Pixels): U32
  - Width: U32
  - Kernel Size: U32
  - Min. Points (%): U32
  - Angle Expected (°): DBL
  - Angle Range (°): DBL
  - Edge Method: enum U32
      0 = First Edge Rake
      1 = Best Edge Rake
      2 = Hough Edge Rake
      3 = First Edge Projection
      4 = Best Edge Projection

## alox_typedef_system_status_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set
    3 = Get
    4 = Set Language
    5 = Status timer

## alox_typedef_tilt_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Tilt
    3 = Get Tilt
    4 = Reset Tilt

## alox_typedef_tilt_measurement.ctl

- Tilt: Cluster
  - Angle (°): DBL
  - Tilt Valid: Boolean

## alox_typedef_tilt_measurement_method.ctl

- Tilt Method: enum U16
    0 = Straight Edge
    1 = Dual Objects

## alox_typedef_tilt_measurement_report_ui.ctl

- Measure Tilt Report UI: Cluster
  - SEMI Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Tilt: Cluster
    - Angle (°): DBL
    - Tilt Valid: Boolean

## alox_typedef_tilt_status_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set
    3 = Get
    4 = Set Language
    5 = Get Description

## alox_typedef_titlebar_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Update

## alox_typedef_user_action.ctl

- User Action: enum U16
    0 = Exit App
    1 = Create Profile
    2 = Delete Profile
    3 = Edit Profile
    4 = Change Profile
    5 = Edit Properties
    6 = Get Image
    7 = Get Image Setup
    8 = Autofocus
    9 = Autofocus Setup
    10 = Refocus
    11 = Refocus Setup
    12 = Refocus Oxyd
    13 = Refocus Oxyd Setup
    14 = Detect Circle
    15 = Detect Circle Setup
    16 = Tracking
    17 = Tracking Setup
    18 = Measure Oxyd
    19 = Measure Oxyd Setup
    20 = Search Wavelength
    21 = Search Wavelength Setup
    22 = Search Object
    23 = Search Object Setup
    24 = Auto Exposure
    25 = Auto Exposure Setup
    26 = Bowing
    27 = Bowing Setup
    28 = Tilt
    29 = Tilt Setup
    30 = Mesa
    31 = Mesa Setup

## alox_typedef_user_type.ctl

- User: enum U16
    0 = Operator
    1 = Engineer
    2 = Admin

## alox_typedef_users_managers_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Get User
    3 = Login
    4 = Logout
    5 = User Rights
    6 = Check Password

## alox_typedef_watchdog_manager_method.ctl

- Method: enum U16
    0 = Start
    1 = Stop

## alox_typedef_watchdog_state.ctl

- WatchDog State: enum U16
    0 = Connect
    1 = Heartbeat Running
    2 = Heartbeat Get
    3 = Heartbeat Check
    4 = Restart Core
    5 = Exit

## alox_typedef_websocket_image_stream_options.ctl

- Stream Options: Cluster
  - Width: I32
  - Height: I32
  - Quality: I32

## alox_typedef_websocket_stream_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start Stream
    3 = Stop Stream
    4 = Is Started?
    5 = Stream Image

## alox_typedef_wl_heartbeat_event.ctl

- WL: Cluster
  - WL SERVER STATUS: Cluster
    - SERVER: Boolean
    - WL STATUS: Boolean
    - HEARTBEAT: U32
  - ERROR COUNT: U32

## alox_typedef_xy_coordinate_system_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Wafer Coord. Sys
    3 = Get Wafer Coord. Sys
    4 = Set Chuck Coord. Sys
    5 = Get Chuck Coord. Sys
    6 = Convert
    7 = Wafer Coord OK ?

## alox_typedef_xy_coordinate_system_type.ctl

- Coord. System Type: enum U16
    0 = XY Stages
    1 = Wafer
    2 = Chuck

## alox_typedef_xy_heartbeat_event.ctl

- XY HEARTBEAT REPORT: Cluster
  - XY SERVER STATUS: Cluster
    - SERVER: Boolean
    - X STAGE STATUS: Boolean
    - Y STAGE STATUS: Boolean
    - HEARTBEAT: U32
  - ERROR COUNT: U32

## alox_typedef_xy_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Show UI
    3 = Get Heartbeat
    4 = Get Status
    5 = Setup
    6 = Cleanup
    7 = Is Inited?
    8 = Is Connected?
    9 = Get Serial Number
    10 = Set Speed
    11 = Get Speed
    12 = Get Position
    13 = Move Absolute
    14 = Move Relative
    15 = Move Wait
    16 = Stop
    17 = Enable Joystick
    18 = Disable Joystick
    19 = Joystick Enabled?
    20 = Log
    21 = Get Client Queue
    22 = Get Joystick Button
    23 = Move Absolute XY
    24 = Get Position XY
    25 = Get Last Position XY
    26 = Set Wafer Diameter
    27 = Set Chuck Origin
    28 = Set Wafer Origin
    29 = Set Wafer Limit
    30 = Set Wafer Check Freq.
    31 = Do Reference

## alox_typedef_xy_map_manager_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Set Refnum
    3 = Update

## alox_typedef_xy_stages_type.ctl

- XY Stage: enum U16
    0 = X
    1 = Y

## typedef_timestamp_convert_method.ctl

- Method: enum U16
    0 = Excel To LabVIEW
    1 = LabVIEW To Excel

## alox_dongle_infos.ctl

- Dongle Infos: Cluster
  - All OK?: Boolean
  - Serial Number: String
  - Expiration Date: Waveform
  - Last Execution Date: Waveform
  - Licence Extra Infos: String
  - Dongle Detected: Boolean
  - Key1 OK: Boolean
  - Key2 OK: Boolean
  - Key3 OK: Boolean
  - Data1 read OK: Boolean
  - Data2 read OK: Boolean
  - Time not expired: Boolean
  - Time not resetted: Boolean

## alox_dongle_infos.ctl

- Dongle Infos: Cluster
  - All OK?: Boolean
  - Serial Number: String
  - Expiration Date: Waveform
  - Last Execution Date: Waveform
  - Licence Extra Infos: String
  - Dongle Detected: Boolean
  - Key1 OK: Boolean
  - Key2 OK: Boolean
  - Key3 OK: Boolean
  - Data1 read OK: Boolean
  - Data2 read OK: Boolean
  - Time not expired: Boolean
  - Time not resetted: Boolean

## typedef_ext_autofocus_fast_session.ctl

- Fast AF Session: Cluster
  - Duration (ms): DBL
  - T0: DBL
  - Last Timestamp: U32
  - Current Z: DBL
  - Init Speed (mm/s): DBL
  - Init Z (mm): DBL
  - AF Data: 2D array
    - (no label): DBL
  - Move Timestamp: U32

## typedef_ext_cam_heartbeat_data.ctl

- Camera Server Report: Cluster
  - SERVER: Boolean
  - CAM HIMAG STATUS: Boolean
  - CAM LOMAG STATUS: Boolean
  - Z STAGE STATUS: Boolean
  - HEARTBEAT: U32

## typedef_ext_camera_autofocus_data.ctl

- Camera Autofocus Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Autofocus Speed: enum U16
      0 = Slow
      1 = Fast
  - Autofocus Method: enum U16
      0 = FFT
      1 = Sobel
      2 = Prewitt
      3 = Diff
      4 = Gradient
      5 = Sigma
      6 = FULL
  - Duration (s): I32
  - Z Min: DBL
  - Z Max: DBL
  - Step: DBL
  - Subsampling: DBL
  - ROI: 1D array
    - (no label): I32
  - Acquisition Timeout (ms): I32

## typedef_ext_camera_autofocus_state.ctl

- AF State: enum U16
    0 = Start
    1 = Goto Z Pos
    2 = Wait Started
    3 = Measure
    4 = Finished
    5 = Error

## typedef_ext_camera_camera_command.ctl

- Function: enum U16
    0 = Get Heartbeat
    1 = Cam Setup
    2 = Cam Start
    3 = Cam Get Image
    4 = Cam Stream Next Image
    5 = Cam Get Acquisition Status
    6 = Cam Software Trigger
    7 = Cam Set Exposure
    8 = Cam Get Exposure
    9 = Cam Set Gain
    10 = Cam Get Gain
    11 = Cam Get Images Array
    12 = Cam Stop
    13 = Cam Cleanup
    14 = Exit
    15 = Show UI
    16 = Cam Autofocus
    17 = Cam Set FlatField
    18 = Cam Refocus
    19 = Cam Flip
    20 = Cam Get Autofocus Data
    21 = Cam Get Autofocus Progress
    22 = Cam Autofocus Run
    23 = Cam Refocus Run
    24 = Cam Set Led
    25 = Cam Get Led
    26 = Z Setup
    27 = Z Get Serial
    28 = Z Get Status
    29 = Z Get Error
    30 = Z Set Speed
    31 = Z Get Speed
    32 = Z Get Position
    33 = Z Move Absolute
    34 = Z Move Relative
    35 = Z Wait End Move
    36 = Z Stop
    37 = Z Enable Joystick
    38 = Z Disable Joystick
    39 = Z Joystick Enabled ?
    40 = Z Set Min Limit
    41 = Z Set Max Limit
    42 = Z Cleanup
    43 = Z Get Reference
    44 = Z Do Reference
    45 = Log

## typedef_ext_camera_camera_command_type_LED.ctl

- Camera Output: LED: enum U16
    0 = GiGe
    1 = USB

## typedef_ext_camera_flip_data.ctl

- Camera Flip Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Horizontal Flip: Boolean
  - Vertical Flip: Boolean

## typedef_ext_camera_gain_data.ctl

- Camera Gain Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Gain: DBL

## typedef_ext_camera_get_image_method.ctl

- Server Image Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status

## typedef_ext_camera_get_image_state.ctl

- State: enum U16
    0 = Stopped
    1 = Start
    2 = Acquire
    3 = Finished
    4 = Error

## typedef_ext_camera_getautofocusdata.ctl

- Camera Get Autofocus Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_getexposure_data.ctl

- Camera Get Exposure Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_getgain_data.ctl

- Camera Get Gain Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_getimage_data.ctl

- Camera Get Image Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_getled_data.ctl

- Camera Get LED Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_init_data.ctl

- Camera Init Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Camera Name: String
  - Number of buffers: I32
  - Continuous Mode: Boolean

## typedef_ext_camera_log_data.ctl

- Cam Log Data: Cluster
  - Log: Boolean
  - Path: Path
  - Max Lines: I32
  - Max Count: I32

## typedef_ext_camera_mgmt_queue_data.ctl

- Camera Queue Data: Cluster
  - Camera Command: enum U16
      0 = Get Heartbeat
      1 = Cam Setup
      2 = Cam Start
      3 = Cam Get Image
      4 = Cam Stream Next Image
      5 = Cam Get Acquisition Status
      6 = Cam Software Trigger
      7 = Cam Set Exposure
      8 = Cam Get Exposure
      9 = Cam Set Gain
      10 = Cam Get Gain
      11 = Cam Get Images Array
      12 = Cam Stop
      13 = Cam Cleanup
      14 = Exit
      15 = Show UI
      16 = Cam Autofocus
      17 = Cam Set FlatField
      18 = Cam Refocus
      19 = Cam Flip
      20 = Cam Get Autofocus Data
      21 = Cam Get Autofocus Progress
      22 = Cam Autofocus Run
      23 = Cam Refocus Run
      24 = Cam Set Led
      25 = Cam Get Led
      26 = Z Setup
      27 = Z Get Serial
      28 = Z Get Status
      29 = Z Get Error
      30 = Z Set Speed
      31 = Z Get Speed
      32 = Z Get Position
      33 = Z Move Absolute
      34 = Z Move Relative
      35 = Z Wait End Move
      36 = Z Stop
      37 = Z Enable Joystick
      38 = Z Disable Joystick
      39 = Z Joystick Enabled ?
      40 = Z Set Min Limit
      41 = Z Set Max Limit
      42 = Z Cleanup
      43 = Z Get Reference
      44 = Z Do Reference
      45 = Log
  - Notifier Name: String
  - Command Data: Variant
  - Command Request Timestamp: U32
  - Command Sending Timestamp: U32
  - Command Receiving Timestamp: U32

## typedef_ext_camera_refocus_data.ctl

- Camera Refocus Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Autofocus Method: enum U16
      0 = FFT
      1 = Sobel
      2 = Prewitt
      3 = Diff
      4 = Gradient
      5 = Sigma
      6 = FULL
  - Refocus Method: enum U16
      0 = Fin
      1 = Oxyd
  - Steps: I32
  - Step (mm): DBL
  - Fin ROI Width (pixels): I32
  - Fin ROI Height (pixels): I32
  - Oxyd X Pos (pixels): I32
  - Oxyd Y Pos (pixels): I32
  - Oxyd Diam (pixels): I32
  - Acquisition Timeout (ms): I32
  - Wait (ms): I32
  - Save Images: Boolean
  - Folder: Path

## typedef_ext_camera_refocus_session.ctl

- Refocus Session: Cluster
  - Positions: 1D array
    - Position: DBL
  - Scores: 1D array
    - Position: DBL
  - Index Pos: I32
  - Focus Pos: DBL
  - Focus Score: DBL
  - Init Z (mm): DBL
  - Init Speed (mm/s): DBL
  - Move timestamp: I32

## typedef_ext_camera_refocus_state.ctl

- Refocus State: enum U16
    0 = Start
    1 = Get Positions
    2 = Move Position
    3 = Check Position
    4 = Mesure Position
    5 = Get Focus
    6 = Move Focus
    7 = Wait Focus
    8 = Finished
    9 = Error

## typedef_ext_camera_release_data.ctl

- Camera Release Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_setexposure_data.ctl

- Camera Set Exposure Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Exposure: DBL

## typedef_ext_camera_setflatfield_data.ctl

- Camera Ext Set FlatField: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Enable: Boolean
  - FFC Path: Path
  - DFC Path: Path
  - Correction Factor: DBL

## typedef_ext_camera_setled_data.ctl

- Camera Set LED Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Led: Boolean

## typedef_ext_camera_simulation_data.ctl

- Cluster: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Simulate: Boolean
  - Simul Path: Path

## typedef_ext_camera_start_data.ctl

- Camera Start Data : Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Triggered: Boolean

## typedef_ext_camera_status_data.ctl

- Camera Status Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_stop_data.ctl

- Camera Stop Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_stream_data.ctl

- Camera Stream Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Simulation: Boolean
  - Simulation Path: Path
  - Simulation Timer (ms): U32
  - Acquisition Timeout (ms): I32

## typedef_ext_camera_trigsoft_data.ctl

- Camera Trigger Soft Data: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.

## typedef_ext_camera_z_get_error_data.ctl

- Z Stage Get Error Data: Cluster
  - Extra Param: String

## typedef_ext_z_cleanup_data.ctl

- Z Stage Cleanup Data: Cluster
  - Extra Params: String

## typedef_ext_z_get_position_data.ctl

- Z Stage Get Pos Data: Cluster
  - Extra Param: String

## typedef_ext_z_get_speed_data.ctl

- Z Stage Get  Speed Data: Cluster
  - Extra Param: String

## typedef_ext_z_getserial_data.ctl

- Z Stage Get Serial Data: Cluster
  - Type: Boolean

## typedef_ext_z_getstatus_data.ctl

- Z Stage Get Status Data: Cluster
  - Extra Param: String

## typedef_ext_z_init_data.ctl

- Z Stage Init Data: Cluster
  - Extra Param: String

## typedef_ext_z_joystick_data.ctl

- Z Stage Joystick Data: Cluster
  - Joystick ID: I32
  - Joystick Axis: I32

## typedef_ext_z_move_absolute_data.ctl

- Z Move Abs Data: Cluster
  - Pos (mm): DBL

## typedef_ext_z_move_relative_data.ctl

- Z Stage Move Rel Data: Cluster
  - Move (mm): DBL

## typedef_ext_z_move_wait_data.ctl

- Z Stage Wait Data: Cluster
  - Add. Wait (ms): U32
  - Polling Cycle (ms): U32
  - Add. Wait Only: Boolean

## typedef_ext_z_set_speed_data.ctl

- Z Stage Set Speed Data: Cluster
  - Speed(mm/s): DBL

## typedef_ext_z_stop_data.ctl

- Z Stage Stop Data: Cluster
  - Extra Param: String

## HW TCP Client Command.ctl

- State: enum U16
    0 = Connect
    1 = Send Command
    2 = Read
    3 = Quit

## HW TCP Client Queue.ctl

- Cluster: Cluster
  - State: enum U16
      0 = Connect
      1 = Send Command
      2 = Read
      3 = Quit
  - State Data: Variant

## HW TCP Datatype.ctl

- TCP DataType: enum U16
    0 = Command
    1 = Response
    2 = Image
    3 = Heartbeat
    4 = Statut

## HW TCP Response Data.ctl

- Response Data: Cluster
  - Notifier Name: String
  - Response (Flattened): String
  - Command Request timestamp: U32
  - TCP Command Sending timestamp: U32
  - TCP Command Receiving timestamp: U32
  - Pre-flattening data timestamp: U32
  - Post-Flattening data timestamp: U32
  - TCP Sending timestamp: U32

## HW TCP Server Command.ctl

- State: enum U16
    0 = Listen
    1 = Read
    2 = Send Heartbeat
    3 = Send Response
    4 = Quit

## HW TCP Server Queue.ctl

- Cluster: Cluster
  - State: enum U16
      0 = Listen
      1 = Read
      2 = Send Heartbeat
      3 = Send Response
      4 = Quit
  - State Data: Variant

## typedef_ext_wl_calibrate_data.ctl

- WL Calibrate Data: Cluster
  - Grating Data: U32

## typedef_ext_wl_command.ctl

- WL Command: enum U16
    0 = Show UI
    1 = Set Timeout
    2 = Get Heartbeat
    3 = Setup
    4 = Cleanup
    5 = Is Connected ?
    6 = Get Serial
    7 = Is Inited ?
    8 = Set Scan Speed
    9 = Get Scan Speed
    10 = Get Position
    11 = Set Position
    12 = Get Units
    13 = Set Units
    14 = Scan
    15 = Get Groove/mm
    16 = Get Blaze (nm)
    17 = Get Grating
    18 = Reset
    19 = Calibrate
    20 = Select Gratings
    21 = Exit
    22 = Log

## typedef_ext_wl_heartbeat_data.ctl

- WL SERVER STATUS: Cluster
  - SERVER: Boolean
  - WL STATUS: Boolean
  - HEARTBEAT: U32

## typedef_ext_wl_log_data.ctl

- WL Log Data: Cluster
  - Log: Boolean
  - Path: Path
  - Max Lines: I32
  - Max Count: I32

## typedef_ext_wl_properties.ctl

- WL Settings: Cluster
  - Com: U32
  - Baudrate: U16
  - Timeout (ms): I32

## typedef_ext_wl_queue_data.ctl

- WL Queue Data: Cluster
  - WL Command: enum U16
      0 = Show UI
      1 = Set Timeout
      2 = Get Heartbeat
      3 = Setup
      4 = Cleanup
      5 = Is Connected ?
      6 = Get Serial
      7 = Is Inited ?
      8 = Set Scan Speed
      9 = Get Scan Speed
      10 = Get Position
      11 = Set Position
      12 = Get Units
      13 = Set Units
      14 = Scan
      15 = Get Groove/mm
      16 = Get Blaze (nm)
      17 = Get Grating
      18 = Reset
      19 = Calibrate
      20 = Select Gratings
      21 = Exit
      22 = Log
  - Notifier Name: String
  - Command Data: Variant
  - Command Request Timestamp: U32
  - Command Sending Timestamp: U32
  - Command Receiving Timestamp: U32

## typedef_ext_wl_scan_data.ctl

- WL Scan Data: Cluster
  - Start: U16
  - End: U16

## typedef_ext_wl_session.ctl

- WL Session: Cluster
  - COM Port Out: Refnum (kind 14: Instr)
  - Serial Number: String

## typedef_ext_wl_set_grating_data.ctl

- WL Grating Data: Cluster
  - Grating Data: U32

## typedef_ext_wl_set_position_data.ctl

- WL Set Position Data: Cluster
  - Position: U32

## typedef_ext_wl_set_speed_data.ctl

- WL Set Speed Data: Cluster
  - Speed: U8

## typedef_ext_wl_set_timeout_data.ctl

- WL Timeout Data: Cluster
  - Timeout (ms): I32

## typedef_ext_wl_set_units_data.ctl

- WL Set Units Data: Cluster
  - WL Units: enum U16
      0 = Centimeters
      1 = Nanometers
      2 = Angstroms

## typedef_ext_wl_units.ctl

- WL Units: enum U16
    0 = Centimeters
    1 = Nanometers
    2 = Angstroms

## typedef_spectral_products_query.ctl

- Query Method: enum U16
    0 = Position
    1 = Type
    2 = Groove/mm  
    3 = Blaze (nm)
    4 = Current grating
    5 = Scan speed
    6 = Step size
    7 = Zero off.m1g1
    8 = Zero off.m1g2
    9 = Zero off.m2g1
    10 = Zero off.m2g2
    11 = Cal. m1g1
    12 = Cal. m1g2
    13 = # of gratings
    14 = Current units
    15 = Gr/mm m1g1
    16 = Blaze m1g1
    17 = Gr/mm m1g2
    18 = Blaze m1g2
    19 = Serial number
    20 = Novram #14

## typedef_ext_joystick_data.ctl

- XY Joystick Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Joystick ID: I32
  - Joystick Axis: I32

## typedef_ext_xy_axis.ctl

- Axis: enum U16
    0 = X
    1 = Y

## typedef_ext_xy_command.ctl

- XY Command: enum U16
    0 = Show UI
    1 = Get Heartbeat
    2 = Setup
    3 = Cleanup
    4 = Get Serial
    5 = Get Status
    6 = Get Error
    7 = Set Speed
    8 = Get Speed
    9 = Get Position
    10 = Move Absolute
    11 = Move Relative
    12 = Wait End Move
    13 = Stop
    14 = Enable Joystick
    15 = Disable Joystick
    16 = Joystick Enabled ?
    17 = Exit
    18 = Log
    19 = Get Joystick Button
    20 = Move Absolute XY
    21 = Get Position XY
    22 = Set Chuck Origin
    23 = Set Wafer Origin
    24 = Set Wafer Diameter
    25 = Set Wafer Limit
    26 = Set Wafer Check Freq
    27 = Get Reference
    28 = Do Reference

## typedef_ext_xy_get_error_data.ctl

- XY Stage Get Error Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y

## typedef_ext_xy_get_position_data.ctl

- XY Get Position Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y

## typedef_ext_xy_get_serial_data.ctl

- XY Stage Get Serial Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Type: Boolean

## typedef_ext_xy_get_speed_data.ctl

- XY Get Speed Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y

## typedef_ext_xy_get_status_data.ctl

- XY Stage Get Status Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y

## typedef_ext_xy_heartbeat_data.ctl

- SERVER STATUS: Cluster
  - SERVER: Boolean
  - X STAGE STATUS: Boolean
  - Y STAGE STATUS: Boolean
  - HEARTBEAT: U32

## typedef_ext_xy_log_data.ctl

- XY Log Data: Cluster
  - Log: Boolean
  - Path: Path
  - Max Lines: I32
  - Max Count: I32

## typedef_ext_xy_mgmt_queue_data.ctl

- XY Queue Data: Cluster
  - XY Command: enum U16
      0 = Show UI
      1 = Get Heartbeat
      2 = Setup
      3 = Cleanup
      4 = Get Serial
      5 = Get Status
      6 = Get Error
      7 = Set Speed
      8 = Get Speed
      9 = Get Position
      10 = Move Absolute
      11 = Move Relative
      12 = Wait End Move
      13 = Stop
      14 = Enable Joystick
      15 = Disable Joystick
      16 = Joystick Enabled ?
      17 = Exit
      18 = Log
      19 = Get Joystick Button
      20 = Move Absolute XY
      21 = Get Position XY
      22 = Set Chuck Origin
      23 = Set Wafer Origin
      24 = Set Wafer Diameter
      25 = Set Wafer Limit
      26 = Set Wafer Check Freq
      27 = Get Reference
      28 = Do Reference
  - Notifier Name: String
  - Command Data: Variant
  - Command Request Timestamp: U32
  - Command Sending Timestamp: U32
  - Command Receiving Timestamp: U32

## typedef_ext_xy_move_absolute_data.ctl

- XY Move Absolute Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Move (mm): DBL

## typedef_ext_xy_move_absolute_data_2.ctl

- XY Move Absolute Data 2: Cluster
  - Move X (mm): DBL
  - Move Y (mm): DBL

## typedef_ext_xy_move_relative_data.ctl

- XY Move Relative Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Move (mm): DBL

## typedef_ext_xy_move_wait_data.ctl

- XY Wait Move Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Add. Wait (ms): U32
  - Polling Cycle (ms): U32
  - Add. Wait Only: Boolean

## typedef_ext_xy_set_speed_data.ctl

- XY Stage Set Speed Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y
  - Speed (mm/s): DBL

## typedef_ext_xy_stop_data.ctl

- XY Stop Data: Cluster
  - Axis: enum U16
      0 = X
      1 = Y

## alox_typedef_auto_exposure_data.ctl

- AE Data: Cluster
  - AutoExpo: DBL
  - ExpoMin: DBL
  - ExpoMax: DBL
  - Brightness Min: DBL
  - Brightness Max: DBL

## alox_typedef_auto_exposure_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Results

## alox_typedef_auto_exposure_function_parameters.ctl

- Auto Exposure Params: Cluster
  - Mode: enum U16
      0 = Current Wavelength
      1 = All Wavelengths
  - Exposure Min (µs): DBL
  - Exposure Max (µs): DBL
  - Target Brightness: U32
  - ROI Diameter (µm): DBL
  - Show Window: Boolean
  - Overlay ROI: Boolean
  - Overlay Result: Boolean
  - BypassCircle: Boolean

## alox_typedef_auto_exposure_function_session.ctl

- Auto Exposure Session: Cluster
  - Vi Reference: Refnum (kind 8)
  - Expo Image: Refnum (kind 29: IMAQImage)
  - Measures Count: I32
  - t0: U32
  - Init Exposure: DBL
  - Wavelengths: 1D array
    - Wavelength (nm): U32
  - Current WL Index: I32
  - Init WL: U32
  - Acq Image: Refnum (kind 29: IMAQImage)
  - Init Expo OK: Boolean
  - Init WL OK: Boolean
  - Measures Retry: I32

## alox_typedef_auto_exposure_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Set Wavelength
    3 = Check Wavelength
    4 = Set Exposure
    5 = Wait
    6 = Measure Expo
    7 = Measure Finished
    8 = Set Auto Expo
    9 = Finished
    10 = Error
    11 = Stopped

## alox_typedef_auto_exposure_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_auto_exposure_mode.ctl

- Auto Expo Mode: enum U16
    0 = Current Wavelength
    1 = All Wavelengths

## alox_typedef_auto_exposure_report.ctl

- Auto Exposure Report: Cluster
  - Exposure1: DBL
  - Brightness1: DBL
  - Exposure2: DBL
  - Brightness2: DBL
  - Exposure: DBL
  - Brightness: DBL
  - Found Exposure: Boolean
  - Process Time (ms): DBL
  - wl(nm): U32
  - workin wl(nm): U32

## alox_typedef_auto_exposure_theoric_data.ctl

- Theoric Expo Data: Cluster
  - Wavelength (nm): DBL
  - Theoric Expo: DBL

## alox_typedef_autofocus_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Last Measure

## alox_typedef_autofocus_function_parameters.ctl

- Autofocus Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Autofocus Method: enum U16
      0 = FFT
      1 = Sobel
      2 = Prewitt
      3 = Diff
      4 = Gradient
      5 = Sigma
      6 = FULL
  - Autofocus Speed: enum U16
      0 = Slow
      1 = Fast
  - Focus Detection Method: enum U16
      0 = Curve Maximum
      1 = Fit Maximum
      2 = Local Maximum
  - Duration (s): I32
  - Z Step (mm): DBL
  - Subsampling: DBL
  - Optional Rectangle: 1D array
    - (no label): I32
  - Show UI: Boolean
  - Overlay Score: Boolean
  - Overlay Position: Boolean

## alox_typedef_autofocus_function_report.ctl

- Autofocus Report: Cluster
  - Best Z Position: DBL
  - Best Score: DBL
  - Focus Data: 2D array
    - Numeric: DBL
  - Graph Data: 1D array
    - Graph Data: Cluster
      - X: DBL
      - Y: DBL
      - Y Polynom: DBL
  - Multi XY Data: 1D array
    - (no label): Cluster
      - (no label): 1D array
        - X: DBL
      - (no label): 1D array
        - X: DBL
  - Valid ?: Boolean
  - Progress %: DBL

## alox_typedef_autofocus_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Goto Z Pos
    3 = Focus Measure
    4 = Focus Display
    5 = Focus Finished
    6 = Finished
    7 = Error
    8 = Stopped

## alox_typedef_autofocus_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_bowing_computation_results.ctl

- PARA RESULTS: Cluster
  - Result A: DBL
  - Result B: DBL
  - Valid: Boolean
  - Valid A: Boolean
  - Valid B: Boolean

## alox_typedef_bowing_measurement_chuck_properties.ctl

- Chuck Properties: Cluster
  - dZ1 Chuck: DBL
  - dZ3 Chuck: DBL
  - dZ4 Chuck: DBL
  - dZ5 Chuck: DBL
  - Max Parallelism: DBL
  - Wafer Diameter (mm): DBL
  - dZ2 Chuck: DBL
  - bowChuckA: DBL
  - bowChuckB: DBL

## alox_typedef_bowing_measurement_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status
    5 = Get Last Measure
    6 = Abort

## alox_typedef_bowing_measurement_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Move to Pos Zi
    3 = Wait Pos Zi
    4 = Autofocus Large
    5 = Autofocus Fine
    6 = Measure Bowing
    7 = Finished
    8 = Error
    9 = Stopped

## alox_typedef_bowing_measurement_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_bowing_measurement_parameters.ctl

- Bowing  Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Measure Distance (mm): DBL
  - Max Bowing (µm): DBL
  - Show Window: Boolean
  - Overlay Bowing: Boolean
  - Overlay Parallelism: Boolean
  - Fast method: Boolean

## alox_typedef_bowing_measurement_report.ctl

- Bowing Measurement Report: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Bowing: Cluster
    - Z1: DBL
    - Z2: DBL
    - Z3: DBL
    - Value: DBL

## alox_typedef_bowing_measurement_session.ctl

- Bowing Session: Cluster
  - Vi reference: Refnum (kind 8)
  - Z Measures: 2D array
    - Y: DBL
  - Initial WL: U32
  - Last Move Timestamp: U32
  - Error Count: U32
  - Bowing Measured: Boolean

## alox_typedef_detect_circle_algo_method.ctl

- Algo Method: enum U16
    0 = Shape
    1 = Obj learning
    2 = Blobs

## alox_typedef_detect_circle_function_best_method.ctl

- Best Circle Method: enum U16
    0 = Centered
    1 = Score
    2 = Largest

## alox_typedef_detect_circle_function_hough.ctl

- Hough params: Cluster
  - dp: DBL
  - minDist: DBL
  - Param1: DBL
  - Param2: DBL

## alox_typedef_detect_circle_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Results
    7 = Set Params
    8 = Force Results

## alox_typedef_detect_circle_function_output_image.ctl

- Output Image Type: enum U16
    0 = Source
    1 = Pre-Processed
    2 = Binarized
    3 = Filtered

## alox_typedef_detect_circle_function_parameters.ctl

- Detect Circle Params: Cluster
  - Algo Method: enum U16
      0 = Shape
      1 = Obj learning
      2 = Blobs
  - Circle Settings: Cluster
    - Best Circle Method: enum U16
        0 = Centered
        1 = Score
        2 = Largest
    - Radius Min (µm): DBL
    - Radius Max (µm): DBL
  - Pre Processings: Cluster
    - Downsampling: enum U16
        0 = None
        1 = 25%
        2 = 50%
        3 = 75%
        4 = 90%
    - Morph.Open Size: I32
    - Morph.Close Size: I32
    - Morph.Dilate Size: I32
    - Morph.Erode Size: I32
  - Shape Settings: Cluster
    - Edge Threshold: I32
    - Min Length: I32
    - Occlusion: Boolean
    - Min Score(%): DBL
    - Extend research: Boolean
    - Rectangle: Boolean
    - Ellipses: Boolean
    - Circles: Boolean
  - Blobs Settings: Cluster
    - Method: U32
    - Object type: U32
    - Window Size: I32
    - Remove Smalls: Boolean
    - Small Iterations: I32
    - Binary Invert: Boolean
    - Remove Border: Boolean
    - Fill Holes: Boolean
    - Circ. Factor Min: DBL
    - Circ. Factor Max: DBL
  - Overlays: Cluster
    - Overlay Best Circle: Boolean
    - Overlay All Circles: Boolean
    - Overlay Objects: Boolean
  - MRQT: Cluster
    - dp: DBL
    - param2: DBL
    - minDist: DBL
    - param1: DBL
  - OBJ LEARNING: Cluster
    - SCORE: DBL
    - OCCLUSION: DBL
    - Search Model Path: Path
    - Learn Mask Path: Path

## alox_typedef_detect_circle_function_result.ctl

- Detect Circle Results: Cluster
  - Best Circle: Cluster
    - Center: Cluster
      - X: SGL
      - Y: SGL
    - Radius: DBL
  - All Circles: 1D array
    - All Circles: Cluster
      - Center: Cluster
        - X: SGL
        - Y: SGL
      - Radius: DBL
  - Best Circle Index: I32
  - Found: Boolean
  - Process Time (ms): DBL

## alox_typedef_detect_circle_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Detect Circle
    3 = Finished
    4 = Error
    5 = Stopped

## alox_typedef_detect_circle_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_detect_object_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Results

## alox_typedef_detect_object_function_parameters.ctl

- Detect Object Params: Cluster
  - Start Search Pos (mm): Cluster
    - X: SGL
    - Y: SGL
  - Search Model Path: Path
  - Learn Mask Path: Path
  - Edge Threshold: I32
  - Min Length: I32
  - Min Score (%): DBL
  - Max Occlusion (%): DBL
  - Morph Open: I32
  - Morph Close: I32
  - Max Moves: I32
  - Step (mm): DBL
  - Autofocus: Boolean
  - Refocus: Boolean
  - Show Window: Boolean
  - Overlay Match: Boolean
  - Overlay Contours: Boolean
  - Offset Y: DBL
  - Offset X: DBL

## alox_typedef_detect_object_function_results.ctl

- Detect Object Report: Cluster
  - Search Positions: 1D array
    - Point: Cluster
      - X: SGL
      - Y: SGL
  - Current XY Pos (mm): Cluster
    - X: SGL
    - Y: SGL
  - Current Pos Index: I32
  - Autofocus Z Pos (mm): DBL
  - Autofocus Z Score: DBL
  - Found Pos (mm): Cluster
    - X: SGL
    - Y: SGL
  - Autofocus Graph Data: 1D array
    - Graph Data: Cluster
      - X: DBL
      - Y: DBL
      - Y Polynom: DBL
  - Autofocus Data: 2D array
    - Numeric: DBL
  - Found Object ?: Boolean
  - Best Match (pixels) : Cluster
    - Position: Cluster
      - x: SGL
      - y: SGL
    - Angle: SGL
    - Scale: SGL
    - Score: SGL
    - Bounding Box: 1D array
      - (no label): Cluster
        - x: SGL
        - y: SGL
    - Contrast Reversed: Boolean
    - Occlusion %: SGL
    - Template Target Curve Score: SGL
    - Target Template Curve Score: SGL
    - Correlation Score: SGL
  - All Matches (pixel): 1D array
    - GM Match Report: Cluster
      - Position: Cluster
        - x: SGL
        - y: SGL
      - Angle: SGL
      - Scale: SGL
      - Score: SGL
      - Bounding Box: 1D array
        - (no label): Cluster
          - x: SGL
          - y: SGL
      - Contrast Reversed: Boolean
      - Occlusion %: SGL
      - Template Target Curve Score: SGL
      - Target Template Curve Score: SGL
      - Correlation Score: SGL

## alox_typedef_detect_object_function_session.ctl

- Detect Object Session: Cluster
  - Vi Reference: Refnum (kind 8)
  - Model Image: Refnum (kind 29: IMAQImage)
  - Temp Image: Refnum (kind 29: IMAQImage)
  - XY Move timestamp: U32
  - Error Count: U32

## alox_typedef_detect_object_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Move To Pos
    3 = Position OK?
    4 = Autofocus Init
    5 = Autofocus
    6 = Autofocus Finished
    7 = Refocus Init
    8 = Refocus
    9 = Refocus Finished
    10 = Detect Object
    11 = Move To Object
    12 = Object Pos OK ?
    13 = Finished
    14 = Error
    15 = Stopped

## alox_typedef_detect_object_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_get_image_exposure_mode.ctl

- Exposure Mode: enum U16
    0 = Fixed
    1 = Auto
    2 = Current

## alox_typedef_get_image_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Image

## alox_typedef_get_image_function_parameters.ctl

- Get Image Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Led: Boolean
  - Exposure Mode: enum U16
      0 = Fixed
      1 = Auto
      2 = Current
  - Low Mag Exposure : DBL
  - Optional Rectangle: 1D array
    - (no label): I32
  - ZOOM: DBL
  - Brightness: I32
  - Contrast: I32
  - Gamma: DBL
  - Simulation: Boolean
  - Simulation Path: Path
  - Simulation Timer ms: U32
  - Overlay Name: Boolean
  - Overlay Type: Boolean
  - Overlay Timestamp: Boolean
  - Overlay Index: Boolean
  - Overlay HW  Infos: Boolean
  - Overlay Scale: Boolean
  - Camera LEDcmd: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Gain: DBL

## alox_typedef_get_image_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Setup Hardware
    3 = Acquire
    4 = Finished
    5 = Error
    6 = Stopped

## alox_typedef_get_image_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_measure_mesa_diameters_report.ctl

- Diameters Measurements: Cluster
  - External: DBL
  - Fit: DBL
  - Median: DBL
  - Mean: DBL
  - Surface: DBL
  - Inscribed: DBL
  - Circumscribed: DBL
  - Circ/Insc Ratio: DBL
  - STDDEV: DBL
  - Accuracy: DBL

## alox_typedef_measure_mesa_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Results
    7 = Set Params

## alox_typedef_measure_mesa_function_parameters.ctl

- Measure Oxyd Params: Cluster
  - Mesa Search Dir: enum U16
      0 = Outside To Inside
      1 = Inside To Outside
  - Mesa Edge Polarity: enum U16
      0 = All
      1 = Rising Edges
      2 = Falling Edges
  - Mesa Strength: DBL
  - Mesa Kernel: U32
  - Mesa Width: U32
  - Mesa Step: DBL
  - Mesa Min Diam (µm): DBL
  - Oxyd Search Dir: enum U16
      0 = Outside To Inside
      1 = Inside To Outside
  - Oxyd Edge Polarity: enum U16
      0 = All
      1 = Rising Edges
      2 = Falling Edges
  - Oxyd Strength: DBL
  - Oxyd Kernel: U32
  - Oxyd Width: U32
  - Oxyd Step: DBL
  - Oxyd Max Diam (µm): DBL
  - Oxyd Measure Type: enum U16
      0 = Mean
      1 = Median
      2 = Fit
      3 = Surface
      4 = Circumscribed
      5 = Inscribed
  - Target Diam (µm): DBL
  - Fit Max Dist.: U32
  - Fit Min Score: DBL
  - Fit Max Iterations: I32
  - Reject Outliers: Boolean
  - Overlay External: Boolean
  - Overlay Oxyd Diameters: Boolean
  - Overlay Oxyd: Boolean
  - Overlay Target: Boolean
  - Overlay Oxyd Edges: Boolean
  - Overlay Oxyd Measure: Boolean
  - Mesa Max Diam (µm): DBL
  - Oxyd Min Diam (µm): DBL
  - OVERLAY MESA SEARCH LINES: Boolean
  - Overlay Mesa Edges: Boolean
  - Meas Mesa: Boolean
  - Meas Oxid: Boolean
  - Mesa Shape Type: enum U16
      0 = Circular
      1 = Line
      2 = Rectangle
  - oxid result offset: DBL

## alox_typedef_measure_mesa_function_results.ctl

- Measure Mesa Results: Cluster
  -  External Circle: Cluster
    - Center: Cluster
      - X: SGL
      - Y: SGL
    - Radius: DBL
  -  Oxyd Circle: Cluster
    - Center: Cluster
      - X: SGL
      - Y: SGL
    - Radius: DBL
  - Diameters Measurements: Cluster
    - External: DBL
    - Fit: DBL
    - Median: DBL
    - Mean: DBL
    - Surface: DBL
    - Inscribed: DBL
    - Circumscribed: DBL
    - Circ/Insc Ratio: DBL
    - STDDEV: DBL
    - Accuracy: DBL
  - Diameters Coordinates: 1D array
    - Line: Cluster
      - Start X: I32
      - Start Y: I32
      - End X: I32
      - End Y: I32
  - Diameters Lengths (Pixels): 1D array
    - Numeric: DBL
  - Points OXYD Used: 1D array
    - Point: Cluster
      - X: SGL
      - Y: SGL
  - Mesa Found : Boolean
  - Oxyd Found: Boolean
  - Process Time (ms): DBL
  - Oxyd Search Lines: 1D array
    - Line: Cluster
      - Start X: I32
      - Start Y: I32
      - End X: I32
      - End Y: I32
  - Mesa Search Lines: 1D array
    - Line: Cluster
      - Start X: I32
      - Start Y: I32
      - End X: I32
      - End Y: I32
  - Points MESA Used: 1D array
    - Point: Cluster
      - X: SGL
      - Y: SGL
  - Gradient Values: 1D array
    - Numeric: DBL

## alox_typedef_measure_mesa_function_session.ctl

- MESA Session: Cluster
  - Run Vi Refnum: Refnum (kind 8)
  - State Error Count: U32
  - t0: DBL
  - Oxyd Measures: 2D array
    - (no label): DBL
  - Auto Expo Done: Boolean
  - Wavelength Done: Boolean
  - trefocus: DBL
  - Autofocus Err Count: U32
  - Refocus Err Count: U32
  - Detect Circle Err Count: U32
  - Tracking Err Count: U32
  - Circle Detected: Boolean
  - XY Move Timestamp: U32
  - t0 save: DBL

## alox_typedef_measure_mesa_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Measure
    3 = Finished
    4 = Error
    5 = Stopped

## alox_typedef_measure_mesa_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_measure_oxid_results_diameters_report.ctl

- Oxyd: Cluster
  - Fit Diam. (µm): DBL
  - Median Diam. (µm): DBL
  - Average Diam. (µm): DBL
  - Surface Diam. (µm): DBL
  - Inscribed Diam. (µm): DBL
  - Circumscribed Diam. (µm): DBL
  - Circumscribed/Inscribed: DBL
  - Oxyd Measure Type: enum U16
      0 = Mean
      1 = Median
      2 = Fit
      3 = Surface
      4 = Circumscribed
      5 = Inscribed
  - STDEV FIT/#: DBL

## alox_typedef_measure_oxyd_mesa_shape_type.ctl

- Mesa Shape Type: enum U16
    0 = Circular
    1 = Line
    2 = Rectangle

## alox_typedef_mesa_measurement_display_report.ctl

- Measure Mesa Report UI: Cluster
  - SEMI Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - External: Cluster
    - Fit Diam. (µm): DBL
  - Oxyd: Cluster
    - Fit Diam. (µm): DBL
    - Median Diam. (µm): DBL
    - Average Diam. (µm): DBL
    - Surface Diam. (µm): DBL
    - Inscribed Diam. (µm): DBL
    - Circumscribed Diam. (µm): DBL
    - Circumscribed/Inscribed: DBL
    - Oxyd Measure Type: enum U16
        0 = Mean
        1 = Median
        2 = Fit
        3 = Surface
        4 = Circumscribed
        5 = Inscribed
    - STDEV FIT/#: DBL
  - Oxyd Rate: Cluster
    - Rate (µm/min): DBL
    - Remain Time (s): DBL
  - Graph Data: 1D array
    - (no label): Cluster
      - X: 1D array
        - Float Output: DBL
      - X: 1D array
        - Float Output: DBL
  - Linear Fit: Cluster
    - Slope: DBL
    - Intercept: DBL
    - Residual: DBL
    - Fit Score(%): DBL
  - Measured Diam. (µm): DBL
  - Target Diam. (µm): DBL

## alox_typedef_mesa_measurement_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status
    5 = Get Last Measure
    6 = Abort

## alox_typedef_mesa_measurement_function_parameters.ctl

- Mesa Measurement Params: Cluster
  - Start Position (mm): Cluster
    - X: SGL
    - Y: SGL
  - Tilt Correction: Boolean
  - Refocus Time (s): DBL
  - Refocus Retries: I32
  - Oxyd Target (µm): DBL
  - Line Reg. Time (s): DBL
  - Singleshot: Boolean
  - # Averages: U32
  - Show Window: Boolean
  - Overlay Time: Boolean
  - Overlay Oxyd Rate: Boolean
  - Overlay Oxyd Diameter: Boolean
  - Overlay Oxyd Status: Boolean
  - positionsctrl: Boolean
  - analysis: Boolean
  - Tracking: Boolean
  - WL Mode: enum U16
      0 = MANUAL
      1 = SCAN
  - EXPOSURE MODE: U16

## alox_typedef_mesa_measurement_function_parameters_exposure.ctl

- EXPOSURE MODE: U16

## alox_typedef_mesa_measurement_function_state.ctl

- State: enum U16
    0 = Start
    1 = Move Start
    2 = Wait Move
    3 = Search Init
    4 = Search Object
    5 = Autofocus Large
    6 = Refocus
    7 = Detect Circle Init
    8 = Detect Circle
    9 = Track MESA
    10 = Auto Expo
    11 = Search Wavelength
    12 = Set Wavelength
    13 = Measure MESA Init
    14 = Measure MESA Refocus
    15 = Measure MESA
    16 = Display Save
    17 = Display Save ERROR
    18 = Finished
    19 = Error
    20 = Stopped

## alox_typedef_mesa_measurement_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_mesa_measurement_report.ctl

- Measure Mesa Report: Cluster
  - SEMI Job: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Timestamp: Cluster
    - Unix Timestamp: I64
  - External: Cluster
    - Fit Diam. (µm): DBL
  - Oxid: Cluster
    - Fit Diam. (µm): DBL
    - Median Diam. (µm): DBL
    - Average Diam. (µm): DBL
    - Surface Diam. (µm): DBL
    - Inscribed Diam. (µm): DBL
    - Circumscribed Diam. (µm): DBL
    - Circumscribed/Inscribed: DBL
    - Oxyd Measure Type: enum U16
        0 = Mean
        1 = Median
        2 = Fit
        3 = Surface
        4 = Circumscribed
        5 = Inscribed
    - STDEV FIT/#: DBL
  - Oxyd Rate: Cluster
    - Rate (µm/min): DBL
    - Remain Time (s): DBL
  - Linear Fit: Cluster
    - Slope: DBL
    - Intercept: DBL
    - Residual: DBL
    - Fit Score(%): DBL
  - Positions: Cluster
    - x: DBL
    - y: DBL
    - z: DBL
  - Wavelength: Cluster
    - WL: DBL
  - mesa measure report values: Cluster
    - Oxyd Diam.: DBL
    - External Diameter: DBL
    - Oxyd Target (µm): DBL
    - Oxid Filtered: 1D array
      - Oxid Filtered: DBL
    - #Average: I32
    - Oxid Row: 1D array
      - Oxid Filtered: DBL
    - Oxyd Diam. 2: DBL

## alox_typedef_oxyd_averaged_measurement_report.ctl

- Oxyd Average Report: Cluster
  - External Diameter: DBL
  - Fitted Diameter: DBL
  - Median Diameter: DBL
  - Mean Diameter: DBL
  - Surface Diameter: DBL
  - Inscribed Diameter: DBL
  - Circumscribed Diameter: DBL
  - Circularity: DBL
  - # Samples: DBL
  - Oxyd Measure Type: enum U16
      0 = Mean
      1 = Median
      2 = Fit
      3 = Surface
      4 = Circumscribed
      5 = Inscribed
  - STDDEV FIT/#: DBL

## alox_typedef_refocus_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Last Measure

## alox_typedef_refocus_function_parameters.ctl

- Refocus Fin Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Autofocus Method: enum U16
      0 = FFT
      1 = Sobel
      2 = Prewitt
      3 = Diff
      4 = Gradient
      5 = Sigma
      6 = FULL
  - Steps: I32
  - Step (mm): DBL
  - ROI Width (px): I32
  - ROI Height (px): I32
  - Wait (ms): I32
  - Show UI: Boolean
  - Overlay Score: Boolean
  - Overlay Position: Boolean
  - Overlay ROI: Boolean
  - Offset (mm): DBL

## alox_typedef_refocus_function_results.ctl

- Refocus Report: Cluster
  - Best Z Position: DBL
  - Zs: 1D array
    - Numeric: DBL
  - Best Score: DBL
  - Scores: 1D array
    - Numeric: DBL
  - Valid ?: Boolean
  - Progress %: DBL

## alox_typedef_refocus_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Measure
    3 = Finished
    4 = Error
    5 = Stopped

## alox_typedef_refocus_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_refocus_oxyd_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Last Measure

## alox_typedef_refocus_oxyd_function_parameters.ctl

- Refocus Oxyd Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Autofocus Method: enum U16
      0 = FFT
      1 = Sobel
      2 = Prewitt
      3 = Diff
      4 = Gradient
      5 = Sigma
      6 = FULL
  - Steps: I32
  - Step (mm): DBL
  - ROI Diameter (µm): I32
  - Wait (ms): I32
  - Show UI: Boolean
  - Overlay Score: Boolean
  - Overlay Position: Boolean
  - Overlay ROI: Boolean

## alox_typedef_refocus_oxyd_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Measure
    3 = Finished
    4 = Error
    5 = Stopped

## alox_typedef_refocus_oxyd_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_search_wavelength_detect_circle_method.ctl

- DETECT CIRCLE METHOD: enum U16
    0 = SINGLE (AT START WL)
    1 = FULL (AT EVERY WL)

## alox_typedef_search_wavelength_function_expo_mode.ctl

- WL Expo Mode: enum U16
    0 = Fixed Expo
    1 = Auto Expo

## alox_typedef_search_wavelength_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Last Measure

## alox_typedef_search_wavelength_function_mode.ctl

- WL Mode: enum U16
    0 = MANUAL
    1 = SCAN

## alox_typedef_search_wavelength_function_mode_mesa.ctl

- WL Mode: enum U16
    0 = Manual
    1 = Auto

## alox_typedef_search_wavelength_function_parameters.ctl

- Wavelength Params: Cluster
  - WL Mode: enum U16
      0 = MANUAL
      1 = SCAN
  - Manual WL (nm): DBL
  - Start WL: DBL
  - End WL: DBL
  - Step WL: DBL
  - Min. Contrast: DBL
  - WL ROI Type: enum U16
      0 = Dual Spots
      1 = Spot-Annular
  - Dual Spots Size (µm): DBL
  - Dual Spots Distance (µm): DBL
  - Dual Spots Angle (°): DBL
  - Annul. Spot Size (µm): DBL
  - Annul. Diam 1 (µm): DBL
  - Annul. Diam 2 (µm): DBL
  - Show UI: Boolean
  - Overlay ROIs: Boolean
  - Overlay Results: Boolean
  - Overlay Oxid Mesurement: Boolean
  - DETECT CIRCLE METHOD: enum U16
      0 = SINGLE (AT START WL)
      1 = FULL (AT EVERY WL)
  - Step Refocus: I32
  - WL shift (nm): I32

## alox_typedef_search_wavelength_function_report.ctl

- Wavelength Report: Cluster
  - Current WL (nm): DBL
  - Current Expo (µs): DBL
  - Current Contrast: DBL
  - Optimal WL (nm): DBL
  - Optimal Expo (µs): DBL
  - Optimal Contrast: DBL
  - Min Contrast: DBL
  - Progress(%): DBL
  - Valid ?: Boolean
  - Spectrum Graph: 1D array
    - (no label): Cluster
      - Array: 1D array
        - Numeric: DBL
      - Array: 1D array
        - Numeric: DBL
  - Histogram Graph: 1D array
    - (no label): Cluster
      - Array: 1D array
        - Numeric: I32
      - Array: 1D array
        - Numeric: I32
  - Error Ref count: I32

## alox_typedef_search_wavelength_function_roi_type.ctl

- WL ROI Type: enum U16
    0 = Dual Spots
    1 = Spot-Annular

## alox_typedef_search_wavelength_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Init WL
    3 = Move WL
    4 = WL Pos OK ?
    5 = Refocus
    6 = Detect circle
    7 = Measure
    8 = Move Opt WL
    9 = Check Opt WL
    10 = Finished
    11 = Error
    12 = Stopped

## alox_typedef_search_wavelength_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_search_wavelength_manual_rois.ctl

- Manual ROIs: Cluster
  - ROI Non Oxyd: Cluster
    - Global Rectangle: 1D array
      - (no label): I32
    - Contours: 1D array
      - (no label): Cluster
        - ID: enum U32
            0 = External
            1 = Internal
        - Type: U32
        - Coordinates: 1D array
          - (no label): I32
  - ROI Oxyd: Cluster
    - Global Rectangle: 1D array
      - (no label): I32
    - Contours: 1D array
      - (no label): Cluster
        - ID: enum U32
            0 = External
            1 = Internal
        - Type: U32
        - Coordinates: 1D array
          - (no label): I32

## alox_typedef_tilt_measurement_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status
    5 = Get Last Measure
    6 = Abort

## alox_typedef_tilt_measurement_function_session.ctl

- Tilt Session: Cluster
  - Vi Reference: Refnum (kind 8)
  - OBJ1 Model Image: Refnum (kind 29: IMAQImage)
  - OBJ1 Mask Image: Refnum (kind 29: IMAQImage)
  - OBJ1 Temp Image: Refnum (kind 29: IMAQImage)
  - OBJ2 Model Image: Refnum (kind 29: IMAQImage)
  - OBJ2 Mask Image: Refnum (kind 29: IMAQImage)
  - OBJ2 Temp Image: Refnum (kind 29: IMAQImage)
  - Masked Image: Refnum (kind 29: IMAQImage)
  - Move Timestamp: U32
  - Error Count: U32

## alox_typedef_tilt_measurement_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Move To Pos
    3 = Position OK?
    4 = Autofocus Init
    5 = Autofocus
    6 = Autofocus Finished
    7 = Refocus Init
    8 = Refocus
    9 = Refocus Finished
    10 = Measure Tilt (Straight Edge)
    11 = Measure Tilt (Objects)
    12 = Notify Client
    13 = Display & Save
    14 = Finished
    15 = Error
    16 = Stopped

## alox_typedef_tilt_measurement_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## alox_typedef_tilt_measurement_overlay_options.ctl

- Overlay Options: Cluster
  - Overlay Straight: Boolean
  - Overlay Lines: Boolean
  - Overlay Edges: Boolean
  - Overlay Tilt: Boolean

## alox_typedef_tilt_measurement_parameters.ctl

- Tilt  Params: Cluster
  - Camera Type: enum U32
      0 = High Mag.
      1 = Low Mag.
  - Start Search Pos (mm): Cluster
    - X: SGL
    - Y: SGL
  - Tilt Method: enum U16
      0 = Straight Edge
      1 = Dual Objects
  - Max Moves: I32
  - Step (mm): DBL
  - Straight Edge Detection: Cluster
    - Search Direction: U32
    - Edge Polarity: U32
    - Minimum Edge Strength: U32
    - Step (Pixels): U32
    - Width: U32
    - Kernel Size: U32
    - Min. Points (%): U32
    - Angle Expected (°): DBL
    - Angle Range (°): DBL
    - Edge Method: enum U32
        0 = First Edge Rake
        1 = Best Edge Rake
        2 = Hough Edge Rake
        3 = First Edge Projection
        4 = Best Edge Projection
  - Dual Objects Detection: Cluster
    - Obj1 Model Path: Path
    - Obj1 Mask Path: Path
    - Obj2 Model Path: Path
    - Obj2 Mask Path: Path
    - Edge Threshold: U32
    - Min Length: I32
    - Morph Open: I32
    - Morph Close: I32
    - Obj1 Min Score (%): DBL
    - Obj1 Max Occl (%): DBL
    - Obj2 Min Score (%): DBL
    - Obj2 Max Occl (%): DBL
  - Autofocus: Boolean
  - Refocus: Boolean
  - Show Window: Boolean
  - Overlay Match: Boolean
  - Overlay Straight Edge: Boolean
  - Overlay Edges: Boolean
  - Overlay Tilt: Boolean

## alox_typedef_tilt_measurement_report.ctl

- Measure Tilt Report: Cluster
  - SEMI Job Infos: Cluster
    - Control Job ID: String
    - Process Job ID: String
    - Lot ID: String
    - Wafer ID: String
    - Zone ID: String
  - Straight Edge Tilt Report: Cluster
    - Point1: Cluster
      - X: SGL
      - Y: SGL
    - Point2: Cluster
      - X: SGL
      - Y: SGL
    - Tilt Angle (°): DBL
    - Found Edges (%): DBL
    - Tilt Found?: Boolean
    - # edges OK: Boolean
    - Tilt Tol OK: Boolean
  - Best Match1 (pixels) : Cluster
    - Position: Cluster
      - x: SGL
      - y: SGL
    - Angle: SGL
    - Scale: SGL
    - Score: SGL
    - Bounding Box: 1D array
      - (no label): Cluster
        - x: SGL
        - y: SGL
    - Contrast Reversed: Boolean
    - Occlusion %: SGL
    - Template Target Curve Score: SGL
    - Target Template Curve Score: SGL
    - Correlation Score: SGL
  - Object1 Found: Boolean
  - Best Match2 (pixels): Cluster
    - Position: Cluster
      - x: SGL
      - y: SGL
    - Angle: SGL
    - Scale: SGL
    - Score: SGL
    - Bounding Box: 1D array
      - (no label): Cluster
        - x: SGL
        - y: SGL
    - Contrast Reversed: Boolean
    - Occlusion %: SGL
    - Template Target Curve Score: SGL
    - Target Template Curve Score: SGL
    - Correlation Score: SGL
  - Object2 Found : Boolean
  - Tilt: Cluster
    - Angle (°): DBL
    - Tilt Valid: Boolean
  - Edges Process Time (ms): U32
  - Match Process Time (ms): U32
  - Process Time (ms): U32
  - Best Match1 (mm wafer): Cluster
    - X: SGL
    - Y: SGL
  - Best Match2 (mm wafer): Cluster
    - X: SGL
    - Y: SGL
  - Object1 Index: I32
  - Object2 Index: I32

## alox_typedef_tilt_measurement_straight_edge_report.ctl

- Straight Edge Tilt Report: Cluster
  - Point1: Cluster
    - X: SGL
    - Y: SGL
  - Point2: Cluster
    - X: SGL
    - Y: SGL
  - Tilt Angle (°): DBL
  - Found Edges (%): DBL
  - Tilt Found?: Boolean
  - # edges OK: Boolean
  - Tilt Tol OK: Boolean

## alox_typedef_tracking_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Run Once
    5 = Get Status
    6 = Get Results
    7 = Set Params

## alox_typedef_tracking_function_parameters.ctl

- Tracking Params: Cluster
  - Tracking Diam. (µm): DBL
  - Overlay Tracking Zone: Boolean

## alox_typedef_tracking_function_results.ctl

- Tracking Results: Cluster
  - Move X (mm): DBL
  - Move Y (mm): DBL
  - Move X (pix): DBL
  - Move Y (pix): DBL
  - Moved X: Boolean
  - Moved Y: Boolean
  - Process Time (ms): DBL

## alox_typedef_tracking_function_state.ctl

- State: enum U16
    0 = Start
    1 = Started
    2 = Track
    3 = Finished
    4 = Error
    5 = Stopped

## alox_typedef_tracking_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## Optris - Method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Get Firmware Rev
    3 = Get Hardware Rev
    4 = Get Serial
    5 = Get Sensor Infos
    6 = Get Obj Temp.
    7 = Get Emissivity
    8 = Set Emissitivity
    9 = Get Head Temp.
    10 = Get Spot Illumination
    11 = Set Spot Illumination
    12 = Get Box Temp
    13 = Get Actual Temp
    14 = Set Transmissivity
    15 = Get Transmissivity

## typedef_cam_exposure_wavelength_method.ctl

- Method: enum U16
    0 = Load
    1 = Get Exp/WL

## PythonVersionsEnum.ctl

- Python Version: enum U16
    0 = 2.7
    1 = 3.6

## scan_wl_autofocus_data.ctl

- AF Data: Cluster
  - AF Data: 2D array
    - Numeric: DBL
  - Time: DBL
  - Progress%: DBL

## scan_wl_autofocus_focus_data.ctl

- AF Focus Data: Cluster
  - Best Z: DBL
  - Best Score: DBL
  - Graph Data: 1D array
    - Graph Data: Cluster
      - X: DBL
      - Y: DBL
      - Y Polynom: DBL
  - Valid: Boolean
  - Multi Graph Data: 1D array
    - (no label): Cluster
      - AF Data Out: 1D array
        - Numeric: DBL
      - Diff = Y - Fit: 1D array
        - Numeric: DBL

## scan_wl_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status

## scan_wl_function_profile.ctl

- scan_wl profile: Cluster
  - Z1: DBL
  - Z2: DBL
  - duration: DBL
  - Client Tag: String
  - Sample Tag: String
  - Z Stage Settings: Cluster
    - Interface: U16
    - Port: I32
    - Baudrate: U16
    - S/N (USB): String
    - Speed (mm/s): DBL
  - Output Folder: Path

## scan_wl_function_state.ctl

- State: enum U16
    0 = Start
    1 = Set WL
    2 = Wait WL
    3 = Set High Expo
    4 = Wait High Expo
    5 = AF Move Zmin
    6 = AF Wait Zmin
    7 = AF Move Zmax
    8 = AF Wait
    9 = AF Measure
    10 = AF Finished
    11 = AF Move Focus
    12 = AF Wait Focus
    13 = AF Error
    14 = Set Wanted Expo
    15 = Wait Wanted Expo
    16 = Acquire
    17 = Finished?
    18 = Finished
    19 = Error
    20 = Stopped

## scan_wl_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## scan_wl_monochromator_settings.ctl

- Monochromator Settings: Cluster
  - Port: I32
  - Baudrate: U16
  - Timeout (ms): I32

## scan_wl_session.ctl

- scan_wl session: Cluster
  - Z Session: Cluster
    - Axis: I32
    - Identification: String
    - Serial Number: String
    - Axes: 1D array
      - Conn. Axes: String
  - Mono. Session: Cluster
    - COM Port Out: Refnum (kind 14: Instr)
    - Serial Number: String
  - WL Pos: DBL
  - Z Pos: DBL
  - Timestamp: U32
  - Counter: U32
  - Awant Index: I32
  - Ahigh Exposure: DBL
  - AF t0: U32
  - AF Data: 2D array
    - Numeric: DBL
  - Image: Refnum (kind 29: IMAQImage)
  - AF Error: Boolean
  - Awant Exposure: DBL

## scan_wl_ui_event.ctl

- UI Event: Cluster
  - Event: enum U16
      0 = Image Display
      1 = AF Measured
      2 = AF Finished
      3 = State Changed
      4 = Expo Changed
      5 = WL Changed
  - Param: Variant

## scan_wl_ui_event_type.ctl

- Event: enum U16
    0 = Image Display
    1 = AF Measured
    2 = AF Finished
    3 = State Changed
    4 = Expo Changed
    5 = WL Changed

## scan_wl_ui_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Generate Event
    3 = Get Event

## scan_wl_zstage_session.ctl

- Z Session: Cluster
  - Axis: I32
  - Identification: String
  - Serial Number: String
  - Axes: 1D array
    - Conn. Axes: String

## scan_wl_zstage_settings.ctl

- Z Stage Settings: Cluster
  - Interface: U16
  - Port: I32
  - Baudrate: U16
  - S/N (USB): String
  - Speed (mm/s): DBL

## scan_wl_autofocus_data.ctl

- AF Data: Cluster
  - AF Data: 2D array
    - Numeric: DBL
  - Time: DBL
  - Progress%: DBL

## scan_wl_autofocus_focus_data.ctl

- AF Focus Data: Cluster
  - Best Z: DBL
  - Best Score: DBL
  - Graph Data: 1D array
    - Graph Data: Cluster
      - X: DBL
      - Y: DBL
      - Y Polynom: DBL
  - Valid: Boolean
  - Multi Graph Data: 1D array
    - (no label): Cluster
      - AF Data Out: 1D array
        - Numeric: DBL
      - Diff = Y - Fit: 1D array
        - Numeric: DBL

## scan_wl_function_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Start
    3 = Stop
    4 = Get Status

## scan_wl_function_profile.ctl

- scan_wl profile: Cluster
  - Z1: DBL
  - Z2: DBL
  - duration: DBL
  - Client Tag: String
  - Sample Tag: String
  - Z Stage Settings: Cluster
    - Interface: U16
    - Port: I32
    - Baudrate: U16
    - S/N (USB): String
    - Speed (mm/s): DBL
  - Output Folder: Path

## scan_wl_function_state.ctl

- State: enum U16
    0 = Start
    1 = Set WL
    2 = Wait WL
    3 = Set High Expo
    4 = Wait High Expo
    5 = AF Move Zmin
    6 = AF Wait Zmin
    7 = AF Move Zmax
    8 = AF Wait
    9 = AF Measure
    10 = AF Finished
    11 = AF Move Focus
    12 = AF Wait Focus
    13 = AF Error
    14 = Set Wanted Expo
    15 = Wait Wanted Expo
    16 = Acquire
    17 = Finished?
    18 = Finished
    19 = Error
    20 = Stopped

## scan_wl_function_status.ctl

- Status: enum U16
    0 = Off
    1 = Off Cond OK
    2 = Running
    3 = Finished
    4 = Failed

## scan_wl_monochromator_settings.ctl

- Monochromator Settings: Cluster
  - Port: I32
  - Baudrate: U16
  - Timeout (ms): I32

## scan_wl_session.ctl

- scan_wl session: Cluster
  - Z Session: Cluster
    - Axis: I32
    - Identification: String
    - Serial Number: String
    - Axes: 1D array
      - Conn. Axes: String
  - Mono. Session: Cluster
    - COM Port Out: Refnum (kind 14: Instr)
    - Serial Number: String
  - WL Pos: DBL
  - Z Pos: DBL
  - Timestamp: U32
  - Counter: U32
  - Awant Index: I32
  - Ahigh Exposure: DBL
  - AF t0: U32
  - AF Data: 2D array
    - Numeric: DBL
  - Image: Refnum (kind 29: IMAQImage)
  - AF Error: Boolean
  - Awant Exposure: DBL

## scan_wl_ui_event.ctl

- UI Event: Cluster
  - Event: enum U16
      0 = Image Display
      1 = AF Measured
      2 = AF Finished
      3 = State Changed
      4 = Expo Changed
      5 = WL Changed
  - Param: Variant

## scan_wl_ui_event_type.ctl

- Event: enum U16
    0 = Image Display
    1 = AF Measured
    2 = AF Finished
    3 = State Changed
    4 = Expo Changed
    5 = WL Changed

## scan_wl_ui_method.ctl

- Method: enum U16
    0 = Init
    1 = Exit
    2 = Generate Event
    3 = Get Event

## scan_wl_zstage_session.ctl

- Z Session: Cluster
  - Axis: I32
  - Identification: String
  - Serial Number: String
  - Axes: 1D array
    - Conn. Axes: String

## scan_wl_zstage_settings.ctl

- Z Stage Settings: Cluster
  - Interface: U16
  - Port: I32
  - Baudrate: U16
  - S/N (USB): String
  - Speed (mm/s): DBL

## alox_simulator_typedef_images_manager_method.ctl

- Method: enum U16
    0 = Create
    1 = Dispose
    2 = Get Image
    3 = Set Output Type

## alox_simulator_typedef_images_method.ctl

- Method: enum U16
    0 = Create
    1 = Dispose
    2 = Get Image
    3 = Set Output Type

## alox_simulator_typedef_output_image.ctl

- Output Image: enum U16
    0 = Clock
    1 = Vcsel

## aloxsim_output_image_typedef.ctl

- Output Image: enum U16
    0 = Clock
    1 = Vcsel

## Wait for Boolean Notifier Data.ctl

- Cluster: Cluster
  - Value: Boolean
  - error: Cluster
    - status: Boolean
    - code: I32
    - source: String

## Wait for DBL 2D Array Notifier Data.ctl

- Cluster: Cluster
  - Value: 2D array
    - Numeric: DBL
  - error: Cluster
    - status: Boolean
    - code: I32
    - source: String

## Wait for DBL Notifier Data.ctl

- Cluster: Cluster
  - Value: DBL
  - error in (no error): Cluster
    - status: Boolean
    - code: I32
    - source: String

## Wait for Image Notifier Data.ctl

- Cluster: Cluster
  - Flattened image: String
  - error in (no error): Cluster
    - status: Boolean
    - code: I32
    - source: String

## Wait for STR Notifier Data.ctl

- Cluster: Cluster
  - Value: String
  - error in (no error): Cluster
    - status: Boolean
    - code: I32
    - source: String

## Wait for U32 Notifier Data.ctl

- Cluster: Cluster
  - Value: U32
  - error: Cluster
    - status: Boolean
    - code: I32
    - source: String

