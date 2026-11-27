# PresetCraftAppLicator manual

<a href="manuale.md">🇮🇹 Italiano</a>

This manual describes every window and option of the program. To set up the suite from scratch (NAS, PC, phone, VPN)
and for the everyday workflow, start from the **[suite guide](suite-guide.md)**.

## Contents

1. [The main window](#1-the-main-window)
2. [Camera profiles (Ricoh GR)](#2-camera-profiles-ricoh-gr)
3. [Automatic import](#3-automatic-import)
4. [AI engine](#4-ai-engine)
5. [Settings](#5-settings)
6. [Backups and log](#6-backups-and-log)
7. [The icon next to the clock](#7-the-icon-next-to-the-clock)
8. [Setup guide, updates and uninstall](#8-setup-guide-updates-and-uninstall)
9. [What it writes and where](#9-what-it-writes-and-where)

---

## 1. The main window

**Top right**

| Button | What it does |
|---|---|
| **Automatic import…** | Watched folders, the phone, faces and DNG ([chapter 3](#3-automatic-import)). The ● dot in front of the name means it is on. |
| **AI engine…** | Search by subject on the phone ([chapter 4](#4-ai-engine)). |
| **Settings** | Language, LightCraft, profiles, backups, updates ([chapter 5](#5-settings)). |
| **☰** | *Open log* and *Backups…* ([chapter 6](#6-backups-and-log)). |

**Library panel**

- The menu with the LightCraft libraries it knows; below, the chosen library's path and its AI search status (*N photos
  cataloged · M described*, or the **Analyze for AI search** button if it hasn't been analysed yet).
- **Add library…** (the folder with the `catalog.snap` file), **Remove from list** (only forgets it, deletes nothing),
  **Refresh** (reads the library again).
- **Photos to check:** *Whole library*, *Last import* (with date, time and number of photos), *From the … import onwards*
  (the last 5 imports). **Advanced filters…** opens the history of all imports: choose a date range from the calendar
  (days with imports are blue) and, if you like, times, or select only some imports; *Check these photos*.

**The middle**

- A message says what will happen (for example *2 photos will get their camera profile*).
- Four panels with the numbers: *Photos checked*, *To update*, *Already right*, *No profile*.
- The list of photos. Colours: **blue** will get the profile, **green** already right, **yellow** left as is, **no
  colour** no profile. The columns (the **Columns** button, or right-click the header) are: *File*, *Folder*, *Camera*,
  *Taken*, *Imported*, *In-camera style*, *Current profile*, *Camera profile*, *Status*. Click a header to sort, drag it
  to move it. The divider between list and preview can be moved: when the columns don't fit the scroll bar appears.
- On the right the **preview** of the selected photo (the one the camera saved in the raw), with date, camera, style and
  the profile it will get.

**At the bottom**

- Version, **☕ Buy me a coffee**, and the *⬆ Version x available* link when there is a new one.
- **Styles and profiles…** and **Apply profiles**.
- Status lines: AI engine progress (with *Details* and *Pause*), last import, automatic import status and next check.

---

## 2. Camera profiles (Ricoh GR)

Cameras apply the chosen style (on Ricoh GR cameras: *Image Control*, for example Positive Film) to the JPEG and to the
preview embedded in the raw, but not to the raw data: that's why in LightCraft the colours change right after opening.
PresetCraftAppLicator reads from each raw the chosen style, finds the matching profile, imports it into the library and
assigns it to the photo. Only the profile changes: exposure, white balance and the other settings stay as they are.

**Applying profiles by hand**

1. Close LightCraft (the program waits if it's open).
2. Choose the library and the photos to check, check the list.
3. Press **Apply profiles**: first a catalog backup, then the profiles.

With automatic import on, new photos get their profile by themselves.

**Right-click one or more photos**

| Item | Effect |
|---|---|
| *Apply the profile* / *Leave as is* | Changes the choice for those photos (the choice is remembered). |
| *Choose the profile for this photo…* (or *for these N photos…*) | A different profile only for those photos; the list marks it as for this photo only. |
| *Change the profile of every photo with this style…* | Changes the style's rule for that camera: it applies to every photo with that style, also future ones. Asks for confirmation. |
| *Show in File Explorer* | Opens the file's folder. |

Photos you already gave another profile in LightCraft are not touched unless you ask.

**Styles and profiles…** lists the styles found in the checked photos and, for each, the profile to use: *Automatic* (the
one the program found), a profile of your choice or *Don't change*. It is also for styles without a profile (*Unknown
style*).

**Which profiles it uses:** Adobe *Camera Matching* profiles (`.dcp`) already on the PC (installed with Lightroom or
Camera Raw) or in a folder chosen in Settings; for the GR IV also included **measured** profiles (Standard, Vivid,
Positive Film, Negative Film, Cinema Yellow and Green, Hi-Contrast B&W, Bleach Bypass, Retro, Cross Processing), more
faithful or not made by Adobe. Some Adobe styles come as *Look* profiles (`.xmp`): they are turned into `.cube` LUTs and
imported into a group named after the camera. HDR Tone is not covered yet.

**Cameras:** Ricoh GR III, GR IIIx, GR IV in DNG (tested with the GR IV). The code also reads the style of Pentax files
(DNG and PEF), untested. Photos of other cameras keep LightCraft's default profile.

---

## 3. Automatic import

At the top the **On** switch (*check the folders in the background, also after the PC restarts*) with the status: last
check and last import. Below, three tabs.

### Import tab

- **Watched folders:** the folder → library list. **Add folder…** with *Import into library* and *Include subfolders*
  (for photos organised by year, month or day; to change it remove and re-add the folder). If the folder already holds
  photos not in the library, it asks whether to import or ignore them. **Remove** stops watching it.
- **When to import:** *Check every* (minutes, 2 by default) and *Transfer finished after* (seconds without changes, 60):
  a transfer's photos are imported all together, with a single backup, once nothing else arrives.

Photos stay where they are: they are not copied or moved. If LightCraft is open it waits. A photo removed from the
library is not imported again. NAS service folders (`#recycle`, `@eaDir`, `@Recycle`…) are ignored.

### Phone tab

- **Service folder on the NAS:** *Choose or create…* a new, empty folder (for example `Y:\ViewerCraftStar`). In the
  ViewerCraftStar app set the same folder in *Settings › Service folder*.
- **Libraries on the phone:** the libraries to see in the app's Library section.
- **Search by subject (AI):** the status and the *AI engine…* button.

For each ticked library the program writes into the service folder the index, developed previews and smart previews for
the phone's editor, refreshes them when the library changes (with LightCraft closed), and applies the changes made on the
phone: stars, flags, labels, texts, stacks, trash, develop settings and masks (after a catalog backup).

### Faces and DNG tab

- **Faces — Name faces by itself when the similarity is at least N %:** with LightCraft's face recognition, only for
  people you have already named on at least 5 faces, and only if no other person looks almost as similar. On the phone
  these names have a dashed border: a tap to confirm or change them. **Undo automatic names** removes those never
  confirmed.
- **Raw to DNG — Convert proprietary raws to DNG before importing them** (CR2, NEF, ORF, ARW, RAF…), with Adobe DNG
  Converter (free; *Install Adobe DNG Converter* if it is missing). The DNG is written next to the raw, with the same name,
  and it is what enters LightCraft; the original stays in the folder (remove it with CleanCraftNas, *Raws already
  converted to DNG*). **Also convert those already in the libraries (N):** in the background, with LightCraft closed and
  after a backup, the DNG takes the raw's place with stars, flag, label, texts, keywords, develop settings, stack and
  albums; the raw goes to LightCraft's trash.

---

## 4. AI engine

The AI engine catalogs the photos of the libraries you choose, so ViewerCraftStar can find them by what is in them
(*cats*, *vintage car*). It works in the background at low priority, only on new photos.

**Installation** (from the setup guide or this window): choose the **model** (recommended by graphics card: *giant* with
a 16 GB NVIDIA card, *huge* with 8 GB, *small* without a suitable card), the **folder** (10–22 GB) and the **description
language**. Engine (a Python environment with PyTorch) and model download in the background.

**Search on the phone panel**

- *Catalog photos for search by subject* — on/off.
- *Model for the phone* — 8 bit (same results as the original) or 4 bit (smaller).
- *Write photo descriptions* — with a 12 GB GPU or more: one or two sentences and keywords per photo, with Qwen3-VL;
  search on the phone gets more precise in the details. About half a second per photo on a 24 GB GPU (2–3 hours per
  18,000 photos the first time). If there is a newer description model for your card, it offers it.
- *Description language* — Italian or English: the one you search in on the phone. Existing descriptions are kept;
  changing it, after a confirmation with the estimated time, rewrites them little by little.

**When to work panel**

- *Stop when another program uses the graphics card (games, renders) and resume later.*
- *Large analyses only from … to …* — a time window for first analyses (over 1,000 photos); new photos from an import
  are analysed right away anyway.

**Libraries for AI search panel:** the queue, with the order (↑ ↓) and ✕ to remove one. A library's **first analysis**
starts from the main window (*Analyze for AI search*, with number of photos and estimated time).

**Buttons:** *Pause/Resume*, *Change model or folder…* (the engine is moved, not downloaded again), *Remove the engine*,
*Log*.

The engine only reads previews: photos and catalogs are never changed. It stops (saving its work) when LightCraft is open
or the graphics card's memory is about to run out, and resumes by itself; if Windows restarts the graphics driver it
resumes where it was. The MetaCLIP 2 search model is licensed CC BY-NC 4.0: **non-commercial use only**.

---

## 5. Settings

| Panel | Options |
|---|---|
| **Language** | Automatic (Windows language), Italian, English, German, Spanish, French, Portuguese. |
| **Path to lightcraft-cli.exe** | Found by itself in LightCraft's installation folder; *Browse…* to set it. |
| **Additional profiles folder** | Optional: where to look for more `.dcp`/`.xmp` profiles besides Lightroom's and Camera Raw's. |
| **Backups** | *Next to the library* (default) or *In this folder* (for example the service folder), and how many backups to keep per library (5). |
| **Updates** | *Check for updates at startup* and *Check now*. |

At the bottom: version, *Buy me a coffee*, **Setup guide…**.

---

## 6. Backups and log

- Before every change to a library (profiles, imports, changes from the phone, conversions) the program saves a copy of
  the catalog in `PresetCraftAppLicator backup - <library>`. The last N are kept (Settings).
- **☰ › Backups…** lists them with date and size: *Open folder*, *Delete selected*, *Delete all*, *Delete backups older
  than N days* (1 = keep only today's). They go to the Windows Recycle Bin (or the NAS recycle bin).
- To go back to a backup: close LightCraft and copy the backup folder's content over the catalog's files.
- **☰ › Open log** opens `presetcraft.log` with everything the program did.

---

## 7. The icon next to the clock

While automatic import is on there is an icon in the notification area. The dot shows the status: **green** on and
waiting, **blue** photos arriving or work in progress, **yellow** waiting for LightCraft to close, **red** error.
**Double-click** opens the program; **right-click** shows what it is doing (import, conversions, AI engine, last import)
and the commands *Check now*, *Open log*, *Quit*. A Windows notification arrives when the work is done.

---

## 8. Setup guide, updates and uninstall

- **Setup guide:** at the first start and from *Settings › Setup guide…*. The steps are described in the
  [suite guide](suite-guide.md#52-the-setup-guide); with *I already have a service folder* it restores the settings saved
  there (new or reinstalled PC).
- **Updates:** the program checks by itself and shows the link next to the version; one click downloads, verifies and
  installs the new version (settings are kept; the background process stops and restarts). Silent install:
  `PresetCraftAppLicator-Setup-x.y.z.exe --silent` or `msiexec /i PresetCraftAppLicator-x.y.z.msi /qn`.
- **Uninstall:** from *Windows Settings › Apps*. It removes program, shortcuts and automatic import and, if you like,
  settings and log; it asks whether to remove the AI engine and its files in the service folder too. Photos, libraries
  and backups are never touched.

---

## 9. What it writes and where

| Where | What |
|---|---|
| LightCraft libraries | Profiles, imports and changes, always through `lightcraft-cli` and after a backup. |
| Photo folders | Only converted DNGs (if on) and the hidden `.viewercraftstar` folder (presets for the phone, status for the houses, the PC's heartbeat). Photos are never changed. |
| Service folder | `libraries\<library>\` (index, previews, smart previews, AI search, changes from the phone), `settings\` (copy of the settings), `backups\` (if chosen). |
| `%LOCALAPPDATA%\PresetCraftAppLicator` | Settings, log, automatic import state; the AI engine in the folder you chose. |
