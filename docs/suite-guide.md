# Suite guide: LightCraft with your photos on a NAS

<a href="guida-suite.md">🇮🇹 Italiano</a>

This guide explains from scratch how to set up the three programs of the suite and how to use them every day:

- **[PresetCraftAppLicator](https://github.com/poty87/PresetCraftAppLicator)** (Windows PC) — the heart of the system: it
  imports into LightCraft the photos that reach the NAS, puts your libraries on the phone, applies the changes made on the
  phone and, for Ricoh GR cameras, gives photos the profile of the style they were shot with;
- **[ViewerCraftStar](https://github.com/poty87/ViewerCraftStar)** (Android) — your LightCraft library on the phone:
  browse, search (also by subject, with AI), rate, flag, develop and, if you like, import new photos and send them home;
- **[CleanCraftNas](https://github.com/poty87/CleanCraftNas)** (Windows PC) — keeps the NAS clean: it deletes from disk
  the photos you put in LightCraft's trash and the raws already converted to DNG.

Each program also has its own manual, with every screen and option: [PresetCraftAppLicator](manual.md),
[ViewerCraftStar](https://github.com/poty87/ViewerCraftStar/blob/main/docs/manual.md), [CleanCraftNas](https://github.com/poty87/CleanCraftNas/blob/main/docs/manual.md).

Each program also works on its own. The examples use made-up names: the NAS is called `NAS-HOME`, the photos' shared
folder `Photos`, the network drive `Y:`. Use your own.

## Contents

1. [What you need](#1-what-you-need)
2. [How it is organised: three kinds of folders](#2-how-it-is-organised-three-kinds-of-folders)
3. [Preparing the NAS](#3-preparing-the-nas)
4. [Preparing the PC](#4-preparing-the-pc)
5. [Installing PresetCraftAppLicator](#5-installing-presetcraftapplicator)
6. [Installing ViewerCraftStar on the phone](#6-installing-viewercraftstar-on-the-phone)
7. [Installing CleanCraftNas](#7-installing-cleancraftnas)
8. [Everyday use](#8-everyday-use)
9. [Away from home: the VPN](#9-away-from-home-the-vpn)
10. [Updates, backups and a new PC](#10-updates-backups-and-a-new-pc)
11. [Common problems](#11-common-problems)

---

## 1. What you need

**Required**

| What | Notes |
|---|---|
| A PC with Windows 10 or 11 | Where LightCraft runs. The programs install for your user, without administrator rights. |
| [LightCraft](https://github.com/storytold/lightcraft) | With your libraries. PresetCraftAppLicator uses its `lightcraft-cli.exe`, installed with LightCraft. |
| A NAS (or a server) with an SMB shared folder | Any brand: Synology, QNAP, TrueNAS, Unraid, even a Windows shared folder. |

**For the phone** (optional)

| What | Notes |
|---|---|
| An Android 13 or later phone | For ViewerCraftStar. |
| Your NAS's sync app | Only to *send* photos from the phone: an app that uploads a phone folder to the NAS (Synology Drive, QNAP Qfile, Nextcloud, Syncthing…). Browsing and editing the library don't need it. |
| [OpenVPN for Android](https://play.google.com/store/apps/details?id=de.blinkt.openvpn) and an OpenVPN server | Only to use the phone **away from home** (see [chapter 9](#9-away-from-home-the-vpn)). |

**Optional on the PC**

| What | What for |
|---|---|
| An NVIDIA graphics card (8–24 GB) | The **AI engine** for search by subject on the phone. It also works without, with a smaller and slower model. With 12 GB or more it can also write **descriptions** of the photos. |
| Adobe DNG Converter (free) | Converting proprietary raws (CR2, NEF, ORF, ARW, RAF…) to DNG. PresetCraftAppLicator installs it if you turn the option on. |
| Adobe *Camera Matching* profiles | Only for the **Ricoh GR** profiles: the ones installed with Lightroom or Camera Raw. Measured profiles are included for the GR IV. |

**Cameras.** The suite works with the photos of every camera LightCraft opens. Only the picture-style profiles and the
direct Wi-Fi import from the camera are specific to Ricoh GR cameras; photos of other brands keep LightCraft's default
profile and everything else works the same.

---

## 2. How it is organised: three kinds of folders

Before installing, it helps to know what goes where. There are **three kinds of folders**, to keep separate:

| Folder | Example | What it holds | Who uses it |
|---|---|---|---|
| **Photos** | `Y:\Ricoh`, `Y:\Canon` | The photo files (raw, JPEG). They always stay there: LightCraft reads them from that folder. | LightCraft, PresetCraftAppLicator (watches it), the phone (writes `.xmp` files there), CleanCraftNas (deletes) |
| **LightCraft libraries** | `D:\Catalogs\Ricoh` | LightCraft's catalog (the folder with the `catalog.snap` file). Best on a PC disk. | LightCraft, PresetCraftAppLicator, CleanCraftNas |
| **Service folder** | `Y:\ViewerCraftStar` | Everything PresetCraftAppLicator produces: library index and previews for the phone, AI search, changes made on the phone, a copy of the settings, backups. **New and empty**, not inside the photos or the catalogs. | PresetCraftAppLicator, ViewerCraftStar |

The data flow, in short:

```
  Phone (ViewerCraftStar)                    NAS                          PC
  ───────────────────────────────   ─────────────────────────   ─────────────────────────────
  browse / search / edit ◄─────────── service folder ◄─────────── PresetCraftAppLicator
  changes (stars, develop) ─────────► service folder ───────────► applied to LightCraft

  photos sent from the phone ─(sync app)─► photos folder ──────► imported into LightCraft
  photos copied from the PC ──────────────► photos folder ──────► imported into LightCraft
                                                                LightCraft trash ─► CleanCraftNas
```

---

## 3. Preparing the NAS

Screens differ between brands; the names in brackets are Synology DSM's, as an example.

### 3.1 Shared folder and user

1. Create (or use) a **shared folder** for the photos, for example `Photos` (*Control Panel › Shared Folder*). Inside,
   organise the photos as you like, for example one subfolder per camera.
2. Create a **user** for the programs (or use yours) with **read and write** access to that shared folder. Writing is
   needed: the phone writes `.xmp` files there, PresetCraftAppLicator the service folder and converted DNGs,
   CleanCraftNas deletes.
3. Check that the **SMB** service is on (*Control Panel › File Services › SMB*).

### 3.2 NAS name and fixed address

- **NAS name:** only letters, digits and hyphens, at most 15 characters (for example `NAS-HOME`). **No underscore**
  (`_`), spaces or accents: some programs can't find the files on a NAS named `MY_NAS`.
- **Fixed address:** give the NAS always the same address on your home network (a DHCP reservation on the router, for
  example `192.168.1.10`). The phone app and the VPN need it.

### 3.3 NAS recycle bin

Turn on the **recycle bin** of the shared folder (Synology: *Shared Folder › Edit › Enable Recycle Bin*, folder
`#recycle`; QNAP: *Network & File Services › Network Recycle Bin*, folder `@Recycle`). Files deleted by CleanCraftNas can
then be recovered for a while. PresetCraftAppLicator never looks inside these folders.

### 3.4 Where photos from the phone arrive (only if you send from the phone)

To send photos from the phone, set up your NAS's sync app on the phone to upload ViewerCraftStar's **folder synced with
the NAS** (by default `Pictures/VCS_Dng_Sync`) **into a photos folder watched by PresetCraftAppLicator** (for example
`Photos\Ricoh`). With Synology Drive: *Photo Backup*, source folder `Pictures/VCS_Dng_Sync`, destination the photos
folder. The upload is done by the sync app with its own rules (for example only on Wi-Fi), also away from home.

---

## 4. Preparing the PC

LightCraft finds each photo by its **path**. With a NAS, how you connect it matters a lot.

### 4.1 A network drive with a fixed letter

Map the shared folder to a fixed letter and **always** use it when adding folders to LightCraft:

```powershell
net use Y: \\NAS-HOME\Photos /persistent:no
```

If one day you use `Y:` and another `\\NAS-HOME\Photos`, the library sees the photos as missing.

### 4.2 The password, once

Save the NAS password in Windows Credential Manager, with the **plain user name** (no `NAS-HOME\` in front):

```powershell
cmdkey /add:NAS-HOME /user:youruser /pass
```

`/pass` without a value asks for the password: it is never written anywhere.

### 4.3 Reconnect the drive at every logon

Windows often tries to restore network drives before the network is ready, then asks for the password again or shows
error 1219 (*"Multiple connections to a server or shared resource by the same user, using more than one user name, are not
allowed"*). CleanCraftNas's [`connect-nas.ps1`](https://github.com/poty87/CleanCraftNas/blob/main/tools/connect-nas.ps1)
avoids both: it waits for the NAS, closes leftover connections and maps the drive with the saved password. Copy it to a
folder of yours (for example `C:\Tools`) and run it at every logon with Task Scheduler:

```powershell
$args = '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Tools\connect-nas.ps1" -Drive Y: -Share \\NAS-HOME\Photos'
$action  = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" -Argument $args
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = 'PT15S'
Register-ScheduledTask -TaskName 'Connect NAS' -Action $action -Trigger $trigger `
    -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive)
```

### 4.4 LightCraft and the libraries

1. Install [LightCraft](https://github.com/storytold/lightcraft) and create (or open) your libraries.
2. Add the photo folders **from the `Y:` drive** (for example `Y:\Ricoh`).
3. Keep the libraries on a PC disk (they are catalogs: they work better locally).

---

## 5. Installing PresetCraftAppLicator

### 5.1 Installation

Download `PresetCraftAppLicator-Setup-x.y.z.exe` from the [latest release](https://github.com/poty87/PresetCraftAppLicator/releases/latest)
and run it. It installs for your user, without administrator rights, with everything it needs (Python included). Choose
the language and, if you like, the desktop shortcut.

### 5.2 The setup guide

At the first start the **setup guide** opens (reopen it any time from *Settings › Setup guide…*):

1. **How do you want to start** — *First setup*, or *I already have a service folder: restore the settings saved there*
   (new or reinstalled PC, see [chapter 10](#10-updates-backups-and-a-new-pc)).
2. **LightCraft and its libraries** — the program finds `lightcraft-cli.exe` by itself; with *Add library…* choose the
   LightCraft library folders.
3. **The service folder** — choose or create a **new, empty** folder on the NAS, for example `Y:\ViewerCraftStar`. It holds
   the index for the phone, previews, AI search, a copy of the settings and (recommended) the backups.
4. **Incoming new photos** — for each library, the photos folder to watch (for example `Y:\Ricoh` for the Ricoh
   library). It is the photos folder, not the service folder.
5. **The phone (ViewerCraftStar)** — the libraries to see in ViewerCraftStar and the *developed previews* (recommended:
   the phone shows photos as they are in LightCraft, edits included).
6. **Search by subject** (optional) — install the AI engine (see below). It can be done later too.
7. **Summary** — check and press *Start*. Everything can be changed later.

### 5.3 The main window

Top right: **Automatic import…**, **AI engine…**, **Settings** and **☰** (log and backups). In the middle the chosen
library, the photos to check and the list with each photo's profile status; at the bottom **Styles and profiles…** and
**Apply profiles**.

- **Profiles (Ricoh GR):** choose which photos to check (*Last import*, *Whole library*, *Advanced filters…*), check the
  list (blue = will get the profile, green = already right, yellow = left as is) and press *Apply profiles*. With
  automatic import on, it does this by itself.
- Right-click a photo: *Choose the profile for this photo…* (only that one) or *Change the profile of every photo with
  this style…* (the rule for that style).
- Before every change a copy of the catalog is saved (backup).

### 5.4 Automatic import

From **Automatic import…** (the *On* switch at the top, with the status), three tabs:

| Tab | Options |
|---|---|
| **Import** | The watched folders and the library of each, *Include subfolders*, how often to check (every 2 minutes by default) and how many seconds to wait for a transfer to be complete (60). |
| **Phone** | The service folder, the libraries to see on the phone, the link to the AI engine. |
| **Faces and DNG** | Automatic face names (with the similarity threshold) and **conversion to DNG** of proprietary raws, also those already in the libraries. |

How it behaves:

- A scheduled task starts it at Windows logon; an **icon next to the clock** shows its status (green waiting, blue
  working, yellow waiting for LightCraft to close, red error). Double-click opens the program.
- A transfer's photos are imported **all together** once nothing has arrived for a minute, with one backup.
- **If LightCraft is open it waits** until you close it. A photo removed from the library is not imported again.
- **Conversion to DNG** (if on): a proprietary raw that has just arrived is converted to a DNG next to it, with the same
  name, and the DNG enters LightCraft; the original stays in the folder (remove it later with CleanCraftNas, *Raws already
  converted to DNG*). For raws already in the libraries: the DNG takes their place with stars, flags, labels, texts,
  develop settings, stacks and albums, and the raw goes to LightCraft's trash.

### 5.5 AI engine (search by subject on the phone)

From the **AI engine…** button:

- **Installation:** choose the model (the recommended one depends on the graphics card: *giant* with a 16 GB NVIDIA card,
  *huge* with 8 GB, *small* without a suitable card), the **folder** (10–22 GB) and the **description language** (Italian
  or English: the one you will search in on the phone). Engine and model download in the background.
- **Search on the phone:** *Catalog photos for search by subject*, the phone model's precision (8 bit, same as the
  original) and, with a 12 GB card or more, *Write photo descriptions* (more precise in the details: colours, objects).
- **When to work:** stop when another program uses the graphics card, and a time window for large analyses.
- **Libraries for AI search:** the queue. You decide a library's **first analysis** from the main window (*Analyze for AI
  search*, with the estimated time); afterwards new photos are analysed by themselves.

The engine only reads previews: photos and catalogs are never changed. If LightCraft is open or the graphics card's
memory is about to run out it stops and resumes later. The search model (MetaCLIP 2) is for **non-commercial use only**.

---

## 6. Installing ViewerCraftStar on the phone

### 6.1 Installation and permissions

1. Download the `.apk` from the [latest release](https://github.com/poty87/ViewerCraftStar/releases/latest) and open it on
   the phone (Android asks to allow installing from the browser or file manager).
2. Open the app and allow access to photos (**Allow all**), notifications and nearby devices (for the Ricoh camera). Then
   turn on **media management** once in the Android page the app opens: sending and moving to the bin no longer ask for
   confirmation every time. *Settings › Permissions* shows them all.

### 6.2 Settings

| Section | What to set |
|---|---|
| **Language** | Same as the phone, Italian or English. |
| **Permissions** | Shows the permissions granted and asks for the missing ones. |
| **Raw folders on the phone** | **New photos folder** (not synced: photos to review arrive here; default `Pictures/VCS_Import_Photo`) and **folder synced with the NAS** (the one your sync app uploads; default `Pictures/VCS_Dng_Sync`). |
| **NAS** | The NAS's **IP address** (for example `192.168.1.10`), **user** and **password**, then **Test connection**. After the test choose the **photos folder on the NAS** (where sent photos arrive) and whether to look for raws **in subfolders too**. The password stays on the phone, encrypted. |
| **Service folder** | The **same service folder** chosen in PresetCraftAppLicator (for example `ViewerCraftStar` in the shared folder): the libraries come from here. |
| **NAS speed** | Tries for a few seconds different ways of reading a large photo from the NAS (one or more connections, one or more reads at once): try it at home and over the VPN. |
| **Developing on the phone** | The develop engine version and the space used by smart previews and originals downloaded for the editor (about 1 GB each at most; beyond that the least used are removed), with *Clear*. |
| **Search by subject (AI)** | Shows how much the downloaded model takes and deletes it. |
| **Sending notifications** | Tell you when sending is finished or paused. |
| **Defaults** | Creator and copyright to put on photos; move to the next photo after a flag or a rating. |
| **Settings backup** | *Save backup* writes a zip in the service folder (settings, photo metadata, pending changes, never the password); *Restore…* brings one back. |

The **VPN** settings open from the **VPN** tab (the shield) in the bottom bar: see [chapter 9](#9-away-from-home-the-vpn).

### 6.3 The Ricoh GR camera (optional)

With the round **Import** button › **Camera** the app connects to a Ricoh GR over Bluetooth and Wi-Fi and downloads its
photos, without the camera maker's app. The first time it shows the pairing steps (on the camera: *Wireless
Communication › Pairing › Execute Pairing*, then OK twice while the QR code is shown).

---

## 7. Installing CleanCraftNas

Download `CleanCraftNas-Setup-x.y.z.exe` from the [latest release](https://github.com/poty87/CleanCraftNas/releases/latest)
and run it (it too installs for your user, without administrator rights). In the setup guide add the LightCraft
libraries. In **Settings**: language, path of `lightcraft-cli.exe` (found by itself), where to keep the **backups** and
how many, updates.

---

## 8. Everyday use

### 8.1 Bringing photos home: two ways

**A — From the PC, as always.** Copy the photos into the watched folder (card reader, the camera's software, any tool).
PresetCraftAppLicator imports them by itself (converting them to DNG if you asked, and giving Ricoh GR photos their
profile). On the phone you see them in the Library like all the others.

**B — From the phone (a plus).**

1. **Import** (round button at the bottom left of *To review*): **Camera** (Ricoh GR) or **External disk** (a USB stick,
   disk or card reader plugged into the phone: the first time choose the folder and press *Allow*, afterwards it opens by
   itself). Choose the photos and import: they go to the new photos folder.
2. **Review** in *To review*: flags (pick / reject), stars, labels, title, caption, keywords and, if you like, a
   LightCraft preset with preview.
3. **Send to the NAS** (blue round button at the bottom right): the photos move to the synced folder, the sync app uploads
   them and ViewerCraftStar writes a `.xmp` with your choices next to each. Rejected photos go to the phone's bin (30
   days).
4. In **Sent** follow each photo: 🔴 not on the NAS yet, 🔵 safe on the NAS (enters LightCraft when the PC is on),
   🟢 already in LightCraft. Blue and green ones can be removed from the phone with one tap.

Formats the phone doesn't recognise as photos (Canon CR3 on Android 17) go through the PC (way A).

### 8.2 The Library on the phone

- **Browse** by date, with stacks; pull the gallery down to refresh it.
- **Search with AI** what is in the photo (*orange cat*, *sunset at the beach*), or **Filters**: text, stars, dates,
  labels, camera, flag, file type.
- **Edit** stars, flags, labels, texts (also many photos at once, with a long press), **group into stacks**, **move to
  LightCraft's trash**, **download** to the phone.
- **Develop** with LightCraft's engine: light, colour, effects, detail, crop and **masks**, including the *Object* AI
  mask (one tap on the subject).

Changes show at once with an orange dot: PresetCraftAppLicator applies them to LightCraft (with LightCraft closed, after
a backup) and the dot disappears when the index confirms them.

### 8.3 Keeping the NAS clean

1. In LightCraft (or from the phone) move the photos you don't want to the **trash**.
2. Close LightCraft and open **CleanCraftNas**: you see the photos in the trash, the files to delete (also the twin JPEG,
   if you like) and the space freed. For each photo choose *delete from disk and trash*, *trash only* or *keep in trash*
   (right click).
3. Press **Delete from disk and empty the trash**: first a backup of the catalog, then the files go to the NAS recycle
   bin.
4. **☰ › Raws already converted to DNG…** removes the original raws that already have their DNG in the library. A raw
   shows only if no photo uses it, the DNG says it was made from that very file and the DNG is lossless.

---

## 9. Away from home: the VPN

At home the phone talks to the NAS on the local network. Away from home the NAS can't be reached from the internet (as it
should be): the phone joins your home network with an **OpenVPN VPN**, built into ViewerCraftStar.

In short, what you need:

1. **A line that can receive connections** (no CGNAT; with fibre you sometimes have to ask your provider for a public IP).
2. **A name for your home** with a DDNS service (for example `home.example.net`), which follows the public address.
3. **On the router:** a fixed address for the NAS and **forwarding of a UDP port** (the guide uses 1195; any free port
   works) to port 1194 of the OpenVPN server on the NAS. With two routers in a row (for example the provider's modem and
   a Wi-Fi router) forward on both.
4. **An OpenVPN server**: on the NAS (Synology: the free *VPN Server* package), on the router or on a PC. Export the
   profile (`.ovpn`, or Synology's zip) and fix it as the guide explains (external address, and the setting that avoids
   writes timing out on mobile data).
5. **On the phone:** install *OpenVPN for Android* (no need to open it), then in ViewerCraftStar open the **VPN** tab in
   the bottom bar, import the profile, enter server, port, user and password and choose how it turns on: **manual**,
   **when the app opens away from home** or **fully automatic** (it also turns off on the home Wi-Fi). The **shield** in
   the bottom bar turns it on and off.

All the steps, with the menu paths: **[remote access guide](https://github.com/poty87/ViewerCraftStar/blob/main/docs/remote-access.md)**.

The NAS's sync app usually doesn't need the VPN: it uploads photos on its own.

---

## 10. Updates, backups and a new PC

- **Updates:** PresetCraftAppLicator and CleanCraftNas check by themselves for a new version and show a link next to the
  version number; one click downloads and installs it (settings are kept). ViewerCraftStar updates by downloading the new
  `.apk` from the releases page.
- **Catalog backups:** before every change PresetCraftAppLicator and CleanCraftNas save a copy of the library's catalog
  (5 per library by default). The **Backups…** windows list and delete them.
- **Settings:** PresetCraftAppLicator keeps a copy of its settings in the service folder (`settings\settings.json`);
  ViewerCraftStar saves its own with *Save backup*.
- **New or reinstalled PC:** reconnect the `Y:` drive (chapter 4), reinstall LightCraft and PresetCraftAppLicator, and in
  the setup guide choose *I already have a service folder: restore the settings saved there*. The AI engine has to be
  reinstalled (the models are on the PC), but the work done is in the service folder and isn't redone.

---

## 11. Common problems

| Problem | What to do |
|---|---|
| The NAS asks for the password again, or error 1219 at Windows start | Use the reconnect script of [chapter 4.3](#43-reconnect-the-drive-at-every-logon) and save the password with `cmdkey`. |
| LightCraft sees photos as missing | The folder was added with a different path (`\\NAS-HOME\…` instead of `Y:\…`) or the letter changed. In LightCraft: *Find Missing Photos* / *Locate Photo*. |
| A program can't find files on the NAS but File Explorer can | The NAS name has invalid characters (underscore, spaces): rename it ([3.2](#32-nas-name-and-fixed-address)). |
| Photos sent from the phone stay red | The sync app hasn't uploaded them yet, or uploads them to a folder other than the watched one ([3.4](#34-where-photos-from-the-phone-arrive-only-if-you-send-from-the-phone)). |
| Photos stay blue | The home PC is off or LightCraft is open: they enter LightCraft when the PC is on and LightCraft closed. |
| Automatic import doesn't start | Look at the icon next to the clock: yellow = close LightCraft; red = open the log (☰ › Open log). |
| The phone says "NAS not reachable" away from home | Turn on the VPN (shield at the bottom) and check the [VPN guide](https://github.com/poty87/ViewerCraftStar/blob/main/docs/remote-access.md). Android keeps one VPN at a time: if another one is on (for example AdGuard), the shield explains it. |
| The screen goes black for a moment while the AI engine works | The graphics driver resets under load: lower the card's power limit (for example to 80% with the graphics card's tool). The engine resumes where it was. |
| A CR3 file can't be imported from the phone | Android doesn't recognise CR3 files as photos: copy it to the NAS from the PC (way A). |
