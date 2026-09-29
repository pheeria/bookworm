# bookworm

A FastAPI service that turns a text prompt into a print-ready book cover in the
trade formats German literary publishers actually use.

```
POST /generate  { text, title, author }
   -> Claude writes the art direction and the image prompt
   -> OpenAI paints the artwork
   -> the typography engine sets the type in real trade geometry
   -> front.png / front.jpg / front.svg / spread.pdf
```

The division of labour is deliberate. Image models cannot set type — generated
lettering is malformed, unlicensed and unprintable — so they never see the title.
The image model paints artwork; the title, author, Gattungsbezeichnung, spine and
back-cover copy are set afterwards as outlined vector type at the exact trim size.

## Quick start

```bash
brew install cairo          # Debian: apt-get install libcairo2
uv sync --extra dev
cp .env.example .env        # add your keys; it runs without them, see below
uv run uvicorn bookworm.main:app --reload
```

```bash
curl -X POST localhost:8000/generate -H 'content-type: application/json' -d '{
  "text": "Eine Übersetzerin kehrt in die Stadt ihrer Kindheit zurück und findet dort nur noch die Sprache wieder, in der sie nie gelebt hat.",
  "title": "Die zweite Sprache",
  "author": "Helena Marr",
  "format": "kiwi_klappenbroschur",
  "pages": 336,
  "isbn": "978-3-462-00123-4",
  "price": "€ 24,00 [D]"
}'
```

The response carries the art direction, the full print geometry, and URLs for each
asset. `?inline=front_png` returns the image directly instead of JSON.

Generate covers from the sample EPUBs, reading title, author and page count out of
the container:

```bash
uv run scripts/from_epub.py examples/*.epub --local
```

## It runs without either key

Both model steps degrade rather than fail, so the service is useful offline and a
missing key never turns into a 500:

| Missing | What happens |
|---|---|
| `ANTHROPIC_API_KEY` | A deterministic brief is derived from a hash of the input: palette, layout, typeface and genre line are chosen from the built-in catalogue, and the blurb is cut from the input text. `art_direction_meta.source` reports `fallback`. |
| `OPENAI_API_KEY` | The cover renders with a procedural vector motif instead of painted artwork, and says so in `notes`. |

Everything else — geometry, typesetting, rasterising — is local and deterministic.

## Formats

`GET /catalogue` lists them with computed spine widths. These are the customary
trade formats associated with each imprint, taken from general book-trade practice
rather than from a publisher's production spec sheet. **Reconcile against the
`Umschlagvorgabe` the publisher's production department sends you before going to
press.**

| Key | Format | Trim (mm) | Binding |
|---|---|---|---|
| `rororo_taschenbuch` | rororo Taschenbuch | 118 × 190 | Taschenbuch |
| `rowohlt_paperback` | Paperback | 135 × 205 | Paperback |
| `rowohlt_hardcover` | Hardcover mit Schutzumschlag | 140 × 215 | Hardcover, 85 mm Klappen |
| `kiwi_paperback` | KiWi-Paperback | 125 × 190 | Paperback |
| `kiwi_taschenbuch` | KiWi-Taschenbuch | 125 × 200 | Taschenbuch |
| `kiwi_klappenbroschur` | Klappenbroschur | 135 × 215 | Klappenbroschur, 90 mm Klappen |
| `kiwi_hardcover` | Hardcover Leinen mit Schutzumschlag | 135 × 210 | Hardcover, 85 mm Klappen |
| `suhrkamp_taschenbuch` | suhrkamp taschenbuch | 108 × 177 | Taschenbuch |
| `din_a5_hardcover` | Hardcover DIN A5 | 148 × 210 | Hardcover, 85 mm Klappen |
| `grossformat_hardcover` | Großformatiges Hardcover | 155 × 230 | Hardcover, 95 mm Klappen |

What the geometry accounts for:

- **Beschnittzugabe** 3 mm on every outer edge.
- **Sicherheitsabstand** 5 mm — live type is kept inside it.
- **Rückenstärke** from the page count: `Bogen × (g/m² × Volumen ÷ 1000)`. A
  hardcover jacket additionally clears both boards plus a rounding allowance.
  Below 7 mm the spine is left as flat colour and `notes` says why.
- **Überstand** 3 mm on hardcovers, so the jacket is sized to the case rather
  than the book block.
- **Klappen** on flapped formats, laid out as `back flap | back | spine | front | front flap`.
- A reserved white **EAN field** (45 × 30 mm) on the back cover. It is reserved,
  not drawn — the printer drops in the real barcode film.
- `marks: true` adds trim and fold lines for proofing.

## Output

| Asset | What it is |
|---|---|
| `front_svg` / `spread_svg` | Vector master. Type is already outlined. |
| `front_pdf` / `spread_pdf` | Vector PDF at exact physical page size, **no embedded fonts** — nothing to license or substitute. |
| `front_png` / `spread_png` | 300 dpi by default, with the resolution stamped in the file. |
| `front_jpg` | sRGB JPEG for catalogue and web use. |
| `direction_json` | The brief, geometry and seed, for reproducing the cover. |

PDFs are RGB. A repro house that needs CMYK with a specific ICC profile should
convert the vector PDF; converting here would mean guessing the press profile.

## Typography

Type is shaped with HarfBuzz and emitted as outlined glyph paths, so one set of
shaped advances drives the line fitting, the SVG, the PNG and the PDF — there is
no second text engine to disagree with the first, and the output has no font
dependency.

- Display titles are fitted by trying every line count up to four and keeping
  whichever gives the largest type, with the line breaks balanced to even out the
  rag.
- Type is positioned off the **cap line**, not the baseline, which is what the eye
  aligns to at display sizes.
- Capital umlauts open the leading automatically. `SCHULD / SÜHNE` set at 0.88
  leading collides; German titles are full of Ä, Ö and Ü.
- Display sizes get slightly negative tracking; small labels get positive.
- `ß` uppercases to `SS`, per German typographic practice.

Faces are the macOS system fonts, grouped into logical families
(`geometric` is Futura, `neoclassical` is Didot, `grotesk` is Helvetica Neue, and
so on) with a fallback chain. Drop TTF/OTF files into `src/bookworm/fonts/` to
override. **Check the licence before printing commercially** — the bundled macOS
faces are licensed for use on the machine, not for redistribution.

## Layouts

| Template | Idiom |
|---|---|
| `kiwi_flat` | Flat ground, left-aligned type stack in the upper half, artwork below. |
| `rororo_band` | Full-bleed ground with a horizontal band carrying the title. |
| `type_block` | Display type filling the cover edge to edge, each line set to its own size. No imagery. |
| `didone_centre` | Centred neoclassical setting between hairline rules. |
| `photo_duotone` | Artwork across the upper two thirds, type below. |

Any part of the brief can be pinned: `template`, `type_family`, `palette`,
`artwork`, `motif`, `genre_line`, `blurb`, `seed`. Pinning the template moves the
typeface with it unless you pin that too. Pinning a `motif` implies you want it
drawn.

Sixteen palettes ship in the flat, slightly austere register the idiom lives in —
`rororo_rot`, `kobalt`, `schwefel`, `pergament`, `graphit` and so on. The art
direction model may also return its own hex values.

`spine_direction` defaults to `top_to_bottom`, which is what most contemporary
German trade books do; `bottom_to_top` gives the older continental convention.

## Models

Set via env: `BOOKWORM_CLAUDE_MODEL` (default `claude-opus-5`) and
`BOOKWORM_IMAGE_MODEL` (default `gpt-image-2`; the installed SDK also accepts
`gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`, `gpt-image-1.5`, `gpt-image-1`).
Artwork is generated once, for the front panel, and cover-cropped to the planned
placement. The response reports the artwork's `effective_dpi` over that placement
and flags it in `notes` when it had to be resampled up, so an upscale is never
passed off as native detail.

## Tests

```bash
uv run pytest
```

36 tests, no network and no credentials: the art-direction and image-generation
steps are exercised through their fallbacks, and the OpenAI path is covered with a
stub client. A live image-generation call has not been exercised — that needs a
real `OPENAI_API_KEY`.
