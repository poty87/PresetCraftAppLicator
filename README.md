<p align="center"><img src="assets/icon.png" width="96" alt=""></p>

<h1 align="center">PresetCraftAppLicator</h1>

<p align="center">
Give your <b>LightCraft</b> photos the camera profile that matches the style you shot them with.<br>
<a href="#-italiano">🇮🇹 Italiano</a> · <a href="https://github.com/poty87/PresetCraftAppLicator/releases/latest">⬇ Download</a>
</p>

> PresetCraftAppLicator is an independent tool. It is not made by, affiliated with or endorsed by the authors of
> [LightCraft](https://github.com/storytold/lightcraft), Ricoh, Pentax or Adobe.

## Why it exists

Many cameras let you pick a picture style when you shoot. Ricoh GR cameras call it *Image Control*, with styles such as
Positive Film, Negative Film or Hard Monotone. The style is applied to the JPEG and to the preview embedded in the raw file,
**but not to the raw data**.

That is why, when you open a raw photo in [LightCraft](https://github.com/storytold/lightcraft), the colours first look
right (LightCraft shows the camera's embedded preview), and a moment later they change: LightCraft then develops the raw file
with its own default profile, which knows nothing about the style you chose.

Adobe publishes **Camera Matching** profiles (`.dcp`) that reproduce each camera's styles, and they are installed together
with Lightroom or Camera Raw. LightCraft can use `.dcp` profiles, but it cannot tell which style each photo was shot with.

PresetCraftAppLicator does that part:

1. it reads, from every raw file, the style chosen in the camera (it is stored in the manufacturer's notes inside the file);
2. it finds the matching Camera Matching profile (`Camera Positive Film` for Positive Film, and so on);
3. it imports the profiles into the LightCraft library and gives each photo its own one.

Only the profile changes: exposure, white balance and every other adjustment stay as they are.

## How to use it

1. Import your photos into LightCraft as usual, then **close LightCraft**.
2. Open PresetCraftAppLicator and choose the library.
3. Choose which photos to check:
   - **Recently added**: the same photos as LightCraft's *Recently Added* (the 30 days up to the last import). This is the
     usual choice after an import.
   - **Whole library**: every photo.
4. The list shows, for each photo:
   - the style it was shot with;
   - its current profile;
   - the camera profile it will get.

   Click a row to see the camera's own preview, with the colours of the style chosen when shooting.
5. Click **Apply profiles** and confirm. A copy of the catalog is saved first. Then open LightCraft.

| Colour | Meaning |
|---|---|
| blue | the photo will get its camera profile |
| green | it already has the right profile |
| yellow | it is left as it is (right-click › *Leave as is*, or a profile you chose by hand in LightCraft) |
| no colour | no profile for its style, the style can't be read, or the file is missing |

Photos where you already chose a different profile in LightCraft are **not touched** unless you right-click them and choose
*Apply the profile*.

### Measured profiles (Ricoh GR IV)

For the GR IV, PresetCraftAppLicator also includes **measured** profiles: 3D LUTs made by comparing, photo by photo,
LightCraft's default rendering of a raw file with the preview the camera saved in the same file. The two come from the
same raw, so they match pixel for pixel.

| Style | Used by default |
|---|---|
| Standard, Vivid, Positive Film, Cinema (Yellow), Cinema (Green) | *Measured …*: closer to the camera than Adobe's profiles in LightCraft |
| Hi-Contrast B&W, Bleach Bypass, Retro, Cross Processing | *Measured …*: Adobe makes no profile for them |
| Monotone, Soft Monotone, Hard Monotone | Adobe's *Camera …* profile; the measured one is offered as an alternative |

They reproduce colour and tones only. Grain, vignetting and other local effects of some styles (Hi-Contrast B&W,
Retro…) are not part of a LUT. They were measured with the style settings at their defaults and with LightCraft's
rendering as of version 0.21. `tools/measure_styles.py` measures them again from your own photos.

### Styles without a profile

Styles whose code is not known yet show up as *Unknown style (code)*. Click **Styles and profiles…** to pick the profile
to use for each style, or to leave those photos alone. Your choices are remembered.

## Supported cameras

- **Ricoh** (GR III, GR IIIx, GR IV and others that use the same maker notes), DNG files
- **Pentax**, DNG and PEF files

JPEG photos are not changed, because the style is already part of the image.

### Look profiles (Cinema Yellow, Cinema Green…)

Adobe ships some camera styles as **Look** profiles (`.xmp`) instead of `.dcp`: the *Camera Standard* profile plus a 3D
colour table. The GR IV's *Camera Cinema Yellow* and *Camera Cinema Green* are like that. LightCraft cannot read these
files, but it can use 3D LUTs, so PresetCraftAppLicator turns the colour table into a `.cube` LUT and imports it into a
profile group named after the camera (for example *RICOH GR IV*).

LightCraft applies a LUT to its own rendering of the photo, not on top of Camera Standard, so the result is close to the
camera's but not identical. The LUT keeps the look's colour and leaves the brightness to LightCraft. On the test photos this was
the closest match to the camera's own preview.

The Camera Matching profiles are **not included** (they belong to Adobe). PresetCraftAppLicator uses the ones already on your
PC:
- `C:\ProgramData\Adobe\CameraRaw\CameraProfiles\Camera` and `C:\ProgramData\Adobe\CameraRaw\Settings\Adobe\Profiles\Camera`,
  installed by Camera Raw, Lightroom or the free Adobe DNG Converter;
- the same folders inside the Lightroom Classic and Lightroom installation (`C:\Program Files\Adobe\…\Resources`);
- `%APPDATA%\Adobe\CameraRaw\CameraProfiles` and `%APPDATA%\Adobe\CameraRaw\Settings`;
- an extra folder you choose in **Settings**.

## Same features as CleanCraftNas

PresetCraftAppLicator is a sibling of [CleanCraftNas](https://github.com/poty87/CleanCraftNas) and shares its basics:

- several libraries in a drop-down menu;
- six languages: Italiano, English, Deutsch, Español, Français, Português;
- sortable, reorderable columns that you can show or hide;
- a backup of the catalog before every change, with a **Backups…** window to open or delete them;
- a log of every change;
- a silent check for updates at start, with a one-click update;
- a setup wizard with the same dark look.

If your photos live on a NAS, mount the share as a drive letter as explained in the
[CleanCraftNas guide](https://github.com/poty87/CleanCraftNas#nas-setup).

## Install

Download `PresetCraftAppLicator-Setup-x.y.z.exe` from the [latest release](https://github.com/poty87/PresetCraftAppLicator/releases/latest)
and run it. It installs for your user only (no administrator rights) and includes everything it needs, Python included.
An `.msi` is attached too, for silent installs:

```
msiexec /i PresetCraftAppLicator-x.y.z.msi /qn PCA_LANGUAGE=en PCA_DESKTOP=0
```

## Build from source

Windows, Python 3.13:

```
powershell -ExecutionPolicy Bypass -File build.ps1
```

`dist\` then holds the MSI and the setup. To run from source:
`pip install sv_ttk pillow` and `python src\presetcraft.py`.

## License

[MIT](LICENSE).

---

## 🇮🇹 Italiano

**PresetCraftAppLicator** dà alle foto di LightCraft il profilo della fotocamera che riproduce lo stile con cui le hai
scattate.

Le fotocamere applicano lo stile scelto (sulle Ricoh GR: *Image Control*, per esempio Positive Film) alla JPEG e
all'anteprima salvata nel raw, ma non ai dati raw. Per questo in LightCraft, appena apri una foto, i colori sono giusti (è
l'anteprima della fotocamera) e poco dopo cambiano: LightCraft sviluppa il raw con il suo profilo predefinito.

Adobe fornisce i profili **Camera Matching** (`.dcp`) che riproducono gli stili di ogni fotocamera, installati insieme a
Lightroom o Camera Raw. PresetCraftAppLicator:
1. legge da ogni raw lo stile scelto in fotocamera;
2. trova il profilo corrispondente;
3. lo importa nella libreria LightCraft e lo assegna a ogni foto.

Cambia solo il profilo: esposizione, bilanciamento del bianco e le altre regolazioni restano come sono.

**Come si usa:**
1. Importa le foto in LightCraft e **chiudi LightCraft**.
2. Apri PresetCraftAppLicator e scegli la libreria.
3. Scegli *Aggiunte di recente* (le stesse di LightCraft) oppure *Tutta la libreria*.
4. Controlla l'elenco: blu = riceverà il profilo, verde = già a posto, giallo = resta com'è.
5. Premi **Applica i profili**. Prima viene salvata una copia del catalogo.

Le foto a cui hai già scelto un altro profilo in LightCraft non vengono toccate, a meno che tu non lo chieda con il tasto
destro.

Per la GR IV ci sono anche profili **misurati**: LUT ricavate confrontando la resa di LightCraft con l'anteprima salvata
dalla fotocamera nello stesso raw. Sono usati per Standard, Vivid, Positive Film, Cinema (Yellow) e (Green), dove sono più
fedeli dei profili Adobe in LightCraft, e per Hi-Contrast B&W, Bleach Bypass, Retro e Cross Processing, che Adobe non fa.
Per i tre Monotone resta il profilo Adobe. Riproducono colore e toni, non grana e vignettatura.

Alcuni stili Adobe li fornisce come profili **Look** (`.xmp`), per esempio *Cinema Yellow* e *Cinema Green* della GR IV:
PresetCraftAppLicator li trasforma in LUT `.cube` e li importa in LightCraft in un gruppo col nome della fotocamera. Il
risultato è vicino a quello della fotocamera ma non identico, perché LightCraft applica la LUT alla sua resa della foto.

Per gli stili senza un profilo (per esempio quelli di cui non si conosce ancora il codice, mostrati come *Stile
sconosciuto*) usa **Stili e profili…** e scegli tu il profilo da usare.

**Fotocamere supportate:** Ricoh (GR III, GR IIIx, GR IV…) in DNG e Pentax in DNG e PEF. I profili Adobe non sono inclusi:
vengono usati quelli già presenti sul PC, o una cartella scelta nelle Impostazioni.
