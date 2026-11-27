# Manuale di PresetCraftAppLicator

<a href="manual.md">🇬🇧 English</a>

Questo manuale descrive tutte le finestre e le opzioni del programma. Per installare la suite da zero (NAS, PC, telefono,
VPN) e per il flusso di lavoro di tutti i giorni parti dalla **[guida della suite](guida-suite.md)**.

## Indice

1. [La finestra principale](#1-la-finestra-principale)
2. [Profili della fotocamera (Ricoh GR)](#2-profili-della-fotocamera-ricoh-gr)
3. [Importazione automatica](#3-importazione-automatica)
4. [Motore IA](#4-motore-ia)
5. [Impostazioni](#5-impostazioni)
6. [Backup e registro](#6-backup-e-registro)
7. [L'icona vicino all'orologio](#7-licona-vicino-allorologio)
8. [Configurazione guidata, aggiornamenti e disinstallazione](#8-configurazione-guidata-aggiornamenti-e-disinstallazione)
9. [Cosa scrive e dove](#9-cosa-scrive-e-dove)

---

## 1. La finestra principale

**In alto a destra**

| Pulsante | A cosa serve |
|---|---|
| **Importazione automatica…** | Le cartelle osservate, il telefono, volti e DNG ([capitolo 3](#3-importazione-automatica)). Il pallino ● davanti al nome indica che è attiva. |
| **Motore IA…** | La ricerca per soggetto sul telefono ([capitolo 4](#4-motore-ia)). |
| **Impostazioni** | Lingua, LightCraft, profili, backup, aggiornamenti ([capitolo 5](#5-impostazioni)). |
| **☰** | *Apri registro* e *Backup…* ([capitolo 6](#6-backup-e-registro)). |

**Riquadro Libreria**

- Il menu con le librerie LightCraft che conosce; sotto, il percorso della libreria scelta e lo stato della ricerca IA
  (*N foto catalogate · M descritte*, oppure il pulsante **Analizza per la ricerca IA** se non è ancora stata analizzata).
- **Aggiungi libreria…** (la cartella con il file `catalog.snap`), **Togli dall'elenco** (la dimentica soltanto, non
  cancella niente), **Aggiorna** (rilegge la libreria).
- **Foto da esaminare:** *Tutta la libreria*, *Ultima importazione* (con data, ora e numero di foto), *Dall'importazione
  del … in poi* (le ultime 5 importazioni). **Filtri avanzati…** apre lo storico di tutte le importazioni: scegli un
  intervallo di date dal calendario (i giorni con importazioni sono in blu) e, se vuoi, di ore, oppure seleziona solo
  alcune importazioni; *Esamina queste foto*.

**Il centro**

- Un messaggio dice cosa succederà (per esempio *2 foto riceveranno il profilo della fotocamera*).
- Quattro riquadri con i numeri: *Foto esaminate*, *Da aggiornare*, *Già a posto*, *Senza profilo*.
- L'elenco delle foto. Colori: **blu** riceverà il profilo, **verde** già a posto, **giallo** resta com'è, **senza
  colore** nessun profilo. Le colonne (pulsante **Colonne**, o tasto destro sull'intestazione) sono: *File*, *Cartella*,
  *Fotocamera*, *Scattata*, *Importata*, *Stile in fotocamera*, *Profilo attuale*, *Profilo fotocamera*, *Stato*. Si
  ordinano con un clic sull'intestazione e si spostano trascinandole. Il divisorio tra elenco e anteprima si sposta:
  quando le colonne non ci stanno compare la barra per scorrere.
- A destra l'**anteprima** della foto selezionata (quella salvata dalla fotocamera nel raw), con data, fotocamera, stile e
  il profilo che riceverà.

**In basso**

- Versione, **☕ Offrimi un caffè**, e il link *⬆ Versione x disponibile* quando ce n'è una nuova.
- **Stili e profili…** e **Applica i profili**.
- Le righe di stato: avanzamento del Motore IA (con *Dettagli* e *Pausa*), ultima importazione, stato dell'importazione
  automatica e prossimo controllo.

---

## 2. Profili della fotocamera (Ricoh GR)

Le fotocamere applicano lo stile scelto (sulle Ricoh GR: *Image Control*, per esempio Positive Film) alla JPEG e
all'anteprima nel raw, ma non ai dati raw: per questo in LightCraft i colori cambiano subito dopo l'apertura.
PresetCraftAppLicator legge da ogni raw lo stile scelto, trova il profilo corrispondente, lo importa nella libreria e lo
assegna alla foto. Cambia solo il profilo: esposizione, bilanciamento del bianco e le altre regolazioni restano.

**Applicare i profili a mano**

1. Chiudi LightCraft (il programma aspetta se è aperto).
2. Scegli la libreria e le foto da esaminare, controlla l'elenco.
3. Premi **Applica i profili**: prima un backup del catalogo, poi i profili.

Con l'importazione automatica attiva le foto nuove ricevono il profilo da sole.

**Tasto destro su una o più foto**

| Voce | Effetto |
|---|---|
| *Applica il profilo* / *Non toccare* | Cambia la scelta per quelle foto (la scelta si ricorda). |
| *Scegli il profilo per questa foto…* (o *per queste N foto…*) | Un profilo diverso solo per quelle foto; nell'elenco compare «· solo questa foto». |
| *Cambia il profilo di tutte le foto con questo stile…* | Cambia la regola dello stile per quella fotocamera: vale per tutte le foto con quello stile, anche future. Chiede conferma. |
| *Mostra in Esplora file* | Apre la cartella del file. |

Le foto a cui hai già dato un altro profilo in LightCraft non vengono toccate, a meno che tu non lo chieda.

**Stili e profili…** elenca gli stili trovati nelle foto esaminate e per ognuno il profilo da usare: *Automatico* (quello
trovato dal programma), un profilo a scelta o *Non cambiare*. Serve anche per gli stili senza profilo (*Stile
sconosciuto*).

**Quali profili usa:** i profili Adobe *Camera Matching* (`.dcp`) già sul PC (installati con Lightroom o Camera Raw) o in
una cartella scelta nelle Impostazioni; per la GR IV anche profili **misurati** inclusi (Standard, Vivid, Positive Film,
Negative Film, Cinema Yellow e Green, Hi-Contrast B&W, Bleach Bypass, Retro, Cross Processing), più fedeli o non fatti da
Adobe. Alcuni stili Adobe arrivano come profili *Look* (`.xmp`): vengono trasformati in LUT `.cube` e importati in un
gruppo col nome della fotocamera. HDR Tone non è ancora coperto.

**Fotocamere:** Ricoh GR III, GR IIIx, GR IV in DNG (provata con la GR IV). Il codice legge anche lo stile dei Pentax (DNG
e PEF), non provato. Le foto delle altre fotocamere tengono il profilo predefinito di LightCraft.

---

## 3. Importazione automatica

In cima l'interruttore **Attiva** (*controlla le cartelle in background, anche dopo il riavvio del PC*) con lo stato:
ultimo controllo e ultima importazione. Sotto, tre schede.

### Scheda Importazione

- **Cartelle osservate:** l'elenco cartella → libreria. **Aggiungi cartella…** con *Importa nella libreria* e *Includi le
  sottocartelle* (per foto organizzate per anno, mese o giorno; per cambiarlo togli e riaggiungi la cartella). Se dentro
  ci sono già foto non in libreria, chiede se importarle o ignorarle. **Togli** smette di osservarla.
- **Quando importare:** *Controlla ogni* (minuti, 2 di predefinito) e *Trasferimento finito dopo* (secondi senza novità,
  60): le foto di un trasferimento vengono importate tutte insieme, con un solo backup, quando non arriva più niente.

Le foto restano dove sono: non vengono copiate né spostate. Se LightCraft è aperto aspetta. Una foto tolta dalla
libreria non viene reimportata. Le cartelle di servizio dei NAS (`#recycle`, `@eaDir`, `@Recycle`…) vengono ignorate.

### Scheda Telefono

- **Cartella di servizio sul NAS:** *Scegli o crea…* una cartella nuova e vuota (per esempio `Y:\ViewerCraftStar`). Nell'app
  ViewerCraftStar imposta la stessa cartella in *Impostazioni › Cartella di servizio*.
- **Librerie sul telefono:** le librerie da vedere nella sezione Libreria dell'app.
- **Ricerca per soggetti (IA):** lo stato e il pulsante *Motore IA…*.

Per ogni libreria spuntata il programma scrive nella cartella di servizio indice, anteprime sviluppate e smart preview per
l'editor del telefono, li aggiorna quando la libreria cambia (con LightCraft chiuso), e applica le modifiche fatte dal
telefono: stelle, bandierine, etichette, testi, pile, cestino, sviluppo e maschere (dopo un backup del catalogo).

### Scheda Volti e DNG

- **Volti — Dai il nome da solo quando la somiglianza è almeno N %:** con il riconoscimento dei volti di LightCraft, solo
  per le persone a cui hai già dato il nome su almeno 5 volti e solo se nessun'altra persona è quasi altrettanto simile.
  Sul telefono questi nomi hanno il bordo tratteggiato: un tocco per confermarli o cambiarli. **Annulla i nomi
  automatici** toglie quelli mai confermati.
- **Raw in DNG — Converti in DNG i raw proprietari prima di importarli** (CR2, NEF, ORF, ARW, RAF…), con Adobe DNG
  Converter (gratuito; *Installa Adobe DNG Converter* se manca). Il DNG nasce accanto al raw, con lo stesso nome, ed è
  quello che entra in LightCraft; l'originale resta nella cartella (si toglie con CleanCraftNas, *Raw già convertiti in
  DNG*). **Converti anche quelli già nelle librerie (N):** in background, con LightCraft chiuso e dopo un backup, il DNG
  prende il posto del raw con stelle, bandierina, etichetta, testi, parole chiave, sviluppo, pila e album; il raw va nel
  cestino di LightCraft.

---

## 4. Motore IA

Il Motore IA cataloga le foto delle librerie che scegli, così ViewerCraftStar le trova per quello che contengono
(*gatti*, *auto d'epoca*). Lavora in background a bassa priorità, solo sulle foto nuove.

**Installazione** (dalla configurazione guidata o da questa finestra): scegli il **modello** (consigliato in base alla
scheda video: *giant* con NVIDIA da 16 GB, *huge* da 8 GB, *piccolo* senza scheda adatta), la **cartella** (10–22 GB) e la
**lingua delle descrizioni**. Motore (ambiente Python con PyTorch) e modello si scaricano in background.

**Riquadro Ricerca sul telefono**

- *Cataloga le foto per la ricerca per soggetti* — acceso/spento.
- *Modello per il telefono* — 8 bit (stessi risultati dell'originale) o 4 bit (più piccolo).
- *Scrivi le descrizioni delle foto* — con una GPU da 12 GB o più: una o due frasi e parole chiave per foto, con
  Qwen3-VL; la ricerca sul telefono diventa più precisa nei dettagli. Circa mezzo secondo a foto su una GPU da 24 GB
  (2–3 ore ogni 18.000 foto la prima volta). Se c'è un modello di descrizioni più nuovo per la tua scheda, lo propone.
- *Lingua delle descrizioni* — italiano o inglese: quella in cui cerchi sul telefono. Chi ha già descrizioni le tiene;
  cambiandola, dopo una conferma con il tempo stimato, vengono riscritte poco alla volta.

**Riquadro Quando lavorare**

- *Fermati se un altro programma usa la scheda video (giochi, render) e riprendi dopo.*
- *Analisi grandi solo dalle … alle …* — fascia oraria per le prime analisi (oltre 1.000 foto); le foto nuove di
  un'importazione si analizzano comunque subito.

**Riquadro Librerie per la ricerca IA:** la coda, con l'ordine (↑ ↓) e ✕ per toglierne una. La **prima analisi** di una
libreria si avvia dalla finestra principale (*Analizza per la ricerca IA*, con numero di foto e tempo stimato).

**Pulsanti:** *Pausa/Riprendi*, *Cambia modello o cartella…* (il motore si sposta, non si riscarica), *Rimuovi il
motore*, *Registro*.

Il motore legge solo le anteprime: foto e cataloghi non vengono modificati. Si ferma (salvando il lavoro) se LightCraft è
aperto o se la memoria della scheda video sta per finire, e riprende da solo; se Windows riavvia il driver della scheda
video riprende da dove era arrivato. Il modello di ricerca MetaCLIP 2 è sotto licenza CC BY-NC 4.0: **solo uso non
commerciale**.

---

## 5. Impostazioni

| Riquadro | Opzioni |
|---|---|
| **Lingua** | Automatica (lingua di Windows), italiano, inglese, tedesco, spagnolo, francese, portoghese. |
| **Percorso di lightcraft-cli.exe** | Trovato da solo nella cartella di installazione di LightCraft; *Sfoglia…* per indicarlo. |
| **Cartella di profili aggiuntiva** | Facoltativa: dove cercare altri profili `.dcp`/`.xmp` oltre a quelli di Lightroom e Camera Raw. |
| **Backup** | *Accanto alla libreria* (predefinito) o *In questa cartella* (per esempio la cartella di servizio), e quanti backup tenere per libreria (5). |
| **Aggiornamenti** | *Controlla gli aggiornamenti all'avvio* e *Controlla ora*. |

In fondo: versione, *Offrimi un caffè*, **Configurazione guidata…**.

---

## 6. Backup e registro

- Prima di ogni modifica a una libreria (profili, importazioni, modifiche dal telefono, conversioni) il programma salva una
  copia del catalogo in `PresetCraftAppLicator backup - <libreria>`. Se ne tengono le ultime N (Impostazioni).
- **☰ › Backup…** li elenca con data e dimensione: *Apri cartella*, *Elimina selezionati*, *Elimina tutti*, *Elimina i
  backup più vecchi di N giorni* (1 = tieni solo quelli di oggi). Finiscono nel Cestino di Windows (o nel cestino del NAS).
- Per tornare a un backup: chiudi LightCraft e copia il contenuto della cartella del backup al posto dei file del catalogo.
- **☰ › Apri registro** apre `presetcraft.log` con tutto quello che il programma ha fatto.

---

## 7. L'icona vicino all'orologio

Mentre l'importazione automatica è attiva c'è un'icona nell'area di notifica. Il pallino dice lo stato: **verde** attiva
e in attesa, **blu** foto in arrivo o lavoro in corso, **giallo** aspetta la chiusura di LightCraft, **rosso** errore.
**Doppio clic** apre il programma; **tasto destro** mostra cosa sta facendo (importazione, conversioni, Motore IA, ultima
importazione) e i comandi *Controlla adesso*, *Apri registro*, *Chiudi*. A lavoro finito arriva una notifica di Windows.

---

## 8. Configurazione guidata, aggiornamenti e disinstallazione

- **Configurazione guidata:** al primo avvio e da *Impostazioni › Configurazione guidata…*. I passi sono descritti nella
  [guida della suite](guida-suite.md#52-la-configurazione-guidata); con *Ho già una cartella di servizio* ripristina le
  impostazioni salvate lì (PC nuovo o reinstallato).
- **Aggiornamenti:** il programma controlla da solo e mostra il link accanto alla versione; un clic scarica, verifica e
  installa la nuova versione (le impostazioni restano; il processo in background si ferma e riparte). Installazione
  silenziosa: `PresetCraftAppLicator-Setup-x.y.z.exe --silent` oppure `msiexec /i PresetCraftAppLicator-x.y.z.msi /qn`.
- **Disinstallazione:** da *Impostazioni di Windows › App*. Toglie programma, collegamenti e importazione automatica e, se
  vuoi, impostazioni e registro; chiede se togliere anche il Motore IA e i suoi file nella cartella di servizio. Foto,
  librerie e backup non vengono mai toccati.

---

## 9. Cosa scrive e dove

| Dove | Cosa |
|---|---|
| Librerie LightCraft | Profili, importazioni e modifiche, sempre tramite `lightcraft-cli` e dopo un backup. |
| Cartelle delle foto | Solo i DNG convertiti (se attivo) e la cartella nascosta `.viewercraftstar` (preset per il telefono, resoconto per le casette, battito del PC). Le foto non vengono mai modificate. |
| Cartella di servizio | `libraries\<libreria>\` (indice, anteprime, smart preview, ricerca IA, modifiche dal telefono), `settings\` (copia delle impostazioni), `backups\` (se scelto). |
| `%LOCALAPPDATA%\PresetCraftAppLicator` | Impostazioni, registro, stato dell'importazione automatica; il Motore IA nella cartella scelta. |
