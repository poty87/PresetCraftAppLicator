<p align="center"><img src="assets/icon.png" width="96" alt=""></p>

<h1 align="center">PresetCraftAppLicator</h1>

<p align="center">
Give your <b>LightCraft</b> photos the camera profile that matches the style you shot them with.<br>
<a href="#-italiano">🇮🇹 Italiano</a> · <a href="https://github.com/poty87/PresetCraftAppLicator/releases/latest">⬇ Download</a>
</p>

> PresetCraftAppLicator is an independent tool. It is not made by, affiliated with or endorsed by the authors of
> [LightCraft](https://github.com/storytold/lightcraft), Ricoh, Pentax or Adobe.

> [!IMPORTANT]
> **At the moment it works only with Ricoh GR cameras** (GR III, GR IIIx, GR IV), DNG files. The measured profiles are
> included for the GR IV only.

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
   - **Last import**: only the photos of the latest import (the usual choice after an import).
   - **From the … import onwards**: one of the recent imports and everything imported after it — handy after two
     imports in a row. The menu lists the last 5 imports with their date, time and number of photos (pieces of the same
     import a few minutes apart count as one).
   - **Whole library**: every photo.
   - **Advanced filters…** (next to the menu): the history of every import, which you can narrow down with a date range
     (pick the days from a calendar, where days with imports are blue) and, optionally, times; then check all the photos
     imported in that range, or only the imports you select.
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

To give a different profile to **some photos only**, select them, right-click and choose *Choose the profile for this
photo…*: the list shows "· this photo only" next to the profile, and nothing else changes — other photos with the same style
and the photos that automatic import brings in keep following the style's rule. *Change the profile of every photo with
this style…* instead changes the rule for that camera style: it applies to every photo with that style, including future
automatic imports, and asks for confirmation first.

## Automatic import

If your photos land in a folder on their own (for example the raw files uploaded from your phone to a NAS), PresetCraftAppLicator
can watch that folder in the background, import the new photos into the LightCraft library and give each one its camera
profile. When you open LightCraft, the photos you just shot are already there, with the right colours.

1. Click **Automatic import…**.
2. Choose the library, click **Add folder…** and pick the folder where new photos arrive. Leave **Include subfolders**
   ticked if your photos go into subfolders (for example by year, month or day): new subfolders are picked up too.
   If the folder already holds photos that are not in the library, you choose whether to import them too or ignore them.
3. Tick **On** and click **Save**.

How it works:
- a scheduled task starts it in the background when you sign in to Windows (no administrator rights);
- when nothing is arriving it checks the folders every few minutes (2 by default);
- **a transfer is imported in one go.** Photos from a phone arrive one at a time, so as soon as new files show up the
  folder is checked every 10 seconds, and the import starts only when nothing has arrived or changed for a while (60
  seconds by default). All the new photos are then imported together, with a single catalog backup;
- photos are added where they are: nothing is copied or moved;
- if LightCraft is open, it waits until you close it (and if you open LightCraft halfway, the rest is finished later);
- the catalog is backed up before every import, and every import is written to the log;
- a photo you remove from the library is not imported again;
- photos that arrive in the folder and that you import yourself also get their profile.

### Ratings and flags from the phone (ViewerCraftStar)

ViewerCraftStar, a companion Android app, writes `.xmp` files next to the DNG files on the NAS with the flag, stars, colour
label, title, caption, keywords, creator and copyright you set on the phone. Automatic import picks them up: new or
changed `.xmp` files written by ViewerCraftStar are applied to the photos already in the library (removing a value
works too), and photos imported later get them as well. `.xmp` files written by LightCraft or other programs are left
alone, and the photo's profile is never touched.

ViewerCraftStar can also pick a **preset** for each photo, from the library's own presets (for example the GR IV styles or
presets imported from Lightroom): PresetCraftAppLicator publishes the list in a hidden `.viewercraftstar` folder inside the
watched folder, and applies the chosen preset in LightCraft (develop settings reset, the camera-style profile, then the
preset). Photos with a preset chosen on the phone are never given the automatic profile again: the app's list shows them
as *Preset chosen on the phone*.

Presets can also be **imported from the phone**: ViewerCraftStar uploads the chosen files (Lightroom `.xmp` presets,
`.lrtemplate`, `.lcpreset`, Luminar looks or `.zip` bundles of these) to `.viewercraftstar/import`, and PresetCraftAppLicator
imports them into the library (group *ViewerCraftStar*) and publishes the updated list.

Finally, PresetCraftAppLicator writes a small report (`.viewercraftstar/status.json`) saying which photos of the watched
folder are in the library and which version of the phone's metadata has been applied (right after an import, without
waiting for the next check), plus a heartbeat (`.viewercraftstar/heartbeat.json`, at most once a minute) so the phone
knows whether the home PC is on. ViewerCraftStar shows a green house for photos already in LightCraft and a blue house
for photos whose DNG and latest metadata are safe on the NAS: both can be moved to the phone's bin, and blue ones enter
LightCraft as soon as the home PC is on — useful when travelling with the PC off.

### Notification area icon

While automatic import is on, an icon sits in the notification area. Its dot shows the state: **green** = on and
waiting for photos, **blue** = photos arriving or being imported, **yellow** = waiting for LightCraft to be closed,
**red** = error. Hover it for a short status.

- **Double-click** opens PresetCraftAppLicator.
- **Right-click** shows what it is doing right now (photos arriving, *Importing: 4 of 12 photos*, time left, last
  import) and the commands *Check now*, *Open log* and *Quit* (it starts again at the next sign-in).
- A Windows notification tells you when the photos have been imported.

The icon uses [pystray](https://github.com/moses-palmer/pystray) (LGPLv3).

## Uninstall

Uninstall from **Settings › Apps › Installed apps** (or run the setup again). PresetCraftAppLicator's own uninstall
wizard opens. It removes the program, its shortcuts and the automatic import (background process, icon and scheduled
task), and can delete your settings and log too. Your photos, your LightCraft libraries and the catalog backups next to
them are never touched.

The cleanup is done by the installer package itself, so it also happens with `msiexec /x` or a silent uninstall
(`PresetCraftAppLicator-Setup-x.y.z.exe --silent --uninstall [--remove-data]`). Updates stop the background process and
start it again once the new version is installed.

### Measured profiles (Ricoh GR IV)

For the GR IV, PresetCraftAppLicator also includes **measured** profiles: 3D LUTs made by comparing, photo by photo,
LightCraft's default rendering of a raw file with the preview the camera saved in the same file. The two come from the
same raw, so they match pixel for pixel.

| Style | Used by default |
|---|---|
| Standard, Vivid, Positive Film, Negative Film, Cinema (Yellow), Cinema (Green) | *Measured …*: closer to the camera than Adobe's profiles in LightCraft |
| Hi-Contrast B&W, Bleach Bypass, Retro, Cross Processing | *Measured …*: Adobe makes no profile for them |
| Monotone, Soft Monotone, Hard Monotone | Adobe's *Camera …* profile; the measured one is offered as an alternative |
| HDR Tone | not covered yet (no reference photos; its local tone mapping is not something a LUT can reproduce) |

They reproduce colour and tones only. Grain, vignetting and other local effects of some styles (Hi-Contrast B&W,
Retro…) are not part of a LUT. They were measured with the style settings at their defaults and with LightCraft's
rendering as of version 0.21. `tools/measure_styles.py` measures them again from your own photos.

### Styles without a profile

Styles whose code is not known yet show up as *Unknown style (code)*. Click **Styles and profiles…** to pick the profile
to use for each style, or to leave those photos alone. Your choices are remembered.

## Supported cameras

**At the moment only Ricoh GR cameras are supported:** GR III, GR IIIx and GR IV, DNG files. It has been tested with the
GR IV, and the measured profiles exist for the GR IV only. The code can also read the style of Pentax files (DNG and PEF),
but that has not been tested.

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
- a backup of the catalog before every change (the last 5 are kept, adjustable in the settings), with a **Backups…**
  window to open or delete them — including *Delete backups older than N days* (1 keeps only today's);
- a log of every change (automatic imports included);
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

> [!IMPORTANT]
> **Al momento funziona solo con le Ricoh GR** (GR III, GR IIIx, GR IV), file DNG. I profili misurati sono inclusi solo per
> la GR IV.

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
3. In *Foto da esaminare* scegli *Ultima importazione*, *Dall'importazione del … in poi* (le ultime 5 importazioni con
   data, ora e numero di foto: comodo dopo due importazioni di fila) oppure *Tutta la libreria*. Con **Filtri
   avanzati…** vedi lo storico di tutte le importazioni, lo restringi con un intervallo di date (scelte da un calendario,
   dove i giorni con importazioni sono in blu) e, se vuoi, di ore, ed esamini le foto dell'intervallo o solo le
   importazioni che selezioni.
4. Controlla l'elenco: blu = riceverà il profilo, verde = già a posto, giallo = resta com'è.
5. Premi **Applica i profili**. Prima viene salvata una copia del catalogo: se ne tengono le ultime 5 (si cambia
   nelle impostazioni) e dalla finestra **Backup…** puoi eliminare quelle selezionate, tutte, o quelle più vecchie di un
   certo numero di giorni (1 = tieni solo quelle di oggi).

Le foto a cui hai già scelto un altro profilo in LightCraft non vengono toccate, a meno che tu non lo chieda con il tasto
destro.

Per dare un profilo diverso **solo ad alcune foto** selezionale e scegli, con il tasto destro, *Scegli il profilo per questa
foto…*: nell'elenco accanto al profilo compare «· solo questa foto» e nient'altro cambia, né le altre foto con lo stesso
stile né quelle portate dall'importazione automatica. *Cambia il profilo di tutte le foto con questo stile…* invece cambia
la regola dello stile per quella fotocamera: vale per tutte le foto con quello stile, anche per le prossime importazioni
automatiche, e prima chiede conferma.

**Importazione automatica.** Se le foto arrivano da sole in una cartella (per esempio i raw caricati dal telefono sul NAS),
PresetCraftAppLicator può osservarla in background, importare le foto nuove nella libreria LightCraft e dare a ciascuna il
profilo del suo stile. Quando apri LightCraft trovi già le foto appena scattate, con i colori giusti.
1. Premi **Importazione automatica…**.
2. Scegli la libreria, premi **Aggiungi cartella…** e indica la cartella in cui arrivano le foto. Lascia spuntato
   **Includi le sottocartelle** se le foto finiscono in sottocartelle (per esempio per anno, mese o giorno): vengono viste
   anche le sottocartelle nuove. Se dentro ci sono già foto che non sono nella libreria, scegli se importarle o ignorarle.
3. Spunta **Attiva** e premi **Salva**.

Un'attività pianificata la avvia all'accesso a Windows, senza diritti di amministratore. Quando non arriva niente controlla
le cartelle ogni pochi minuti (2 di predefinito). **Un trasferimento si importa in una volta sola:** le foto dal telefono
arrivano una alla volta, quindi appena compaiono file nuovi la cartella si controlla ogni 10 secondi e l'importazione
parte solo quando non arriva e non cambia più niente da un po' (60 secondi di predefinito). Allora tutte le foto nuove
vengono importate insieme, con un solo backup del catalogo. Le foto restano dove sono. Se LightCraft è aperto aspetta che
venga chiuso. Una foto tolta dalla libreria non viene reimportata.

**Valutazioni dal telefono (ViewerCraftStar).** L'app Android ViewerCraftStar scrive accanto ai DNG sul NAS dei file
`.xmp` con bandierina, stelle, etichetta colore, titolo, didascalia, parole chiave, autore e copyright decisi sul telefono.
L'importazione automatica li applica alle foto in LightCraft, anche quando un valore viene tolto. Gli `.xmp` di LightCraft
o di altri programmi non vengono toccati, e il profilo della foto resta com'è. Dal telefono si può anche scegliere un
**preset** della libreria (per esempio gli stili GR IV o i preset importati da Lightroom): PresetCraftAppLicator ne pubblica
l'elenco in una cartella nascosta `.viewercraftstar` nella cartella osservata e lo applica in LightCraft (sviluppo
azzerato, profilo dello stile di scatto, poi il preset). Le foto con un preset scelto sul telefono non ricevono più il
profilo automatico. Dal telefono si possono anche **importare preset da file** (`.xmp` di Lightroom, `.lrtemplate`,
`.lcpreset`, look di Luminar o `.zip` che li contengono): ViewerCraftStar li carica in `.viewercraftstar/import` e
PresetCraftAppLicator li importa nella libreria (gruppo *ViewerCraftStar*) e ne ripubblica l'elenco. Infine scrive un piccolo
resoconto (`.viewercraftstar/status.json`) con le foto della cartella che sono in libreria e la versione dei metadati del
telefono applicata (subito dopo un'importazione, senza aspettare il controllo successivo), e un battito
(`.viewercraftstar/heartbeat.json`, al massimo una volta al minuto) con cui il telefono sa se il PC di casa è acceso.
ViewerCraftStar mostra una casetta verde per le foto già in LightCraft e una blu per quelle con DNG e ultimi metadati al
sicuro sul NAS: entrambe si possono togliere dal telefono, e le blu entrano in LightCraft appena il PC di casa è acceso —
utile in viaggio con il PC spento.

**Icona nell'area di notifica.** Mentre l'importazione automatica è attiva c'è un'icona vicino all'orologio. Il pallino
dice lo stato: **verde** = attiva e in attesa, **blu** = foto in arrivo o importazione in corso, **giallo** = aspetta
la chiusura di LightCraft, **rosso** = errore. **Doppio clic** apre l'app; **tasto destro** mostra cosa sta facendo
(foto in arrivo, *Importazione: 4 di 12 foto*, quanto manca, ultima importazione) e i comandi *Controlla adesso*, *Apri
registro* e *Chiudi*. A importazione finita arriva una notifica di Windows. L'icona usa
[pystray](https://github.com/moses-palmer/pystray) (LGPLv3).

**Disinstallazione.** Da **Impostazioni › App › App installate** si apre la disinstallazione guidata di
PresetCraftAppLicator: toglie programma, collegamenti e importazione automatica (processo in background, icona e attività
pianificata) e, se vuoi, anche impostazioni e registro. Foto, librerie LightCraft e backup dei cataloghi non vengono mai
toccati. La pulizia la fa il pacchetto di installazione stesso, quindi avviene anche con `msiexec /x` o con la
disinstallazione silenziosa. Gli aggiornamenti fermano il processo in background e lo fanno ripartire alla fine.

Per la GR IV ci sono anche profili **misurati**: LUT ricavate confrontando la resa di LightCraft con l'anteprima salvata
dalla fotocamera nello stesso raw. Sono usati per Standard, Vivid, Positive Film, Negative Film, Cinema (Yellow) e (Green),
dove sono più fedeli dei profili Adobe in LightCraft, e per Hi-Contrast B&W, Bleach Bypass, Retro e Cross Processing, che
Adobe non fa. Per i tre Monotone resta il profilo Adobe. HDR Tone non è ancora coperto. Riproducono colore e toni, non grana
e vignettatura.

Alcuni stili Adobe li fornisce come profili **Look** (`.xmp`), per esempio *Cinema Yellow* e *Cinema Green* della GR IV:
PresetCraftAppLicator li trasforma in LUT `.cube` e li importa in LightCraft in un gruppo col nome della fotocamera. Il
risultato è vicino a quello della fotocamera ma non identico, perché LightCraft applica la LUT alla sua resa della foto.

Per gli stili senza un profilo (per esempio quelli di cui non si conosce ancora il codice, mostrati come *Stile
sconosciuto*) usa **Stili e profili…** e scegli tu il profilo da usare.

**Fotocamere supportate:** al momento solo le Ricoh GR (GR III, GR IIIx, GR IV) in DNG, provata con la GR IV. Il codice
legge anche lo stile dei file Pentax (DNG e PEF), ma non è stato provato. I profili Adobe non sono inclusi:
vengono usati quelli già presenti sul PC, o una cartella scelta nelle Impostazioni.
