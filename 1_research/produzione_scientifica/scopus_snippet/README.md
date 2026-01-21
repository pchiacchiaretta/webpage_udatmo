# Scopus Snippet Generator (ORCID → OpenAlex → Drupal 7)

Questo progetto genera **snippet HTML pronti da incollare in Drupal 7** con le pubblicazioni dei membri del laboratorio, recuperate automaticamente tramite **ORCID** usando i metadati di **OpenAlex** (senza API key Scopus).

Vengono prodotti 3 snippet (ognuno con **titolo + immagine hero + intro + lista numerata**):
- **Articoli** (riviste / journal)
- **Conferenze** (include anche EGU)
- **Libri e capitoli di libri**

In più viene generato un file di controllo con gli elementi esclusi dai filtri.

---

## Requisiti

- Python **3.10+**
- Accesso a internet
- Virtualenv consigliato

Dipendenze:
- `certifi` (serve soprattutto su macOS per evitare errori SSL)

---

## Installazione

1) Crea una cartella progetto e entra:
```bash
mkdir scopus_snippet
cd scopus_snippet
````

2. Crea e attiva un virtualenv:

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Crea `requirements.txt`:

```txt
certifi==2025.8.3
```

4. Installa:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## File di input

### 1) `orcid_members.csv`

Contiene l’elenco dei membri.

Esempio:

```csv
orcid,name,filter_profile
0000-0003-4971-4509,Piero Di Carlo,none
0000-0002-9164-7293,Eleonora Aruffo,none
0000-0003-2925-0006,Alessandra Mascitelli,none
0000-0003-1089-9809,Piero Chiacchiaretta,atmo_soft
```

* `orcid`: ORCID dell’autore
* `name`: nome leggibile (solo per log a schermo)
* `filter_profile`: nome del profilo filtro da usare (vedi sotto)

> Nota: lo script accetta anche la colonna `apply_filter` al posto di `filter_profile`.

---

### 2) Profili filtro in `filters/`

Crea una cartella `filters/` con uno o più JSON.

Esempi:

**`filters/none.json`** (nessun filtro: include tutto)

```json
{ "mode": "none" }
```

**`filters/atmo_soft.json`** (filtro “morbido”)

```json
{
  "mode": "include_if_any",
  "min_concept_score": 0.25,
  "include_concepts": ["Atmospheric science","Atmospheric chemistry","Aerosol","Air pollution","Meteorology","Climate"],
  "include_title_keywords": ["atmospher","aerosol","flux","deposition","boundary layer","pm2.5","pm10","ozone","dust"],
  "exclude_title_keywords": ["multi-echo"],
  "exclude_dois": []
}
```

Come funziona il filtro:

* Se `mode` è `none` → include tutto.
* Se `mode` è `include_if_any`:

  * esclude se matcha `exclude_title_keywords` o `exclude_dois`
  * include se matcha una keyword del titolo **oppure** se ha un concept tra `include_concepts` con score ≥ `min_concept_score`

> Il matching sulle keyword è robusto: gestisce trattini Unicode (`–`, `—`, ecc.), spazi e punteggiatura.

---

## Come si esegue

Assumendo che lo script si chiami `orcid_to_drupal_snippet.py`:

```bash
python orcid_to_drupal_snippet.py
```

Opzioni utili:

```bash
python orcid_to_drupal_snippet.py \
  --members orcid_members.csv \
  --filters-dir filters \
  --max 120
```

---

## Output generati

* `snippet_journals.html`
  **Articoli** con immagine: `/sites/st02/files/pubblicazioni-big.jpg`

* `snippet_conferences.html`
  **Conferenze** (EGU inclusa) con immagine: `/sites/st02/files/conferenze-big.jpg`

* `snippet_books.html`
  **Libri e capitoli di libri** con immagine: `/sites/st02/files/libri-bg.jpg`

* `excluded.html`
  Lista (non wrappata) degli elementi scartati dai profili filtrati (utile per tarare i filtri).

---

## Incollare in Drupal 7

1. Apri il file snippet che ti serve (es. `snippet_journals.html`)
2. Copia tutto l’HTML
3. Incolla nella pagina/nodo Drupal (con formato testo che consenta HTML)

> Se Drupal filtra i tag `<style>`, sposta il CSS nel tema Drupal e lascia nello snippet solo l’HTML (chiedimi e ti preparo la versione “senza style”).

---

## Note su classificazione “Conferenze” e “Libri”

* **Conferenze**: riconosciute da `work.type` e/o dal tipo della source (`conference`) e da euristiche sul nome.
* **EGU**: viene forzata dentro “Conferenze” anche se non marcata come conference.
* **Libri/Capitoli**: riconosciuti principalmente da `work.type` (`book`, `book-chapter`, `edited-book`) con fallback.

Se trovi un record classificato male, si può correggere con una regola extra o aggiungendo keyword di esclusione/inclusione nel profilo filtro.

---

## Aggiornare i risultati

Per aggiornare le pubblicazioni:

1. aggiorna `orcid_members.csv` (se cambiano membri)
2. eventualmente aggiorna i profili in `filters/`
3. rilancia lo script

Gli snippet verranno rigenerati.

```
::contentReference[oaicite:0]{index=0}
```
