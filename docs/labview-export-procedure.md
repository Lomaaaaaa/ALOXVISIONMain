# Exporting the Vision Core LabVIEW code to HTML

Follow these steps to export the documentation of every ALOX VI (front panel and block diagram, **with all the cases of every Case
structure**) so it can be analysed for the migration. Do them on a PC with LabVIEW, the PC at the fab or a copy of the code.

## 0. Prepare (once)

1. **Work on a copy.** Copy `D:\ALOX\Vision Core` to a new folder, for example `D:\ALOX_export\Vision Core`. Never let LabVIEW save
   anything in the original code.
2. **Make the inventory.** Open a Command Prompt and run:

   ```bat
   dir /S /B "D:\ALOX\Vision Core\sources\*.vi" "D:\ALOX\Vision Core\sources\*.ctl" > vision_core_files.txt
   ```

   Keep `vision_core_files.txt`; it is sent with the export (step 5).
3. **Create an empty output folder**, for example `D:\ALOX_export\html`.

## 1. Fix the export tool (once)

The "Project Documentation Tool" from the NI community stops with **error 1012 "Cannot load block diagram"** when it reaches a
password-protected VI (for example PI's `Mercury_GCS_Configuration_Setup.vi`). Fix it once:

1. Open the tool's `recursive Documentation.vi` (File » Open, in the tool's folder). The window title must **not** say "(clone)".
2. Press **Ctrl+E** (block diagram), then **Ctrl+F** and search for **Get VI Bookmarks**.
3. Right-click the **error out** wire coming out of that node, then **Insert » Dialog & User Interface » Clear Errors**.
4. **Save** the tool (only the tool).

If error 1012 appears again on another node, do the same on that node's error wire.

## 2. Run the export

1. Open `generate project documentation.vi` (the tool's main VI).
2. Select the project (`.lvproj`) of the copy made in step 0 and the output folder `D:\ALOX_export\html`.
3. Run it (white arrow ▶). It can take a long time for about 1,000 VIs.
4. If an error dialog still appears, click **Continue**.

## 3. Check the result on one VI (2 minutes)

Open the output folder and open the HTML page of a VI that contains a Case structure (for example `alox_camera_server.vi`).
It must show **one block diagram image per case**, not only the visible case.

- **All cases are there:** go to step 5.
- **Only the visible case:** the tool does not export hidden frames. Use step 4 instead.

## 4. Fallback without the tool: the Print dialog

Do this one folder at a time (`core`, `extcamera`, `vision`, `tools`, …):

1. **File » New VI** (a blank VI; it is never saved).
2. In Windows Explorer, select all the `.vi` files of the folder and **drag them onto the blank VI's block diagram**. LabVIEW loads them.
3. **File » Print…**, then select the VIs of that folder in the list (Shift+click).
4. **VI documentation**, then **Custom**, and tick: **Connector pane**, **Description**, **Front panel**, **Controls descriptions
   and data types**, **Block diagram**, **Hidden frames**, **List of subVIs**.
5. Destination **HTML file**, images **PNG**, 24-bit, into an empty folder named after the folder (for example
   `D:\ALOX_export\html_core`).
6. Close the blank VI **without saving**.

## 5. Send

1. Zip the output folder(s). One zip per folder if they are large.
2. Send the zip(s) and `vision_core_files.txt`.

Do **not** export NI's `vi.lib`, `user.lib`, `instr.lib`, the PI driver or OpenG: they are not needed.
