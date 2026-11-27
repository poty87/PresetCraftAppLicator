# Guida della suite: LightCraft con le foto sul NAS

<a href="suite-guide.md">🇬🇧 English</a>

Questa guida spiega da zero come mettere in funzione i tre programmi della suite e come usarli tutti i giorni:

- **[PresetCraftAppLicator](https://github.com/poty87/PresetCraftAppLicator)** (PC Windows) — il centro del sistema:
  importa da solo in LightCraft le foto che arrivano sul NAS, porta le librerie sul telefono, applica le modifiche fatte
  dal telefono e, con le Ricoh GR, dà alle foto il profilo dello stile di scatto;
- **[ViewerCraftStar](https://github.com/poty87/ViewerCraftStar)** (Android) — la libreria LightCraft sul telefono:
  sfogli, cerchi (anche per soggetto, con l'IA), dai stelle e bandierine, sviluppi e, se vuoi, importi e spedisci a casa
  le foto nuove;
- **[CleanCraftNas](https://github.com/poty87/CleanCraftNas)** (PC Windows) — tiene pulito il NAS: cancella dal disco le
  foto che hai messo nel cestino di LightCraft e i raw già convertiti in DNG.

Ogni programma ha anche il suo manuale, con tutte le schermate e le opzioni: [PresetCraftAppLicator](manuale.md),
[ViewerCraftStar](https://github.com/poty87/ViewerCraftStar/blob/main/docs/manuale.md), [CleanCraftNas](https://github.com/poty87/CleanCraftNas/blob/main/docs/manuale.md).

Ogni programma funziona anche da solo. Gli esempi usano nomi inventati: il NAS si chiama `NAS-CASA`, la cartella
condivisa delle foto `Foto`, l'unità di rete `Y:`. Usa i tuoi.

## Indice

1. [Cosa serve](#1-cosa-serve)
2. [Come è organizzato: tre tipi di cartelle](#2-come-è-organizzato-tre-tipi-di-cartelle)
3. [Preparare il NAS](#3-preparare-il-nas)
4. [Preparare il PC](#4-preparare-il-pc)
5. [Installare PresetCraftAppLicator](#5-installare-presetcraftapplicator)
6. [Installare ViewerCraftStar sul telefono](#6-installare-viewercraftstar-sul-telefono)
7. [Installare CleanCraftNas](#7-installare-cleancraftnas)
8. [L'uso di tutti i giorni](#8-luso-di-tutti-i-giorni)
9. [Fuori casa: la VPN](#9-fuori-casa-la-vpn)
10. [Aggiornamenti, backup e PC nuovo](#10-aggiornamenti-backup-e-pc-nuovo)
11. [Problemi comuni](#11-problemi-comuni)

---

## 1. Cosa serve

**Indispensabile**

| Cosa | Note |
|---|---|
| PC con Windows 10 o 11 | Dove gira LightCraft. I programmi si installano per il tuo utente, senza diritti di amministratore. |
| [LightCraft](https://github.com/storytold/lightcraft) | Con le tue librerie. PresetCraftAppLicator usa il suo `lightcraft-cli.exe`, installato insieme a LightCraft. |
| Un NAS (o un server) con una cartella condivisa SMB | Qualsiasi marca: Synology, QNAP, TrueNAS, Unraid, anche una cartella condivisa di Windows. |

**Per il telefono** (facoltativo)

| Cosa | Note |
|---|---|
| Telefono Android 13 o successivo | Per ViewerCraftStar. |
| App di sincronizzazione del NAS | Solo se vuoi *spedire* foto dal telefono: un'app che carica una cartella del telefono sul NAS (Synology Drive, QNAP Qfile, Nextcloud, Syncthing…). Per sfogliare e modificare la libreria non serve. |
| [OpenVPN for Android](https://play.google.com/store/apps/details?id=de.blinkt.openvpn) e un server OpenVPN | Solo per usare il telefono **fuori casa** (vedi il [capitolo 9](#9-fuori-casa-la-vpn)). |

**Facoltativo sul PC**

| Cosa | A cosa serve |
|---|---|
| Scheda video NVIDIA (8–24 GB) | Il **Motore IA** per la ricerca per soggetto sul telefono. Funziona anche senza, con un modello più piccolo e più lento. Con 12 GB o più può anche scrivere le **descrizioni** delle foto. |
| Adobe DNG Converter (gratuito) | Convertire in DNG i raw proprietari (CR2, NEF, ORF, ARW, RAF…). PresetCraftAppLicator lo installa da solo se lo attivi. |
| Profili Adobe *Camera Matching* | Solo per i profili delle **Ricoh GR**: sono quelli installati con Lightroom o Camera Raw. Per la GR IV ci sono anche profili misurati inclusi. |

**Fotocamere.** La suite funziona con le foto di tutte le fotocamere che LightCraft apre. Sono specifici delle Ricoh GR
solo i profili dello stile di scatto e l'importazione diretta dalla fotocamera via Wi-Fi; le foto delle altre marche
restano con il profilo predefinito di LightCraft e tutto il resto funziona uguale.

---

## 2. Come è organizzato: tre tipi di cartelle

Prima di installare conviene avere chiaro dove sta cosa. Ci sono **tre tipi di cartelle**, da tenere separate:

| Cartella | Esempio | Cosa contiene | Chi la usa |
|---|---|---|---|
| **Foto** | `Y:\Ricoh`, `Y:\Canon` | I file delle foto (raw, JPEG). Restano sempre lì: LightCraft le legge da quella cartella. | LightCraft, PresetCraftAppLicator (le osserva), il telefono (vi scrive gli `.xmp`), CleanCraftNas (cancella) |
| **Librerie LightCraft** | `D:\Cataloghi\Ricoh` | Il catalogo di LightCraft (la cartella con il file `catalog.snap`). Meglio su un disco del PC. | LightCraft, PresetCraftAppLicator, CleanCraftNas |
| **Cartella di servizio** | `Y:\ViewerCraftStar` | Tutto quello che produce PresetCraftAppLicator: indice e anteprime delle librerie per il telefono, ricerca IA, modifiche fatte dal telefono, copia delle impostazioni, backup. **Nuova e vuota**, non dentro le foto né dentro i cataloghi. | PresetCraftAppLicator, ViewerCraftStar |

Il percorso dei dati, in breve:

```
  Telefono (ViewerCraftStar)                NAS                              PC
  ────────────────────────────   ───────────────────────────   ─────────────────────────────
  sfogli / cerchi / modifichi ◄── cartella di servizio ◄─────── PresetCraftAppLicator
  modifiche (stelle, sviluppo) ──► cartella di servizio ──────► applicate a LightCraft

  foto spedite dal telefono ─(app di sync)─► cartella foto ──► importate in LightCraft
  foto copiate dal PC ──────────────────────► cartella foto ──► importate in LightCraft
                                                              cestino LightCraft ─► CleanCraftNas
```

---

## 3. Preparare il NAS

Le schermate cambiano da una marca all'altra; i nomi tra parentesi sono quelli di Synology DSM, come esempio.

### 3.1 Cartella condivisa e utente

1. Crea (o usa) una **cartella condivisa** per le foto, per esempio `Foto` (*Pannello di controllo › Cartella condivisa*).
   Dentro puoi organizzare le foto come vuoi, per esempio una sottocartella per fotocamera.
2. Crea un **utente** per i programmi (o usa il tuo) con **lettura e scrittura** su quella cartella condivisa. Scrittura
   serve: il telefono vi scrive gli `.xmp`, PresetCraftAppLicator la cartella di servizio e i DNG convertiti,
   CleanCraftNas cancella.
3. Controlla che il servizio **SMB** sia attivo (*Pannello di controllo › Servizi file › SMB*).

### 3.2 Nome del NAS e indirizzo fisso

- **Nome del NAS:** solo lettere, numeri e trattino, al massimo 15 caratteri (per esempio `NAS-CASA`). **Niente trattino
  basso** (`_`), spazi o accenti: alcuni programmi non trovano i file su un NAS chiamato `MIO_NAS`.
- **Indirizzo fisso:** dai al NAS sempre lo stesso indirizzo nella rete di casa (prenotazione DHCP sul router, per
  esempio `192.168.1.10`). Serve all'app del telefono e alla VPN.

### 3.3 Cestino del NAS

Attiva il **cestino** sulla cartella condivisa (Synology: *Cartella condivisa › Modifica › Attiva Cestino*, cartella
`#recycle`; QNAP: *Servizi di rete e file › Cestino di rete*, cartella `@Recycle`). Così i file che CleanCraftNas cancella
restano recuperabili per un po'. PresetCraftAppLicator non guarda mai dentro queste cartelle.

### 3.4 Dove arrivano le foto dal telefono (solo se spedisci dal telefono)

Se vuoi spedire foto dal telefono, configura l'app di sincronizzazione del NAS sul telefono perché carichi la cartella
**«sincronizzata con il NAS»** di ViewerCraftStar (di predefinito `Pictures/VCS_Dng_Sync`) **in una cartella delle foto
osservata da PresetCraftAppLicator** (per esempio `Foto\Ricoh`). Con Synology Drive: *Backup delle foto*, cartella
sorgente `Pictures/VCS_Dng_Sync`, destinazione la cartella delle foto. Il caricamento lo fa l'app di sincronizzazione con
le sue regole (per esempio solo in Wi-Fi), anche fuori casa.

---

## 4. Preparare il PC

LightCraft trova ogni foto dal suo **percorso**. Con un NAS conta molto come lo colleghi.

### 4.1 Un'unità di rete con una lettera fissa

Collega la cartella condivisa a una lettera fissa e usa **sempre** quella quando aggiungi cartelle a LightCraft:

```powershell
net use Y: \\NAS-CASA\Foto /persistent:no
```

Se un giorno usi `Y:` e un altro `\\NAS-CASA\Foto`, la libreria vede le foto come mancanti.

### 4.2 La password, una volta sola

Salva la password del NAS nella Gestione credenziali di Windows, con il **solo nome utente** (senza `NAS-CASA\` davanti):

```powershell
cmdkey /add:NAS-CASA /user:tuoutente /pass
```

`/pass` senza valore chiede la password: non resta scritta da nessuna parte.

### 4.3 Ricollegare l'unità a ogni accesso

Windows spesso prova a ricollegare le unità di rete prima che la rete sia pronta, e poi chiede di nuovo la password o dà
l'errore 1219 (*«connessioni multiple ad un server … con più di un nome utente non sono consentite»*). Lo script
[`connect-nas.ps1`](https://github.com/poty87/CleanCraftNas/blob/main/tools/connect-nas.ps1) di CleanCraftNas evita
entrambe le cose: aspetta il NAS, chiude le connessioni rimaste aperte e collega l'unità con la password salvata. Copialo in
una tua cartella (per esempio `C:\Strumenti`) e fallo partire a ogni accesso con l'Utilità di pianificazione:

```powershell
$args = '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Strumenti\connect-nas.ps1" -Drive Y: -Share \\NAS-CASA\Foto'
$action  = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" -Argument $args
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = 'PT15S'
Register-ScheduledTask -TaskName 'Collega NAS' -Action $action -Trigger $trigger `
    -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive)
```

### 4.4 LightCraft e le librerie

1. Installa [LightCraft](https://github.com/storytold/lightcraft) e crea (o apri) le tue librerie.
2. Aggiungi le cartelle delle foto **dall'unità `Y:`** (per esempio `Y:\Ricoh`).
3. Tieni le librerie su un disco del PC (sono cataloghi: lavorano meglio in locale).

---

## 5. Installare PresetCraftAppLicator

### 5.1 Installazione

Scarica `PresetCraftAppLicator-Setup-x.y.z.exe` dall'[ultima versione](https://github.com/poty87/PresetCraftAppLicator/releases/latest)
e avvialo. Si installa per il tuo utente, senza diritti di amministratore, con tutto quello che serve (Python compreso).
Scegli la lingua e, se vuoi, il collegamento sul desktop.

### 5.2 La configurazione guidata

Al primo avvio parte la **configurazione guidata** (si riapre quando vuoi da *Impostazioni › Configurazione guidata…*):

1. **Come vuoi iniziare** — *Prima configurazione*, oppure *Ho già una cartella di servizio: ripristina le impostazioni
   salvate lì* (PC nuovo o reinstallato, vedi il [capitolo 10](#10-aggiornamenti-backup-e-pc-nuovo)).
2. **LightCraft e le sue librerie** — il programma trova da solo `lightcraft-cli.exe`; con *Aggiungi libreria…* scegli le
   cartelle delle librerie LightCraft.
3. **La cartella di servizio** — scegli o crea una cartella **nuova e vuota** sul NAS, per esempio `Y:\ViewerCraftStar`.
   Qui vanno indice per il telefono, anteprime, ricerca IA, copia delle impostazioni e (consigliato) i backup.
4. **Foto nuove in arrivo** — per ogni libreria, la cartella delle foto da osservare (per esempio `Y:\Ricoh` per la
   libreria Ricoh). È la cartella delle foto, non quella di servizio.
5. **Il telefono** — le librerie da vedere su ViewerCraftStar e le *anteprime sviluppate* (consigliate: il telefono
   mostra le foto come sono in LightCraft, ritocchi compresi).
6. **Ricerca per soggetti** (facoltativa) — installa il Motore IA (vedi sotto). Si può fare anche dopo.
7. **Riepilogo** — controlla e premi *Inizia*. Tutto si può cambiare anche dopo.

### 5.3 La finestra principale

In alto a destra: **Importazione automatica**, **Motore IA**, **Impostazioni** e **☰** (registro e backup). Al centro la
libreria scelta, le foto da esaminare e l'elenco con lo stato del profilo di ogni foto; in basso **Stili e profili…** e
**Applica i profili**.

- **Profili (Ricoh GR):** scegli quali foto esaminare (*Ultima importazione*, *Tutta la libreria*, *Filtri avanzati…*),
  controlla l'elenco (blu = riceverà il profilo, verde = già a posto, giallo = resta com'è) e premi *Applica i profili*.
  Con l'importazione automatica attiva lo fa da solo.
- Con il tasto destro su una foto: *Scegli il profilo per questa foto…* (solo quella) o *Cambia il profilo di tutte le
  foto con questo stile…* (la regola per quello stile).
- Prima di ogni modifica viene salvata una copia del catalogo (backup).

### 5.4 Importazione automatica

Da **Importazione automatica** (interruttore *Attiva* in alto, con lo stato), tre schede:

| Scheda | Opzioni |
|---|---|
| **Importazione** | Le cartelle osservate e la libreria di ognuna, *Includi le sottocartelle*, ogni quanti minuti controllare (2 di predefinito) e quanti secondi aspettare che un trasferimento sia finito (60). |
| **Telefono** | La cartella di servizio, le librerie da vedere sul telefono, il collegamento al Motore IA. |
| **Volti e DNG** | Nomi automatici dei volti (con la soglia di somiglianza) e **conversione in DNG** dei raw proprietari, anche di quelli già nelle librerie. |

Come si comporta:

- Un'attività pianificata la avvia all'accesso a Windows; un'**icona vicino all'orologio** ne mostra lo stato (verde in
  attesa, blu al lavoro, giallo se aspetta che chiudi LightCraft, rosso errore). Doppio clic apre il programma.
- Le foto di un trasferimento si importano **tutte insieme** quando non arriva più niente da un minuto, con un solo backup.
- **Se LightCraft è aperto aspetta** che lo chiudi. Una foto tolta dalla libreria non viene reimportata.
- **Conversione in DNG** (se attiva): un raw proprietario appena arrivato viene convertito in un DNG accanto a lui, con lo
  stesso nome, ed entra in LightCraft il DNG; l'originale resta nella cartella (lo togli poi con CleanCraftNas,
  *Raw già convertiti in DNG*). Per i raw già nelle librerie: il DNG prende il loro posto con stelle, bandierine,
  etichette, testi, sviluppo, pile e album, e il raw va nel cestino di LightCraft.

### 5.5 Motore IA (ricerca per soggetto sul telefono)

Dal pulsante **Motore IA**:

- **Installazione:** scegli il modello (quello consigliato dipende dalla scheda video: *giant* con NVIDIA da 16 GB, *huge*
  da 8 GB, *piccolo* senza scheda adatta), la **cartella** (10–22 GB) e la **lingua delle descrizioni** (italiano o
  inglese: quella in cui cercherai sul telefono). Motore e modello si scaricano in background.
- **Ricerca sul telefono:** *Cataloga le foto per la ricerca per soggetti*, la precisione del modello sul telefono (8 bit,
  come l'originale) e, con una scheda da 12 GB o più, *Scrivi le descrizioni delle foto* (più precisione nei dettagli:
  colori, oggetti).
- **Quando lavorare:** fermarsi se un altro programma usa la scheda video, e una fascia oraria per le analisi grandi.
- **Librerie per la ricerca IA:** la coda. La **prima analisi** di una libreria la decidi tu, dalla finestra principale
  (*Analizza per la ricerca IA*, con il tempo stimato); dopo, le foto nuove si analizzano da sole.

Il motore legge solo le anteprime: foto e cataloghi non vengono modificati. Se LightCraft è aperto o la memoria della
scheda video sta per finire si ferma e riprende dopo. Il modello di ricerca (MetaCLIP 2) è per **solo uso non commerciale**.

---

## 6. Installare ViewerCraftStar sul telefono

### 6.1 Installazione e permessi

1. Scarica l'`.apk` dall'[ultima versione](https://github.com/poty87/ViewerCraftStar/releases/latest) e aprilo sul
   telefono (Android chiede di consentire l'installazione dal browser o dal gestore file).
2. Apri l'app e concedi l'accesso alle foto (**Consenti tutto**), le notifiche e i dispositivi nelle vicinanze (per la
   fotocamera Ricoh). Poi attiva una volta la **gestione dei media** nella pagina di Android che l'app apre: spedire e
   spostare nel cestino non chiedono più conferma ogni volta. *Impostazioni › Permessi* li mostra tutti.

### 6.2 Impostazioni

| Sezione | Cosa impostare |
|---|---|
| **Lingua** | Come il telefono, italiano o inglese. |
| **Permessi** | Mostra i permessi concessi e chiede quelli che mancano. |
| **Cartelle dei raw sul telefono** | **Cartella delle foto nuove** (non sincronizzata: qui arrivano le foto da rivedere; predefinita `Pictures/VCS_Import_Photo`) e **cartella sincronizzata con il NAS** (quella che carica l'app di sincronizzazione; predefinita `Pictures/VCS_Dng_Sync`). |
| **NAS** | **Indirizzo IP** del NAS (per esempio `192.168.1.10`), **utente** e **password**, poi **Prova connessione**. Dopo la prova scegli la **cartella delle foto sul NAS** (quella dove arrivano le foto spedite) e se cercare i raw **anche nelle sottocartelle**. La password resta sul telefono, cifrata. |
| **Cartella di servizio** | La **stessa cartella di servizio** scelta in PresetCraftAppLicator (per esempio `ViewerCraftStar` nella cartella condivisa): da qui arrivano le librerie. |
| **Velocità del NAS** | Prova per qualche secondo diversi modi di leggere una foto grande dal NAS (una o più connessioni, una o più letture insieme): da provare a casa e in VPN. |
| **Sviluppo sul telefono** | La versione del motore di sviluppo e lo spazio usato dalle smart preview e dagli originali scaricati per l'editor (al massimo circa 1 GB ciascuno; oltre si tolgono i meno usati), con *Svuota*. |
| **Ricerca per soggetti (IA)** | Mostra quanto occupa il modello scaricato e lo elimina. |
| **Notifiche della spedizione** | Avvisare quando la spedizione è finita o sospesa. |
| **Valori predefiniti** | Autore e copyright da mettere sulle foto; passare alla foto successiva dopo una bandierina o un voto. |
| **Backup delle impostazioni** | *Salva backup* scrive uno zip nella cartella di servizio (impostazioni, metadati delle foto, modifiche in attesa, mai la password); *Ripristina…* lo riprende. |

Le impostazioni della **VPN** si aprono dalla scheda **VPN** (lo scudo) nella barra in basso: vedi il
[capitolo 9](#9-fuori-casa-la-vpn).

### 6.3 La fotocamera Ricoh GR (facoltativo)

Con il tondo **Importa** › **Fotocamera** l'app si collega a una Ricoh GR via Bluetooth e Wi-Fi e ne scarica le foto,
senza l'app del produttore. La prima volta mostra i passaggi per l'associazione (sulla fotocamera: *Wireless
Communication › Pairing › Execute Pairing*, poi OK due volte mentre si vede il QR).

---

## 7. Installare CleanCraftNas

Scarica `CleanCraftNas-Setup-x.y.z.exe` dall'[ultima versione](https://github.com/poty87/CleanCraftNas/releases/latest) e
avvialo (anche lui per il tuo utente, senza diritti di amministratore). Nella configurazione guidata aggiungi le librerie
LightCraft. In **Impostazioni**: lingua, percorso di `lightcraft-cli.exe` (trovato da solo), dove tenere i **backup** e
quanti, aggiornamenti.

---

## 8. L'uso di tutti i giorni

### 8.1 Portare a casa le foto: due strade

**A — Dal PC, come sempre.** Copi le foto nella cartella osservata (lettore di schede, programma della fotocamera,
qualsiasi strumento). PresetCraftAppLicator le importa da solo (ed eventualmente le converte in DNG e dà il profilo alle
Ricoh GR). Dal telefono le vedi nella Libreria come tutte le altre.

**B — Dal telefono (un plus).**

1. **Importa** (tondo in basso a sinistra in *Da rivedere*): **Fotocamera** (Ricoh GR) o **Disco esterno** (chiavetta,
   disco USB o lettore di schede collegato al telefono: la prima volta scegli la cartella e premi *Consenti*, poi si apre
   da solo). Scegli le foto e importa: finiscono nella cartella di lavoro.
2. **Rivedi** in *Da rivedere*: bandierine (scelta / scartata), stelle, etichette, titolo, didascalia, parole chiave e,
   se vuoi, un preset di LightCraft con l'anteprima.
3. **Spedisci al NAS** (tondo blu in basso a destra): le foto passano nella cartella sincronizzata, l'app di
   sincronizzazione le carica e ViewerCraftStar scrive accanto a ognuna un `.xmp` con le tue scelte. Le scartate vanno
   nel cestino del telefono (30 giorni).
4. In **Inviate** segui ogni foto: 🔴 non ancora sul NAS, 🔵 al sicuro sul NAS (entra in LightCraft quando il PC è
   acceso), 🟢 già in LightCraft. Le blu e le verdi si tolgono dal telefono con un tocco.

I formati che il telefono non riconosce come foto (su Android 17 i Canon CR3) passano dal PC (strada A).

### 8.2 La Libreria sul telefono

- **Sfoglia** per data, con le pile; tira giù la galleria per aggiornarla.
- **Cerca con l'IA** quello che c'è nella foto (*gatto arancione*, *tramonto al mare*), oppure **Filtri**: testo, stelle,
  date, etichette, fotocamera, bandierina, tipo di file.
- **Modifica** stelle, bandierine, etichette, testi (anche di molte foto insieme, con la pressione lunga), **raggruppa in
  pile**, **sposta nel cestino di LightCraft**, **scarica** sul telefono.
- **Sviluppa** con il motore di LightCraft: luce, colore, effetti, dettaglio, ritaglio e **maschere**, compresa la
  maschera IA *Oggetto* (un tocco sul soggetto).

Le modifiche si vedono subito con un pallino arancione: PresetCraftAppLicator le applica a LightCraft (con LightCraft
chiuso, dopo un backup) e il pallino sparisce quando l'indice le conferma.

### 8.3 Tenere pulito il NAS

1. In LightCraft (o dal telefono) metti nel **cestino** le foto che non vuoi.
2. Chiudi LightCraft e apri **CleanCraftNas**: vedi le foto nel cestino, i file da cancellare (anche il JPEG gemello, se
   vuoi) e lo spazio liberato. Per ogni foto puoi scegliere *cancella dal disco e dal cestino*, *solo dal cestino* o
   *lascia nel cestino* (tasto destro).
3. Premi **Cancella dal disco e svuota il cestino**: prima un backup del catalogo, poi i file vanno nel cestino del NAS.
4. **☰ › Raw già convertiti in DNG…** toglie i raw originali che hanno già il loro DNG in libreria. Un raw compare solo se
   nessuna foto lo usa, se il DNG dice di essere stato fatto proprio da quel file e se il DNG è senza perdita.

---

## 9. Fuori casa: la VPN

A casa il telefono parla con il NAS sulla rete locale. Fuori casa il NAS non è raggiungibile da internet (ed è giusto
così): il telefono entra nella rete di casa con una **VPN OpenVPN**, che è dentro ViewerCraftStar.

In breve, quello che serve:

1. **Una linea che riceva connessioni** (niente CGNAT; con la fibra a volte va chiesto l'IP pubblico all'operatore).
2. **Un nome per casa tua** con un servizio DDNS (per esempio `casa.esempio.net`), che segue l'indirizzo pubblico.
3. **Sul router:** indirizzo fisso del NAS e **inoltro di una porta UDP** (la guida usa la 1195; qualsiasi porta libera
   va bene) verso la porta 1194 del server OpenVPN sul NAS. Con due router in cascata (per esempio modem dell'operatore e router Wi-Fi) l'inoltro va fatto su tutti e due.
4. **Un server OpenVPN**: sul NAS (Synology: pacchetto gratuito *VPN Server*), sul router o su un PC. Esporta il profilo
   (`.ovpn`, o lo zip di Synology) e correggilo come spiegato nella guida (indirizzo esterno, e l'impostazione che evita
   le scritture interrotte sui dati mobili).
5. **Sul telefono:** installa *OpenVPN for Android* (non serve aprirlo), poi in ViewerCraftStar apri la scheda **VPN**
   nella barra in basso, importa il profilo, inserisci server, porta, utente e password e scegli come si accende: **manuale**, **all'avvio
   dell'app fuori casa** o **completamente automatica** (si spegne anche sul Wi-Fi di casa). Lo **scudo** nella barra in
   basso la accende e spegne.

Tutti i passaggi, con le schermate dei menu: **[guida all'accesso da fuori casa](https://github.com/poty87/ViewerCraftStar/blob/main/docs/remote-access.md#-italiano)**.

L'app di sincronizzazione del NAS di solito non ha bisogno della VPN: carica le foto per conto suo.

---

## 10. Aggiornamenti, backup e PC nuovo

- **Aggiornamenti:** PresetCraftAppLicator e CleanCraftNas controllano da soli se c'è una versione nuova e mostrano un
  link accanto al numero di versione; un clic scarica e installa (le impostazioni restano). ViewerCraftStar si aggiorna
  scaricando il nuovo `.apk` dalla pagina delle versioni.
- **Backup dei cataloghi:** prima di ogni modifica PresetCraftAppLicator e CleanCraftNas salvano una copia del catalogo
  della libreria (5 per libreria di predefinito). Dalle finestre **Backup…** li vedi e li elimini.
- **Impostazioni:** PresetCraftAppLicator tiene una copia delle sue impostazioni nella cartella di servizio
  (`settings\settings.json`); ViewerCraftStar le sue con *Salva backup*.
- **PC nuovo o reinstallato:** ricollega l'unità `Y:` (capitolo 4), reinstalla LightCraft e PresetCraftAppLicator e nella
  configurazione guidata scegli *Ho già una cartella di servizio: ripristina le impostazioni salvate lì*. Il Motore IA va
  reinstallato (i modelli sono sul PC), ma il lavoro fatto è nella cartella di servizio e non si rifà.

---

## 11. Problemi comuni

| Problema | Cosa fare |
|---|---|
| Il NAS chiede di nuovo la password, o errore 1219 all'avvio di Windows | Usa lo script di ricollegamento del [capitolo 4.3](#43-ricollegare-lunità-a-ogni-accesso) e salva la password con `cmdkey`. |
| LightCraft vede le foto come mancanti | La cartella è stata aggiunta con un percorso diverso (`\\NAS-CASA\…` invece di `Y:\…`) o la lettera è cambiata. In LightCraft: *Find Missing Photos* / *Locate Photo*. |
| Un programma non trova i file sul NAS ma Esplora risorse sì | Il nome del NAS ha caratteri non validi (trattino basso, spazi): rinominalo ([3.2](#32-nome-del-nas-e-indirizzo-fisso)). |
| Le foto spedite dal telefono restano rosse | L'app di sincronizzazione non le ha ancora caricate, o le carica in una cartella diversa da quella osservata ([3.4](#34-dove-arrivano-le-foto-dal-telefono-solo-se-spedisci-dal-telefono)). |
| Le foto restano blu | Il PC di casa è spento o LightCraft è aperto: entrano in LightCraft quando il PC è acceso e LightCraft chiuso. |
| L'importazione automatica non parte | Controlla l'icona vicino all'orologio: gialla = chiudi LightCraft; rossa = apri il registro (☰ › Apri registro). |
| Il telefono dice «NAS non raggiungibile» fuori casa | Accendi la VPN (scudo in basso) e controlla la [guida VPN](https://github.com/poty87/ViewerCraftStar/blob/main/docs/remote-access.md#-italiano). Android tiene una VPN alla volta: se un'altra è accesa (per esempio AdGuard), lo scudo lo spiega. |
| Lo schermo diventa nero per un attimo mentre lavora il Motore IA | Il driver della scheda video si riavvia sotto carico: abbassa il limite di potenza della scheda (per esempio all'80% con il programma della scheda video). Il motore riprende da dove era arrivato. |
| Un file CR3 non si importa dal telefono | Android non riconosce i CR3 come foto: copialo sul NAS dal PC (strada A). |
